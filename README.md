# IAQ V2 -- Tableau de Bord de Qualite de l'Air Interieur

Guide complet pas a pas. Ce document est destine a toute personne souhaitant
deployer et faire fonctionner ce systeme de surveillance de la qualite de l'air,
meme sans experience prealable en programmation ou en electronique.

---

## 1. Presentation du projet

Ce projet permet de mesurer cinq parametres de l'air en temps reel :

| Parametre   | Capteur    | Unite | Seuil Activation | Seuil Desactivation |
|:------------|:-----------|:------|:-----------------|:--------------------|
| CO2         | MH-Z19     | ppm   | 2000              | 1800               |
| TVOC        | CCS811     | ppb   | 220               | 150                |
| CO          | MQ-7 (via ADS1115) | ppm   | 25        | 18                 |
| Temperature | DHT22      | C     | 27                | 25                 |
| Humidite    | DHT22      | %     | 60                | 55                 |

Note : ces seuils sont ceux du firmware fusion (plus sensibles pour la maison).
Le backend app.py a ses propres seuils (warn/alert) pour les emails.

L'architecture est la suivante :

1. **L'ESP32-S3** (microcontroleur) lit les capteurs toutes les 2 secondes
   et envoie les donnees toutes les 5 secondes.
2. Il envoie en **HTTPS POST** (JSON) vers **deux serveurs Render** simultanement :
   - `https://iaq-maison.onrender.com` — dashboard web (Mahdi)
   - `https://iaq-backend.onrender.com` — application Android (Nini)
3. **Le serveur Flask** (`app.py`) valide, stocke dans SQLite, detecte les alertes,
   et envoie un **email Gmail** si un seuil critique est depasse.
4. **Le tableau de bord web** (`index.html`) affiche les graphiques en temps reel
   via Chart.js et WebSocket (Socket.IO).
5. **La page educative** (`infos.html`) explique chaque capteur et ses seuils de sante.
6. En cas de panne WiFi, les donnees sont **sauvegardees sur la flash interne** (LittleFS)
   et envoyees automatiquement aux deux serveurs au retour de la connexion.
7. **Calibration automatique** du MQ-7 : R0 calcule dynamiquement pendant les 60
   premieres secondes apres le demarrage (plus besoin de calibration manuelle).

---

## 2. Contenu du dossier

| Fichier / Dossier            | Lignes | Role |
|:-----------------------------|:-------|:-----|
| `app.py`                     | 824    | Serveur web Python Flask (backend Mahdi). |
| `templates/index.html`       | 819    | Interface web du tableau de bord (frontend). |
| `templates/infos.html`       | 212    | Page educative des capteurs et seuils de sante. |
| `esp32_iaq/esp32_iaq_fusion.ino` | 549 | **Firmware actif** — fusion V2 + Nini (double backend). |
| `esp32_iaq/esp32_iaq_v2.ino` | 547    | Firmware V2 original Mahdi (archive). |
| `esp32_iaq/esp32_iaq_nini.ino` | 570  | Firmware Nini original (archive / reference). |
| `esp32_iaq/mq7_calibration.ino` | 82  | Script de calibration du MQ-7 via ADS1115. |
| `requirements.txt`           | 31     | Dependances Python epinglees (Flask, gunicorn...). |
| `Procfile`                   | 1      | Commande de demarrage pour le Cloud Render. |
| `.python-version`            | 1      | Force Python 3.11.9 sur Render. |
| `README.md`                  | -      | Ce fichier. |

---

## 3. Installation du serveur

### 3.1 Option A : En local (Windows 11)

**Pre-requis :**
- **Python 3.10 ou superieur** : Telecharger sur `https://www.python.org/downloads/`.
  Lors de l'installation, cocher imperativement la case **"Add Python to PATH"**.

**Demarrage pas a pas :**

1. Ouvrir l'Explorateur de fichiers et naviguer jusqu'au dossier `iaq_project`.
2. Cliquer dans la barre d'adresses, taper `cmd` et appuyer sur Entree.
3. Creer un environnement isole :

```
python -m venv venv
```

4. Activer l'environnement :

```
venv\Scripts\activate
```

5. Installer les dependances :

```
pip install -r requirements.txt
```

6. Lancer le serveur :

```
python app.py
```

7. Ouvrir `http://127.0.0.1:5000` dans un navigateur.

IMPORTANT : Ne pas fermer la fenetre noire. Ctrl+C pour arreter.

### 3.2 Option B : Herbergement Cloud (Render)

Le serveur est pre-configure pour Render (plan gratuit).

1. Pousser le code sur GitHub.
2. Creer un **Web Service** sur `https://dashboard.render.com`.
3. Connecter le depot GitHub.
4. Configurer :
   - **Runtime** : Python 3
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `gunicorn -k eventlet -w 1 app:app`
5. (Optionnel) Ajouter un Disk (`/data`, 1 GB) et la variable `DB_PATH=/data/iaq.db`
   pour une base de donnees persistante.
6. Cliquer sur **Create Web Service**.

Le site sera accessible a `https://votre-nom.onrender.com`.

Note : Le fichier `.python-version` force Python 3.11.9 pour eviter les
incompatibilites avec Python 3.14.

---

## 4. Liste complete des fonctions du serveur (`app.py`)

### 4.1 Fonctions internes

| Fonction                   | Ligne  | Role |
|:---------------------------|:-------|:-----|
| `get_db()`                 | 92     | Ouvre une connexion SQLite dans le contexte Flask. |
| `close_db()`               | 99     | Ferme la connexion en fin de requete. |
| `init_db()`                | 106    | Cree les tables `mesures` et `alertes` avec index. |
| `cleanup_old_data()`       | 136    | Supprime les donnees plus vieilles que 30 jours (planifie a 3h00). |
| `validate_sensor_value()`  | 148    | Verifie qu'une valeur est numerique et dans la plage autorisee. |
| `validate_measurement()`   | 159    | Applique la validation a tous les 5 champs capteur. |
| `send_email_alert_async()` | 173    | Envoie un email Gmail d'alerte en arriere-plan (threading). |
| `verifier_alertes()`       | 200    | Compare chaque valeur aux seuils, insere en BDD, declenche email. |
| `require_api_key()`        | 253    | Decorateur : bloque les requetes sans header `X-API-KEY`. |
| `_insert_single()`         | 292    | Insere une seule mesure → verifier_alertes → WebSocket. |
| `_insert_batch()`          | 320    | Insere un lot (max 100) → verifier_alertes → WebSocket. |

### 4.2 Routes API

| Methode | Adresse          | Auth    | Rate Limit | Description |
|:--------|:-----------------|:--------|:-----------|:------------|
| GET     | `/`              | —       | —          | Tableau de bord HTML. |
| GET     | `/infos`         | —       | —          | Page educative des capteurs. |
| GET     | `/api/health`    | —       | —          | Test de vie (`{"statut":"ok"}`). |
| POST    | `/api/mesures`   | API Key | 30/min     | Reception donnees (single ou batch). |
| GET     | `/api/data`      | —       | —          | Donnees paginées et filtrables par date. |
| GET     | `/api/stats`     | —       | —          | Moyenne, min, max par capteur. |
| GET     | `/api/alertes`   | —       | —          | Historique des alertes (filtrable). |
| GET     | `/api/export`    | —       | —          | Telechargement CSV (compatible Excel). |
| POST    | `/api/clear`     | API Key | —          | Supprime toutes les donnees. |
| POST    | `/api/seed`      | API Key | —          | Genere 1440 mesures de test (DEBUG only). |

### 4.3 Parametres configurables

| Variable                 | Valeur par defaut         | Description |
|:-------------------------|:--------------------------|:------------|
| `DATABASE`               | `os.environ.get("DB_PATH", "iaq.db")` | Chemin BDD (Render Disk compatible). |
| `API_KEY`                | `"SECRET_IAQ_2026"`       | Cle d'authentification. |
| `DEBUG`                  | `False`                   | Active /api/seed et logs detailles. |
| `DATA_RETENTION_DAYS`    | `30`                      | Duree de conservation en jours. |
| `SENSOR_OFFLINE_MINUTES` | `5`                       | Delai "HORS LIGNE" sur le dashboard. |
| `EMAIL_ALERTS_ENABLED`   | `True`                    | Active/desactive les emails d'alerte. |
| `EMAIL_SENDER`           | A configurer              | Adresse Gmail robot. |
| `EMAIL_PASSWORD`          | A configurer             | Mot de passe d'application Google. |
| `EMAIL_RECEIVER`         | A configurer              | Adresse destinataire des alertes. |

### 4.4 Base de donnees SQLite

**Table `mesures` :** id, timestamp, co2, tvoc, co, temperature, humidite

**Table `alertes` :** id, timestamp, capteur, niveau (warn/alert), valeur, seuil, message

Les deux tables possedent un index sur `timestamp`.

---

## 5. Liste complete des fonctions du firmware fusion (`esp32_iaq_fusion.ino`)

Le firmware fusion combine la robustesse de la V2 (watchdog, OTA, LittleFS, NTP,
ArduinoJson) avec les ameliorations de Nini (calibration R0 dynamique, lecture
CCS811 correcte, scan I2C, seuils sensibles, machine a etats locale).

### 5.1 Bibliotheques (12)

| Bibliotheque          | Role |
|:----------------------|:-----|
| `Arduino.h`           | Base Arduino (inclus explicitement). |
| `WiFi.h`              | Connexion reseau WiFi. |
| `WiFiClientSecure.h`  | Connexion HTTPS chiffree (Render). |
| `HTTPClient.h`        | Requetes HTTP POST / GET. |
| `ArduinoJson.h`       | Serialisation / deserialisation JSON. |
| `Adafruit_CCS811.h`   | Capteur TVOC via I2C (avec `available()` + `readData()`). |
| `Adafruit_ADS1X15.h`  | Convertisseur ADC 16 bits pour MQ-7 via I2C. |
| `DHT.h`               | Capteur Temperature / Humidite DHT22. |
| `esp_task_wdt.h`      | Watchdog Timer (15 secondes). |
| `time.h`              | Horloge NTP (UTC+1 Algerie, pas de DST). |
| `LittleFS.h`          | Stockage flash non-volatile (buffer hors-ligne). |
| `ArduinoOTA.h`        | Mise a jour du firmware par WiFi (OTA). |

### 5.2 Fonctions

| Fonction                | Role |
|:------------------------|:-----|
| `scanI2C()`             | Scan des bus I2C au demarrage (diagnostic). |
| `setup()`               | Init : LittleFS, WDT, 2x I2C, Pins, WiFi, OTA, NTP, capteurs. |
| `loop()`                | WDT → OTA → WiFi → Polling 2s (capteurs + alertes) → Envoi 5s. |
| `mhzChecksum()`         | Checksum UART pour protocole MH-Z19. |
| `lireCO2()`             | MH-Z19 : cmd 0x86 → checksum → CO2 ppm. |
| `lireTVOC()`            | CCS811 : `available()` → `readData()` → `getTVOC()` (cap 1187 ppb). |
| `lireCO()`              | ADS1115 → pont diviseur x1.5 → calibration R0 60s → courbe MQ-7. |
| `lireTemperature()`     | DHT22 → `readTemperature()`. |
| `lireHumidite()`        | DHT22 → `readHumidity()`. |
| `envoyerMesures()`      | NTP timestamp → health check → buffer ou envoi double serveur. |
| `verifierServeur()`     | GET /api/health (timeout 3s). |
| `envoyerUneMesure()`    | POST JSON vers un serveur (flag `analyserReponse`). |
| `ajouterAuBuffer()`     | Append JSONL dans /mesures.jsonl (max 50 Ko). |
| `envoyerBuffer()`       | Batch POST max 50 mesures (flag `supprimerApres`). |
| `traiterAlertes()`      | Parse reponse serveur → LEDs uniquement (pas de buzzer). |
| `gererWiFi()`           | Reconnexion non-bloquante toutes les 30s. |
| `connecterWiFi()`       | Connexion initiale (40 tentatives, 20s max). |

### 5.3 Differences cles entre fusion et V2

| Aspect | V2 (ancien) | Fusion (actif) |
|:-------|:------------|:---------------|
| Polling / Envoi | 10s combine | 2s polling, 5s envoi (separes) |
| Serveurs | 1 seul (local ou Render) | 2 Render (Mahdi + Nini) |
| Calibration MQ-7 | R0 fixe (`#define MQ7_R0 10.0`) | R0 dynamique 60s au boot |
| Lecture CCS811 | `getTVOC()` directement | `available()` + `readData()` + cap 1187 |
| Seuils | CLAUDE.md originaux | Nini (plus sensibles) |
| Buzzer | Machine etats + bips serveur | Machine etats locale uniquement |
| NTP | `configTime(3600, 3600)` (DST bug) | `configTime(3600, 0)` (correct) |
| JSON envoye | co2, tvoc, co, temp, hum | + fan, buzzer, etat_air |
| Scan I2C | Non | Oui (diagnostic au boot) |
| Validation seuils | Sans verifier NAN/r0Ready | Verifie capteur valide avant comparaison |

### 5.4 Mecanismes de securite integres

- **Watchdog (15s)** : Redemarre l'ESP32 si le code se bloque.
- **Timeout I2C (1s)** : Empeche le bus I2C de bloquer indefiniment.
- **HTTPS** : Communication chiffree via WiFiClientSecure + setInsecure().
- **Cle API** : Header X-API-KEY sur chaque requete POST.
- **Buffer LittleFS (50 Ko)** : Sauvegarde flash non-volatile si serveur hors-ligne.
  Envoye aux deux serveurs au retour de connexion.
- **OTA** : Mise a jour sans fil (mot de passe : `iaqadmin`).
- **NTP** : Horodatage autonome UTC+1 (Algerie, pas de DST).
- **Calibration croisee** : `setEnvironmentalData(hum, temp)` ameliore CCS811.
- **Valeurs NAN** : Les champs invalides sont omis du JSON.
- **Validation capteurs** : Seuils verifies uniquement si capteur valide et calibre.
- **TVOC fallback** : Garde la derniere valeur valide si CCS811 rate un cycle.
- **Hysteresis** : Seuils ON/OFF separes pour eviter le clignotement.
- **Machine a etats locale** : IDLE → BUZZING 2s → FAN_ON → retour IDLE.
  Controle uniquement local, `traiterAlertes()` ne touche plus au buzzer.
- **LEDs** : Desactivees par defaut (GPIO 25/26 non utilisables sur WROOM-1).

### 5.5 Parametres a modifier avant televersement

| Variable         | Ligne | Valeur actuelle                | Ce qu'il faut mettre |
|:-----------------|:------|:-------------------------------|:---------------------|
| `WIFI_SSID`      | 25    | `"IdoomFibre_AT3P2evDS_EXT"`   | Le nom du reseau WiFi. |
| `WIFI_PASSWORD`  | 26    | `"zevwhF9e"`                   | Le mot de passe WiFi. |
| `SERVER_URL_1`   | 29    | `"https://iaq-maison.onrender.com/api/mesures"` | Dashboard Mahdi. |
| `SERVER_URL_2`   | 30    | `"https://iaq-backend.onrender.com/api/mesures"` | Backend Nini. |
| `HEALTH_URL_1`   | 31    | `"https://iaq-maison.onrender.com/api/health"` | Health check serveur 1. |

---

## 6. Calibration du MQ-7

### 6.1 Calibration automatique (firmware fusion)

Le firmware fusion calibre automatiquement le MQ-7 pendant les **60 premieres
secondes** apres le demarrage. Pendant ce temps, le capteur CO affiche "NAN"
sur le moniteur serie. Apres 60s, la valeur R0 est calculee et les mesures
de CO commencent.

Pour une calibration optimale : demarrer l'ESP32 dans une **piece bien aeree**
ou en exterieur. La valeur R0 calculee est affichee dans le moniteur serie.

### 6.2 Calibration manuelle (`mq7_calibration.ino`)

Pour une calibration plus precise (script dedie) :

1. Brancher l'ESP32 avec l'ADS1115 et le MQ-7 en **exterieur ou piece bien aeree**.
2. Ouvrir `esp32_iaq/mq7_calibration.ino` dans Arduino IDE.
3. Televerser et ouvrir le Moniteur Serie (115200 bauds).
4. Attendre 60 secondes (60 lectures).
5. La valeur **R0** est affichee a la fin.

Note : le firmware fusion n'a pas besoin de cette etape (calibration automatique),
mais le script reste utile pour verifier la valeur R0 en air pur.

Le script applique la meme formule de pont diviseur (R1=10k, R2=20k) que
le firmware principal.

---

## 7. Liste complete des fonctions du frontend (`index.html`)

### 7.1 Fonctionnalites du tableau de bord

- **3 onglets** : Graphiques temps reel, Statistiques, Historique alertes.
- **5 graphiques** : CO2, TVOC, CO, Temperature, Humidite avec lignes de seuil.
- **Theme sombre / clair** : Sauvegarde dans localStorage.
- **Zoom interactif** : Molette ou pincement tactile (Hammer.js).
- **Filtrage par date** : Champs "Du" / "Au" sur chaque onglet.
- **Export CSV** : Bouton de telechargement.
- **WebSocket temps reel** : Rafraichissement instantane (Socket.IO).
- **Badge d'alertes** : Compteur rouge sur l'onglet alertes.
- **Indicateur en direct** : "EN LIGNE" (vert) ou "HORS LIGNE" (rouge pulsant).
- **Responsive mobile** : Grille adaptative sous 700px.
- **0 dependance CDN** : Toutes les librairies JS servies localement (static/js/).

---

## 8. Page educative (`infos.html`)

Accessible via le lien **"Comprendre les capteurs"** en haut du tableau de bord.

- **CO2** : Gaz naturel produit par la respiration. >2000 ppm = air confine.
- **TVOC** : Composes organiques volatils (peintures, colles). Certains sont cancerigenes.
- **CO** : Gaz inodore et mortel. >35 ppm = evacuer. ~300 deces/an en France.
- **Temperature** : OMS recommande 18-22 C. >35 C = risque coup de chaleur.
- **Humidite** : Ideal 40-60%. Trop humide = moisissures et acariens.

---

## 9. Cablage materiel

### 9.1 Avertissements critiques

**AVERTISSEMENT 1 -- Alimentation** :
Ne jamais utiliser une pile 9V. Utiliser un adaptateur secteur >=2A.

**AVERTISSEMENT 2 -- Tension du MQ-7** :
Le MQ-7 delivre jusqu'a 5V. L'ADS1115 tolere max 4.096V en gain GAIN_ONE.
Le pont diviseur R1=10 kOhm / R2=20 kOhm reduit la tension a max 3.33V.

**AVERTISSEMENT 3 -- Bus I2C** :
Si les cables depassent 15 cm, ajouter des pull-up de 4.7 kOhm.

### 9.2 Schema d'alimentation

```
Adaptateur secteur (9-12V 2A)
        |
   [Diode D1] (protection inversion)
        |
   [LM2596 IN+]---[LM2596 IN-]
        |                |
   Regler la vis      GND commun
   jusqu'a 5.00V
        |
   [LM2596 OUT+]---[LM2596 OUT-]
        |                |
     +5V rail         GND rail
```

### 9.3 Branchements complets

**ESP32-S3 (alimentation)**

| Broche ESP32 | Connecter a          |
|:-------------|:---------------------|
| `5VIN`       | LM2596 OUT+ (+5V)    |
| `GND`        | LM2596 OUT- (masse)  |

**MH-Z19 (CO2 par infrarouge NDIR)**

| Broche MH-Z19 | Connecter a      |
|:---------------|:-----------------|
| `VIN` (+5V)    | LM2596 OUT+ (+5V) |
| `GND`          | GND commun       |
| `TX`           | ESP32 `GPIO 18` (RX_CO2) |
| `RX`           | ESP32 `GPIO 17` (TX_CO2) |

Communication UART 9600 bauds.

**ADS1115 (Convertisseur ADC 16 bits) -- Bus I2C SECONDAIRE**

| Broche ADS1115 | Connecter a      |
|:---------------|:-----------------|
| `VDD`          | ESP32 `3.3V`     |
| `GND`          | ESP32 `GND`      |
| `SDA`          | ESP32 `GPIO 2` (SDA2 -- bus Wire1) |
| `SCL`          | ESP32 `GPIO 1` (SCL2 -- bus Wire1) |
| `A0`           | Sortie pont diviseur (voir MQ-7) |
| `ADDR`         | GND (adresse 0x48) |

**CCS811 (TVOC) -- Bus I2C PRINCIPAL -- Alimenter en 3.3V uniquement**

| Broche CCS811 | Connecter a       |
|:--------------|:------------------|
| `VCC`         | ESP32 `3.3V`      |
| `GND`         | ESP32 `GND`       |
| `SDA`         | ESP32 `GPIO 8` (SDA -- bus Wire, defaut ESP32-S3) |
| `SCL`         | ESP32 `GPIO 9` (SCL -- bus Wire, defaut ESP32-S3) |
| `WAKE`        | ESP32 `GND`       |

**DHT22 (Temperature et Humidite)**

| Broche DHT22 | Connecter a       |
|:-------------|:------------------|
| `VCC`        | ESP32 `3.3V`      |
| `GND`        | ESP32 `GND`       |
| `DATA`       | ESP32 `GPIO 4`    |

**MQ-7 (Monoxyde de carbone) -- PONT DIVISEUR OBLIGATOIRE**

| Broche MQ-7 | Connecter a                         |
|:------------|:------------------------------------|
| `VCC`       | LM2596 OUT+ (+5V)                   |
| `GND`       | GND commun                          |
| `A0`        | Voir schema du pont diviseur        |

```
MQ-7 A0 ---[R1 = 10 kOhms]---+--- ADS1115 A0
                              |
                       [R2 = 20 kOhms]
                              |
                             GND
```

Tension resultante : (20 / (10 + 20)) x 5V = 3.33V (securise pour l'ADS1115).

**Ventilateur 5V (via MOSFET IRLZ44N)**

| Broche MOSFET | Connecter a                         |
|:--------------|:------------------------------------|
| Gate (G)      | Resistance 1 kOhm puis `GPIO 38`   |
| Drain (D)     | Fil negatif (-) du ventilateur      |
| Source (S)    | GND commun                          |

Le fil positif (+) du ventilateur va sur OUT+ (5V) du LM2596.

**Buzzer actif (via transistor 2N2222)**

| Broche 2N2222 | Connecter a                         |
|:--------------|:------------------------------------|
| Base (B)      | Resistance 1 kOhm puis `GPIO 15`   |
| Collecteur (C)| Fil negatif (-) du buzzer           |
| Emetteur (E)  | GND commun                          |

**LEDs d'indication (optionnelles -- desactivees par defaut)**

Les LEDs sont desactivees dans le firmware (LED_OK_PIN = -1, LED_ALERT_PIN = -1).
Raison : GPIO 25 n'est pas expose sur le module WROOM-1, et GPIO 26 est reserve
a la flash SPI interne (utilisation dangereuse).
Pour les reactiver, choisir deux GPIOs libres (ex: GPIO 10 et GPIO 11) et
modifier les defines en haut de esp32_iaq_v2.ino.

---

## 10. Televersement du code vers l'ESP32

### 10.1 Pre-requis

- **Arduino IDE 2.x** : Telecharger sur `https://www.arduino.cc/en/software`.
- Installer le support ESP32 : Menu Fichier > Preferences > URLs de gestionnaire,
  ajouter : `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
  Puis Outils > Gestionnaire de cartes, chercher "esp32" et installer.
- Installer les bibliotheques (Outils > Gerer les bibliotheques) :
  - `Adafruit CCS811 Library`
  - `Adafruit ADS1X15` (par Adafruit)
  - `DHT sensor library` (par Adafruit)
  - `ArduinoJson` (par Benoit Blanchon)

### 10.2 Configuration

1. Ouvrir `esp32_iaq/esp32_iaq_fusion.ino` dans Arduino IDE.
2. Modifier `WIFI_SSID` et `WIFI_PASSWORD` (lignes 25-26).
3. Verifier les URLs des serveurs Render (lignes 29-31).
4. Menu Outils > Type de carte : `ESP32S3 Dev Module`.
5. Menu Outils > Port : choisir le port COM (ex: COM3).
6. Cliquer sur `Telecharger` (fleche droite).

### 10.3 Verification

Ouvrir le Moniteur Serie (115200 bauds). L'ESP32 doit afficher :
- `[LittleFS] OK.`
- `Scan Wire (CCS811)` + `Scan Wire1 (ADS1115)` avec adresses I2C trouvees
- `[ADS1115] OK`
- `[CCS811] OK`
- Pendant 60s : calibration MQ-7 (CO affiche NAN)
- Puis mesures toutes les 2 secondes, envoi toutes les 5 secondes.

### 10.4 Mises a jour OTA (sans cable)

Apres le premier televersement par USB, les mises a jour suivantes
peuvent etre faites par WiFi :
1. Dans Arduino IDE : Outils > Port > choisir `esp32-salon` (reseau).
2. Entrer le mot de passe OTA : `iaqadmin`.
3. Televerser normalement.

---

## 11. Notifications par email

Le serveur envoie automatiquement un email lorsqu'un capteur atteint
le niveau **"ALERTE"** (et non "Attention").

### 11.1 Configuration Gmail

1. Creer un compte Gmail dedie au systeme (ex: `votre-projet-iaq@gmail.com`).
2. Activer la double authentification sur ce compte.
3. Generer un **Mot de passe d'application** :
   `https://myaccount.google.com/apppasswords`
4. Modifier `app.py` (lignes 48-51) :
   - `EMAIL_SENDER` = l'adresse Gmail robot.
   - `EMAIL_PASSWORD` = le mot de passe d'application (16 caracteres).
   - `EMAIL_RECEIVER` = votre adresse personnelle.

### 11.2 Format de l'email recu

```
Sujet : ⚠️ ALERTE IAQ : Danger CO Eleve !
Corps :
  Capteur concerne : CO
  Valeur Actuelle  : 42 ppm
  Seuil d'Alerte   : 35 ppm
  Veuillez aerrer la piece immediatement.
```

---

## 12. Formule mathematique du MQ-7

Le capteur MQ-7 delivre une tension analogique proportionnelle au gas.
Le calcul du CO en ppm suit ces etapes :

1. **Lecture** : L'ADS1115 lit la tension apres pont diviseur sur le canal A0.
2. **Inversion** : `tension_reelle = tension_pont x (10 + 20) / 20` (ratio = 1.5)
3. **Resistance** : `Rs = RL x (5.0 - tension_reelle) / tension_reelle` (RL = 10 kOhm)
4. **Ratio** : `ratio = Rs / R0` (R0 = valeur calibree en air pur)
5. **Concentration** : `log(ppm) = (log10(ratio) - 1.398) / -0.699`
6. **Resultat** : `CO (ppm) = 10^log(ppm)`

---

## 13. Securite

| Point                 | Statut    | Detail |
|:----------------------|:----------|:-------|
| Cle API               | Active    | Header X-API-KEY requis pour POST. |
| HTTPS                 | Active    | WiFiClientSecure + setInsecure. |
| Rate Limiting         | Actif     | 30/min POST, 200/min global. |
| Validation donnees    | Active    | Plages numeriques verifiees. |
| Compression           | Active    | Gzip/Brotli via Flask-Compress. |
| WebSocket             | Actif     | Socket.IO pousse les mises a jour. |
| Nettoyage auto BDD    | Actif     | Tous les jours a 3h00 (APScheduler). |
| Email alertes         | Actif     | Gmail via smtplib + threading. |

---

## 14. Ameliorations realisees (Roadmap completee)

Toutes les ameliorations de la roadmap ont ete implementees sauf le Deep Sleep
(incompatible avec le pre-chauffage MQ-7 et MH-Z19).

Le firmware fusion ajoute les ameliorations suivantes par rapport a la V2 :

1. Correction bugs bloquants
2. Watchdog Timer (15s)
3. Timeout I2C (1s)
4. Seuils ventilateur en constantes (avec hysteresis)
5. Validation donnees backend
6. Cle API (X-API-KEY)
7. Rate limiting (flask-limiter)
8. Nettoyage automatique BDD (APScheduler)
9. Alertes backend + table SQLite
10. Dashboard 5 graphiques (Chart.js)
11. Annotations seuils sur graphes
12. Indicateur capteur en ligne
13. Page statistiques (min/moy/max)
14. Export CSV
15. Page educative capteurs
16. WebSocket temps reel (Socket.IO)
17. Horloge NTP autonome (UTC+1 Algerie, pas de DST)
18. Stockage persistant LittleFS
19. Constantes nommees pour seuils
20. Script de calibration MQ-7 dedie
21. Log des erreurs HTTP
22. Reconnexion WiFi non-bloquante
23. ADC externe ADS1115 (16 bits)
24. ~~Deep Sleep~~ (ignore : capteurs gaz)
25. Mise a jour OTA (ArduinoOTA)
26. Unification des seuils
27. Notifications email Gmail
28. Hebergement Cloud Render
29. **Fusion V2 + Nini** : double backend, calibration R0 dynamique
30. **Scan I2C** au demarrage (diagnostic)
31. **Lecture CCS811 corrigee** : `available()` + `readData()` + cap 1187 ppb
32. **Validation capteurs** avant comparaison seuils (NAN, r0Ready)
33. **Machine a etats locale** : buzzer/ventilateur sans dependance serveur
34. **TVOC fallback** : garde la derniere valeur valide si CCS811 rate
35. **NTP corrige** : pas de DST pour l'Algerie
