/*
 * ============================================================
 *   PROJET ESP32 - SURVEILLANCE QUALITE DE L'AIR INTERIEUR
 *   VERSION GLOBALE FUSIONNEE
 * ============================================================
 *   Fonctionnalites :
 *   1) Connexion WiFi
 *   2) Lecture MH-Z19 / CCS811 / MQ-7 / DHT22
 *   3) Calibration MQ-7 pendant 60 s
 *   4) Gestion alerte avec hysteresis
 *   5) Buzzer 2 s puis ventilateur
 *   6) Envoi JSON vers backend/dashboard Render
 * ============================================================
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <Adafruit_CCS811.h>
#include <DHT.h>
#include <math.h>

// ===================== WiFi =====================
const char* ssid = "IdoomFibre_AT3P2evDS_EXT";
const char* pass = "zevwhF9e";

// ===================== Backend Flask / Render =====================
const char* SERVER_URL = "https://iaq-backend.onrender.com/api/mesures";
static const uint32_t SEND_PERIOD_MS = 5000;
WiFiClientSecure client;
uint32_t lastSend = 0;
unsigned long lastWiFiRetry = 0;

// ===================== PINS =====================
// MH-Z19 / MH-Z1911A UART
static const int RX_CO2 = 18;
static const int TX_CO2 = 17;
HardwareSerial CO2Serial(1);

// I2C 1 : CCS811
static const int SDA_CCS = 8;
static const int SCL_CCS = 9;

// I2C 2 : ADS1115
static const int SDA2_PIN = 2;
static const int SCL2_PIN = 1;

// ADS1115
static const uint8_t ADS_ADDR = 0x48;
static const int MQ7_CH = 0;

// DHT22
#define DHTPIN 4
#define DHTTYPE DHT22
DHT dht(DHTPIN, DHTTYPE);

// Sorties
static const int BUZZER_PIN = 15;
static const int FAN_PIN    = 38;

// ===================== Bus I2C =====================
TwoWire I2C_CCS = TwoWire(0);
TwoWire I2C_ADS = TwoWire(1);

// ===================== Capteurs =====================
Adafruit_ADS1115 ads;
Adafruit_CCS811 ccs;

bool ccs_ok = false;
bool ads_ok = false;

// ===================== Seuils avec hystérésis =====================
// CO ppm
static const float CO_ON  = 25.0;
static const float CO_OFF = 18.0;

// CO2 ppm
static const int CO2_ON  = 2000;
static const int CO2_OFF = 1800;

// TVOC ppb
static const float TVOC_ON  = 220.0;
static const float TVOC_OFF = 150.0;

// Température °C
static const float TEMP_ON  = 27.0;
static const float TEMP_OFF = 25.0;

// Humidité %
static const float HUM_ON  = 60.0;
static const float HUM_OFF = 55.0;

// ===================== Timings =====================
static const uint32_t POLL_MS = 2000;
static const uint32_t BUZZ_MS = 2000;
uint32_t lastPoll = 0;

// ===================== MQ-7 =====================
// Pont diviseur : R1 = 10k, R2 = 20k
static const float MQ7_DIVIDER_FACTOR = 1.5f;

// MQ-7
static const float VCC_DIV = 5.0f;
static const float RL_OHMS = 10000.0f;
static const uint32_t R0_CAL_MS = 60000;

bool r0Ready = false;
float R0 = 1.0f;
double r0Sum = 0.0;
uint32_t r0Count = 0;
uint32_t bootTime = 0;

// ===================== Etat machine alerte =====================
enum AlertState { IDLE, BUZZING, FAN_ON };
AlertState alertState = IDLE;
uint32_t buzzStart = 0;
bool fanState = false;
bool buzzerState = false;

// ============================================================
// OUTILS
// ============================================================

void buzzerOn()  { digitalWrite(BUZZER_PIN, HIGH); buzzerState = true; }
void buzzerOff() { digitalWrite(BUZZER_PIN, LOW);  buzzerState = false; }
void fanOn()     { digitalWrite(FAN_PIN, HIGH);    fanState = true; }
void fanOff()    { digitalWrite(FAN_PIN, LOW);     fanState = false; }

float calcRs(float vout_real) {
  if (vout_real <= 0.0001f || vout_real >= VCC_DIV) return NAN;
  return RL_OHMS * (VCC_DIV - vout_real) / vout_real;
}

float ratioToPpm(float ratio) {
  if (ratio <= 0.000001f) return NAN;

  const float m = -0.699f;
  const float b =  1.398f;

  float logppm = (log10f(ratio) - b) / m;
  return powf(10.0f, logppm);
}

String computeAirState(bool anyGasHigh, bool tempHigh, bool humHigh) {
  if (anyGasHigh || tempHigh || humHigh) return "Air danger";
  return "Air normal";
}

void updateAlertState(bool anyHigh, bool allLow) {
  uint32_t now = millis();

  switch (alertState) {
    case IDLE:
      if (anyHigh) {
        alertState = BUZZING;
        buzzStart = now;
        buzzerOn();
        fanOff();
      }
      break;

    case BUZZING:
      if (allLow) {
        buzzerOff();
        fanOff();
        alertState = IDLE;
      } else if (now - buzzStart >= BUZZ_MS) {
        buzzerOff();
        fanOn();
        alertState = FAN_ON;
      }
      break;

    case FAN_ON:
      if (allLow) {
        fanOff();
        alertState = IDLE;
      } else {
        fanOn();
      }
      break;
  }
}

// ============================================================
// MH-Z19 / MH-Z1911A
// ============================================================

static uint8_t mhzChecksum(const uint8_t *buf) {
  uint8_t sum = 0;
  for (int i = 1; i < 8; i++) sum += buf[i];
  return (uint8_t)(0xFF - sum + 1);
}

static bool readFrame9(uint8_t *out, uint32_t timeoutMs) {
  uint32_t t0 = millis();
  int idx = 0;

  while (millis() - t0 < timeoutMs) {
    while (CO2Serial.available() && idx < 9) {
      out[idx++] = (uint8_t)CO2Serial.read();
    }
    if (idx >= 9) return true;
    delay(1);
  }
  return false;
}

int readCO2ppmUART() {
  uint8_t cmd[9] = {0xFF, 0x01, 0x86, 0, 0, 0, 0, 0, 0};
  cmd[8] = mhzChecksum(cmd);

  while (CO2Serial.available()) CO2Serial.read();
  CO2Serial.write(cmd, 9);

  uint8_t resp[9];
  if (!readFrame9(resp, 250)) return -1;
  if (resp[0] != 0xFF) return -2;
  if (resp[1] != 0x86) return -3;
  if (mhzChecksum(resp) != resp[8]) return -4;

  return (int)resp[2] * 256 + (int)resp[3];
}

// ============================================================
// I2C scan
// ============================================================

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

// ============================================================
// WiFi
// ============================================================

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid,pass);

  Serial.print("Connexion WiFi");
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 40) {
    delay(500);
    Serial.print(".");
    tries++;
  }

  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("WiFi connecte");
    Serial.print("IP ESP32: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("Echec connexion WiFi au demarrage");
  }
}

void manageWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  if (millis() - lastWiFiRetry >= 30000) {
    Serial.println("[WiFi] Tentative de reconnexion...");
    WiFi.disconnect();
    WiFi.begin(ssid,pass);
    lastWiFiRetry = millis();
  }
}

// ============================================================
// Envoi backend
// ============================================================

bool sendToFlask(
  int co2,
  int tvoc,
  float co_ppm,
  float temp,
  float hum,
  bool fan,
  bool buzzer,
  const String& etat_air
) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi deconnecte, envoi impossible");
    return false;
  }

  HTTPClient http;
  client.setInsecure();
  http.begin(client, SERVER_URL);
  http.addHeader("Content-Type", "application/json");

  String json = "{";
  json += "\"co2\":" + String(co2) + ",";
  json += "\"tvoc\":" + String(tvoc) + ",";
  json += "\"co\":" + String(co_ppm, 1) + ",";
  json += "\"temperature\":" + String(temp, 1) + ",";
  json += "\"humidite\":" + String(hum, 1) + ",";
  json += "\"fan\":" + String(fan ? "true" : "false") + ",";
  json += "\"buzzer\":" + String(buzzer ? "true" : "false") + ",";
  json += "\"etat_air\"😕"" + etat_air + "\"";
  json += "}";

  Serial.print("Serveur cible: ");
  Serial.println(SERVER_URL);
  Serial.print("JSON envoye: ");
  Serial.println(json);

  int httpResponseCode = http.POST(json);

  Serial.print("Code HTTP: ");
  Serial.println(httpResponseCode);

  if (httpResponseCode > 0) {
    String response = http.getString();
    Serial.println("Reponse serveur:");
    Serial.println(response);
    http.end();
    return true;
  } else {
    Serial.print("Erreur HTTP detail: ");
    Serial.println(http.errorToString(httpResponseCode));
    http.end();
    return false;
  }
}

// ============================================================
// SETUP
// ============================================================

void setup() {
  Serial.begin(115200);
  delay(300);

  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(FAN_PIN, OUTPUT);
  buzzerOff();
  fanOff();

  // I2C 1 : CCS811
  I2C_CCS.begin(SDA_CCS, SCL_CCS);
  I2C_CCS.setClock(100000);

  // I2C 2 : ADS1115
  I2C_ADS.begin(SDA2_PIN, SCL2_PIN);
  I2C_ADS.setClock(100000);

  scanI2C(I2C_CCS, "I2C_CCS");
  scanI2C(I2C_ADS, "I2C_ADS");

  // ADS1115 sur bus 2
  if (!ads.begin(ADS_ADDR, &I2C_ADS)) {
    Serial.println("ERREUR: ADS1115 introuvable sur I2C2");
    ads_ok = false;
  } else {
    ads.setGain(GAIN_ONE);
    ads_ok = true;
    Serial.println("ADS1115 OK");
  }

  // CCS811 sur bus 1
  if (!ccs.begin(CCS811_ADDRESS, &I2C_CCS)) {
    Serial.println("ERREUR: CCS811 introuvable sur I2C1");
    ccs_ok = false;
  } else {
    while (!ccs.available()) delay(10);
    ccs.setDriveMode(CCS811_DRIVE_MODE_1SEC);
    ccs_ok = true;
    Serial.println("CCS811 OK");
  }

  dht.begin();
  CO2Serial.begin(9600, SERIAL_8N1, RX_CO2, TX_CO2);

  connectWiFi();

  Serial.print("Serveur Flask/Render: ");
  Serial.println(SERVER_URL);

  bootTime = millis();
  Serial.println("Calibration MQ7 R0 pendant 60 s...");
}

// ============================================================
// LOOP
// ============================================================

void loop() {
  uint32_t now = millis();

  manageWiFi();

  if (now - lastPoll < POLL_MS) {
    delay(2);
    return;
  }
  lastPoll = now;

  // ===== DHT22 =====
  float hum  = dht.readHumidity();
  float temp = dht.readTemperature();

  // ===== CCS811 =====
  float tvoc = NAN;

  if (ccs_ok && !isnan(hum) && !isnan(temp)) {
    ccs.setEnvironmentalData(hum, temp);
  }

  if (ccs_ok && ccs.available()) {
    if (!ccs.readData()) {
      int t = ccs.getTVOC();
      if (t >= 0 && t <= 5000) {
        if (t > 1187) t = 1187;
        tvoc = (float)t;
      } else {
        tvoc = NAN;
      }
    } else {
      tvoc = NAN;
    }
  }

  // ===== MQ-7 via ADS1115 =====
  int16_t raw_ads = 0;
  float v_ads = NAN;
  float v_mq7 = NAN;
  float Rs = NAN;
  float co_ppm = NAN;

  if (ads_ok) {
    raw_ads = ads.readADC_SingleEnded(MQ7_CH);
    v_ads = ads.computeVolts(raw_ads);
    v_mq7 = v_ads * MQ7_DIVIDER_FACTOR;
    Rs = calcRs(v_mq7);

    if (!r0Ready) {
      if (now - bootTime <= R0_CAL_MS) {
        if (!isnan(Rs)) {
          r0Sum += Rs;
          r0Count++;
        }
      } else {
        R0 = (float)(r0Sum / (double)max((uint32_t)1, r0Count));
        r0Ready = true;
        Serial.print("MQ7 R0 = ");
        Serial.println(R0, 2);
      }
    } else {
      if (!isnan(Rs)) {
        float ratio = Rs / R0;
        co_ppm = ratioToPpm(ratio);
      }
    }
  }

  // ===== MH-Z19 =====
  int co2 = readCO2ppmUART();

  // ===== Validations =====
  bool co2Valid = (co2 >= 0);
  bool tvocValid = !isnan(tvoc);
  bool coValid = (r0Ready && !isnan(co_ppm));
  bool tempValid = !isnan(temp);
  bool humValid = !isnan(hum);

  // ===== Décision gaz avec hystérésis =====
  bool anyGasHigh =
      (co2Valid && co2 > CO2_ON) ||
      (tvocValid && tvoc > TVOC_ON) ||
      (coValid   && co_ppm >= CO_ON);

  bool allGasLow =
      (!co2Valid || co2 < CO2_OFF) &&
      (!tvocValid || tvoc < TVOC_OFF) &&
      (!coValid   || co_ppm < CO_OFF);

  bool tempHigh = tempValid && (temp > TEMP_ON);
  bool humHigh  = humValid  && (hum > HUM_ON);

  bool tempLow = (!tempValid || temp < TEMP_OFF);
  bool humLow  = (!humValid || hum < HUM_OFF);

  bool anyHigh = anyGasHigh || tempHigh || humHigh;
  bool allLow  = allGasLow && tempLow && humLow;

  updateAlertState(anyHigh, allLow);

  String etat_air = computeAirState(anyGasHigh, tempHigh, humHigh);

  // ===== Valeurs à envoyer =====
  int co2_send    = co2Valid ? co2 : -1;
  int tvoc_send   = tvocValid ? (int)tvoc : -1;
  float co_send   = coValid ? co_ppm : -1.0;
  float temp_send = tempValid ? temp : -1.0;
  float hum_send  = humValid ? hum : -1.0;

  // ===== Envoi backend =====
  if (now - lastSend >= SEND_PERIOD_MS) {
    lastSend = now;
    sendToFlask(
      co2_send,
      tvoc_send,
      co_send,
      temp_send,
      hum_send,
      fanState,
      buzzerState,
      etat_air
    );
  }

  // ===== Affichage série =====
  Serial.print("CO2=");
  Serial.print(co2Valid ? String(co2) : "NA");
  Serial.print(" ppm | ");

  Serial.print("TVOC=");
  Serial.print(tvocValid ? String((int)tvoc) : "NA");
  Serial.print(" ppb | ");

  Serial.print("MQ7_raw=");
  Serial.print(ads_ok ? String(raw_ads) : "NA");
  Serial.print(" | Vads=");
  Serial.print(!isnan(v_ads) ? String(v_ads, 3) : "NA");
  Serial.print(" V | Vmq7=");
  Serial.print(!isnan(v_mq7) ? String(v_mq7, 3) : "NA");
  Serial.print(" V | ");

  Serial.print("CO_MQ7=");
  if (!ads_ok) {
    Serial.print("ADS_OFF");
  } else if (!r0Ready) {
    Serial.print("CAL");
  } else {
    Serial.print(coValid ? String(co_ppm, 1) : "NA");
  }
  Serial.print(" ppm | ");

  Serial.print("T=");
  Serial.print(tempValid ? String(temp, 1) : "NA");
  Serial.print(" C | ");

  Serial.print("H=");
  Serial.print(humValid ? String(hum, 1) : "NA");
  Serial.print(" % | ");

  Serial.print("FAN=");
  Serial.print(fanState ? "ON" : "OFF");
  Serial.print(" | BUZZER=");
  Serial.print(buzzerState ? "ON" : "OFF");
  Serial.print(" | ETAT=");
  Serial.println(etat_air);
}
