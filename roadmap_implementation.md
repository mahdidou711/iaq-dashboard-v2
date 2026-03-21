# Guide d'Implémentation de la Roadmap (28 améliorations)

Ce document détaille comment implémenter chacune des 28 améliorations issues de
l'audit final du projet IAQ.

---

## Phase 1 : Corrections Critiques (Sécurité et Fiabilité)

### 1. Authentification API (Clé API)

**Serveur ([app.py](file:///home/mahdidou711/linux_data/Projets/iaq_project/app.py)) :**
```python
API_KEY = "votre_cle_super_secrete_123!"

@app.route('/api/mesures', methods=['POST'])
def receive_data():
    if request.headers.get('X-API-KEY') != API_KEY:
        return jsonify({"erreur": "Non autorisé"}), 401
    # Suite du traitement...
```

**ESP32 ([esp32_iaq_v2.ino](file:///home/mahdidou711/linux_data/Projets/iaq_project/esp32_iaq/esp32_iaq_v2.ino)) :**
```cpp
http.addHeader("X-API-KEY", "votre_cle_super_secrete_123!");
```

### 2. Rate Limiting

`pip install flask-limiter`
```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day"])

@app.route('/api/mesures', methods=['POST'])
@limiter.limit("10 per minute")  # 10 envois maxi par minute par IP
def recevoir_mesures():
    # ...
```

### 3. CORS

`pip install flask-cors`
```python
from flask_cors import CORS
app = Flask(__name__)
CORS(app)  # Autorise toutes les origines (ou CORS(app, origins=["http://monsite.com"]))
```

### 4. Correction du schéma EasyEDA

- **Diviseur MQ-7** : R1=2.2 kΩ entre A0 et GPIO 34, R2=3.3 kΩ entre GPIO 34 et GND.
- **Pull-ups I2C** : 4.7 kΩ entre SDA et 3.3V, 4.7 kΩ entre SCL et 3.3V.

### 5. Nettoyage périodique BDD (APScheduler)

`pip install APScheduler`
```python
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import sqlite3

def clean_old_data():
    limite = datetime.now() - timedelta(days=30)
    conn = sqlite3.connect('iaq.db')
    conn.execute("DELETE FROM mesures WHERE timestamp < ?",
                 (limite.strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit(); conn.close()

scheduler = BackgroundScheduler()
scheduler.add_job(clean_old_data, 'cron', hour=3, minute=0)
scheduler.start()
```

### 6. Fichiers de dépôt

`.gitignore` :
```text
venv/
__pycache__/
*.pyc
iaq.db
.env
```
Ajouter un fichier `LICENSE` (MIT, Apache 2.0, etc.).

### 7. Version pip fixe

```bash
pip freeze > requirements.txt
```
Cela remplacera `flask>=3.0,<4.0` par ex: `Flask==3.1.1`, `Werkzeug==3.1.3`, etc.

---

## Phase 2 : Optimisation du Dashboard (UX)

### 8. Thème page Infos

Dans [infos.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/infos.html), ajouter le même bouton et le même JS de bascule :
```html
<button id="btn-theme" onclick="
  let t = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', t);
  localStorage.setItem('iaq-theme', t);
  this.innerHTML = t === 'dark' ? '☀' : '☾';
">☀</button>
```
Et dupliquer les variables CSS `[data-theme="light"]` depuis [index.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/index.html).

### 9. Sources scientifiques

Ajouter dans chaque carte de [infos.html](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/infos.html) un lien vers la source :
```html
<div class="tip">Source : <a href="https://www.who.int/...">OMS</a>,
<a href="https://www.anses.fr/...">ANSES</a></div>
```

### 10. WebSockets (Flask-SocketIO)

`pip install Flask-SocketIO eventlet`

**Serveur :**
```python
from flask_socketio import SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")

# Dans la route /api/mesures, après insertion :
socketio.emit('nouvelle_mesure', data)

if __name__ == '__main__':
    socketio.run(app)  # Remplace app.run()
```

**Client (index.html) :**
```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
<script>
const socket = io();
socket.on('nouvelle_mesure', data => { /* maj graphiques */ });
</script>
```

### 11. Support Hors-Ligne total

1. Créer `static/js/`.
2. Télécharger `chart.umd.min.js`, `hammer.min.js`, `chartjs-plugin-zoom.min.js`, etc.
3. Remplacer les `<script src="https://cdn...">` par :
```html
<script src="{{ url_for('static', filename='js/chart.umd.min.js') }}"></script>
```

### 12. Décimation Chart.js

Dans [buildChartConfig()](file:///home/mahdidou711/linux_data/Projets/iaq_project/templates/index.html#413-462), activer le plugin natif :
```javascript
plugins: {
  decimation: {
    enabled: true,
    algorithm: 'lttb',    // Largest Triangle Three Buckets
    samples: 500           // Max 500 points affichés
  }
}
```
Note : nécessite `parsing: false` et `indexAxis: 'x'` dans certaines configs.

### 13. Graphiques long terme

Ajouter une route API serveur :
```python
@app.route("/api/stats/weekly")
def stats_weekly():
    db = get_db()
    rows = db.execute("""
        SELECT strftime('%Y-%W', timestamp) as semaine,
               AVG(co2) as co2, AVG(tvoc) as tvoc, AVG(co) as co,
               AVG(temperature) as temperature, AVG(humidite) as humidite
        FROM mesures GROUP BY semaine ORDER BY semaine
    """).fetchall()
    return jsonify([dict(r) for r in rows])
```
Puis ajouter un onglet Chart.js de type `bar` pour les moyennes hebdomadaires.

### 14. Notifications navigateur

```javascript
if ('Notification' in window && Notification.permission !== 'denied') {
  Notification.requestPermission();
}

// Dans updateStatus(), si alerte détectée :
if (worstCls === 'st-alert' && Notification.permission === 'granted') {
  new Notification('🚨 IAQ Alerte', { body: 'Seuil critique dépassé !' });
}
```

### 15. Pagination API

Modifier `/api/data` :
```python
@app.route("/api/data")
def lire_donnees():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 100, type=int)
    offset = (page - 1) * per_page
    # ...
    query += " LIMIT ? OFFSET ?"
    params.extend([per_page, offset])
```

### 16. Compression gzip

`pip install flask-compress`
```python
from flask_compress import Compress
Compress(app)  # Compresse automatiquement les réponses > 500 octets
```

---

## Phase 3 : Robustesse du Firmware (ESP32)

### 17. Horloge NTP

```cpp
#include <time.h>
void setup() {
  configTime(3600, 3600, "pool.ntp.org"); // GMT+1, heure d'été
}
// Dans loop(), avant envoi :
struct tm timeinfo;
if (getLocalTime(&timeinfo)) {
  char ts[20];
  strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", &timeinfo);
  // Utiliser 'ts' dans le JSON
}
```

### 18. Stockage persistant (LittleFS)

```cpp
#include <LittleFS.h>
void setup() { LittleFS.begin(true); }

void sauverBuffer() {
  File f = LittleFS.open("/buffer.json", "w");
  // Sérialiser bufferCount mesures en JSON dans le fichier
  f.close();
}
```

### 19. Constantes nommées pour les seuils ventilateur

Remplacer la ligne 140 :
```cpp
// Avant :
if (co2 > 2000 || tvoc > 600 || co > 35 || temp > 35 || hum > 75)
// Après :
#define SEUIL_VENTILO_CO2   2000
#define SEUIL_VENTILO_TVOC  600
#define SEUIL_VENTILO_CO    35
#define SEUIL_VENTILO_TEMP  35
#define SEUIL_VENTILO_HUM   75
if (co2 > SEUIL_VENTILO_CO2 || tvoc > SEUIL_VENTILO_TVOC || ...)
```

### 20. Calibration MQ-7

```cpp
// Procédure en air pur (extérieur) pendant 10 minutes :
float calibrateMQ7() {
  float somme = 0;
  for (int i = 0; i < 50; i++) {
    int raw = analogRead(MQ7_PIN);
    float v = raw * (3.3 / 4095.0);
    somme += MQ7_RL * (3.3 - v) / v;
    delay(200);
  }
  return somme / 50.0;  // C'est votre R0 réel
}
```

### 21. Log des erreurs HTTP

```cpp
int code = http.POST(body);
if (code == 201) {
  traiterAlertes(http.getString());
} else {
  Serial.printf("[HTTP] Erreur POST : code %d\n", code);
}
```

### 22. Reconnexion WiFi active

```cpp
unsigned long dernierCheckWifi = 0;
void loop() {
  if (millis() - dernierCheckWifi > 30000) {  // Toutes les 30s
    dernierCheckWifi = millis();
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("[WiFi] Reconnexion...");
      WiFi.disconnect(); WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    }
  }
}
```

### 23. ADC externe ADS1115

`Installer : Adafruit ADS1X15` (IDE Arduino > Bibliothèques)
```cpp
#include <Adafruit_ADS1X15.h>
Adafruit_ADS1115 ads;
void setup() { ads.begin(); }
float lireCO() {
  int16_t adc0 = ads.readADC_SingleEnded(0);
  float voltage = ads.computeVolts(adc0);
  // Remplace analogRead(34)
}
```

### 24. Deep Sleep

```cpp
#define uS_TO_S_FACTOR 1000000ULL
#define TIME_TO_SLEEP  10  // Secondes

void loop() {
  // ... lire capteurs, envoyer, puis :
  esp_sleep_enable_timer_wakeup(TIME_TO_SLEEP * uS_TO_S_FACTOR);
  esp_deep_sleep_start();  // L'ESP32 s'éteint et se rallume dans 10s
}
```

### 25. Mise à jour OTA

```cpp
#include <ArduinoOTA.h>
void setup() {
  ArduinoOTA.setHostname("esp32-iaq");
  ArduinoOTA.begin();
}
void loop() {
  ArduinoOTA.handle();  // Écoute les mises à jour WiFi
  // ... reste du code
}
```
Puis dans Arduino IDE : Outils > Port > sélectionner l'ESP32 en réseau.

### 26. Unification des seuils

Modifier `/api/health` côté serveur :
```python
@app.route("/api/health")
def health():
    return jsonify({"statut": "ok", "thresholds": THRESHOLDS})
```
Côté ESP32, parser la réponse de `/api/health` pour récupérer les seuils dynamiquement.

---

## Phase 4 : Fonctionnalités Avancées

### 27. Notifications Telegram

1. Créer un bot via **BotFather** sur Telegram → récupérer le Token.
2. Trouver votre `chat_id` via `https://api.telegram.org/bot<TOKEN>/getUpdates`.

```python
import requests
def send_telegram(message):
    token = "VOTRE_TOKEN"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": "VOTRE_CHAT_ID", "text": message})

# Après vérification des alertes :
if data['co'] > 35:
    send_telegram(f"🚨 CO dangereux: {data['co']} ppm — Évacuer !")
```

### 28. Hébergement Cloud

1. Ajouter `gunicorn` dans [requirements.txt](file:///home/mahdidou711/linux_data/Projets/iaq_project/requirements.txt).
2. Déployer sur Render.com (Start Command: `gunicorn app:app`).
3. Migrer SQLite vers PostgreSQL (gratuit sur Render).
4. Modifier l'URL dans l'ESP32 :
```cpp
#include <WiFiClientSecure.h>
WiFiClientSecure client;
client.setInsecure();
http.begin(client, "https://mon-app.onrender.com/api/mesures");
```
