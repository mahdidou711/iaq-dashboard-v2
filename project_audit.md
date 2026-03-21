# 🔬 AUDIT FINAL DÉTAILLÉ — PROJET IAQ V2

> **Date :** 21 Mars 2026  
> **Méthode :** Lecture intégrale + `wc -l` + `grep` croisé  
> **Scope :** 21 fichiers, 2 243 lignes de code source  

---

## 📁 Arborescence Complète

```
iaq_project/                         RÔLE
├── app.py                  641 L    Serveur backend Flask (Python)
├── requirements.txt         31 L    Dépendances Python épinglées
├── Procfile                  1 L    Commande de démarrage Render
├── .python-version           1 L    Force Python 3.11.9 sur Render
├── .gitignore                7 L    Exclusions Git
├── README.md               519 L    Documentation utilisateur
├── Guide_IAQ.pdf            99 Ko   Guide PDF (LaTeX)
├── guide-header.tex                 En-tête LaTeX du PDF
├── roadmap_implementation.md        Plan de route original
├── project_audit.md                 CE FICHIER
│
├── esp32_iaq/
│   ├── esp32_iaq.ino       527 L    Firmware V1 (ARCHIVE — ne plus utiliser)
│   └── esp32_iaq_v2.ino    491 L    Firmware V2 (ACTIF)
│
├── mq7_calibration/
│   └── mq7_calibration.ino  82 L    Script de calibration MQ-7
│
├── static/js/               374 Ko  6 bibliothèques JavaScript locales
│   ├── chart.umd.min.js              Chart.js (graphiques)
│   ├── chartjs-adapter-date-fns.bundle.min.js   Axe temporel
│   ├── chartjs-plugin-annotation.min.js         Lignes de seuils
│   ├── chartjs-plugin-zoom.min.js               Zoom/Pan
│   ├── hammer.min.js                            Gestes tactiles
│   └── socket.io.min.js                         WebSocket
│
└── templates/
    ├── index.html           819 L    Tableau de bord interactif
    └── infos.html           212 L    Page d'informations capteurs
```

---

## 1. BACKEND PYTHON — `app.py` (641 lignes)

### 1.1 Imports (L1–L24)

| Import | Usage |
|--------|-------|
| `csv`, `io` | Export CSV des données |
| `os` | Variables d'environnement Render (`DB_PATH`, `PORT`) |
| `sqlite3` | Moteur de base de données |
| `logging` | Journalisation serveur |
| `smtplib` + `email.mime.*` | Envoi d'emails Gmail |
| `threading` | Envoi email non-bloquant |
| `Flask` + extensions | Serveur web + rate limiter + CORS + SocketIO + Compress |
| `APScheduler` | Nettoyage automatique BDD à 3h00 |

### 1.2 Configuration (L39–L80)

| Variable | Valeur | Rôle |
|----------|--------|------|
| `DATABASE` | `os.environ.get("DB_PATH", "iaq.db")` | Chemin BDD (Render Disk compatible) |
| `API_KEY` | `SECRET_IAQ_2026` | Authentification ESP32 → Serveur |
| `DEBUG` | `True` | Active `/api/seed` + logs détaillés |
| `DATA_RETENTION_DAYS` | `30` | Suppression auto données > 30 jours |
| `SENSOR_OFFLINE_MINUTES` | `5` | Seuil "capteur hors ligne" |
| `EMAIL_ALERTS_ENABLED` | `True` | Active/désactive les emails |
| `EMAIL_SENDER` | ⚠️ Placeholder | Adresse Gmail robot |
| `EMAIL_PASSWORD` | ⚠️ Placeholder | Mot de passe d'application Google |
| `EMAIL_RECEIVER` | ⚠️ Placeholder | Votre vraie adresse |

**Seuils d'alerte (identiques côté ESP32) :**

| Capteur | Warn | Alert | Unité |
|---------|------|-------|-------|
| CO2 | 1000 | 2000 | ppm |
| TVOC | 300 | 600 | ppb |
| CO | 9 | 35 | ppm |
| Température | 28 | 35 | °C |
| Humidité | 60 | 75 | % |

### 1.3 Fonctions Internes

| Fonction | Lignes | Description |
|----------|--------|-------------|
| `get_db()` | 92–96 | Connexion SQLite lazily via `g` |
| `close_db()` | 99–103 | Fermeture auto en fin de requête |
| `init_db()` | 106–133 | Crée tables `mesures` + `alertes` + index |
| `cleanup_old_data()` | 136–143 | Purge > 30 jours |
| `validate_sensor_value()` | 148–156 | Vérifie type + plage par capteur |
| `validate_measurement()` | 159–168 | Valide les 5 champs d'un JSON |
| `send_email_alert_async()` | 173–195 | Email Gmail via thread daemon |
| `verifier_alertes()` | 200–248 | Détecte warn/alert → INSERT alertes → email |

### 1.4 Routes API

| Route | Méthode | Auth | Rate | Codes Retour | Description |
|-------|---------|------|------|-------------|-------------|
| `/` | GET | — | — | 200 | Dashboard HTML |
| `/infos` | GET | — | — | 200 | Page infos capteurs |
| `/api/health` | GET | — | — | 200 | Vérification en ligne |
| `/api/mesures` | POST | API Key | 30/min | 201, 400, 401 | Réception données single ou batch |
| `/api/data` | GET | — | — | 200 | Données paginées + filtres dates |
| `/api/stats` | GET | — | — | 200 | AVG/MIN/MAX par capteur |
| `/api/alertes` | GET | — | — | 200 | Historique alertes filtrable |
| `/api/export` | GET | — | — | 200 | Téléchargement CSV |
| `/api/clear` | POST | API Key | — | 200, 401 | Vider toute la BDD |
| `/api/seed` | POST | API Key | — | 200, 401, 403 | Générer 1440 points de test (DEBUG only) |

### 1.5 Base de Données SQLite

**Table `mesures` :**

| Colonne | Type | Contrainte |
|---------|------|------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| timestamp | TEXT | NOT NULL |
| co2 | REAL | — |
| tvoc | REAL | — |
| co | REAL | — |
| temperature | REAL | — |
| humidite | REAL | — |

**Table `alertes` :**

| Colonne | Type | Contrainte |
|---------|------|------------|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT |
| timestamp | TEXT | NOT NULL |
| capteur | TEXT | NOT NULL |
| niveau | TEXT | NOT NULL (warn/alert) |
| valeur | REAL | — |
| seuil | REAL | — |
| message | TEXT | — |

**Index :** `idx_mesures_timestamp`, `idx_alertes_timestamp`

---

## 2. FIRMWARE ESP32 — `esp32_iaq_v2.ino` (491 lignes)

### 2.1 Bibliothèques (11 inclusions)

| Librairie | Rôle |
|-----------|------|
| `WiFi.h` | Connexion réseau |
| `WiFiClientSecure.h` | HTTPS vers Render |
| `HTTPClient.h` | Requêtes HTTP POST/GET |
| `ArduinoJson.h` | Sérialisation JSON |
| `Adafruit_CCS811.h` | Capteur TVOC (I2C) |
| `Adafruit_ADS1X15.h` | ADC 16 bits pour MQ-7 (I2C) |
| `DHT.h` | Capteur Température/Humidité |
| `esp_task_wdt.h` | Watchdog Timer |
| `time.h` | Horloge NTP |
| `LittleFS.h` | Stockage flash non-volatile |
| `ArduinoOTA.h` | Mise à jour sans fil |

### 2.2 Brochage (Pins)

| Pin | Composant | Direction |
|-----|-----------|-----------|
| 4 | DHT22 (Température/Humidité) | INPUT |
| 17 | MH-Z19 TX (CO2) | OUTPUT |
| 18 | MH-Z19 RX (CO2) | INPUT |
| 15 | Buzzer (via 2N2222) | OUTPUT |
| 38 | Ventilateur (via IRLZ44N MOSFET) | OUTPUT |
| 25 | LED Verte (OK) | OUTPUT |
| 26 | LED Rouge (Alerte) | OUTPUT |
| SDA/SCL | CCS811 + ADS1115 (bus I2C) | I2C |

### 2.3 Fonctions du Firmware

| Fonction | Lignes | Description |
|----------|--------|-------------|
| `setup()` | 82–139 | Init : LittleFS, WDT 15s, I2C 1000ms, Pins, WiFi, HTTPS, OTA, NTP, MH-Z19 UART 9600, ADS1115 GAIN_ONE, CCS811 |
| `loop()` | 145–200 | WDT reset → OTA handle → WiFi check → Lecture 5 capteurs → Ventilateur → Envoi toutes les 10s |
| `mhzChecksum()` | 204–208 | Calcul checksum UART pour MH-Z19 |
| `lireCO2()` | 211–238 | Protocole binaire MH-Z19 : cmd `0x86` → 9 octets → checksum → `resp[2]*256+resp[3]` |
| `lireTVOC()` | 241–244 | CCS811 I2C → `getTVOC()` |
| `lireCO()` | 247–273 | ADS1115 canal A0 → inversion pont diviseur → courbe MQ-7 |
| `lireTemperature()` | 275–280 | DHT22 → `readTemperature()` |
| `lireHumidite()` | 282–287 | DHT22 → `readHumidity()` |
| `envoyerMesures()` | 292–310 | NTP timestamp → health check → si offline : LittleFS |
| `verifierServeur()` | 313–325 | GET `/api/health` via HTTPS, timeout 3s |
| `envoyerUneMesure()` | 328–353 | POST JSON via HTTPS, timeout 5s, parse alertes |
| `ajouterAuBuffer()` | 356–378 | Append JSONL dans `/mesures.jsonl` (max 50 Ko) |
| `envoyerBuffer()` | 381–427 | Batch POST de max 50 lignes depuis LittleFS |
| `traiterAlertes()` | 430–456 | Parse réponse Flask → Buzzer 3 bips + LEDs |
| `gererWiFi()` | 461–470 | Reconnexion non-bloquante toutes les 30s |
| `connecterWiFi()` | 473–490 | Connexion initiale avec timeout 20s + clignotement LED |

### 2.4 Formule Mathématique du MQ-7

```
Tension pont (ADS1115 A0)
→ Tension réelle = tension_pont × (2.7 + 4.7) / 4.7
→ Rs = RL × (5.0 - tension_réelle) / tension_réelle
→ Ratio = Rs / R0
→ log(ppm) = (log10(ratio) - 1.398) / -0.699
→ CO (ppm) = 10^log(ppm)
```

**Pont diviseur physique :** R1 (haut) = 2.7 kΩ, R2 (bas) = 4.7 kΩ → Vs max = 3.17V (protège ADS1115)

---

## 3. SCRIPT DE CALIBRATION — `mq7_calibration.ino` (82 lignes)

- Lit 60 échantillons à 1/s via ADS1115 canal A0
- Inverse le pont diviseur (même formule que firmware)
- Calcule Rs moyen → R0 = Rs_moyen / 26.0
- Affiche la valeur finale dans le moniteur série

---

## 4. FRONTEND — `index.html` (819 lignes) + `infos.html` (212 lignes)

| Fonctionnalité | Technologie |
|----------------|-------------|
| 5 graphiques temps réel | Chart.js |
| Seuils visuels warn/alert | Plugin Annotation |
| Zoom + Pan tactile | Hammer.js + Plugin Zoom |
| Rafraîchissement instantané | Socket.IO WebSocket |
| Thème Dark / Light | CSS variables + toggle |
| Onglets Graphiques/Stats/Alertes | JS vanilla |
| Export CSV | Lien `/api/export` |
| Indicateur en ligne/hors ligne | Timer JS vs `SENSOR_OFFLINE_MINUTES` |
| 0 dépendance CDN | Tout servi depuis `static/js/` |

---

## 5. FICHIERS DE DÉPLOIEMENT

| Fichier | Contenu | Vérifié |
|---------|---------|---------|
| `Procfile` | `web: gunicorn -k eventlet -w 1 app:app` | ✅ |
| `.python-version` | `3.11.9` | ✅ |
| `requirements.txt` | 31 dépendances (Flask, eventlet, gunicorn…) | ✅ |
| `.gitignore` | `*.db`, `venv/`, `__pycache__/`, `.env` | ✅ |

---

## 6. ANALYSE DE SÉCURITÉ

| Point | Statut | Recommandation |
|-------|--------|----------------|
| Authentification API | ✅ Header `X-API-KEY` | Complexifier la clé en production |
| HTTPS ESP32 → Render | ✅ `WiFiClientSecure` + `setInsecure()` | Suffisant pour Render |
| Rate Limiting | ✅ 30/min POST, 200/min global | — |
| Validation entrées | ✅ Type + plage numérique | — |
| CORS | ✅ `cors_allowed_origins="*"` | Restreindre en prod |
| Email credentials | ⚠️ En dur dans le code | Utiliser variables d'environnement |
| WiFi credentials | ⚠️ En dur dans le `.ino` | Normal pour firmware embarqué |
| `DEBUG = True` | ⚠️ Active `/api/seed` et `/api/clear` | Passer à `False` en production |

---

## 7. VÉRIFICATIONS CROISÉES EFFECTUÉES

| Vérification | Résultat |
|-------------|----------|
| `verifier_alertes()` appelé dans `_insert_single()` ET `_insert_batch()` | ✅ (L312 + L349) |
| `WiFiClientSecure` utilisé dans les 3 fonctions réseau | ✅ (L318, L331, L391) |
| Aucun `analogRead()` résiduel dans V2 | ✅ (tout via ADS1115) |
| Aucun `MQ7_PIN` résiduel dans V2 | ✅ (supprimé) |
| Seuils ESP32 = Seuils Python | ✅ (CO2:2000, TVOC:600, CO:35, Temp:35, Hum:75) |
| `API_KEY` identique ESP32 ↔ Python | ✅ (`SECRET_IAQ_2026`) |
| CCS811 utilisé uniquement pour TVOC | ✅ (CO2 = MH-Z19) |
| Pont diviseur calibration = firmware | ✅ (R1=2.7k, R2=4.7k) |
| Tous les imports Python sont utilisés | ✅ |

---

## 8. BUGS CORRIGÉS LORS DE L'AUDIT

| # | Description | Gravité | Fichier |
|---|-------------|---------|---------|
| 1 | HTTP simple incompatible avec Render HTTPS | 🔴 Critique | `esp32_iaq_v2.ino` |
| 2 | Batch insert sans vérification d'alertes | 🔴 Critique | `app.py` |

---

## 9. ROADMAP 28 POINTS

| Phase | Étapes | Statut |
|-------|--------|--------|
| Phase 1 : Corrections | 1–4 (Bugs, Watchdog, I2C, Seuils ventilateur) | ✅ |
| Phase 2 : Backend | 5–16 (Validation, API, Rate limit, BDD, Dashboard, WebSocket…) | ✅ |
| Phase 3 : Firmware | 17–26 (NTP, LittleFS, Constantes, Calibration, WiFi, ADS1115, OTA…) | ✅ (24 ignoré) |
| Phase 4 : Avancé | 27–28 (Email Gmail, Render Cloud) | ✅ |

---

## 10. ACTIONS AVANT MISE EN SERVICE

> [!IMPORTANT]
> 1. **`esp32_iaq_v2.ino` L31–32** : Remplir `WIFI_SSID` et `WIFI_PASSWORD`
> 2. **`esp32_iaq_v2.ino` L37–38** : Remplacer les URL par `https://VOTRE-NOM.onrender.com/api/mesures` et `/api/health`
> 3. **`app.py` L48–51** : Créer un compte Gmail robot → remplir `EMAIL_SENDER` / `EMAIL_PASSWORD` / `EMAIL_RECEIVER`
> 4. **`esp32_iaq_v2.ino` L57** : Calibrer le MQ-7 avec `mq7_calibration.ino` → remplacer `MQ7_R0`
> 5. **`app.py` L42** : Passer `DEBUG = False`
