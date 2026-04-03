# IAQ V2 -- Indoor Air Quality Dashboard

Complete step by step guide. This document is intended for anyone who wants
to deploy and operate this air quality monitoring system,
even without prior experience in programming or electronics.

---

## 1. Project Overview

This project measures five air parameters in real time:

| Parameter   | Sensor    | Unit | Activation Threshold | Deactivation Threshold |
|:------------|:----------|:-----|:---------------------|:-----------------------|
| CO2         | MH-Z19    | ppm  | 2000                 | 1800                   |
| TVOC        | CCS811    | ppb  | 220                  | 150                    |
| CO          | MQ-7 (via ADS1115) | ppm | 25            | 18                     |
| Temperature | DHT22     | C    | 27                   | 25                     |
| Humidity    | DHT22     | %    | 60                   | 55                     |

Note: these thresholds are those used by the fusion firmware, which is more sensitive for home use.
The `app.py` backend has its own thresholds (`warn`/`alert`) for emails.

The architecture is as follows:

1. **The ESP32-S3** (microcontroller) reads the sensors every 2 seconds
   and sends the data every 5 seconds.
2. It sends data via **HTTPS POST** (JSON) to **two Render servers** simultaneously:
   - `https://iaq-maison.onrender.com` — web dashboard (Mahdi)
   - `https://iaq-backend.onrender.com` — Android application ()
3. **The Flask server** (`app.py`) validates the data, stores it in SQLite, detects alerts,
   and sends a **Gmail email** if a critical threshold is exceeded.
4. **The web dashboard** (`index.html`) displays real time charts
   via Chart.js and WebSocket (Socket.IO).
5. **The educational page** (`infos.html`) explains each sensor and its health thresholds.
6. If WiFi fails, the data is **saved to internal flash memory** (LittleFS)
   and automatically sent to both servers when the connection returns.
7. **Automatic MQ-7 calibration**: `R0` is dynamically calculated during the first 60
   seconds after startup, so manual calibration is no longer needed.

---

## 2. Folder Contents

| File / Folder | Lines | Role |
|:--------------|:------|:-----|
| `app.py` | 824 | Python Flask web server (Mahdi backend). |
| `templates/index.html` | 819 | Web dashboard interface (frontend). |
| `templates/infos.html` | 212 | Educational page about sensors and health thresholds. |
| `esp32_iaq/esp32_iaq_fusion.ino` | 549 | **Active firmware** — fusion V2 +  (dual backend). |
| `esp32_iaq/esp32_iaq_v2.ino` | 547 | Original Mahdi V2 firmware (archive). |
| `esp32_iaq/esp32_iaq_.ino` | 570 | Original  firmware (archive / reference). |
| `esp32_iaq/mq7_calibration.ino` | 82 | MQ-7 calibration script via ADS1115. |
| `requirements.txt` | 31 | Pinned Python dependencies (Flask, gunicorn...). |
| `Procfile` | 1 | Startup command for Render Cloud. |
| `.python-version` | 1 | Forces Python 3.11.9 on Render. |
| `README.md` | - | This file. |

---

## 3. Server Installation

### 3.1 Option A: Local Setup (Windows 11)

**Prerequisites:**
- **Python 3.10 or later**: Download from `https://www.python.org/downloads/`.
  During installation, make sure to check the **"Add Python to PATH"** box.

**Step by step startup:**

1. Open File Explorer and navigate to the `iaq_project` folder.
2. Click in the address bar, type `cmd`, then press Enter.
3. Create an isolated environment:

```bash
python -m venv venv
```

4. Activate the environment:

```bash
venv\Scripts\activate
```

5. Install the dependencies:

```bash
pip install -r requirements.txt
```

6. Start the server:

```bash
python app.py
```

7. Open `http://127.0.0.1:5000` in a browser.

IMPORTANT: Do not close the black window. Press Ctrl+C to stop the server.

### 3.2 Option B: Cloud Hosting (Render)

The server is preconfigured for Render (free plan).

1. Push the code to GitHub.
2. Create a **Web Service** on `https://dashboard.render.com`.
3. Connect the GitHub repository.
4. Configure:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn -k eventlet -w 1 app:app`
5. (Optional) Add a Disk (`/data`, 1 GB) and the variable `DB_PATH=/data/iaq.db`
   for a persistent database.
6. Click **Create Web Service**.

The site will be accessible at `https://your-name.onrender.com`.

Note: the `.python-version` file forces Python 3.11.9 to avoid
incompatibilities with Python 3.14.

---

## 4. Full List of Server Functions (`app.py`)

### 4.1 Internal Functions

| Function | Line | Role |
|:---------|:-----|:-----|
| `get_db()` | 92 | Opens an SQLite connection in the Flask context. |
| `close_db()` | 99 | Closes the connection at the end of the request. |
| `init_db()` | 106 | Creates the `mesures` and `alertes` tables with indexes. |
| `cleanup_old_data()` | 136 | Deletes data older than 30 days, scheduled at 3:00 AM. |
| `validate_sensor_value()` | 148 | Checks that a value is numeric and within the allowed range. |
| `validate_measurement()` | 159 | Applies validation to all 5 sensor fields. |
| `send_email_alert_async()` | 173 | Sends a Gmail alert email in the background using threading. |
| `verifier_alertes()` | 200 | Compares each value with the thresholds, inserts into the database, triggers email. |
| `require_api_key()` | 253 | Decorator: blocks requests without the `X-API-KEY` header. |
| `_insert_single()` | 292 | Inserts a single measurement → `verifier_alertes()` → WebSocket. |
| `_insert_batch()` | 320 | Inserts a batch, max 100 → `verifier_alertes()` → WebSocket. |

### 4.2 API Routes

| Method | Address | Auth | Rate Limit | Description |
|:-------|:--------|:-----|:-----------|:------------|
| GET | `/` | — | — | HTML dashboard. |
| GET | `/infos` | — | — | Educational sensor page. |
| GET | `/api/health` | — | — | Health check (`{"statut":"ok"}`). |
| POST | `/api/mesures` | API Key | 30/min | Receives data, single or batch. |
| GET | `/api/data` | — | — | Paginated data, filterable by date. |
| GET | `/api/stats` | — | — | Average, min, max per sensor. |
| GET | `/api/alertes` | — | — | Alert history, filterable. |
| GET | `/api/export` | — | — | CSV download, Excel compatible. |
| POST | `/api/clear` | API Key | — | Deletes all data. |
| POST | `/api/seed` | API Key | — | Generates 1440 test measurements (DEBUG only). |

### 4.3 Configurable Parameters

| Variable | Default Value | Description |
|:---------|:--------------|:------------|
| `DATABASE` | `os.environ.get("DB_PATH", "iaq.db")` | Database path (Render Disk compatible). |
| `API_KEY` | `"SECRET_IAQ_2026"` | Authentication key. |
| `DEBUG` | `False` | Enables `/api/seed` and detailed logs. |
| `DATA_RETENTION_DAYS` | `30` | Retention period in days. |
| `SENSOR_OFFLINE_MINUTES` | `5` | "OFFLINE" delay on the dashboard. |
| `EMAIL_ALERTS_ENABLED` | `True` | Enables or disables alert emails. |
| `EMAIL_SENDER` | To be configured | Sender Gmail address. |
| `EMAIL_PASSWORD` | To be configured | Google app password. |
| `EMAIL_RECEIVER` | To be configured | Recipient address for alerts. |

### 4.4 SQLite Database

**`mesures` table:** id, timestamp, co2, tvoc, co, temperature, humidite

**`alertes` table:** id, timestamp, capteur, niveau (warn/alert), valeur, seuil, message

Both tables have an index on `timestamp`.

---

## 5. Full List of Fusion Firmware Functions (`esp32_iaq_fusion.ino`)

The fusion firmware combines the robustness of V2, namely watchdog, OTA, LittleFS, NTP,
ArduinoJson, with 's improvements, namely dynamic `R0` calibration, correct
CCS811 reading, I2C scan, sensitive thresholds, and a local state machine.

### 5.1 Libraries (12)

| Library | Role |
|:--------|:-----|
| `Arduino.h` | Arduino base (explicitly included). |
| `WiFi.h` | WiFi network connection. |
| `WiFiClientSecure.h` | Encrypted HTTPS connection (Render). |
| `HTTPClient.h` | HTTP POST / GET requests. |
| `ArduinoJson.h` | JSON serialization / deserialization. |
| `Adafruit_CCS811.h` | TVOC sensor via I2C (with `available()` + `readData()`). |
| `Adafruit_ADS1X15.h` | 16-bit ADC converter for MQ-7 via I2C. |
| `DHT.h` | DHT22 temperature / humidity sensor. |
| `esp_task_wdt.h` | Watchdog Timer (15 seconds). |
| `time.h` | NTP clock (UTC+1 Algeria, no DST). |
| `LittleFS.h` | Non-volatile flash storage (offline buffer). |
| `ArduinoOTA.h` | Firmware update over WiFi (OTA). |

### 5.2 Functions

| Function | Role |
|:---------|:-----|
| `scanI2C()` | Scans the I2C buses at startup (diagnostics). |
| `setup()` | Init: LittleFS, WDT, 2x I2C, pins, WiFi, OTA, NTP, sensors. |
| `loop()` | WDT → OTA → WiFi → 2s polling (sensors + alerts) → 5s sending. |
| `mhzChecksum()` | UART checksum for the MH-Z19 protocol. |
| `lireCO2()` | MH-Z19: cmd `0x86` → checksum → CO2 ppm. |
| `lireTVOC()` | CCS811: `available()` → `readData()` → `getTVOC()` (cap 1187 ppb). |
| `lireCO()` | ADS1115 → voltage divider x1.5 → 60s `R0` calibration → MQ-7 curve. |
| `lireTemperature()` | DHT22 → `readTemperature()`. |
| `lireHumidite()` | DHT22 → `readHumidity()`. |
| `envoyerMesures()` | NTP timestamp → health check → buffer or dual server send. |
| `verifierServeur()` | GET `/api/health` (3s timeout). |
| `envoyerUneMesure()` | POST JSON to one server (`analyserReponse` flag). |
| `ajouterAuBuffer()` | Appends JSONL to `/mesures.jsonl` (max 50 KB). |
| `envoyerBuffer()` | Batch POST, max 50 measurements (`supprimerApres` flag). |
| `traiterAlertes()` | Parses server response → LEDs only (no buzzer). |
| `gererWiFi()` | Non-blocking reconnection every 30s. |
| `connecterWiFi()` | Initial connection (40 attempts, max 20s). |

### 5.3 Key Differences Between Fusion and V2

| Aspect | V2 (old) | Fusion (active) |
|:-------|:---------|:----------------|
| Polling / Sending | Combined every 10s | 2s polling, 5s sending (separate) |
| Servers | 1 only (local or Render) | 2 Render servers (Mahdi + ) |
| MQ-7 Calibration | Fixed `R0` (`#define MQ7_R0 10.0`) | Dynamic `R0` for 60s at boot |
| CCS811 Reading | `getTVOC()` directly | `available()` + `readData()` + cap 1187 |
| Thresholds | Original `CLAUDE.md` values |  values (more sensitive) |
| Buzzer | State machine + server beeps | Local state machine only |
| NTP | `configTime(3600, 3600)` (DST bug) | `configTime(3600, 0)` (correct) |
| JSON Sent | co2, tvoc, co, temp, hum | + fan, buzzer, air_state |
| I2C Scan | No | Yes (boot diagnostics) |
| Threshold Validation | Without checking `NAN` / `r0Ready` | Checks sensor validity before comparison |

### 5.4 Built-in Safety Mechanisms

- **Watchdog (15s)**: Restarts the ESP32 if the code gets stuck.
- **I2C Timeout (1s)**: Prevents the I2C bus from blocking indefinitely.
- **HTTPS**: Encrypted communication via `WiFiClientSecure` + `setInsecure()`.
- **API Key**: `X-API-KEY` header on every POST request.
- **LittleFS Buffer (50 KB)**: Non-volatile flash backup if the server is offline.
  Sent to both servers when the connection returns.
- **OTA**: Wireless update (password: `iaqadmin`).
- **NTP**: Autonomous UTC+1 timestamping (Algeria, no DST).
- **Cross-calibration**: `setEnvironmentalData(hum, temp)` improves CCS811 readings.
- **NAN values**: Invalid fields are omitted from the JSON.
- **Sensor validation**: Thresholds are checked only if the sensor is valid and calibrated.
- **TVOC fallback**: Keeps the last valid value if CCS811 misses one cycle.
- **Hysteresis**: Separate ON/OFF thresholds to prevent flickering.
- **Local state machine**: IDLE → BUZZING 2s → FAN_ON → back to IDLE.
  Local control only, `traiterAlertes()` no longer controls the buzzer.
- **LEDs**: Disabled by default (`GPIO 25/26` not usable on WROOM-1).

### 5.5 Parameters to Modify Before Uploading

| Variable | Line | Current Value | What to Set |
|:---------|:-----|:--------------|:------------|
| `WIFI_SSID` | 25 | `"IdoomFibre_AT3P2evDS_EXT"` | The WiFi network name. |
| `WIFI_PASSWORD` | 26 | `"zevwhF9e"` | The WiFi password. |
| `SERVER_URL_1` | 29 | `"https://iaq-maison.onrender.com/api/mesures"` | Mahdi dashboard. |
| `SERVER_URL_2` | 30 | `"https://iaq-backend.onrender.com/api/mesures"` |  backend. |
| `HEALTH_URL_1` | 31 | `"https://iaq-maison.onrender.com/api/health"` | Server 1 health check. |

---

## 6. MQ-7 Calibration

### 6.1 Automatic Calibration (Fusion Firmware)

The fusion firmware automatically calibrates the MQ-7 during the **first 60
seconds** after startup. During this time, the CO sensor displays `"NAN"`
in the Serial Monitor. After 60s, the `R0` value is calculated and
CO measurements begin.

For optimal calibration, start the ESP32 in a **well-ventilated room**
or outdoors. The calculated `R0` value is displayed in the Serial Monitor.

### 6.2 Manual Calibration (`mq7_calibration.ino`)

For more precise calibration (dedicated script):

1. Connect the ESP32 with the ADS1115 and the MQ-7 **outdoors or in a well-ventilated room**.
2. Open `esp32_iaq/mq7_calibration.ino` in Arduino IDE.
3. Upload it and open the Serial Monitor (115200 baud).
4. Wait 60 seconds (60 readings).
5. The **`R0`** value is displayed at the end.

Note: the fusion firmware does not need this step (automatic calibration),
but the script remains useful to verify the `R0` value in clean air.

The script uses the same voltage divider formula (`R1=10k`, `R2=20k`) as
the main firmware.

---

## 7. Full List of Frontend Functions (`index.html`)

### 7.1 Dashboard Features

- **3 tabs**: Real time charts, Statistics, Alert history.
- **5 charts**: CO2, TVOC, CO, Temperature, Humidity with threshold lines.
- **Dark / light theme**: Saved in `localStorage`.
- **Interactive zoom**: Mouse wheel or touch pinch (Hammer.js).
- **Date filtering**: "From" / "To" fields on each tab.
- **CSV export**: Download button.
- **Real time WebSocket**: Instant refresh (Socket.IO).
- **Alert badge**: Red counter on the alerts tab.
- **Live indicator**: `"ONLINE"` (green) or `"OFFLINE"` (pulsing red).
- **Mobile responsive**: Adaptive grid below 700px.
- **0 CDN dependencies**: All JS libraries served locally (`static/js/`).

---

## 8. Educational Page (`infos.html`)

Accessible via the **"Understand the sensors"** link at the top of the dashboard.

- **CO2**: Natural gas produced by breathing. `>2000 ppm` = stale indoor air.
- **TVOC**: Volatile organic compounds (paints, glues). Some are carcinogenic.
- **CO**: Odorless and deadly gas. `>35 ppm` = evacuate. About 300 deaths/year in France.
- **Temperature**: WHO recommends `18-22 C`. `>35 C` = heat stroke risk.
- **Humidity**: Ideal `40-60%`. Too humid = mold and dust mites.

---

## 9. Hardware Wiring

### 9.1 Critical Warnings

**WARNING 1 -- Power Supply**:
Never use a 9V battery. Use a power adapter rated at `>=2A`.

**WARNING 2 -- MQ-7 Voltage**:
The MQ-7 can output up to 5V. The ADS1115 tolerates a maximum of 4.096V at `GAIN_ONE`.
The `R1=10 kOhm / R2=20 kOhm` voltage divider reduces the voltage to a maximum of 3.33V.

**WARNING 3 -- I2C Bus**:
If the wires are longer than 15 cm, add `4.7 kOhm` pull-up resistors.

### 9.2 Power Supply Diagram

```text
Power adapter (9-12V 2A)
        |
   [Diode D1] (reverse polarity protection)
        |
   [LM2596 IN+]---[LM2596 IN-]
        |                |
 Adjust the screw      Common GND
   until 5.00V
        |
   [LM2596 OUT+]---[LM2596 OUT-]
        |                |
     +5V rail          GND rail
```

### 9.3 Complete Connections

**ESP32-S3 (power supply)**

| ESP32 Pin | Connect to |
|:----------|:-----------|
| `5VIN` | LM2596 OUT+ (+5V) |
| `GND` | LM2596 OUT- (ground) |

**MH-Z19 (NDIR CO2 sensor)**

| MH-Z19 Pin | Connect to |
|:-----------|:-----------|
| `VIN` (+5V) | LM2596 OUT+ (+5V) |
| `GND` | Common GND |
| `TX` | ESP32 `GPIO 18` (RX_CO2) |
| `RX` | ESP32 `GPIO 17` (TX_CO2) |

UART communication at 9600 baud.

**ADS1115 (16-bit ADC Converter) -- SECONDARY I2C BUS**

| ADS1115 Pin | Connect to |
|:------------|:-----------|
| `VDD` | ESP32 `3.3V` |
| `GND` | ESP32 `GND` |
| `SDA` | ESP32 `GPIO 2` (SDA2 -- `Wire1` bus) |
| `SCL` | ESP32 `GPIO 1` (SCL2 -- `Wire1` bus) |
| `A0` | Voltage divider output (see MQ-7) |
| `ADDR` | GND (address `0x48`) |

**CCS811 (TVOC) -- PRIMARY I2C BUS -- Power with 3.3V only**

| CCS811 Pin | Connect to |
|:-----------|:-----------|
| `VCC` | ESP32 `3.3V` |
| `GND` | ESP32 `GND` |
| `SDA` | ESP32 `GPIO 8` (SDA -- `Wire` bus, ESP32-S3 default) |
| `SCL` | ESP32 `GPIO 9` (SCL -- `Wire` bus, ESP32-S3 default) |
| `WAKE` | ESP32 `GND` |

**DHT22 (Temperature and Humidity)**

| DHT22 Pin | Connect to |
|:----------|:-----------|
| `VCC` | ESP32 `3.3V` |
| `GND` | ESP32 `GND` |
| `DATA` | ESP32 `GPIO 4` |

**MQ-7 (Carbon Monoxide) -- VOLTAGE DIVIDER REQUIRED**

| MQ-7 Pin | Connect to |
|:---------|:-----------|
| `VCC` | LM2596 OUT+ (+5V) |
| `GND` | Common GND |
| `A0` | See the voltage divider diagram |

```text
MQ-7 A0 ---[R1 = 10 kOhms]---+--- ADS1115 A0
                              |
                       [R2 = 20 kOhms]
                              |
                             GND
```

Resulting voltage: `(20 / (10 + 20)) x 5V = 3.33V` (safe for the ADS1115).

**5V Fan (via IRLZ44N MOSFET)**

| MOSFET Pin | Connect to |
|:-----------|:-----------|
| Gate (G) | 1 kOhm resistor then `GPIO 38` |
| Drain (D) | Fan negative wire (-) |
| Source (S) | Common GND |

The fan positive wire (+) goes to the LM2596 `OUT+` (5V).

**Active Buzzer (via 2N2222 transistor)**

| 2N2222 Pin | Connect to |
|:-----------|:-----------|
| Base (B) | 1 kOhm resistor then `GPIO 15` |
| Collector (C) | Buzzer negative wire (-) |
| Emitter (E) | Common GND |

**Indicator LEDs (optional -- disabled by default)**

The LEDs are disabled in the firmware (`LED_OK_PIN = -1`, `LED_ALERT_PIN = -1`).
Reason: `GPIO 25` is not exposed on the WROOM-1 module, and `GPIO 26` is reserved
for internal SPI flash, which is unsafe to use.
To re-enable them, choose two free GPIOs, for example `GPIO 10` and `GPIO 11`, and
modify the `#define` values at the top of `esp32_iaq_v2.ino`.

---

## 10. Uploading the Code to the ESP32

### 10.1 Prerequisites

- **Arduino IDE 2.x**: Download from `https://www.arduino.cc/en/software`.
- Install ESP32 support: Menu `File > Preferences > Additional Boards Manager URLs`,
  add: `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
  Then go to `Tools > Board Manager`, search for `"esp32"` and install it.
- Install the libraries (`Tools > Manage Libraries`):
  - `Adafruit CCS811 Library`
  - `Adafruit ADS1X15` (by Adafruit)
  - `DHT sensor library` (by Adafruit)
  - `ArduinoJson` (by Benoit Blanchon)

### 10.2 Configuration

1. Open `esp32_iaq/esp32_iaq_fusion.ino` in Arduino IDE.
2. Modify `WIFI_SSID` and `WIFI_PASSWORD` (lines 25-26).
3. Check the Render server URLs (lines 29-31).
4. `Tools > Board Type`: `ESP32S3 Dev Module`.
5. `Tools > Port`: choose the COM port, for example `COM3`.
6. Click `Upload` (right arrow).

### 10.3 Verification

Open the Serial Monitor (115200 baud). The ESP32 should display:
- `[LittleFS] OK.`
- `Scan Wire (CCS811)` + `Scan Wire1 (ADS1115)` with detected I2C addresses
- `[ADS1115] OK`
- `[CCS811] OK`
- For 60s: MQ-7 calibration (CO displays `NAN`)
- Then measurements every 2 seconds, sending every 5 seconds.

### 10.4 OTA Updates (Without Cable)

After the first upload via USB, the following updates
can be done over WiFi:
1. In Arduino IDE: `Tools > Port > choose esp32-salon` (network).
2. Enter the OTA password: `iaqadmin`.
3. Upload normally.

---

## 11. Email Notifications

The server automatically sends an email when a sensor reaches
the **"ALERT"** level, not the "Warning" level.

### 11.1 Gmail Configuration

1. Create a Gmail account dedicated to the system, for example `your-iaq-project@gmail.com`.
2. Enable two-factor authentication on this account.
3. Generate an **App Password**:
   `https://myaccount.google.com/apppasswords`
4. Edit `app.py` (lines 48-51):
   - `EMAIL_SENDER` = the bot Gmail address.
   - `EMAIL_PASSWORD` = the app password (16 characters).
   - `EMAIL_RECEIVER` = your personal address.

### 11.2 Format of the Received Email

```text
Subject: ⚠️ IAQ ALERT: High CO Danger!
Body:
  Affected sensor   : CO
  Current value     : 42 ppm
  Alert threshold   : 35 ppm
  Please ventilate the room immediately.
```

---

## 12. MQ-7 Mathematical Formula

The MQ-7 sensor outputs an analog voltage proportional to the gas.
The CO calculation in ppm follows these steps:

1. **Reading**: The ADS1115 reads the voltage after the voltage divider on channel A0.
2. **Inversion**: `real_voltage = divider_voltage x (10 + 20) / 20` (ratio = 1.5)
3. **Resistance**: `Rs = RL x (5.0 - real_voltage) / real_voltage` (`RL = 10 kOhm`)
4. **Ratio**: `ratio = Rs / R0` (`R0` = calibrated value in clean air)
5. **Concentration**: `log(ppm) = (log10(ratio) - 1.398) / -0.699`
6. **Result**: `CO (ppm) = 10^log(ppm)`

---

## 13. Security

| Point | Status | Detail |
|:------|:-------|:-------|
| API Key | Active | `X-API-KEY` header required for POST. |
| HTTPS | Active | `WiFiClientSecure` + `setInsecure`. |
| Rate Limiting | Active | `30/min` POST, `200/min` global. |
| Data Validation | Active | Numeric ranges checked. |
| Compression | Active | Gzip/Brotli via `Flask-Compress`. |
| WebSocket | Active | `Socket.IO` pushes updates. |
| Automatic DB Cleanup | Active | Every day at 3:00 AM (`APScheduler`). |
| Email Alerts | Active | Gmail via `smtplib` + threading. |

---

## 14. Implemented Improvements (Completed Roadmap)

All roadmap improvements have been implemented except Deep Sleep,
which is incompatible with MQ-7 and MH-Z19 preheating.

Compared with V2, the fusion firmware adds the following improvements:

1. Fixed blocking bugs
2. Watchdog Timer (15s)
3. I2C Timeout (1s)
4. Fan thresholds as constants (with hysteresis)
5. Backend data validation
6. API key (`X-API-KEY`)
7. Rate limiting (`flask-limiter`)
8. Automatic DB cleanup (`APScheduler`)
9. Backend alerts + SQLite table
10. 5-chart dashboard (`Chart.js`)
11. Threshold annotations on graphs
12. Sensor online indicator
13. Statistics page (min/avg/max)
14. CSV export
15. Educational sensor page
16. Real time WebSocket (`Socket.IO`)
17. Autonomous NTP clock (UTC+1 Algeria, no DST)
18. Persistent LittleFS storage
19. Named constants for thresholds
20. Dedicated MQ-7 calibration script
21. HTTP error logging
22. Non-blocking WiFi reconnection
23. External ADS1115 ADC (16 bits)
24. ~~Deep Sleep~~ (ignored: gas sensors)
25. OTA update (`ArduinoOTA`)
26. Threshold unification
27. Gmail email notifications
28. Render cloud hosting
29. **Fusion V2  **: dual backend, dynamic `R0` calibration
30. **I2C scan** at startup (diagnostics)
31. **Fixed CCS811 reading**: `available()` + `readData()` + cap 1187 ppb
32. **Sensor validation** before threshold comparison (`NAN`, `r0Ready`)
33. **Local state machine**: buzzer/fan without server dependency
34. **TVOC fallback**: keeps the last valid value if CCS811 misses a cycle
35. **Fixed NTP**: no DST for Algeria
