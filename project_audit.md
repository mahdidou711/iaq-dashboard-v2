# 🔬 AUDIT COMPLET DU PROJET IAQ — VERSION FINALE (V2)
> **Date :** 21 Mars 2026 — Post-Roadmap 28 Points  
> **Auteur :** Audit automatique  
> **Portée :** Fichier par fichier, ligne par ligne  

---

## 📁 Arborescence Complète du Projet

```
iaq_project/
├── .git/                          # Historique Git
├── .gitignore                     # Exclusions Git (58 octets)
├── .python-version                # Force Python 3.11.9 sur Render
├── Procfile                       # Commande de démarrage Cloud Render
├── README.md                      # Documentation Markdown (25 Ko)
├── Guide_IAQ.pdf                  # Guide PDF LaTeX (99 Ko)
├── app.py                         # Serveur Backend Flask (640 lignes)
├── requirements.txt               # Dépendances Python (32 lignes)
├── roadmap_implementation.md      # Plan de route original
├── project_audit.md               # CE FICHIER (Audit)
│
├── esp32_iaq/
│   ├── esp32_iaq.ino              # Firmware V1 (ancien, conservé)
│   └── esp32_iaq_v2.ino           # Firmware V2 (actif, 488 lignes)
│
├── mq7_calibration/
│   └── mq7_calibration.ino        # Script de calibration MQ-7 (83 lignes)
│
├── static/js/
│   ├── chart.umd.min.js           # Chart.js (205 Ko)
│   ├── chartjs-adapter-date-fns.bundle.min.js
│   ├── chartjs-plugin-annotation.min.js
│   ├── chartjs-plugin-zoom.min.js
│   ├── hammer.min.js              # Gestionnaire de gestes tactiles
│   └── socket.io.min.js           # WebSocket temps réel
│
└── templates/
    ├── index.html                 # Tableau de bord principal (820 lignes)
    └── infos.html                 # Page d'informations capteurs
```

---

## 📄 AUDIT FICHIER PAR FICHIER

---

### 1. `app.py` — Serveur Backend Flask (640 lignes, 23.5 Ko)

**Rôle :** Cerveau central du système. Reçoit les données de l'ESP32, les stocke en SQLite, les sert au frontend, et envoie les alertes par email.

**Architecture :**
| Section | Lignes | Description |
|---------|--------|-------------|
| Imports | 1–24 | Flask, smtplib, threading, SocketIO, Compress, CORS |
| Configuration | 39–80 | DATABASE, API_KEY, seuils, email, labels |
| Base de données | 92–143 | SQLite : tables `mesures` + `alertes`, index, nettoyage auto |
| Validation | 146–168 | Vérification des plages capteurs (ex: CO2 ∈ [0, 10000]) |
| Email Asynchrone | 171–195 | Envoi Gmail via threading (non-bloquant) |
| Alertes | 198–248 | Détection seuils warn/alert + déclenchement email |
| Routes API | 251–360 | POST /api/mesures (single + batch), GET /api/data |
| Stats | 405–453 | AVG/MIN/MAX par capteur sur période |
| Alertes API | 456–492 | GET /api/alertes avec filtres |
| Export CSV | 495–532 | Téléchargement des données brutes |
| Seed (debug) | 555–604 | Génération de 1440 points de test |
| Démarrage | 624–640 | init_db(), scheduler, gunicorn-ready |

**Points Forts :**
- ✅ Rate Limiter (30 req/min sur POST)
- ✅ Clé API obligatoire (`X-API-KEY`)
- ✅ Compression Gzip/Brotli (Flask-Compress)
- ✅ WebSocket temps réel (SocketIO → `update_needed`)
- ✅ Nettoyage automatique à 3h00 du matin (APScheduler)
- ✅ Email d'alerte asynchrone (Gmail via smtplib)
- ✅ `DATABASE = os.environ.get("DB_PATH", "iaq.db")` → Compatible Render Disk
- ✅ `PORT` dynamique via `os.environ` → Compatible Render
- ✅ `init_db()` au niveau module → Gunicorn-ready

**Points d'Attention :**
- ⚠️ `EMAIL_SENDER` / `EMAIL_PASSWORD` sont des placeholders à remplir
- ⚠️ `DEBUG = True` → À mettre à `False` en production
- ⚠️ `API_KEY = "SECRET_IAQ_2026"` → À complexifier pour la production

---

### 2. `esp32_iaq_v2.ino` — Firmware ESP32 (488 lignes, 20.2 Ko)

**Rôle :** Programme embarqué sur la carte ESP32. Lit les capteurs, envoie au serveur, active le ventilateur et le buzzer si danger.

**Architecture :**
| Section | Lignes | Description |
|---------|--------|-------------|
| Bibliothèques | 15–25 | WiFi, HTTP, JSON, CCS811, ADS1115, DHT, LittleFS, OTA |
| Configuration | 27–63 | SSID, URL serveur, Pins, Seuils, Constantes MQ-7 |
| Objets capteurs | 65–72 | CCS811, DHT22, ADS1115, HardwareSerial (MH-Z19) |
| setup() | 80–136 | Init LittleFS, Watchdog, I2C, OTA, MH-Z19, ADS1115, CCS811 |
| loop() | 142–197 | Watchdog reset, OTA handle, WiFi check, lecture + envoi |
| lireCO2() | 211–237 | Protocole binaire UART MH-Z19 avec checksum |
| lireTVOC() | 240–243 | CCS811 I2C |
| lireCO() | 246–272 | ADS1115 → Pont diviseur R1=2.7k/R2=4.7k → Courbe MQ-7 |
| lireTemperature() | 274–279 | DHT22 |
| lireHumidite() | 281–286 | DHT22 |
| envoyerMesures() | 291–309 | NTP timestamp + Health check + LittleFS fallback |
| verifierServeur() | 312–322 | GET /api/health avec timeout 3s |
| envoyerUneMesure() | 325–351 | POST JSON avec API Key |
| ajouterAuBuffer() | 354–376 | Écriture JSONL sur flash (max 50 Ko) |
| envoyerBuffer() | 379–425 | Batch POST de 50 lignes max depuis LittleFS |
| traiterAlertes() | 428–453 | Buzzer 3 bips + LED rouge/verte |
| gererWiFi() | 459–468 | Reconnexion non-bloquante toutes les 30s |
| connecterWiFi() | 471–487 | Connexion initiale avec timeout 20s |

**Points Forts :**
- ✅ Watchdog Timer (15s) → Auto-reset si plantage
- ✅ LittleFS → Stockage persistant hors-ligne (non-volatile)
- ✅ ArduinoOTA → Mise à jour sans fil par le réseau
- ✅ MH-Z19 via UART → CO2 réel par infrarouge (NDIR)
- ✅ ADS1115 via I2C → Précision 16 bits pour le MQ-7
- ✅ Pont Diviseur R1=2.7k / R2=4.7k → Protection ADS1115 (Vs max 3.17V)
- ✅ Horloge NTP → Timestamps autonomes et exacts
- ✅ Reconnexion WiFi non-bloquante
- ✅ Seuils en constantes nommées (faciles à modifier)
- ✅ Health-check avant chaque envoi

**Points d'Attention :**
- ⚠️ `WIFI_SSID` / `WIFI_PASSWORD` → Placeholders à remplir
- ⚠️ `SERVER_URL` → À changer vers l'URL Render en production
- ⚠️ `MQ7_R0 = 10.0` → Placeholder, à calibrer via le script dédié

---

### 3. `mq7_calibration.ino` — Script de Calibration (83 lignes, 3 Ko)

**Rôle :** Programme dédié pour déterminer la vraie valeur R0 du capteur MQ-7.

**Fonctionnement :**
1. Se connecte à l'ADS1115 via I2C
2. Prend 60 lectures d'une seconde chacune (1 minute totale)
3. Inverse le pont diviseur R1=2.7k / R2=4.7k
4. Calcule Rs moyen puis divise par 26.0 (ratio air pur théorique)
5. Affiche la valeur R0 finale dans le moniteur série

**Points Forts :**
- ✅ Utilise l'ADS1115 (cohérent avec le firmware principal)
- ✅ Formule identique au firmware (même pont diviseur)
- ✅ Instructions claires dans les commentaires d'en-tête

---

### 4. `templates/index.html` — Tableau de Bord (820 lignes, 34.6 Ko)

**Rôle :** Interface graphique web avec graphiques interactifs, alertes visuelles, et export CSV.

**Fonctionnalités :**
- Thème Dark/Light avec switch
- 5 graphiques Chart.js (CO2, TVOC, CO, Température, Humidité)
- Seuils visuels (lignes warn/alert via plugin annotation)
- Zoom et pan tactile (Hammer.js)
- Indicateur capteur en ligne/hors ligne
- Onglets : Graphiques / Statistiques / Alertes
- Export CSV
- Rafraîchissement automatique via SocketIO (temps réel)
- Filtre par dates

**Points Forts :**
- ✅ Responsive (mobile + desktop)
- ✅ Aucune dépendance CDN → Tout en local (`static/js/`)
- ✅ WebSocket push → Pas de polling
- ✅ Seuils injectés dynamiquement par Jinja2 depuis Flask

---

### 5. `templates/infos.html` — Page d'Informations (10.3 Ko)

**Rôle :** Page documentaire expliquant chaque capteur utilisé dans le projet.

---

### 6. `static/js/` — Bibliothèques JavaScript (6 fichiers, 374 Ko total)

| Fichier | Taille | Rôle |
|---------|--------|------|
| `chart.umd.min.js` | 205 Ko | Moteur de graphiques Chart.js |
| `chartjs-adapter-date-fns.bundle.min.js` | 50 Ko | Axe temporel |
| `chartjs-plugin-annotation.min.js` | 34 Ko | Lignes de seuils |
| `chartjs-plugin-zoom.min.js` | 13 Ko | Zoom et panoramique |
| `hammer.min.js` | 21 Ko | Gestes tactiles (pinch/drag) |
| `socket.io.min.js` | 50 Ko | WebSocket temps réel |

**Verdict :** ✅ Toutes les librairies sont servies localement (pas de CDN). Le projet fonctionne sans internet côté navigateur.

---

### 7. `requirements.txt` — Dépendances Python (32 lignes)

| Dépendance | Version | Rôle |
|------------|---------|------|
| Flask | 3.1.3 | Framework web |
| Flask-SocketIO | 5.6.1 | WebSocket temps réel |
| Flask-Compress | 1.23 | Compression Gzip/Brotli |
| flask-cors | 6.0.2 | Cross-Origin (ESP32 → Flask) |
| Flask-Limiter | 4.1.1 | Anti-spam (rate limiting) |
| APScheduler | 3.11.2 | Tâches planifiées (nettoyage BDD) |
| eventlet | 0.40.4 | Serveur async pour SocketIO |
| gunicorn | 21.2.0 | Serveur WSGI production (Render) |

**Verdict :** ✅ Versions épinglées. Gunicorn présent pour le déploiement cloud.

---

### 8. `Procfile` — Commande Render (1 ligne)

```
web: gunicorn -k eventlet -w 1 app:app
```

**Verdict :** ✅ Worker eventlet pour SocketIO. 1 seul worker (obligatoire pour SQLite file-based).

---

### 9. `.python-version` — Version Python (1 ligne)

```
3.11.9
```

**Verdict :** ✅ Résout le bug de compatibilité avec `backports.zstd` sur Python 3.14.

---

### 10. `.gitignore` — Exclusions Git (7 lignes)

Exclut : `__pycache__/`, `*.db`, `venv/`, `.env`, `.DS_Store`, `*.sqlite*`

**Verdict :** ✅ Empêche l'upload de la base de données et du virtualenv.

---

### 11. `esp32_iaq.ino` — Firmware V1 (Ancien, 22.8 Ko)

**Rôle :** Ancienne version du firmware, conservée comme archive.

**Verdict :** ⚠️ Ce fichier n'est plus utilisé. Il pourrait être déplacé dans un dossier `archive/` pour éviter la confusion.

---

## 🔒 ANALYSE DE SÉCURITÉ

| Élément | Statut | Détail |
|---------|--------|--------|
| Clé API | ⚠️ | `SECRET_IAQ_2026` → Acceptée mais à complexifier |
| Email Password | ⚠️ | Placeholder dans le code → À remplir avec un mot de passe d'application Google |
| WiFi Credentials | ⚠️ | Placeholders dans le .ino → À remplir avant téléversement |
| Rate Limiting | ✅ | 30 req/min sur POST, 200/min global |
| Validation données | ✅ | Plages vérifiées pour chaque capteur |
| CORS | ✅ | Activé (nécessaire pour ESP32 cross-origin) |
| Debug mode | ⚠️ | `DEBUG = True` → À mettre à `False` en production |

---

## 📊 TABLEAU RÉCAPITULATIF DES 28 POINTS

| # | Étape | Statut |
|---|-------|--------|
| 1 | Correction des erreurs bloquantes | ✅ |
| 2 | Watchdog Timer | ✅ |
| 3 | Timeout I2C | ✅ |
| 4 | Seuils d'alerte ventilateur | ✅ |
| 5 | Validation des données (backend) | ✅ |
| 6 | Sécurisation API (clé) | ✅ |
| 7 | Rate Limiting | ✅ |
| 8 | Nettoyage automatique BDD | ✅ |
| 9 | Alertes backend + table SQLite | ✅ |
| 10 | Tableau de bord (5 graphiques) | ✅ |
| 11 | Annotations seuils sur graphes | ✅ |
| 12 | Indicateur capteur en ligne | ✅ |
| 13 | Page statistiques | ✅ |
| 14 | Export CSV | ✅ |
| 15 | Page infos capteurs | ✅ |
| 16 | WebSocket temps réel | ✅ |
| 17 | Horloge NTP autonome | ✅ |
| 18 | Stockage persistant LittleFS | ✅ |
| 19 | Constantes nommées seuils | ✅ |
| 20 | Calibration MQ-7 (script dédié) | ✅ |
| 21 | Log des erreurs HTTP | ✅ |
| 22 | Reconnexion WiFi active | ✅ |
| 23 | ADC externe ADS1115 | ✅ |
| 24 | Deep Sleep | ⏭️ Ignoré (pré-chauffage capteurs gaz) |
| 25 | Mise à jour OTA | ✅ |
| 26 | Unification des seuils | ✅ |
| 27 | Notifications Email Gmail | ✅ |
| 28 | Hébergement Cloud Render | ✅ |

---

## ✅ VERDICT FINAL

Le projet IAQ V2 est **complet, fonctionnel et prêt pour la production**.

**Statistiques du projet :**
- **Backend Python :** 640 lignes de code
- **Firmware C++ :** 488 + 83 = 571 lignes de code  
- **Frontend HTML/CSS/JS :** 820 lignes + 374 Ko de librairies
- **Total :** ~2 031 lignes de code source
- **Capteurs supportés :** 5 (MH-Z19, CCS811, MQ-7, DHT22×2)
- **Composants matériels :** ADS1115, 2N2222, IRLZ44N, Buzzer, LEDs, Ventilateur

**Actions requises avant mise en service :**
1. Remplir `WIFI_SSID` et `WIFI_PASSWORD` dans `esp32_iaq_v2.ino`
2. Remplir `SERVER_URL` avec l'URL Render dans `esp32_iaq_v2.ino`
3. Créer un compte Gmail robot et remplir `EMAIL_SENDER`/`EMAIL_PASSWORD` dans `app.py`
4. Calibrer le MQ-7 en air pur avec `mq7_calibration.ino` et reporter la valeur R0
5. Passer `DEBUG = False` dans `app.py` pour la production
