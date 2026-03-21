/*
 * ============================================================
 *  PROJET ESP32 : CAPTEUR DE QUALITÉ DE L'AIR INTÉRIEUR (IAQ)
 *  VERSION 1.5 — FUSION "SON MONTAGE + MES FONCTIONS"
 * ============================================================
 *  Ce firmware combine :
 *  - Le montage physique de [SA copine] (pins, I2C, auto-calibration)
 *  - Les fonctions robustes de [MON code] (Watchdog, LittleFS, OTA, NTP, API Key)
 *
 *  Pensé pour tester MON interface (Dashboard Flask) sur SON breadboard.
 * ============================================================
 */

// --- BIBLIOTHÈQUES ---
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Adafruit_CCS811.h>
#include <Adafruit_ADS1X15.h>
#include <DHT.h>
#include <Wire.h>
#include <esp_task_wdt.h>
#include <time.h>
#include <LittleFS.h>
#include <ArduinoOTA.h>
#include <math.h>

#define WDT_TIMEOUT 15

// ===================== WiFi (SON RÉSEAU) =====================
const char* WIFI_SSID     = "IdoomFibre_AT3P2evDS_EXT";
const char* WIFI_PASSWORD = "zevwhF9e";

// ===================== Serveur (MON DASHBOARD) =====================
const char* SERVER_URL = "https://iaq-dashboard-v2.onrender.com/api/mesures";   // <-- MON URL Render
const char* HEALTH_URL = "https://iaq-dashboard-v2.onrender.com/api/health";
const char* API_KEY    = "SECRET_IAQ_2026";
const char* DEVICE_ID  = "esp32-test-v15";

WiFiClientSecure secureClient;

// ===================== PINS (SON MONTAGE) =====================
// I2C : ESP32-S3 avec SDA=8, SCL=9
static const int SDA_PIN = 8;
static const int SCL_PIN = 9;

// MH-Z19 UART
#define RX_CO2  18
#define TX_CO2  17
HardwareSerial CO2Serial(1);

// DHT22
#define DHT_PIN   4
#define DHT_TYPE  DHT22

// Sorties (SON MONTAGE : pas de LEDs, fan sur 16)
#define BUZZER_PIN  15
#define FAN_PIN     16

// ===================== Capteurs =====================
Adafruit_ADS1115 ads;
Adafruit_CCS811 ccs811;
DHT dht(DHT_PIN, DHT_TYPE);

// ===================== Seuils avec hystérésis (SES VALEURS) =====================
static const float CO_ON     = 25.0f;   static const float CO_OFF     = 18.0f;   // ppm
static const int   CO2_ON    = 2000;     static const int   CO2_OFF    = 1800;     // ppm
static const float TVOC_ON   = 220.0f;   static const float TVOC_OFF   = 150.0f;   // ppb
static const float TEMP_ON   = 27.0f;    static const float TEMP_OFF   = 25.0f;    // °C
static const float HUM_ON    = 60.0f;    static const float HUM_OFF    = 55.0f;    // %

// ===================== Timings =====================
static const uint32_t POLL_MS      = 2000;   // Lecture capteurs toutes les 2s
static const uint32_t SEND_MS      = 5000;   // Envoi serveur toutes les 5s
static const uint32_t BUZZ_MS      = 2000;   // Buzzer pendant 2s avant ventilateur
uint32_t lastPoll = 0;
uint32_t lastSend = 0;

// ===================== MQ-7 Auto-Calibration (SON CODE) =====================
static const float VCC = 5.0f;
static const float RL  = 10000.0f;   // 10 kOhm
static const uint32_t R0_CAL_MS = 60000;  // 60s de calibration au boot

bool r0Ready = false;
float R0 = 1.0f;
double r0Sum = 0.0;
uint32_t r0Count = 0;
uint32_t bootTime = 0;

float calcRs(float vout) {
  if (vout <= 0.0001f) return NAN;
  return RL * (VCC - vout) / vout;
}

float ratioToPpm(float ratio) {
  if (ratio <= 0.000001f) return NAN;
  float logppm = (log10f(ratio) - 1.398f) / -0.699f;
  return powf(10.0f, logppm);
}

// ===================== Machine à états Buzzer/Fan (SON CODE) =====================
enum AlertState { IDLE, BUZZING, FAN_RUNNING };
AlertState alertState = IDLE;
uint32_t buzzStart = 0;
bool fanState = false;
bool buzzerState = false;

void buzzerOn()  { digitalWrite(BUZZER_PIN, HIGH); buzzerState = true; }
void buzzerOff() { digitalWrite(BUZZER_PIN, LOW);  buzzerState = false; }
void fanOn()     { digitalWrite(FAN_PIN, HIGH);    fanState = true; }
void fanOff()    { digitalWrite(FAN_PIN, LOW);     fanState = false; }

void updateAlertState(bool anyHigh, bool allLow) {
  uint32_t now = millis();
  switch (alertState) {
    case IDLE:
      if (anyHigh) {
        alertState = BUZZING;
        buzzStart = now;
        buzzerOn();
        fanOff();
        Serial.println("[ALERTE] BUZZER ACTIVE !");
      }
      break;
    case BUZZING:
      if (allLow) {
        buzzerOff(); fanOff();
        alertState = IDLE;
        Serial.println("[ALERTE] Annulee");
      } else if (now - buzzStart >= BUZZ_MS) {
        buzzerOff(); fanOn();
        alertState = FAN_RUNNING;
        Serial.println("[VENTILATEUR] EN MARCHE");
      }
      break;
    case FAN_RUNNING:
      if (allLow) {
        fanOff();
        alertState = IDLE;
        Serial.println("[VENTILATEUR] Arret");
      }
      break;
  }
}

// ===================== MH-Z19 (MON CODE + SON readFrame) =====================
uint8_t mhzChecksum(const uint8_t *buf) {
  uint8_t sum = 0;
  for (int i = 1; i < 8; i++) sum += buf[i];
  return (uint8_t)(0xFF - sum + 1);
}

int readCO2ppm() {
  uint8_t cmd[9] = {0xFF, 0x01, 0x86, 0, 0, 0, 0, 0, 0};
  cmd[8] = mhzChecksum(cmd);

  while (CO2Serial.available()) CO2Serial.read();
  CO2Serial.write(cmd, 9);

  uint8_t resp[9];
  uint32_t t0 = millis();
  int idx = 0;
  while (millis() - t0 < 250) {
    while (CO2Serial.available() && idx < 9)
      resp[idx++] = (uint8_t)CO2Serial.read();
    if (idx >= 9) break;
    delay(1);
    esp_task_wdt_reset();
  }

  if (idx < 9 || resp[0] != 0xFF || resp[1] != 0x86 || mhzChecksum(resp) != resp[8])
    return -1;

  return (int)resp[2] * 256 + (int)resp[3];
}

// ===================== I2C Scan (SON CODE) =====================
void i2cScan() {
  Serial.println("[I2C] Scan du bus...");
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("[I2C] Trouve : 0x%02X\n", addr);
    }
  }
}

// ===================== WiFi (MON CODE robuste) =====================
unsigned long dernierEssaiWiFi = 0;

void gererWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - dernierEssaiWiFi >= 30000) {
    Serial.println("[WIFI] Reconnexion...");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    dernierEssaiWiFi = millis();
  }
}

void connecterWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("[WIFI] Connexion");
  int t = 0;
  while (WiFi.status() != WL_CONNECTED && t < 40) {
    delay(500); Serial.print("."); t++;
    esp_task_wdt_reset();
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[WIFI] OK ! IP: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[WIFI] Echec (continuera en arriere-plan)");
  }
}

// ===================== Réseau : Health + Envoi (MON CODE) =====================
bool verifierServeur() {
  HTTPClient http;
  http.setTimeout(3000);
  http.begin(secureClient, HEALTH_URL);
  int code = http.GET();
  http.end();
  if (code != 200)
    Serial.printf("[HTTP] Serveur injoignable (%d)\n", code);
  return (code == 200);
}

void envoyerUneMesure(int co2, float tvoc, float co, float temp, float hum, const char* ts) {
  HTTPClient http;
  http.setTimeout(5000);
  http.begin(secureClient, SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-API-KEY", API_KEY);

  JsonDocument doc;
  doc["device_id"] = DEVICE_ID;
  if (co2 >= 0)     doc["co2"]         = co2;
  if (!isnan(tvoc))  doc["tvoc"]        = (int)tvoc;
  if (!isnan(co))    doc["co"]          = co;
  if (!isnan(temp))  doc["temperature"] = temp;
  if (!isnan(hum))   doc["humidite"]    = hum;
  if (strlen(ts) > 0) doc["timestamp"]  = ts;

  String body;
  serializeJson(doc, body);
  int code = http.POST(body);

  if (code == 201) {
    Serial.println("[HTTP] Mesure envoyee OK");
  } else {
    Serial.printf("[HTTP] Echec (%d: %s)\n", code, http.errorToString(code).c_str());
  }
  http.end();
}

// ===================== Buffer LittleFS (MON CODE) =====================
void ajouterAuBuffer(int co2, float tvoc, float co, float temp, float hum, const char* ts) {
  File file = LittleFS.open("/mesures.jsonl", FILE_APPEND);
  if (!file) return;
  if (file.size() > 50000) {
    file.close();
    LittleFS.remove("/mesures.jsonl");
    file = LittleFS.open("/mesures.jsonl", FILE_APPEND);
  }

  JsonDocument doc;
  if (co2 >= 0)     doc["co2"]         = co2;
  if (!isnan(tvoc))  doc["tvoc"]        = (int)tvoc;
  if (!isnan(co))    doc["co"]          = co;
  if (!isnan(temp))  doc["temperature"] = temp;
  if (!isnan(hum))   doc["humidite"]    = hum;
  if (ts && strlen(ts) > 0) doc["timestamp"] = ts;

  serializeJson(doc, file);
  file.print("\n");
  file.close();
}

void envoyerBuffer() {
  File file = LittleFS.open("/mesures.jsonl", FILE_READ);
  if (!file || file.size() == 0) { if (file) file.close(); return; }

  HTTPClient http;
  http.setTimeout(10000);
  http.begin(secureClient, SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-API-KEY", API_KEY);

  JsonDocument doc;
  JsonArray arr = doc.to<JsonArray>();
  int n = 0;

  while (file.available() && n < 50) {
    String line = file.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      JsonDocument ld;
      if (deserializeJson(ld, line) == DeserializationError::Ok) {
        ld["device_id"] = DEVICE_ID;
        arr.add(ld);
        n++;
      }
    }
  }
  file.close();

  if (arr.size() > 0) {
    String body;
    serializeJson(doc, body);
    int code = http.POST(body);
    if (code == 201) {
      LittleFS.remove("/mesures.jsonl");
      Serial.printf("[BUFFER] %d mesures envoyees\n", n);
    }
  }
  http.end();
}

// ===================== Envoi orchestré =====================
void envoyerMesures(int co2, float tvoc, float co, float temp, float hum) {
  char ts[20] = "";
  struct tm ti;
  if (getLocalTime(&ti, 50))
    strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", &ti);

  if (!verifierServeur()) {
    ajouterAuBuffer(co2, tvoc, co, temp, hum, ts);
    return;
  }
  if (LittleFS.exists("/mesures.jsonl")) envoyerBuffer();
  envoyerUneMesure(co2, tvoc, co, temp, hum, ts);
}

// ══════════════════════════════════════════════════════
//  SETUP
// ══════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  delay(300);

  // LittleFS
  if (!LittleFS.begin(true)) {
    Serial.println("[LittleFS] ERREUR");
  } else {
    Serial.println("[LittleFS] OK");
  }

  // Watchdog
  esp_task_wdt_init(WDT_TIMEOUT, true);
  esp_task_wdt_add(NULL);

  // Pins
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(FAN_PIN, OUTPUT);
  buzzerOff();
  fanOff();

  // I2C (SES PINS : SDA=8, SCL=9)
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);

  i2cScan();

  // ADS1115
  if (!ads.begin(0x48)) {
    Serial.println("[ADS1115] ERREUR : introuvable");
  } else {
    ads.setGain(GAIN_ONE);
    Serial.println("[ADS1115] OK");
  }

  // CCS811
  if (!ccs811.begin()) {
    Serial.println("[CCS811] ERREUR : introuvable");
  } else {
    while (!ccs811.available()) delay(10);
    ccs811.setDriveMode(CCS811_DRIVE_MODE_1SEC);
    Serial.println("[CCS811] OK");
  }

  // DHT22
  dht.begin();
  Serial.println("[DHT22] OK");

  // MH-Z19
  CO2Serial.begin(9600, SERIAL_8N1, RX_CO2, TX_CO2);
  Serial.println("[MH-Z19] OK");

  // WiFi (robuste avec timeout)
  connecterWiFi();
  secureClient.setInsecure();

  // OTA
  ArduinoOTA.setHostname(DEVICE_ID);
  ArduinoOTA.setPassword("iaqadmin");
  ArduinoOTA.begin();
  Serial.println("[OTA] OK");

  // NTP
  configTime(3600, 3600, "pool.ntp.org");

  // MQ-7 auto-calibration
  bootTime = millis();
  Serial.println("[MQ-7] Calibration auto R0 pendant 60s...");

  Serial.println("══════════════════════════════════");
  Serial.println("   IAQ V1.5 — PRET !");
  Serial.println("══════════════════════════════════");
}

// ══════════════════════════════════════════════════════
//  LOOP
// ══════════════════════════════════════════════════════
void loop() {
  esp_task_wdt_reset();
  ArduinoOTA.handle();
  gererWiFi();

  uint32_t now = millis();

  // --- Lecture capteurs toutes les 2s ---
  if (now - lastPoll < POLL_MS) return;
  lastPoll = now;

  // DHT22
  float hum  = dht.readHumidity();
  float temp = dht.readTemperature();

  // CCS811 (TVOC uniquement, avec clamp à 1187)
  float tvoc = NAN;
  if (!isnan(hum) && !isnan(temp))
    ccs811.setEnvironmentalData(hum, temp);

  if (ccs811.available() && !ccs811.readData()) {
    int t = ccs811.getTVOC();
    if (t >= 0 && t <= 5000) {
      if (t > 1187) t = 1187;
      tvoc = (float)t;
    }
  }

  // MQ-7 via ADS1115 (auto-calibration R0)
  int16_t raw = ads.readADC_SingleEnded(0);
  float vout = ads.computeVolts(raw);
  float Rs = calcRs(vout);
  float co_ppm = NAN;

  if (!r0Ready) {
    if (now - bootTime <= R0_CAL_MS) {
      if (!isnan(Rs)) { r0Sum += Rs; r0Count++; }
    } else {
      R0 = (float)(r0Sum / (double)max((uint32_t)1, r0Count));
      r0Ready = true;
      Serial.printf("[MQ-7] R0 calibre = %.0f Ohms\n", R0);
    }
  } else {
    if (!isnan(Rs)) co_ppm = ratioToPpm(Rs / R0);
  }

  // MH-Z19 (CO2)
  int co2 = readCO2ppm();
  bool co2Valid  = (co2 >= 0);
  bool tvocValid = !isnan(tvoc);
  bool coValid   = (r0Ready && !isnan(co_ppm));
  bool tempValid = !isnan(temp);
  bool humValid  = !isnan(hum);

  // --- Décision alerte avec hystérésis ---
  bool anyHigh =
    (co2Valid  && co2 > CO2_ON) ||
    (tvocValid && tvoc > TVOC_ON) ||
    (coValid   && co_ppm >= CO_ON);

  bool allLow =
    (!co2Valid  || co2 < CO2_OFF) &&
    (!tvocValid || tvoc < TVOC_OFF) &&
    (!coValid   || co_ppm < CO_OFF);

  // Inclure temp et humidité dans la décision
  if (tempValid && temp > TEMP_ON) anyHigh = true;
  if (humValid  && hum > HUM_ON)   anyHigh = true;
  if (tempValid && temp > TEMP_OFF) allLow = false;
  if (humValid  && hum > HUM_OFF)  allLow = false;

  updateAlertState(anyHigh, allLow);

  // --- Envoi vers MON serveur ---
  if (now - lastSend >= SEND_MS) {
    lastSend = now;
    envoyerMesures(
      co2Valid ? co2 : -9999,
      tvoc,
      coValid ? co_ppm : NAN,
      tempValid ? temp : NAN,
      humValid ? hum : NAN
    );
  }

  // --- Affichage série ---
  Serial.printf("CO2=%s ppm | TVOC=%s ppb | CO=%s ppm | T=%s C | H=%s %% | FAN=%s | BUZ=%s | %s\n",
    co2Valid  ? String(co2).c_str() : "NA",
    tvocValid ? String((int)tvoc).c_str() : "NA",
    coValid   ? String(co_ppm, 1).c_str() : (r0Ready ? "NA" : "CAL"),
    tempValid ? String(temp, 1).c_str() : "NA",
    humValid  ? String(hum, 1).c_str() : "NA",
    fanState ? "ON" : "OFF",
    buzzerState ? "ON" : "OFF",
    (anyHigh ? "DANGER" : "NORMAL")
  );
}
