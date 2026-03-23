# CLAUDE.md — Projet IAQ (Indoor Air Quality)

## Contexte projet

Tableau de bord de qualité de l'air intérieur. Destiné à la copine du développeur
(niveau technique bas) : le code doit rester simple, lisible, et commenté en français
pédagogique. Pas d'over-engineering.

Architecture complète :
```
ESP32-S3 → HTTPS POST JSON → Flask/app.py → SQLite (iaq.db)
                                    ↓
Navigateur ← HTML/Chart.js/Socket.IO ← /  (dashboard)
Gmail ← email automatique si seuil critique
```

---

## Fichiers du projet

| Fichier | Rôle |
|---------|------|
| `app.py` | Serveur Flask — backend complet |
| `templates/index.html` | Dashboard 3 onglets (Chart.js + Socket.IO) |
| `templates/infos.html` | Page éducative capteurs et seuils |
| `esp32_iaq/esp32_iaq_v2.ino` | Firmware ESP32 actif (V2) |
| `esp32_iaq/mq7_calibration.ino` | Script calibration R0 MQ-7 |
| `requirements.txt` | Dépendances Python épinglées |
| `Procfile` | Démarrage gunicorn pour Render |
| `.python-version` | Force Python 3.11.9 sur Render |

**Fichiers exclus du repo (`.gitignore`) :**
- `Guide_IAQ.pdf`, `guide-header.tex` → documentation locale uniquement
- `project_audit.md`, `roadmap_implementation.md` → notes internes
- `iaq.db`, `venv/`, `__pycache__/`

---

## Hardware — ESP32-S3-WROOM-1

### Pins GPIO (vérifiés sur schéma électrique)

| GPIO | Rôle | Capteur/Composant |
|------|------|-------------------|
| GPIO 4 | DHT_PIN | DHT22 (temp/humidité) |
| GPIO 8 | SDA (Wire) | CCS811 — bus I2C principal |
| GPIO 9 | SCL (Wire) | CCS811 — bus I2C principal |
| GPIO 1 | SCL2 (Wire1) | ADS1115 — bus I2C secondaire |
| GPIO 2 | SDA2 (Wire1) | ADS1115 — bus I2C secondaire |
| GPIO 15 | BUZZER_PIN | Buzzer via transistor 2N2222 |
| GPIO 17 | TX_CO2 | MH-Z19 UART |
| GPIO 18 | RX_CO2 | MH-Z19 UART |
| GPIO 38 | VENTILATOR_PIN | Ventilateur via MOSFET IRLZ44N |
| GPIO -1 | LED_OK_PIN | Désactivé (GPIO25 non exposé sur WROOM-1) |
| GPIO -1 | LED_ALERT_PIN | Désactivé (GPIO26 réservé flash SPI) |

### I2C — DEUX BUS SÉPARÉS (CRITIQUE)
- **Wire** (`Wire.begin(8, 9)`) → CCS811 uniquement
- **Wire1** (`Wire1.begin(2, 1)`) → ADS1115 uniquement (`ads.begin(0x48, &Wire1)`)
- Ne jamais mettre les deux capteurs sur le même bus

### Pont diviseur MQ-7 (CRITIQUE)
- R1 = 10 kΩ, R2 = 20 kΩ
- Ratio d'inversion : `(10 + 20) / 20 = 1.5`
- Formule correcte dans `calcRs()` :
  ```cpp
  float v_real = vout * 1.5f;
  return RL * (VCC - v_real) / v_real;
  ```
- `vout` = tension mesurée par ADS1115 (après diviseur)
- Ne jamais utiliser `vout` directement dans la formule sans inversion

### Seuils avec hystérésis (ON/OFF séparés)
- CO2 : ON=2000 / OFF=1800 ppm
- TVOC : ON=600 / OFF=450 ppb
- CO : ON=35 / OFF=25 ppm
- Température : ON=35 / OFF=32 °C
- Humidité : ON=75 / OFF=65 %

---

## Backend — app.py

### Déploiement
- **Local** : `python app.py` → `http://127.0.0.1:5000`
- **Cloud Render** : `gunicorn -k eventlet -w 1 app:app` (Procfile)
- Variable `DB_PATH` pour un disque Render persistant (`/data/iaq.db`)

### Routes API

| Méthode | Route | Auth | Description |
|---------|-------|------|-------------|
| GET | `/` | — | Dashboard HTML |
| GET | `/infos` | — | Page éducative |
| GET | `/api/health` | — | `{"statut":"ok"}` |
| POST | `/api/mesures` | API Key | Reçoit données ESP32 (single ou batch) |
| GET | `/api/data` | — | Données paginées |
| GET | `/api/stats` | — | Min/moy/max par capteur |
| GET | `/api/alertes` | — | Historique alertes |
| GET | `/api/export` | — | Téléchargement CSV |
| POST | `/api/clear` | API Key | Supprime tout |
| POST | `/api/seed` | API Key | 1440 mesures test (DEBUG uniquement) |

### Configuration (haut de app.py)
```python
API_KEY = "SECRET_IAQ_2026"
DEBUG = False           # True active /api/seed
DATA_RETENTION_DAYS = 30
SENSOR_OFFLINE_MINUTES = 5
EMAIL_ALERTS_ENABLED = True
```

### Base de données SQLite
- Table `mesures` : id, timestamp, co2, tvoc, co, temperature, humidite
- Table `alertes` : id, timestamp, capteur, niveau (warn/alert), valeur, seuil, message
- Index sur `timestamp` dans les deux tables

---

## Frontend — index.html

- **Chart.js** + chartjs-plugin-annotation + chartjs-plugin-zoom + hammerjs
- **Socket.IO** pour les mises à jour temps réel
- Tous les JS servis depuis `static/js/` (zéro CDN externe)
- Thème sombre/clair (localStorage)
- Badge alertes (dernières 24h)
- Filtrage par date sur tous les onglets
- Responsive mobile (<700px)

---

## Règles de travail

### Commits
- Commits en anglais, sans mention de Claude ni Co-Authored-By
- Pousser uniquement sur `https://github.com/mahdidou711/iaq-dashboard-v2`

### Style de code
- Commentaires en **français**, pédagogiques, style `// Explication claire pour débutant`
- app.py : style de commentaire lourd comme dans les .ino (sections `# ─── TITRE ───`)
- .ino : commentaires en ligne qui expliquent le "pourquoi", pas juste le "quoi"

### Ce qu'il ne faut PAS faire
- Ne pas ajouter de dépendances sans demande explicite
- Ne pas réintroduire : CORS inutile, rate limiting local, device_id, PWA
- Ne pas utiliser GPIO25 ou GPIO26 pour les LEDs (hardware incompatible)
- Ne pas mettre ADS1115 sur Wire (bus principal) — il doit rester sur Wire1
- Ne pas calculer Rs du MQ-7 sans inverser le pont diviseur (facteur 1.5)
- Ne pas commiter Guide_IAQ.pdf, guide-header.tex, project_audit.md (dans .gitignore)

### Mise à jour documentation
Après toute modification significative, mettre à jour dans l'ordre :
1. `README.md` (retirer les fichiers non poussés des tableaux)
2. `Guide_IAQ.pdf` via pandoc + `guide-header.tex` (local uniquement)

---

## Lancer le projet en local (test)

```bash
cd /home/mahdidou711/linux_data/Projets/iaq_project
source venv/bin/activate
python app.py
# Puis http://localhost:5000
# Bouton "Données test" pour peupler le dashboard
```

Pour régénérer le PDF :
```bash
pandoc README.md -o Guide_IAQ.pdf --pdf-engine=xelatex -H guide-header.tex
```
