/*
 * ============================================================
 *  PROJET ESP32 : CAPTEUR DE QUALITÉ DE L'AIR INTÉRIEUR (IAQ)
 *  VERSION FUSIONNEE V2 ROBUSTE (Maison + Backend Android)
 * ============================================================
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Adafruit_CCS811.h>
#include <Adafruit_ADS1X15.h>
#include <DHT.h>
#include <esp_task_wdt.h>
#include <time.h>
#include <LittleFS.h>
#include <ArduinoOTA.h>
#include <Wire.h>

#define WDT_TIMEOUT 15  

// --- CONFIGURATION DE VOTRE BOX INTERNET (A CHANGER !) ---
const char* WIFI_SSID     = "IdoomFibre_AT3P2evDS_EXT";
const char* WIFI_PASSWORD = "zevwhF9e";

// --- CONFIGURATION SERVEURS RENDER (Double Envoi) ---
const char* SERVER_URL_1 = "https://iaq-maison.onrender.com/api/mesures";
const char* SERVER_URL_2 = "https://iaq-backend.onrender.com/api/mesures";
const char* HEALTH_URL_1 = "https://iaq-maison.onrender.com/api/health";

const char* API_KEY      = "SECRET_IAQ_2026";
const char* OTA_HOSTNAME = "esp32-salon"; // Nom visible sur le reseau pour OTA

WiFiClientSecure secureClient;

// Timings
const unsigned long POLL_INTERVAL_MS = 2000;
const unsigned long SEND_INTERVAL_MS = 5000;

// PINS
#define DHT_PIN          4
#define DHT_TYPE         DHT22
#define RX_CO2           18
#define TX_CO2           17
#define BUZZER_PIN       15
#define VENTILATOR_PIN   38
#define LED_OK_PIN       -1
#define LED_ALERT_PIN    -1

// I2C PINS
static const int SDA_CCS = 8;
static const int SCL_CCS = 9;
static const int SDA2_PIN = 2;
static const int SCL2_PIN = 1;

// MQ-7 Calibration Automatique
static const float MQ7_RL = 10000.0f; // 10kOhm
static const float MQ7_DIVIDER_FACTOR = 1.5f; // R1=10k, R2=20k -> (10+20)/20 = 1.5
static const float VCC_DIV = 5.0f;

static const uint32_t R0_CAL_MS = 60000;
bool r0Ready = false;
float R0 = 10.0f; // Valeur par défaut
double r0Sum = 0.0;
uint32_t r0Count = 0;
uint32_t bootTime = 0;

// SEUILS D'ALERTE AVEC HYSTERESIS (seuils Nini — plus sensibles pour la maison)
const float ALERT_CO2_ON   = 2000.0;  const float ALERT_CO2_OFF   = 1800.0; // ppm
const float ALERT_TVOC_ON  = 220.0;   const float ALERT_TVOC_OFF  = 150.0;  // ppb
const float ALERT_CO_ON    = 25.0;    const float ALERT_CO_OFF    = 18.0;   // ppm
const float ALERT_TEMP_ON  = 27.0;    const float ALERT_TEMP_OFF  = 25.0;   // C
const float ALERT_HUM_ON   = 60.0;    const float ALERT_HUM_OFF   = 55.0;   // %

// MACHINE A ETATS : ALERTE
enum AlertState { IDLE, BUZZING, FAN_ON };
AlertState alertState = IDLE;
uint32_t buzzStart = 0;
const uint32_t BUZZ_DURATION_MS = 2000;

// VARIABLES GLOBALES CAPTEURS
float current_co2  = NAN;
float current_tvoc = NAN;
float current_co   = NAN;
float current_temp = NAN;
float current_hum  = NAN;
String current_etat_air = "Air normal";

bool buzzerState = false;
bool fanState = false;

Adafruit_CCS811 ccs811;
DHT dht(DHT_PIN, DHT_TYPE);
Adafruit_ADS1115 ads;
HardwareSerial CO2Serial(1);

bool ccs811_ok = false;             
bool ads1115_ok = false;
unsigned long dernierEnvoi = 0;
unsigned long dernierPoll = 0;

// DECLARATION
float lireCO2();
float lireTVOC();
float lireCO();
float lireTemperature();
float lireHumidite();
void envoyerMesures();
bool verifierServeur(const char* health_url);
void envoyerUneMesure(const char* url, const char* ts, bool analyserReponse);
void ajouterAuBuffer(const char* ts);
void envoyerBuffer(const char* url, bool supprimerApres);
void traiterAlertes(String response);
float last_valid_tvoc = NAN; // Dernier TVOC valide (securite si CCS811 rate un cycle)
void gererWiFi();
void connecterWiFi();

// SCAN I2C
void scanI2C(TwoWire &bus, const char* busName) {
  Serial.print("Scan ");
  Serial.println(busName);
  for (uint8_t addr = 1; addr < 127; addr++) {
    bus.beginTransmission(addr);
    if (bus.endTransmission() == 0) {
      Serial.print("Trouve 0x");
      if (addr < 16) Serial.print("0");
      Serial.println(addr, HEX);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);

  if (!LittleFS.begin(true)) {
    Serial.println("[LittleFS] ERREUR CRITIQUE.");
  } else {
    Serial.println("[LittleFS] OK.");
  }

  esp_task_wdt_init(WDT_TIMEOUT, true);
  esp_task_wdt_add(NULL);

  Wire.begin(SDA_CCS, SCL_CCS);
  Wire.setTimeOut(1000);

  Wire1.begin(SDA2_PIN, SCL2_PIN);
  Wire1.setTimeOut(1000);

  scanI2C(Wire, "Wire (CCS811)");
  scanI2C(Wire1, "Wire1 (ADS1115)");

  if (BUZZER_PIN >= 0) { pinMode(BUZZER_PIN, OUTPUT); digitalWrite(BUZZER_PIN, LOW); }
  if (VENTILATOR_PIN >= 0) { pinMode(VENTILATOR_PIN, OUTPUT); digitalWrite(VENTILATOR_PIN, LOW); }

  connecterWiFi();
  secureClient.setInsecure();

  ArduinoOTA.setHostname(OTA_HOSTNAME);
  ArduinoOTA.setPassword("iaqadmin");
  ArduinoOTA.begin();

  configTime(3600, 0, "pool.ntp.org"); // UTC+1 Algerie, pas de DST
  dht.begin();
  
  CO2Serial.begin(9600, SERIAL_8N1, RX_CO2, TX_CO2);

  if (!ads.begin(0x48, &Wire1)) {
    Serial.println("[ADS1115] ERREUR");
  } else {
    ads1115_ok = true;
    ads.setGain(GAIN_ONE);
    Serial.println("[ADS1115] OK");
  }

  if (ccs811.begin()) {
    while (!ccs811.available()) delay(10);
    ccs811.setDriveMode(CCS811_DRIVE_MODE_1SEC);
    ccs811_ok = true;
    Serial.println("[CCS811] OK");
  } else {
    Serial.println("[CCS811] ERREUR. Verifiez le cablage I2C");
  }

  bootTime = millis();
}

void loop() {
  esp_task_wdt_reset(); 
  ArduinoOTA.handle();
  gererWiFi();

  unsigned long maintenant = millis();
  
  // POLLING (2s)
  if (maintenant - dernierPoll >= POLL_INTERVAL_MS) {
    dernierPoll = maintenant;
    
    current_temp = lireTemperature();
    current_hum  = lireHumidite();
    current_co2  = lireCO2();        
    current_co   = lireCO();         

    if (ccs811_ok && !isnan(current_temp) && !isnan(current_hum)) {
      ccs811.setEnvironmentalData(current_hum, current_temp);
    }
    float tvoc_lu = lireTVOC();
    if (!isnan(tvoc_lu)) {
      current_tvoc = tvoc_lu;
      last_valid_tvoc = tvoc_lu;
    } else if (!isnan(last_valid_tvoc)) {
      current_tvoc = last_valid_tvoc; // Garde la derniere valeur valide
    }

    // Validation capteurs avant de verifier les seuils (logique Nini)
    bool co2Valid  = !isnan(current_co2);
    bool tvocValid = !isnan(current_tvoc);
    bool coValid   = (r0Ready && !isnan(current_co));
    bool tempValid = !isnan(current_temp);
    bool humValid  = !isnan(current_hum);

    // Seuils HAUTS : au moins un capteur depasse le seuil ON
    bool anyGasHigh =
        (co2Valid  && current_co2  > ALERT_CO2_ON) ||
        (tvocValid && current_tvoc > ALERT_TVOC_ON) ||
        (coValid   && current_co   >= ALERT_CO_ON);
    bool tempHigh = tempValid && (current_temp > ALERT_TEMP_ON);
    bool humHigh  = humValid  && (current_hum  > ALERT_HUM_ON);
    bool anyHigh  = anyGasHigh || tempHigh || humHigh;

    // Seuils BAS : tous les capteurs sont sous le seuil OFF
    bool allGasLow =
        (!co2Valid  || current_co2  < ALERT_CO2_OFF) &&
        (!tvocValid || current_tvoc < ALERT_TVOC_OFF) &&
        (!coValid   || current_co   < ALERT_CO_OFF);
    bool tempLow = (!tempValid || current_temp < ALERT_TEMP_OFF);
    bool humLow  = (!humValid  || current_hum  < ALERT_HUM_OFF);
    bool allLow  = allGasLow && tempLow && humLow;

    // Etat de l'air (identique Nini)
    current_etat_air = (anyGasHigh || tempHigh || humHigh) ? "Air danger" : "Air normal";

    // Machine a etats : IDLE -> BUZZING (2s) -> FAN_ON -> retour IDLE quand tout est calme
    switch (alertState) {
      case IDLE:
        if (anyHigh) {
          alertState = BUZZING;
          buzzStart = millis();
          if (BUZZER_PIN >= 0) { digitalWrite(BUZZER_PIN, HIGH); buzzerState = true; }
          if (VENTILATOR_PIN >= 0) { digitalWrite(VENTILATOR_PIN, LOW); fanState = false; }
        }
        break;

      case BUZZING:
        if (allLow) {
          // Fausse alerte, tout redescendu — on coupe tout
          if (BUZZER_PIN >= 0) { digitalWrite(BUZZER_PIN, LOW); buzzerState = false; }
          if (VENTILATOR_PIN >= 0) { digitalWrite(VENTILATOR_PIN, LOW); fanState = false; }
          alertState = IDLE;
        } else if (millis() - buzzStart >= BUZZ_DURATION_MS) {
          // Buzzer fini -> on passe au ventilateur
          if (BUZZER_PIN >= 0) { digitalWrite(BUZZER_PIN, LOW); buzzerState = false; }
          if (VENTILATOR_PIN >= 0) { digitalWrite(VENTILATOR_PIN, HIGH); fanState = true; }
          alertState = FAN_ON;
        }
        break;

      case FAN_ON:
        if (allLow) {
          // Tout revenu sous les seuils bas -> arret
          if (VENTILATOR_PIN >= 0) { digitalWrite(VENTILATOR_PIN, LOW); fanState = false; }
          alertState = IDLE;
        } else {
          if (VENTILATOR_PIN >= 0) { digitalWrite(VENTILATOR_PIN, HIGH); fanState = true; }
        }
        break;
    }
  }

  // ENVOI (5s)
  if (maintenant - dernierEnvoi >= SEND_INTERVAL_MS) {
    dernierEnvoi = maintenant;
    envoyerMesures();
  }
}

uint8_t mhzChecksum(const uint8_t *buf) {
  uint8_t sum = 0;
  for (int i = 1; i < 8; i++) sum += buf[i];
  return (uint8_t)(0xFF - sum + 1);
}

float lireCO2() {
  uint8_t cmd[9] = {0xFF, 0x01, 0x86, 0, 0, 0, 0, 0, 0};
  cmd[8] = mhzChecksum(cmd);
  while (CO2Serial.available()) CO2Serial.read();
  CO2Serial.write(cmd, 9);
  uint32_t t0 = millis();
  int idx = 0;
  uint8_t resp[9];
  while (millis() - t0 < 250) {
    while (CO2Serial.available() && idx < 9) {
      resp[idx++] = (uint8_t)CO2Serial.read();
    }
    if (idx >= 9) break;
    delay(1);
    esp_task_wdt_reset();
  }
  if (idx < 9 || resp[0] != 0xFF || resp[1] != 0x86 || mhzChecksum(resp) != resp[8]) return NAN;
  return (float)(resp[2] * 256 + resp[3]);
}

float lireTVOC() {
  if (!ccs811_ok) return NAN;
  if (ccs811.available()) {
    if (!ccs811.readData()) {
      int t = ccs811.getTVOC();
      if (t >= 0 && t <= 5000) {
        if (t > 1187) t = 1187;
        return (float)t;
      }
    }
  }
  return NAN;
}

float lireCO() {
  if (!ads1115_ok) return NAN;
  int16_t raw = ads.readADC_SingleEnded(0); 
  float vout_pont = ads.computeVolts(raw);
  
  if (vout_pont < 0.001) return NAN;  
  
  float vout_real = vout_pont * MQ7_DIVIDER_FACTOR; // 1.5
  if (vout_real <= 0.0001f || vout_real >= VCC_DIV) return NAN;
  
  float rs = MQ7_RL * (VCC_DIV - vout_real) / vout_real; 

  if (!r0Ready) {
    if (millis() - bootTime <= R0_CAL_MS) {
      if (!isnan(rs)) {
        r0Sum += rs;
        r0Count++;
      }
      return NAN; 
    } else {
      R0 = (float)(r0Sum / (double)max((uint32_t)1, r0Count));
      r0Ready = true;
    }
  }

  float ratio = rs / R0;
  if (ratio <= 0.000001) return NAN;
  
  const float m = -0.699f;
  const float b =  1.398f;
  float logppm = (log10(ratio) - b) / m;
  return pow(10.0, logppm); 
}

float lireTemperature() {
  float t = dht.readTemperature();  
  return isnan(t) ? NAN : t;
}

float lireHumidite() {
  float h = dht.readHumidity();  
  return isnan(h) ? NAN : h;
}

void envoyerMesures() {
  char ts[20] = "";
  struct tm timeinfo;
  if (getLocalTime(&timeinfo, 50)) {
    strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", &timeinfo);
  }

  bool serveur1_ok = verifierServeur(HEALTH_URL_1);

  if (!serveur1_ok) {
    ajouterAuBuffer(ts);
  } else {
    // Vide le buffer offline vers les DEUX serveurs
    if (LittleFS.exists("/mesures.jsonl")) {
      envoyerBuffer(SERVER_URL_2, false); // Envoie sans supprimer le fichier
      envoyerBuffer(SERVER_URL_1, true);  // Envoie puis supprime si OK
    }
    // Envoi normal — seul le serveur 1 (Mahdi) analyse les alertes buzzer
    envoyerUneMesure(SERVER_URL_1, ts, true);
  }

  // Envoi vers serveur 2 (Nini) — independant, pas d'analyse alertes
  envoyerUneMesure(SERVER_URL_2, ts, false);
}

bool verifierServeur(const char* health_url) {
  HTTPClient http;
  http.setTimeout(3000);  
  http.begin(secureClient, health_url);
  int code = http.GET();
  http.end();
  return (code == 200);   
}

void envoyerUneMesure(const char* url, const char* ts, bool analyserReponse) {
  HTTPClient http;
  http.setTimeout(5000);
  http.begin(secureClient, url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-API-KEY", API_KEY);

  StaticJsonDocument<512> doc;
  if (!isnan(current_co2))  doc["co2"]         = current_co2;
  if (!isnan(current_tvoc)) doc["tvoc"]        = current_tvoc;
  if (!isnan(current_co))   doc["co"]          = current_co;
  if (!isnan(current_temp)) doc["temperature"] = current_temp;
  if (!isnan(current_hum))  doc["humidite"]    = current_hum;
  if (strlen(ts) > 0)       doc["timestamp"]   = ts;

  doc["fan"] = fanState;
  doc["buzzer"] = buzzerState;
  doc["etat_air"] = current_etat_air;

  String body; serializeJson(doc, body);
  int code = http.POST(body);

  if (code == 201 || code == 200) {
    if (analyserReponse) {
      traiterAlertes(http.getString());
    }
  } else {
    Serial.printf("[HTTP] Echec envoi vers %s (Code %d)\n", url, code);
  }
  http.end();
}

void ajouterAuBuffer(const char* ts) {
  File file = LittleFS.open("/mesures.jsonl", FILE_APPEND);
  if (!file) return;

  if (file.size() > 50000) {
    file.close();
    LittleFS.remove("/mesures.jsonl");
    file = LittleFS.open("/mesures.jsonl", FILE_APPEND);
  }

  StaticJsonDocument<512> doc;
  if (!isnan(current_co2))  doc["co2"]         = current_co2;
  if (!isnan(current_tvoc)) doc["tvoc"]        = current_tvoc;
  if (!isnan(current_co))   doc["co"]          = current_co;
  if (!isnan(current_temp)) doc["temperature"] = current_temp;
  if (!isnan(current_hum))  doc["humidite"]    = current_hum;
  if (ts != nullptr && strlen(ts) > 0) doc["timestamp"] = ts;
  
  doc["fan"] = fanState;
  doc["buzzer"] = buzzerState;
  doc["etat_air"] = current_etat_air;

  serializeJson(doc, file);
  file.print("\n");
  file.close();
}

void envoyerBuffer(const char* url, bool supprimerApres) {
  File file = LittleFS.open("/mesures.jsonl", FILE_READ);
  if (!file || file.size() == 0) {
    if (file) file.close();
    return;
  }

  HTTPClient http;
  http.setTimeout(10000);
  http.begin(secureClient, url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-API-KEY", API_KEY);

  StaticJsonDocument<1024> doc;
  JsonArray arr = doc.to<JsonArray>();

  int lignes_envoyees = 0;
  while (file.available() && lignes_envoyees < 50) {
    String line = file.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      StaticJsonDocument<256> lineDoc;
      if (deserializeJson(lineDoc, line) == DeserializationError::Ok) {
        arr.add(lineDoc.as<JsonObject>());
        lignes_envoyees++;
      }
    }
  }

  file.close();

  if (arr.size() > 0) {
    String body; serializeJson(doc, body);
    int code = http.POST(body);
    if ((code == 201 || code == 200) && supprimerApres) {
      LittleFS.remove("/mesures.jsonl");
    }
  }
  http.end();
}

void traiterAlertes(String response) {
  // On lit la reponse du serveur pour les LEDs uniquement.
  // Le buzzer et le ventilateur sont geres localement par la machine a etats (Nini).
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, response)) return;
  if (!doc.containsKey("alertes")) return;
  JsonObject alertes = doc["alertes"];

  bool enAlerte = false;
  for (JsonPair p : alertes) {
    if (String(p.value().as<const char*>()) == "alert") { enAlerte = true; break; }
  }

  if (LED_OK_PIN >= 0 && LED_ALERT_PIN >= 0) {
    digitalWrite(LED_OK_PIN, !enAlerte);
    digitalWrite(LED_ALERT_PIN, enAlerte);
  }
  // PAS de buzzer ici — la machine a etats locale s'en charge
}

unsigned long dernierEssaiWiFi = 0;

void gererWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - dernierEssaiWiFi >= 30000) {
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    dernierEssaiWiFi = millis();
  }
}

void connecterWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tentatives = 0;
  while (WiFi.status() != WL_CONNECTED && tentatives < 40) {
    delay(500);
    tentatives++;
    esp_task_wdt_reset();
  }
}

