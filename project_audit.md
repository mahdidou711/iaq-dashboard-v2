# 🔬 AUDIT COMPLET DU PROJET IAQ — VERSION FINALE (V2)
> **Date :** 21 Mars 2026 — Post-Roadmap 28 Points  

---

## 📁 Arborescence du Projet

```
iaq_project/
├── .gitignore, .python-version
├── Procfile, README.md (519 lignes), Guide_IAQ.pdf
├── guide-header.tex               (En-tête LaTeX du PDF)
├── app.py                         (639 lignes)
├── requirements.txt               (31 lignes)
├── roadmap_implementation.md       (Plan de route original)
├── project_audit.md               (CE FICHIER)
├── esp32_iaq/
│   ├── esp32_iaq.ino              (V1 — archive, 527 lignes)
│   └── esp32_iaq_v2.ino           (V2 — actif, 487 lignes)
├── mq7_calibration/
│   └── mq7_calibration.ino        (82 lignes)
├── static/js/                     (6 libs JS, 374 Ko)
└── templates/
    ├── index.html                 (819 lignes)
    └── infos.html                 (212 lignes)
```

---

## 📄 AUDIT FICHIER PAR FICHIER

### 1. [app.py](file:///home/mahdidou711/linux_data/Projets/iaq_project/app.py) — Backend Flask (639 lignes)

| Section | Lignes | Description |
|---------|--------|-------------|
| Imports | 1–24 | Flask, smtplib, threading, SocketIO, Compress |
| Configuration | 39–80 | DATABASE, API_KEY, seuils, email, labels |
| Base de données | 92–143 | SQLite : `mesures` + `alertes`, index, nettoyage |
| Validation | 146–168 | Plages capteurs (CO2 ∈ [0, 10000], etc.) |
| Email Asynchrone | 171–195 | Gmail via threading (non-bloquant) |
| Alertes | 198–248 | Détection warn/alert + email |
| Routes API | 251–360 | POST /api/mesures, GET /api/data |
| Stats | 405–453 | AVG/MIN/MAX par capteur |
| Export CSV | 495–532 | Téléchargement données brutes |
| Démarrage | 624–640 | init_db(), scheduler, gunicorn-ready |

- ✅ Rate Limiter, Clé API, Compression Gzip, WebSocket, Nettoyage auto BDD
- ✅ `DATABASE = os.environ.get("DB_PATH", "iaq.db")` → Render Disk
- ⚠️ `EMAIL_SENDER` / `EMAIL_PASSWORD` → Placeholders à remplir
- ⚠️ `DEBUG = True` → Passer à `False` en production

---

### 2. [esp32_iaq_v2.ino](file:///home/mahdidou711/linux_data/Projets/iaq_project/esp32_iaq/esp32_iaq_v2.ino) — Firmware C++ (487 lignes)

| Section | Lignes | Description |
|---------|--------|-------------|
| Bibliothèques | 15–25 | WiFi, HTTP, JSON, CCS811, ADS1115, DHT, LittleFS, OTA |
| setup() | 80–136 | Init LittleFS, Watchdog, I2C, OTA, MH-Z19, ADS1115 |
| loop() | 142–197 | Watchdog, OTA, WiFi, lecture capteurs + envoi |
| lireCO2() | 211–237 | Protocole UART MH-Z19 avec checksum |
| lireCO() | 246–272 | ADS1115 → Pont diviseur R1=2.7k/R2=4.7k → MQ-7 |
| envoyerMesures() | 291–309 | NTP timestamp + LittleFS fallback |
| envoyerBuffer() | 379–425 | Batch POST depuis LittleFS |
| traiterAlertes() | 428–453 | Buzzer 3 bips + LED |
| gererWiFi() | 459–468 | Reconnexion non-bloquante 30s |

- ✅ Watchdog 15s, LittleFS, OTA, MH-Z19 NDIR, ADS1115 16-bit, NTP
- ⚠️ `WIFI_SSID` / `SERVER_URL` / `MQ7_R0` → À configurer

---

### 3. [mq7_calibration.ino](file:///home/mahdidou711/linux_data/Projets/iaq_project/mq7_calibration/mq7_calibration.ino) — Calibration (82 lignes)

60 lectures → Rs moyen → R0 = Rs/26.0 → Affichage série
- ✅ Utilise ADS1115 + pont diviseur R1=2.7k/R2=4.7k

---

### 4. [index.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/index.html) — Dashboard (819 lignes)

- Thème Dark/Light, 5 graphiques Chart.js, seuils visuels
- Zoom tactile, WebSocket temps réel, export CSV, filtres dates
- ✅ Aucune dépendance CDN, tout en local

---

### 5. [infos.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/infos.html) — Informations Capteurs (212 lignes)

Page documentaire statique référençant chaque composant matériel (MQ-7, CCS811, DHT22, MH-Z19, ADS1115), leurs plages, et les principes de mesure.

---

### 6. Fichiers de Configuration

| Fichier | Contenu | Verdict |
|---------|---------|---------|
| `Procfile` | `gunicorn -k eventlet -w 1 app:app` | ✅ |
| `.python-version` | `3.11.9` | ✅ |
| `requirements.txt` | 31 dépendances épinglées + gunicorn | ✅ |
| `.gitignore` | Exclut `*.db`, `venv/`, `__pycache__/` | ✅ |

---

## 🔒 Sécurité

| Élément | Statut |
|---------|--------|
| Clé API | ⚠️ À complexifier |
| Email Password | ⚠️ Placeholder |
| WiFi Credentials | ⚠️ Placeholder |
| Rate Limiting | ✅ 30/min POST |
| Validation données | ✅ Plages vérifiées |
| Debug mode | ⚠️ À désactiver |

---

## 📊 Roadmap 28 Points

| # | Étape | ✓ |
|---|-------|---|
| 1–9 | Corrections, Watchdog, I2C, Seuils, Validation, API, Rate Limit, Cleanup, Alertes | ✅ |
| 10–16 | Dashboard, Annotations, Status, Stats, CSV, Infos, WebSocket | ✅ |
| 17–23 | NTP, LittleFS, Constantes, Calibration, Logs HTTP, WiFi, ADS1115 | ✅ |
| 24 | Deep Sleep | ⏭️ |
| 25–28 | OTA, Unification, Email Gmail, Render Cloud | ✅ |

---

## ✅ Verdict Final

**Projet complet et prêt pour la production.**

- **2 172 lignes** de code source (639 + 487 + 82 + 819 + 212 + 31 + 1 + 1 = hors doc)
- **5 capteurs** : MH-Z19, CCS811, MQ-7, DHT22×2
- **27/28 étapes** validées
- **21 fichiers** au total dans le projet

> [!IMPORTANT]
> **Actions avant mise en service :**
> 1. Remplir `WIFI_SSID` + `WIFI_PASSWORD` dans le `.ino`
> 2. Remplir `SERVER_URL` avec l'URL Render
> 3. Créer un compte Gmail robot → remplir `EMAIL_SENDER`/`EMAIL_PASSWORD`
> 4. Calibrer MQ-7 en air pur → reporter R0
> 5. Passer `DEBUG = False` dans `app.py`
