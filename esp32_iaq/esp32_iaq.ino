/*
 * ============================================================
 *  ESP32 — Capteur de qualité de l'air intérieur (IAQ)
 *  Envoie les données au serveur Flask via HTTP POST
 * ============================================================
 *
 *  Capteurs utilisés :
 *    - CCS811  : CO2 (ppm) + TVOC (ppb)      → I2C
 *    - MQ-7    : CO (ppm)                     → Analogique
 *    - DHT22   : Température (°C) + Humidité  → Digital
 *
 *  Librairies à installer (Arduino IDE > Outils > Gérer les bibliothèques) :
 *    1. "ArduinoJson"              par Benoit Blanchon  (v7.x)
 *    2. "Adafruit CCS811 Library"  par Adafruit
 *    3. "DHT sensor library"       par Adafruit
 *    4. "Adafruit Unified Sensor"  par Adafruit  (dépendance du DHT)
 *
 *  Les librairies WiFi et HTTPClient sont déjà incluses avec le board ESP32.
 *
 *  Board Arduino IDE :
 *    Outils > Type de carte > ESP32 Dev Module
 *    (installer le board manager ESP32 si pas encore fait :
 *     Fichier > Préférences > URLs de gestionnaire de cartes >
 *     ajouter : https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json)
 */

// ════════════════════════════════════════════════════════════════
//  LIBRAIRIES
// ════════════════════════════════════════════════════════════════

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Adafruit_CCS811.h>
#include <DHT.h>

// ════════════════════════════════════════════════════════════════
//  CONFIGURATION — MODIFIE CETTE SECTION SELON TON SETUP
// ════════════════════════════════════════════════════════════════

// --- WiFi ---
const char* WIFI_SSID     = "ton_wifi";          // <-- nom de ton réseau WiFi
const char* WIFI_PASSWORD = "ton_mot_de_passe";   // <-- mot de passe WiFi

// --- Serveur Flask ---
// Remplace par l'IP de ton PC (voir ipconfig / hostname -I)
const char* SERVER_URL = "http://192.168.1.100:5000/api/mesures";
const char* HEALTH_URL = "http://192.168.1.100:5000/api/health";
const char* API_KEY    = "";  // <-- si tu as mis IAQ_API_KEY sur le serveur, mets la même clé ici

// --- Identifiant de cet ESP32 (utile si tu en as plusieurs) ---
const char* DEVICE_ID = "esp32-salon";  // <-- change selon la pièce

// --- Intervalle d'envoi ---
const unsigned long SEND_INTERVAL_MS = 10000;  // 10 secondes entre chaque envoi

// ════════════════════════════════════════════════════════════════
//  BRANCHEMENT DES CAPTEURS (PINS)
// ════════════════════════════════════════════════════════════════
//
//  ┌─────────────────────────────────────────────────────────────┐
//  │  CCS811 (CO2 + TVOC) — Communication I2C                   │
//  │                                                             │
//  │  CCS811       ESP32                                         │
//  │  ──────       ─────                                         │
//  │  VCC    ───>  3.3V                                          │
//  │  GND    ───>  GND                                           │
//  │  SDA    ───>  GPIO 21  (SDA par défaut)                     │
//  │  SCL    ───>  GPIO 22  (SCL par défaut)                     │
//  │  WAKE   ───>  GND  (pour garder le capteur actif)           │
//  │                                                             │
//  │  Note : si tu utilises d'autres pins I2C, change avec :     │
//  │         Wire.begin(SDA_PIN, SCL_PIN);                       │
//  ├─────────────────────────────────────────────────────────────┤
//  │  DHT22 (Température + Humidité) — Digital                  │
//  │                                                             │
//  │  DHT22        ESP32                                         │
//  │  ──────       ─────                                         │
//  │  VCC    ───>  3.3V                                          │
//  │  GND    ───>  GND                                           │
//  │  DATA   ───>  GPIO 4   (modifiable ci-dessous)              │
//  │                                                             │
//  │  Résistance de pull-up : 10kΩ entre DATA et VCC             │
//  │  (certains modules DHT22 l'ont déjà intégrée)               │
//  ├─────────────────────────────────────────────────────────────┤
//  │  MQ-7 (CO) — Analogique                                    │
//  │                                                             │
//  │  MQ-7          ESP32                                        │
//  │  ──────        ─────                                        │
//  │  VCC     ───>  5V (via VIN)                                 │
//  │  GND     ───>  GND                                          │
//  │  AOUT    ───>  GPIO 34  (modifiable ci-dessous)             │
//  │                                                             │
//  │  ⚠ Le MQ-7 a besoin de 5V pour le chauffage.               │
//  │  ⚠ La sortie analogique est 0-5V mais l'ESP32 tolère       │
//  │    0-3.3V max. Utilise un pont diviseur de tension          │
//  │    (2 résistances) si ton module n'a pas de régulateur.     │
//  │    Pont diviseur : AOUT → R1(2.2kΩ) → GPIO34 → R2(3.3kΩ)  │
//  │    → GND                                                    │
//  ├─────────────────────────────────────────────────────────────┤
//  │  LED + Buzzer (alertes locales, optionnel)                  │
//  │                                                             │
//  │  LED verte   ───> GPIO 25  (+ résistance 220Ω vers GND)    │
//  │  LED rouge   ───> GPIO 26  (+ résistance 220Ω vers GND)    │
//  │  Buzzer      ───> GPIO 27  (buzzer actif, + vers GPIO)      │
//  └─────────────────────────────────────────────────────────────┘

// --- Pins modifiables ---
#define DHT_PIN        4      // Pin DATA du DHT22
#define DHT_TYPE       DHT22  // DHT11 si tu as un DHT11 à la place
#define MQ7_PIN        34     // Pin analogique du MQ-7 (doit être GPIO 32-39 sur ESP32)
#define LED_OK_PIN     25     // LED verte (optionnel, mettre -1 pour désactiver)
#define LED_ALERT_PIN  26     // LED rouge (optionnel, mettre -1 pour désactiver)
#define BUZZER_PIN     27     // Buzzer (optionnel, mettre -1 pour désactiver)

// --- Calibration MQ-7 ---
// Ces valeurs dépendent de ton capteur. Voir la datasheet du MQ-7.
// R0 = résistance du capteur dans l'air propre (à calibrer)
// Pour une première utilisation, laisse les valeurs par défaut
// et ajuste R0 après avoir laissé le capteur chauffer 24-48h.
#define MQ7_R0         10.0   // Résistance en air propre (kΩ) — à calibrer !
#define MQ7_RL         10.0   // Résistance de charge sur le module (kΩ)

// ════════════════════════════════════════════════════════════════
//  VARIABLES GLOBALES (ne pas modifier)
// ════════════════════════════════════════════════════════════════

Adafruit_CCS811 ccs811;
DHT dht(DHT_PIN, DHT_TYPE);

bool ccs811_ok = false;  // true si le CCS811 est détecté
unsigned long dernierEnvoi = 0;

// --- Buffer hors-ligne (stocke les mesures si le serveur est indisponible) ---
#define BUFFER_MAX 30  // Max 30 mesures en mémoire (~5 min à 10s d'intervalle)

struct Mesure {
  float co2;
  float tvoc;
  float co;
  float temperature;
  float humidite;
  char  timestamp[20];  // "YYYY-MM-DD HH:MM:SS"
};

Mesure buffer[BUFFER_MAX];
int bufferCount = 0;

// ════════════════════════════════════════════════════════════════
//  SETUP — S'exécute une seule fois au démarrage
// ════════════════════════════════════════════════════════════════

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n====================================");
  Serial.println("  ESP32 IAQ — Démarrage");
  Serial.println("====================================");

  // --- Pins LED / Buzzer ---
  if (LED_OK_PIN >= 0)    { pinMode(LED_OK_PIN, OUTPUT);    digitalWrite(LED_OK_PIN, LOW); }
  if (LED_ALERT_PIN >= 0) { pinMode(LED_ALERT_PIN, OUTPUT); digitalWrite(LED_ALERT_PIN, LOW); }
  if (BUZZER_PIN >= 0)    { pinMode(BUZZER_PIN, OUTPUT);    digitalWrite(BUZZER_PIN, LOW); }

  // --- Connexion WiFi ---
  connecterWiFi();

  // --- Initialisation DHT22 ---
  dht.begin();
  Serial.println("[DHT22] Initialisé sur GPIO " + String(DHT_PIN));

  // --- Initialisation CCS811 ---
  if (ccs811.begin()) {
    ccs811_ok = true;
    Serial.println("[CCS811] Initialisé sur I2C (SDA=21, SCL=22)");
    // Le CCS811 a besoin de 20 min de chauffe pour des lectures fiables
    Serial.println("[CCS811] ⚠ Laisser chauffer 20 min pour des valeurs fiables");
  } else {
    ccs811_ok = false;
    Serial.println("[CCS811] ⚠ Non détecté ! Vérifie le branchement I2C.");
    Serial.println("[CCS811]   SDA → GPIO 21, SCL → GPIO 22, WAKE → GND");
  }

  // --- MQ-7 ---
  pinMode(MQ7_PIN, INPUT);
  Serial.println("[MQ-7]   Initialisé sur GPIO " + String(MQ7_PIN));
  Serial.println("[MQ-7]   ⚠ Laisser chauffer 24-48h pour la calibration");

  Serial.println("\n>> Prêt ! Envoi toutes les " + String(SEND_INTERVAL_MS / 1000) + " secondes");
  Serial.println(">> Serveur : " + String(SERVER_URL));
  Serial.println("====================================\n");
}

// ════════════════════════════════════════════════════════════════
//  LOOP — Boucle principale
// ════════════════════════════════════════════════════════════════

void loop() {
  // Vérifier WiFi
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Déconnecté, reconnexion...");
    connecterWiFi();
  }

  // Envoyer les mesures à intervalle régulier
  unsigned long maintenant = millis();
  if (maintenant - dernierEnvoi >= SEND_INTERVAL_MS) {
    dernierEnvoi = maintenant;

    // Lire tous les capteurs
    float co2  = lireCO2();
    float tvoc = lireTVOC();
    float co   = lireCO();
    float temp = lireTemperature();
    float hum  = lireHumidite();

    // Afficher dans le moniteur série
    Serial.println("─── Mesure ───────────────────────");
    Serial.printf("  CO2  : %.0f ppm\n", co2);
    Serial.printf("  TVOC : %.0f ppb\n", tvoc);
    Serial.printf("  CO   : %.1f ppm\n", co);
    Serial.printf("  Temp : %.1f °C\n", temp);
    Serial.printf("  Hum  : %.1f %%\n", hum);

    // Donner la température/humidité au CCS811 pour compenser
    if (ccs811_ok && !isnan(temp) && !isnan(hum)) {
      ccs811.setEnvironmentalData(hum, temp);
    }

    // Envoyer au serveur (ou mettre en buffer si hors ligne)
    envoyerMesures(co2, tvoc, co, temp, hum);
  }
}

// ════════════════════════════════════════════════════════════════
//  FONCTIONS DE LECTURE DES CAPTEURS
// ════════════════════════════════════════════════════════════════

// --- CO2 (CCS811) ---
float lireCO2() {
  if (!ccs811_ok) return NAN;
  if (!ccs811.available()) return NAN;
  if (ccs811.readData()) return NAN;  // readData() retourne 0 si OK
  return ccs811.geteCO2();  // en ppm (400-8192)
}

// --- TVOC (CCS811) ---
float lireTVOC() {
  if (!ccs811_ok) return NAN;
  // Les données sont déjà lues par lireCO2(), on récupère juste la valeur
  return ccs811.getTVOC();  // en ppb (0-1187)
}

// --- CO (MQ-7, analogique) ---
float lireCO() {
  // Lire la valeur analogique (0-4095 sur ESP32, résolution 12 bits)
  int valeurBrute = analogRead(MQ7_PIN);

  // Convertir en tension (0-3.3V)
  float tension = valeurBrute * (3.3 / 4095.0);

  // Convertir en résistance du capteur (Rs)
  // Formule : Rs = RL * (Vc - Vout) / Vout
  // Vc = tension d'alimentation du circuit de mesure (3.3V côté ESP32)
  if (tension < 0.01) return 0;  // éviter division par zéro
  float rs = MQ7_RL * (3.3 - tension) / tension;

  // Ratio Rs/R0 → concentration CO (ppm)
  // Approximation basée sur la courbe de la datasheet MQ-7
  // log(ppm) = -1.525 * log(Rs/R0) + 0.7645
  float ratio = rs / MQ7_R0;
  if (ratio <= 0) return 0;
  float ppm = pow(10, (-1.525 * log10(ratio) + 0.7645));

  return ppm;
}

// --- Température (DHT22) ---
float lireTemperature() {
  float t = dht.readTemperature();  // en °C
  if (isnan(t)) {
    Serial.println("[DHT22] Erreur lecture température");
    return NAN;
  }
  return t;
}

// --- Humidité (DHT22) ---
float lireHumidite() {
  float h = dht.readHumidity();  // en %
  if (isnan(h)) {
    Serial.println("[DHT22] Erreur lecture humidité");
    return NAN;
  }
  return h;
}

// ════════════════════════════════════════════════════════════════
//  ENVOI DES DONNÉES AU SERVEUR
// ════════════════════════════════════════════════════════════════

void envoyerMesures(float co2, float tvoc, float co, float temp, float hum) {
  // Vérifier que le serveur est accessible
  if (!verifierServeur()) {
    // Serveur indisponible → stocker en buffer
    Serial.println("[HTTP] Serveur indisponible, stockage en buffer");
    ajouterAuBuffer(co2, tvoc, co, temp, hum);
    return;
  }

  // D'abord, vider le buffer s'il y a des mesures en attente
  if (bufferCount > 0) {
    envoyerBuffer();
  }

  // Envoyer la mesure actuelle
  envoyerUneMesure(co2, tvoc, co, temp, hum, "");
}

bool verifierServeur() {
  HTTPClient http;
  http.setTimeout(3000);  // timeout 3 secondes
  http.begin(HEALTH_URL);
  int code = http.GET();
  http.end();
  return (code == 200);
}

void envoyerUneMesure(float co2, float tvoc, float co, float temp, float hum, const char* ts) {
  HTTPClient http;
  http.setTimeout(5000);
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  if (strlen(API_KEY) > 0) {
    http.addHeader("X-API-Key", API_KEY);
  }

  // Construire le JSON
  JsonDocument doc;
  doc["device_id"] = DEVICE_ID;
  if (!isnan(co2))  doc["co2"]         = co2;
  if (!isnan(tvoc)) doc["tvoc"]        = tvoc;
  if (!isnan(co))   doc["co"]          = co;
  if (!isnan(temp)) doc["temperature"] = temp;
  if (!isnan(hum))  doc["humidite"]    = hum;
  if (strlen(ts) > 0) doc["timestamp"] = ts;  // timestamp du buffer

  String body;
  serializeJson(doc, body);

  int code = http.POST(body);

  if (code == 201) {
    String response = http.getString();
    Serial.println("[HTTP] OK ! " + response);

    // Traiter les alertes pour LED/buzzer
    traiterAlertes(response);
  } else if (code == 429) {
    Serial.println("[HTTP] Trop de requêtes, ralentir");
  } else if (code == 401) {
    Serial.println("[HTTP] ⚠ Clé API invalide ! Vérifie API_KEY");
  } else {
    Serial.println("[HTTP] Erreur: " + String(code));
  }

  http.end();
}

// ════════════════════════════════════════════════════════════════
//  BUFFER HORS-LIGNE
// ════════════════════════════════════════════════════════════════

void ajouterAuBuffer(float co2, float tvoc, float co, float temp, float hum) {
  if (bufferCount >= BUFFER_MAX) {
    // Buffer plein → écraser la plus ancienne (décalage)
    for (int i = 0; i < BUFFER_MAX - 1; i++) {
      buffer[i] = buffer[i + 1];
    }
    bufferCount = BUFFER_MAX - 1;
    Serial.println("[Buffer] Plein ! Ancienne mesure écrasée");
  }

  Mesure m;
  m.co2  = co2;
  m.tvoc = tvoc;
  m.co   = co;
  m.temperature = temp;
  m.humidite    = hum;

  // Timestamp approximatif (millis depuis le boot, pas l'heure réelle)
  // Le serveur utilisera son propre timestamp si celui-ci est vide
  m.timestamp[0] = '\0';

  buffer[bufferCount] = m;
  bufferCount++;
  Serial.printf("[Buffer] %d/%d mesures en attente\n", bufferCount, BUFFER_MAX);
}

void envoyerBuffer() {
  Serial.printf("[Buffer] Envoi de %d mesures en attente...\n", bufferCount);

  // Envoyer par lot (batch) au serveur
  HTTPClient http;
  http.setTimeout(10000);  // plus de temps pour un lot
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  if (strlen(API_KEY) > 0) {
    http.addHeader("X-API-Key", API_KEY);
  }

  // Construire un tableau JSON
  JsonDocument doc;
  JsonArray arr = doc.to<JsonArray>();

  for (int i = 0; i < bufferCount; i++) {
    JsonObject obj = arr.add<JsonObject>();
    obj["device_id"] = DEVICE_ID;
    if (!isnan(buffer[i].co2))         obj["co2"]         = buffer[i].co2;
    if (!isnan(buffer[i].tvoc))        obj["tvoc"]        = buffer[i].tvoc;
    if (!isnan(buffer[i].co))          obj["co"]          = buffer[i].co;
    if (!isnan(buffer[i].temperature)) obj["temperature"] = buffer[i].temperature;
    if (!isnan(buffer[i].humidite))    obj["humidite"]    = buffer[i].humidite;
    if (strlen(buffer[i].timestamp) > 0) obj["timestamp"] = buffer[i].timestamp;
  }

  String body;
  serializeJson(doc, body);

  int code = http.POST(body);
  http.end();

  if (code == 201) {
    Serial.printf("[Buffer] %d mesures envoyées avec succès !\n", bufferCount);
    bufferCount = 0;  // Vider le buffer
  } else {
    Serial.printf("[Buffer] Échec envoi batch (code %d), on réessaie plus tard\n", code);
  }
}

// ════════════════════════════════════════════════════════════════
//  ALERTES LOCALES (LED + BUZZER)
// ════════════════════════════════════════════════════════════════

void traiterAlertes(String response) {
  // Parser la réponse JSON du serveur
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, response);
  if (err) return;

  if (!doc.containsKey("alertes")) return;
  JsonObject alertes = doc["alertes"];

  bool enAlerte = false;

  // Vérifier si au moins un capteur est en alerte
  for (JsonPair p : alertes) {
    if (String(p.value().as<const char*>()) == "alert") {
      enAlerte = true;
      break;
    }
  }

  // Mettre à jour LED et buzzer
  if (LED_OK_PIN >= 0 && LED_ALERT_PIN >= 0) {
    if (enAlerte) {
      digitalWrite(LED_OK_PIN, LOW);
      digitalWrite(LED_ALERT_PIN, HIGH);
    } else {
      digitalWrite(LED_OK_PIN, HIGH);
      digitalWrite(LED_ALERT_PIN, LOW);
    }
  }

  if (BUZZER_PIN >= 0) {
    if (enAlerte) {
      // 3 bips courts
      for (int i = 0; i < 3; i++) {
        digitalWrite(BUZZER_PIN, HIGH);
        delay(100);
        digitalWrite(BUZZER_PIN, LOW);
        delay(100);
      }
    }
  }
}

// ════════════════════════════════════════════════════════════════
//  CONNEXION WIFI
// ════════════════════════════════════════════════════════════════

void connecterWiFi() {
  Serial.print("[WiFi] Connexion à ");
  Serial.print(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int tentatives = 0;
  while (WiFi.status() != WL_CONNECTED && tentatives < 40) {
    // Timeout après 20 secondes (40 x 500ms)
    delay(500);
    Serial.print(".");
    tentatives++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println(" OK !");
    Serial.print("[WiFi] IP locale : ");
    Serial.println(WiFi.localIP());

    // Clignoter la LED verte pour confirmer
    if (LED_OK_PIN >= 0) {
      for (int i = 0; i < 3; i++) {
        digitalWrite(LED_OK_PIN, HIGH);
        delay(200);
        digitalWrite(LED_OK_PIN, LOW);
        delay(200);
      }
    }
  } else {
    Serial.println(" ÉCHEC !");
    Serial.println("[WiFi] ⚠ Impossible de se connecter.");
    Serial.println("[WiFi]   Vérifie WIFI_SSID et WIFI_PASSWORD");
    Serial.println("[WiFi]   On réessaiera dans la prochaine boucle");
  }
}
