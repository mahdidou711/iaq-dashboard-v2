/*
 * SCRIPT DE CALIBRATION DU CAPTEUR MQ-7 VIA ADS1115
 * Pont diviseur utilisé : R1 = 10 kOhm (en haut), R2 = 20 kOhm (en bas)
 * ADS1115 câblé sur le bus I2C secondaire : SDA2=GPIO2, SCL2=GPIO1
 *
 * Instructions :
 * 1. Branchez votre ESP32 en extérieur ou dans une pièce parfaitement aérée.
 * 2. Téléversez ce code dans l'ESP32.
 * 3. Ouvrez le moniteur série de l'IDE Arduino (vitesse: 115200 bauds).
 * 4. Attendez 60 secondes que la chauffe et la lecture se terminent.
 * 5. Copiez la valeur finale "R0" calculée.
 * 6. Ouvrez le code principal (esp32_iaq_v2.ino) et mettez à jour '#define MQ7_R0' en haut.
 */

#include <Arduino.h>
#include <Adafruit_ADS1X15.h>
#include <Wire.h>

Adafruit_ADS1115 ads;
const float MQ7_RL = 10.0; // Résistance de charge (Load Resistance) en Kilo-Ohms

void setup() {
  Serial.begin(115200);
  delay(2000);
  
  // ADS1115 sur le bus I2C secondaire (SDA2=GPIO2, SCL2=GPIO1) — identique au firmware principal
  Wire1.begin(2, 1);

  Serial.println("\n\n================================================");
  Serial.println("  CALIBRATION DU MQ-7 VIA ADS1115 (PONT DIVISEUR)");
  Serial.println("================================================");

  if (!ads.begin(0x48, &Wire1)) {
    Serial.println("ERREUR CRITIQUE: ADS1115 introuvable sur Wire1 (verifiez SDA2=GPIO2, SCL2=GPIO1).");
    while(1) delay(100);
  }
  ads.setGain(GAIN_ONE);
  Serial.println("ADS1115 détecté. Démarrage...");

  Serial.println("Assurez-vous que le capteur est à l'AIR LIBRE (sans pollution).");
  Serial.println("Le MQ-7 a besoin de chauffer. Lancement sur 60 secondes...\n");

  float rs_sum = 0;
  int num_readings = 60;
  
  for (int i = 0; i < num_readings; i++) {
    int16_t raw = ads.readADC_SingleEnded(0); 
    float tension_pont = ads.computeVolts(raw);
    
    // Protection mathématique
    if (tension_pont < 0.001) tension_pont = 0.001;
    
    // Inversion du pont diviseur : R1=10k (haut), R2=20k (bas) → ratio = (10+20)/20 = 1.5
    float tension_reelle = tension_pont * ((10.0 + 20.0) / 20.0);

    // Sécurité
    if (tension_reelle >= 5.0) tension_reelle = 4.99;

    // Calcul de la résistance instantanée Rs (sur la base d'une alim de chauffe 5V)
    float rs_instant = MQ7_RL * (5.0 - tension_reelle) / tension_reelle;
    rs_sum += rs_instant;
    
    Serial.printf("Lecture %d/%d : Rs = %.2f kOhm (Pont: %.2f V | Réelle: %.2f V)\n", 
                   i+1, num_readings, rs_instant, tension_pont, tension_reelle);
    delay(1000);
  }
  
  float rs_moyen = rs_sum / num_readings;
  
  // Dans l'air pur (20°C, 65% Humidité), le ratio Rs/R0 du MQ-7 est théoriquement ~26.0
  float r0_calcule = rs_moyen / 26.0;

  Serial.println("\n================================================");
  Serial.println("            CALIBRATION TERMINÉE !              ");
  Serial.println("================================================");
  Serial.printf("Moyenne de votre air pur (Rs) : %.2f kOhm\n", rs_moyen);
  Serial.printf("----> VOTRE VALEUR R0 FINALE : %.2f <----\n", r0_calcule);
  Serial.println("\nAction requise :");
  Serial.println("Ouvrez 'esp32_iaq_v2.ino' et modifiez la ligne :");
  Serial.printf("   #define MQ7_R0 %.2f\n", r0_calcule);
  Serial.println("================================================\n");
}

void loop() {
  delay(10000);
}
