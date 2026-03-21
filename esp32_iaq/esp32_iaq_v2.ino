/*
 * ============================================================
 *  PROJET ESP32 : CAPTEUR DE QUALITÉ DE L'AIR INTÉRIEUR (IAQ)
 *  VERSION 2 - ROBUSTE & SPÉCIALE DÉBUTANTS
 * ============================================================
 *  CE PROGRAMME FAIT PLUSIEURS CHOSES AUTOMATIQUEMENT :
 *  1. Il se connecte en quelques secondes à votre réseau WiFi.
 *  2. Il lit l'air de la pièce (CO2, Température, Humidité, Gaz Toxicol).
 *  3. Il envoie ces donnees vers le serveur (PC ou Cloud).
 *  4. Si l'air est devenu dangereux, il allume un Ventilateur & fait bipper la boite !
 *  5. Si l'ordinateur de l'ESP32 plante, il s'auto-guérit (Watchdog).
 * ============================================================
 */

// --- INCLUSION DES BIBLIOTHÈQUES (Des plugins magiques rajoutés à la carte mère) ---
#include <WiFi.h>              // Permet à l'antenne ESP32 d'activer le WiFi
#include <WiFiClientSecure.h>  // Permet les connexions HTTPS (chiffrées) vers le Cloud
#include <HTTPClient.h>        // Permet d'envoyer les textos (données) au serveur
#include <ArduinoJson.h>       // Change la donnée pure en version "Site Web Lisible" (le modèle JSON)
#include <Adafruit_CCS811.h>   // Plugin du Capteur noir de l'air de la marque Adafruit (TVOC)
#include <Adafruit_ADS1X15.h>  // Convertisseur analogique ultra precis pour MQ-7
#include <DHT.h>               // Plugin pour sonder le Capteur de chaleur Blanc du DHT22 
#include <esp_task_wdt.h>      // L'armure de Chien de Garde (Redémarreur)
#include <time.h>              // Horloge internet (NTP)
#include <LittleFS.h>          // Disque dur miniature interne
#include <ArduinoOTA.h>        // Mise à jour du code sans valise (par le WiFi)

// WDT_TIMEOUT (15): Définit "15 Secondes de vide" maxi avant de déclencher l'auto-Reset
#define WDT_TIMEOUT 15  

// --- CONFIGURATION DE VOTRE BOX INTERNET (A CHANGER !) ---
const char* WIFI_SSID     = "ton_wifi";          // <-- REMPLACER ! Votre "Nom" Livebox/Freebox
const char* WIFI_PASSWORD = "ton_mot_de_passe";  // <-- REMPLACER ! Votre mot clé internet de base

// --- CONFIGURATION DU DOSSIER CIBLE WINDOWS 11 ---
// Modifiez ce fameux numéro (Ex. 192.168.1.5 ...) par 'Adresse IPv4' récupérée sur la console CMD !!
const char* SERVER_URL = "http://192.168.1.100:5000/api/mesures";
const char* HEALTH_URL = "http://192.168.1.100:5000/api/health";
const char* API_KEY    = "SECRET_IAQ_2026";  // Clé d'authentification serveur

const char* DEVICE_ID = "esp32-salon"; // Comment s'appellera l'objet sur le graphique ?
WiFiClientSecure secureClient;  // Client HTTPS sécurisé pour Render

const unsigned long SEND_INTERVAL_MS = 10000;  // Rythme Chrono de 10 secondes entre envoies

// --- QUEL ENTRÉE DU CAPTEUR VA DANS LE PIN DE L'ESP ? ---
#define DHT_PIN          4      // Grosse vis 4 de l'ESP - branché vers le "DHT22 Humidité"
#define DHT_TYPE         DHT22  // Nature de l'objet
#define RX_CO2           18     // Pin série RX pour le capteur NDIR MH-Z19
#define TX_CO2           17     // Pin série TX pour le capteur NDIR MH-Z19
#define BUZZER_PIN       15     // Broche en direction du "2N2222" transistor de la cloche
#define VENTILATOR_PIN   38     // Broche qui actionne de front le MOSFET "IRLZ44N" à ventil'
#define LED_OK_PIN       25     // OPTION : La led qui dis tout marche bien !
#define LED_ALERT_PIN    26     // La led de CATASTROPHE Rouge

// --- FORMULES MATHEMATIQUES INVISIBLES POUR GAZ ---
#define MQ7_R0         10.0   
#define MQ7_RL         10.0   

// --- SEUILS D'ALERTE AVEC HYSTERESIS (ON/OFF séparés pour éviter le clignotement) ---
const float ALERT_CO2_ON   = 2000.0;  const float ALERT_CO2_OFF   = 1800.0; // ppm
const float ALERT_TVOC_ON  = 600.0;   const float ALERT_TVOC_OFF  = 450.0;  // ppb
const float ALERT_CO_ON    = 35.0;    const float ALERT_CO_OFF    = 25.0;   // ppm
const float ALERT_TEMP_ON  = 35.0;    const float ALERT_TEMP_OFF  = 32.0;   // C
const float ALERT_HUM_ON   = 75.0;    const float ALERT_HUM_OFF   = 65.0;   // %

// --- MACHINE A ETATS POUR LE BUZZER ET LE VENTILATEUR ---
enum AlertState { IDLE, BUZZING, FAN_ON };
AlertState alertState = IDLE;
uint32_t buzzStart = 0;
const uint32_t BUZZ_DURATION_MS = 2000; // Durée du buzzer avant passage au ventilateur

Adafruit_CCS811 ccs811;      // TVOC
DHT dht(DHT_PIN, DHT_TYPE);
Adafruit_ADS1115 ads;        // Convertisseur Analogique (MQ-7)
HardwareSerial CO2Serial(1); // Port serie du MH-Z19

bool ccs811_ok = false;             
bool ads1115_ok = false;
unsigned long dernierEnvoi = 0;     // Compte chrono du dernier top départ d'action

// --- LISTE DE SURVIE HORS LIGNE OBLIGATOIRE ---
// La mémoire RAM disparaît avec LittleFS. Les données sont écrites dans "/mesures.jsonl"

// ================================================================ //
// LE BOUTON D'ACTION D'OUVERTURE: LE SETUP (ALLUMAGE SEUL FOIS)
// ================================================================ //
void setup() {
  Serial.begin(115200);   // Allumage Câble pour la lecture
  delay(500);             // Fait un souffle de 0,5 Sec de base

  if (!LittleFS.begin(true)) { // true = formater si erreur
    Serial.println("[LittleFS] ERREUR CRITIQUE. Disque dur mort.");
  } else {
    Serial.println("[LittleFS] Disque dur OK.");
  }

  // Lancement du Chien de Garde invisible qui reset l'ordi s'il cale
  esp_task_wdt_init(WDT_TIMEOUT, true);
  esp_task_wdt_add(NULL);

  // LA REPARATION N1 de MON ANALYSE : Limiter l'écoute I2C
  Wire.begin();
  Wire.setTimeOut(1000); 

  // Dit à l'ESP quels trous envoie de la puissance (OUTPUT) et lesquels absorbent pour lire (INPUT)
  if (BUZZER_PIN >= 0) { pinMode(BUZZER_PIN, OUTPUT); digitalWrite(BUZZER_PIN, LOW); } // Éteind le Buzzer !
  if (VENTILATOR_PIN >= 0) { pinMode(VENTILATOR_PIN, OUTPUT); digitalWrite(VENTILATOR_PIN, LOW); } // Ferme le ventilo

  if (LED_OK_PIN >= 0) { pinMode(LED_OK_PIN, OUTPUT); digitalWrite(LED_OK_PIN, LOW); }
  if (LED_ALERT_PIN >= 0) { pinMode(LED_ALERT_PIN, OUTPUT); digitalWrite(LED_ALERT_PIN, LOW); }

  connecterWiFi();  // Actionne la fente secrete vers le bas
  secureClient.setInsecure(); // Désactive la vérif de certificat (suffisant pour Render)
  
  // --------- CONFIGURATION OTA ---------
  ArduinoOTA.setHostname(DEVICE_ID);   // Nom dans votre IDE Arduino
  ArduinoOTA.setPassword("iaqadmin");  // Sécurité pour flasher à distance
  ArduinoOTA.begin();                  // Démarre l'écoute !
  // -------------------------------------

  configTime(3600, 3600, "pool.ntp.org"); // Synchronisation de l'heure sur serveur mondial NTP
  dht.begin();      // Demarre le thermometre
  
  // MH-Z19 (Capteur de CO2 dédié)
  CO2Serial.begin(9600, SERIAL_8N1, RX_CO2, TX_CO2);
  Serial.println("[MH-Z19] OK : Capteur CO2 infrarouge demarre");

  // ADS1115 (Convertisseur analogique pour le CO / MQ-7)
  if (!ads.begin(0x48)) {
    Serial.println("[ADS1115] ERREUR : Puce I2C Introuvable (Verifiez SDA/SCL)");
  } else {
    ads1115_ok = true;
    ads.setGain(GAIN_ONE);
    Serial.println("[ADS1115] OK : Convertisseur ADC demarre");
  }

  // Analyse de démarrage si Ok (Tout est bon et détecté dans l'I2C) - S'il pleure c'est une soudure d'étain de fond
  if (ccs811.begin()) {
    ccs811_ok = true; 
    Serial.println("[CCS811] OK : Air Quality Ready !");
  } else {
    Serial.println("[CCS811] ERREUR. LE FIL EST COUPÉ (Vérifiez qu'il y a I2C (SDA=21, SCL=22) !)");
  }
}


// ================================================================ //
// BOUBLE PERMANENTE (LE COEUR QUI BAT 60X FOIS ET A L'INFINI)
// ================================================================ //
void loop() {
  // Dit au fameux Garde (Watchdog) de ne pas redémarrer le système à la minute où on la joue car on est réveillés
  esp_task_wdt_reset(); 

  // Écoute si un nouveau code logiciel est envoyé par l'IDE Arduino via le réseau !
  ArduinoOTA.handle();

  // Reconnexion WiFi active et 100% NON BLOQUANTE
  gererWiFi();

  // Combien de temps passé depuis minuit
  unsigned long maintenant = millis();
  
  // Est il arrivé aux 10 Secondes fatidiques ? OUI ! Envoi en Cours !
  if (maintenant - dernierEnvoi >= SEND_INTERVAL_MS) {
    dernierEnvoi = maintenant;

    float co2  = lireCO2();        
    float tvoc = lireTVOC();       
    float co   = lireCO();         
    float temp = lireTemperature();
    float hum  = lireHumidite();   

    Serial.println("─── Valeur d'écran envoyées  ──────────────────");
    Serial.printf("  CO2  : %.0f ppm\n", co2);
    Serial.printf("  TVOC : %.0f ppb\n", tvoc);
    Serial.printf("  CO   : %.1f ppm\n", co);
    Serial.printf("  Temp : %.1f C\n", temp);
    Serial.printf("  Hum  : %.1f %%\n", hum);

    // ********* MACHINE A ETATS : BUZZER → VENTILATEUR ************
    // Détecte si un seuil HAUT est franchi (activation)
    bool seuilHaut = false;
    if (co2 > ALERT_CO2_ON || tvoc > ALERT_TVOC_ON || co > ALERT_CO_ON || temp > ALERT_TEMP_ON || hum > ALERT_HUM_ON) {
      seuilHaut = true;
    }
    // Détecte si TOUS les seuils sont retombés sous le seuil BAS (désactivation)
    bool toutCalme = true;
    if (co2 > ALERT_CO2_OFF || tvoc > ALERT_TVOC_OFF || co > ALERT_CO_OFF || temp > ALERT_TEMP_OFF || hum > ALERT_HUM_OFF) {
      toutCalme = false;
    }

    unsigned long maintenant2 = millis();
    switch (alertState) {
      case IDLE:
        if (seuilHaut) {
          alertState = BUZZING;
          buzzStart = maintenant2;
          if (BUZZER_PIN >= 0) digitalWrite(BUZZER_PIN, HIGH);
          if (VENTILATOR_PIN >= 0) digitalWrite(VENTILATOR_PIN, LOW);
          if (LED_ALERT_PIN >= 0) digitalWrite(LED_ALERT_PIN, HIGH);
          if (LED_OK_PIN >= 0) digitalWrite(LED_OK_PIN, LOW);
          Serial.println("[ALERTE] BUZZER ACTIVE !");
        } else {
          if (LED_OK_PIN >= 0) digitalWrite(LED_OK_PIN, HIGH);
          if (LED_ALERT_PIN >= 0) digitalWrite(LED_ALERT_PIN, LOW);
        }
        break;

      case BUZZING:
        if (toutCalme) {
          // Tout est redescendu, fausse alerte
          if (BUZZER_PIN >= 0) digitalWrite(BUZZER_PIN, LOW);
          if (VENTILATOR_PIN >= 0) digitalWrite(VENTILATOR_PIN, LOW);
          if (LED_OK_PIN >= 0) digitalWrite(LED_OK_PIN, HIGH);
          if (LED_ALERT_PIN >= 0) digitalWrite(LED_ALERT_PIN, LOW);
          alertState = IDLE;
          Serial.println("[ALERTE] Annulée (valeurs revenues à la normale)");
        } else if (maintenant2 - buzzStart >= BUZZ_DURATION_MS) {
          // Fin du buzzer → passage au ventilateur
          if (BUZZER_PIN >= 0) digitalWrite(BUZZER_PIN, LOW);
          if (VENTILATOR_PIN >= 0) digitalWrite(VENTILATOR_PIN, HIGH);
          alertState = FAN_ON;
          Serial.println("[VENTILATEUR] EN MARCHE (après buzzer)");
        }
        break;

      case FAN_ON:
        if (toutCalme) {
          // Tout est revenu sous les seuils bas → arrêt
          if (VENTILATOR_PIN >= 0) digitalWrite(VENTILATOR_PIN, LOW);
          if (LED_OK_PIN >= 0) digitalWrite(LED_OK_PIN, HIGH);
          if (LED_ALERT_PIN >= 0) digitalWrite(LED_ALERT_PIN, LOW);
          alertState = IDLE;
          Serial.println("[VENTILATEUR] Arrêt (valeurs revenues à la normale)");
        }
        break;
    }

    // Le capteur CCS811 peut moduler sa mesure plus justement via l'autre puce Température croisée
    if (ccs811_ok && !isnan(temp) && !isnan(hum)) {
      ccs811.setEnvironmentalData(hum, temp);
    }

    // Le Factionnaire d'action: Go en bas emballer cela !!
    envoyerMesures(co2, tvoc, co, temp, hum);
  }
}

// ================================================================
// MESUREURS INCLUANTS GARDE ANTI FREEEZE ET NOUVEAUX CAPTEURS
// ================================================================

// Utilitaire pour le MH-Z19 (Securite de la transmission de message série)
uint8_t mhzChecksum(const uint8_t *buf) {
  uint8_t sum = 0;
  for (int i = 1; i < 8; i++) sum += buf[i];
  return (uint8_t)(0xFF - sum + 1);
}

// Mesure du CO2 Réél via le capteur infrarouge MH-Z19
float lireCO2() {
  uint8_t cmd[9] = {0xFF, 0x01, 0x86, 0, 0, 0, 0, 0, 0};
  cmd[8] = mhzChecksum(cmd);

  while (CO2Serial.available()) CO2Serial.read(); // Purge des messages fantomes
  CO2Serial.write(cmd, 9);

  uint32_t t0 = millis();
  int idx = 0;
  uint8_t resp[9];
  
  // Attente du retour pendant max 250ms
  while (millis() - t0 < 250) {
    while (CO2Serial.available() && idx < 9) {
      resp[idx++] = (uint8_t)CO2Serial.read();
    }
    if (idx >= 9) break;
    delay(1);
    esp_task_wdt_reset(); // Pat le chien !
  }

  // Verif d'integrité du message
  if (idx < 9 || resp[0] != 0xFF || resp[1] != 0x86 || mhzChecksum(resp) != resp[8]) {
    return NAN; // Silence radio ou parasite serie
  }
  
  return (float)(resp[2] * 256 + resp[3]);
}

// Gaz volatil
float lireTVOC() {
  if (!ccs811_ok) return NAN;
  return ccs811.getTVOC();
}

// Gaz brut MQ-7 lu impeccablement via l'ADS1115 + Pont Diviseur Physique
float lireCO() {
  if (!ads1115_ok) return NAN;

  // Lecture sur le canal A0 de la puce (précision 16 bits au lieu des 12 bits abimés de l'ESP32)
  int16_t raw = ads.readADC_SingleEnded(0); 
  float tension_pont = ads.computeVolts(raw);
  
  if (tension_pont < 0.001) return NAN;  

  // --- RECONSTRUCTION DE LA TENSION AVANT LE PONT DIVISEUR ---
  // Vous utilisez R1 (Haut) = 2.7k et R2 (Bas) = 4.7k. Ratio d'inversion = (2.7 + 4.7) / 4.7 = 1.574
  float tension_reelle = tension_pont * ((2.7 + 4.7) / 4.7);

  // Sécurité si la tension reconstruite dépasse 5V (impossible physiquement, mais évite les bugs)
  if (tension_reelle >= 5.0) tension_reelle = 4.99;

  // Alimentation de chauffe 5.0V (VCC) d'après le schéma + RL 10kOhm.
  float rs = MQ7_RL * (5.0 - tension_reelle) / tension_reelle; 
  float ratio = rs / MQ7_R0;
  
  if (ratio <= 0.000001) return NAN;
  
  // Equation exacte pour le MQ-7 (Courbe Monoxyde Datasheet)
  float logppm = (log10(ratio) - 1.398) / -0.699;
  return pow(10.0, logppm); 
}

// Température Standard
float lireTemperature() {
  float t = dht.readTemperature();  
  if (isnan(t)) return NAN;
  return t;
}

// Hydrométrie Air Eau
float lireHumidite() {
  float h = dht.readHumidity();  
  if (isnan(h)) return NAN;
  return h;
}

// ================================================================
// LES SECTIONS INTERNET LOCALE  (LE PARLEUR HTTP)
// ================================================================
void envoyerMesures(float co2, float tvoc, float co, float temp, float hum) {
  // Capture de l'heure exacte (NTP)
  char ts[20] = "";
  struct tm timeinfo;
  if (getLocalTime(&timeinfo, 50)) { // Attend max 50ms par précaution
    strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", &timeinfo);
  }

  // Lance un pivert (Check de validation Serveur) ! 
  if (!verifierServeur()) {
    // Si Windows est Mort ou Eteint, ca rentre les données dans un sac interne pour plus tard
    ajouterAuBuffer(co2, tvoc, co, temp, hum, ts);
    return;
  }
  // Si le sac d'avant n'est pas vide (30 valeurs possibles max), alors videz-le avant.
  if (LittleFS.exists("/mesures.jsonl")) envoyerBuffer();
  // Sinon lance le tir tout naturel à la 10éme de seconde 
  envoyerUneMesure(co2, tvoc, co, temp, hum, ts);
}

// Juste tester la porte d'acceuil pour un 200 HTTP !!
bool verifierServeur() {
  HTTPClient http;
  http.setTimeout(3000);  
  http.begin(secureClient, HEALTH_URL);
  int code = http.GET();
  if (code != 200) {
    Serial.printf("[HTTP] Serveur injoignable (Erreur %d: %s)\n", code, http.errorToString(code).c_str());
  }
  http.end();
  return (code == 200);   // Code internet ok monde universel Windows & navigateur ok 
}

// Poste final Windows
void envoyerUneMesure(float co2, float tvoc, float co, float temp, float hum, const char* ts) {
  HTTPClient http;
  http.setTimeout(5000);  // Un temps mort assez long (5 sec)
  http.begin(secureClient, SERVER_URL);
  http.addHeader("Content-Type", "application/json"); // Langue Windows : Bonjour Web Site.
  http.addHeader("X-API-KEY", API_KEY);               // Prouver l'identité de l'ESP32

  // Boite structure de réponse
  StaticJsonDocument<512> doc;
  doc["device_id"] = DEVICE_ID;
  if (!isnan(co2))  doc["co2"]         = co2;       // Ne rajoute l'étiquette QUE si le code n'est pas "Not a number"
  if (!isnan(tvoc)) doc["tvoc"]        = tvoc;
  if (!isnan(co))   doc["co"]          = co;
  if (!isnan(temp)) doc["temperature"] = temp;
  if (!isnan(hum))  doc["humidite"]    = hum;
  if (strlen(ts) > 0) doc["timestamp"] = ts;

  String body; serializeJson(doc, body); // Imprime le dictionnaire en tant qu'un mot ligne
  int code = http.POST(body); // Poste PUSH Ligne dans internet Box 

  if (code == 201) {  // Accepté sur Windows Mahdi (Dashboard Validé) !!
    traiterAlertes(http.getString());   // Analyse le mot de réponse du PC qui dira si y a un risque à avertir Local en BIP.
  } else {
    Serial.printf("[HTTP] Echec de l'envoi direct (Code %d: %s)\n", code, http.errorToString(code).c_str());
  }
  http.end();
}

// La Machine a Retenue Interne (Sur le disque SSD gravé)
void ajouterAuBuffer(float co2, float tvoc, float co, float temp, float hum, const char* ts) {
  File file = LittleFS.open("/mesures.jsonl", FILE_APPEND);
  if (!file) return;

  // Lame couperet pour empêcher le fichier d'exploser (max ~50KB)
  if (file.size() > 50000) {
    file.close();
    LittleFS.remove("/mesures.jsonl");
    file = LittleFS.open("/mesures.jsonl", FILE_APPEND);
  }

  StaticJsonDocument<512> doc;
  if (!isnan(co2))  doc["co2"]         = co2;
  if (!isnan(tvoc)) doc["tvoc"]        = tvoc;
  if (!isnan(co))   doc["co"]          = co;
  if (!isnan(temp)) doc["temperature"] = temp;
  if (!isnan(hum))  doc["humidite"]    = hum;
  if (ts != nullptr && strlen(ts) > 0) doc["timestamp"] = ts;

  serializeJson(doc, file);
  file.print("\n");
  file.close();
}

// Le Vommissement de retour Wifi Rétabli
void envoyerBuffer() {
  File file = LittleFS.open("/mesures.jsonl", FILE_READ);
  if (!file || file.size() == 0) {
    if (file) file.close();
    return;
  }

  HTTPClient http;
  http.setTimeout(10000);  // 10 sec en dur limite (Très très large paquet)
  http.begin(secureClient, SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-API-KEY", API_KEY);

  StaticJsonDocument<512> doc;
  JsonArray arr = doc.to<JsonArray>();

  int lignes_envoyees = 0;
  while (file.available() && lignes_envoyees < 50) { // Max 50 par payload (limite RAM JsonArray)
    String line = file.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      StaticJsonDocument<256> lineDoc;
      if (deserializeJson(lineDoc, line) == DeserializationError::Ok) {
        JsonObject obj = lineDoc.as<JsonObject>();
        obj["device_id"] = DEVICE_ID;
        arr.add(obj);
        lignes_envoyees++;
      }
    }
  }

  bool reste_des_donnees = file.available();
  file.close();

  if (arr.size() > 0) {
    String body; serializeJson(doc, body); // Serialisation JSON massive 
    int code = http.POST(body);
    
    if (code == 201) {  // Accepté sur Windows Mahdi (Dashboard Validé) !!
      // Par sécurité on détruit tout le fichier en cas de succès pour la V2
      LittleFS.remove("/mesures.jsonl"); 
    } else {
      Serial.printf("[HTTP] Echec de l'envoi du buffer LittleFS (Code %d: %s)\n", code, http.errorToString(code).c_str());
    }
  }
  http.end();
}

// Le Système Flash Police local Buzzer ! 
void traiterAlertes(String response) {
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, response)) return; // Si Flask renvoi un bug du type string HTML 500, le bot ignore pour pas peter sa flash mémoire
  if (!doc.containsKey("alertes")) return;
  JsonObject alertes = doc["alertes"];  

  bool enAlerte = false;
  // Scrin de toutes le paires [Titre, valeur] 
  for (JsonPair p : alertes) {
    if (String(p.value().as<const char*>()) == "alert") { enAlerte = true; break; } // ALERTE ROUGE sur UNE seule ?
  }

  if (LED_OK_PIN >= 0 && LED_ALERT_PIN >= 0) {
    digitalWrite(LED_OK_PIN, !enAlerte);   // Eteindra la Verte automatique
    digitalWrite(LED_ALERT_PIN, enAlerte); // Allumera la rouge automatique if TRUE !
  }

  if (BUZZER_PIN >= 0 && enAlerte) {
    for (int i = 0; i < 3; i++) { // Tire sur la pin BUZZER 3 Fois !! Bip, Bip, Bip.
        digitalWrite(BUZZER_PIN, HIGH);
        delay(100);
        digitalWrite(BUZZER_PIN, LOW);
        delay(100);
        esp_task_wdt_reset(); 
    }
  }
}

unsigned long dernierEssaiWiFi = 0;

// Reconnexion "Silencieuse" (Tâche de fond) pour ne jamais ralentir loop()
void gererWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  if (millis() - dernierEssaiWiFi >= 30000) { // Tente toutes les 30s
    Serial.println("[WIFI] Tentative de reconnexion active en arriere-plan...");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    dernierEssaiWiFi = millis();
  }
}

// Tentative d'amorce vers ton SSID WiFI (Nom de ta box) au demarrage !
void connecterWiFi() {
  WiFi.mode(WIFI_STA);   // Mode simple invité (Station)
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tentatives = 0;
  
  // Limite le boot block crash infini car ESP32 parfois rate les box Free / orange de Wpa3.
  while (WiFi.status() != WL_CONNECTED && tentatives < 40) {
    delay(500); // Wait de demi seconde 
    tentatives++;
    esp_task_wdt_reset();  // Pat le chien encore ici pendant que le wifi essaie
  }
  
  // Ok Je te Fait l'acconpagnement visuel ! (La led VERTE va te faire 3 coucous) 
  if (WiFi.status() == WL_CONNECTED && LED_OK_PIN >= 0) {
    for (int i=0; i<3; i++) { digitalWrite(LED_OK_PIN, HIGH); delay(200); digitalWrite(LED_OK_PIN, LOW); delay(200); }
  }
}
