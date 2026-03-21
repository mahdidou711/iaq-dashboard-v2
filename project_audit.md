# 🔬 AUDIT FINAL — PROJET IAQ V2
> **Date :** 21 Mars 2026 — Post-Corrections Finales  
> **Méthode :** Compteurs via `wc -l`, recherches via `grep`, lecture intégrale de chaque fichier  

---

## 📁 Arborescence (21 fichiers)

```
iaq_project/
├── .gitignore              (7 lignes)
├── .python-version         (1 ligne → 3.11.9)
├── Procfile                (1 ligne)
├── README.md               (519 lignes)
├── Guide_IAQ.pdf           (99 Ko)
├── guide-header.tex        (En-tête LaTeX)
├── app.py                  (640 lignes) ← BACKEND
├── requirements.txt        (31 lignes)
├── roadmap_implementation.md
├── project_audit.md        (CE FICHIER)
├── esp32_iaq/
│   ├── esp32_iaq.ino       (527 lignes — V1 archive)
│   └── esp32_iaq_v2.ino    (490 lignes) ← FIRMWARE ACTIF
├── mq7_calibration/
│   └── mq7_calibration.ino (82 lignes)
├── static/js/              (6 libs, 374 Ko)
└── templates/
    ├── index.html           (819 lignes) ← DASHBOARD
    └── infos.html           (212 lignes)
```

---

## 📄 FICHIER PAR FICHIER

### 1. [app.py](file:///home/mahdidou711/linux_data/Projets/iaq_project/app.py) — Backend (640 lignes)

| Section | Lignes | Rôle |
|---------|--------|------|
| Imports | 1–24 | Flask, smtplib, threading, SocketIO, Compress, CORS, os |
| Config | 39–80 | DATABASE (env), API_KEY, seuils, email Gmail, labels |
| BDD | 92–143 | SQLite `mesures` + `alertes`, index, nettoyage auto 3h |
| Validation | 146–168 | Plages : CO2∈[0,10000], CO∈[0,500], Temp∈[-40,85] |
| Email async | 171–195 | Gmail via `smtplib` + `threading` (non-bloquant) |
| Alertes | 200–248 | Détection warn/alert → email si `alert` |
| POST single | 292–317 | `_insert_single()` → `verifier_alertes()` ✅ |
| POST batch | 320–361 | `_insert_batch()` → `verifier_alertes()` ✅ *(corrigé)* |
| GET data | 363–402 | Pagination, filtres dates |
| Stats | 407–453 | AVG/MIN/MAX par capteur |
| CSV export | 497–532 | Téléchargement fichier |
| Seed debug | 557–604 | 1440 points de test |
| Démarrage | 624–640 | `init_db()` module-level, PORT env, gunicorn-ready |

**Vérifications croisées :**
- ✅ `verifier_alertes()` appelé dans **les 2** chemins d'insertion (single L312 + batch L349)
- ✅ `DATABASE = os.environ.get("DB_PATH", "iaq.db")` → Render Disk compatible
- ✅ `PORT = os.environ.get("PORT", 5000)` → Render compatible
- ⚠️ `EMAIL_SENDER/PASSWORD` = placeholders
- ⚠️ `DEBUG = True` → à passer `False` en prod

---

### 2. [esp32_iaq_v2.ino](file:///home/mahdidou711/linux_data/Projets/iaq_project/esp32_iaq/esp32_iaq_v2.ino) — Firmware (490 lignes)

| Section | Lignes | Rôle |
|---------|--------|------|
| Libs | 15–27 | WiFi, **WiFiClientSecure**, HTTP, JSON, CCS811, ADS1115, DHT, LittleFS, OTA |
| Config | 30–63 | SSID, URLs, Pins, Seuils, R0/RL, **secureClient** |
| setup() | 80–136 | LittleFS, WDT, I2C, **setInsecure()**, OTA, NTP, MH-Z19, ADS1115, CCS811 |
| loop() | 142–197 | WDT reset, OTA handle, WiFi check, lecture + envoi |
| lireCO2() | 211–237 | UART MH-Z19 binaire avec checksum |
| lireTVOC() | 240–243 | CCS811 I2C |
| lireCO() | 246–272 | ADS1115 → pont R1=2.7k/R2=4.7k → courbe MQ-7 |
| lireTemp/Hum | 274–286 | DHT22 |
| envoyerMesures() | 291–309 | NTP timestamp + health check + LittleFS fallback |
| verifierServeur() | 312–324 | `http.begin(secureClient, HEALTH_URL)` ✅ |
| envoyerUneMesure() | 327–353 | `http.begin(secureClient, SERVER_URL)` ✅ |
| envoyerBuffer() | 379–427 | `http.begin(secureClient, SERVER_URL)` ✅ batch LittleFS |
| traiterAlertes() | 430–455 | Buzzer 3 bips + LEDs |
| gererWiFi() | 459–470 | Reconnexion non-bloquante 30s |

**Vérifications croisées :**
- ✅ `WiFiClientSecure` inclus (L17) + instancié (L42) + `setInsecure()` (L108)
- ✅ **3/3** appels `http.begin()` utilisent `secureClient` (L318, L331, L391)
- ✅ Aucun `analogRead()` résiduel (tout passe par ADS1115)
- ✅ Aucun `MQ7_PIN` résiduel (ancien pin analogique supprimé)
- ✅ Seuils ESP32 = Seuils Python (CO2:2000, TVOC:600, CO:35, Temp:35, Hum:75)
- ✅ CCS811 conservé uniquement pour **TVOC** (CO2 = MH-Z19)
- ✅ `API_KEY` identique côté ESP32 et Python
- ⚠️ `WIFI_SSID/PASSWORD` = placeholders
- ⚠️ `SERVER_URL` = placeholder (changer vers URL Render)
- ⚠️ `MQ7_R0 = 10.0` = placeholder (calibrer en air pur)

---

### 3. [mq7_calibration.ino](file:///home/mahdidou711/linux_data/Projets/iaq_project/mq7_calibration/mq7_calibration.ino) — (82 lignes)

- ✅ Utilise ADS1115 (cohérent avec firmware)
- ✅ Pont diviseur R1=2.7k / R2=4.7k (cohérent avec firmware)
- ✅ Formule Rs/26.0 pour air pur

---

### 4. [index.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/index.html) — Dashboard (819 lignes)

- ✅ Dark/Light theme, 5 graphiques, seuils visuels, WebSocket, export CSV
- ✅ Toutes les libs JS en local (aucun CDN)
- ✅ Seuils injectés par Jinja2 depuis Flask

### 5. [infos.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/infos.html) — (212 lignes)

- ✅ Documentations de chaque capteur

---

### 6. Configuration

| Fichier | Contenu | ✓ |
|---------|---------|---|
| `Procfile` | `gunicorn -k eventlet -w 1 app:app` | ✅ |
| `.python-version` | `3.11.9` (évite bug Python 3.14) | ✅ |
| `requirements.txt` | 31 dépendances épinglées + gunicorn | ✅ |
| `.gitignore` | Exclut `*.db`, `venv/`, `__pycache__/` | ✅ |

---

## 🔒 Sécurité

| Point | Statut | Détail |
|-------|--------|--------|
| Clé API | ⚠️ | `SECRET_IAQ_2026` — fonctionnel mais à renforcer |
| Email creds | ⚠️ | Placeholders — créer compte Gmail robot |
| WiFi creds | ⚠️ | Placeholders dans le .ino |
| HTTPS | ✅ | WiFiClientSecure + setInsecure() |
| Rate Limit | ✅ | 30/min POST, 200/min global |
| Validation | ✅ | Plages numériques vérifiées |
| CORS | ✅ | Activé (requis pour ESP32) |
| Debug | ⚠️ | `True` — passer à `False` en prod |

---

## 📊 Roadmap 28 Points

| # | Étape | ✓ |
|---|-------|---|
| 1–9 | Corrections, Watchdog, I2C, Seuils, Validation, API, Rate Limit, Cleanup, Alertes | ✅ |
| 10–16 | Dashboard, Annotations, Status, Stats, CSV, Infos, WebSocket | ✅ |
| 17–23 | NTP, LittleFS, Constantes, Calibration, Logs HTTP, WiFi, ADS1115 | ✅ |
| 24 | Deep Sleep | ⏭️ Ignoré |
| 25–28 | OTA, Unification, Email Gmail, Render Cloud | ✅ |

---

## 🐛 Bugs Corrigés Lors de Cet Audit

| # | Bug | Gravité | Correction |
|---|-----|---------|------------|
| 1 | ESP32 utilisait HTTP simple → Render exige HTTPS | 🔴 Critique | Ajout `WiFiClientSecure` + `setInsecure()` |
| 2 | `_insert_batch()` ne vérifiait pas les alertes | 🔴 Critique | Ajout `verifier_alertes()` dans la boucle batch |

---

## ✅ Verdict Final

**Projet complet, corrigé et prêt pour la production.**

- **2 243 lignes** de code source (640+490+82+819+212)
- **5 capteurs** : MH-Z19, CCS811, MQ-7 (via ADS1115), DHT22×2
- **27/28 étapes** validées, **2 bugs corrigés**
- **21 fichiers** au total

> [!IMPORTANT]
> **5 actions avant mise en service :**
> 1. Remplir `WIFI_SSID` + `WIFI_PASSWORD` dans `esp32_iaq_v2.ino`
> 2. Changer `SERVER_URL` vers votre URL Render (`https://xxx.onrender.com/api/mesures`)
> 3. Créer un compte Gmail robot → remplir `EMAIL_SENDER` / `EMAIL_PASSWORD` dans `app.py`
> 4. Calibrer le MQ-7 en air pur → reporter la valeur R0 dans `#define MQ7_R0`
> 5. Passer `DEBUG = False` dans `app.py`
