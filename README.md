# IAQ V2 -- Indoor Air Quality Dashboard

[![Python CI](https://github.com/mahdidou711/iaq-dashboard-v2/actions/workflows/ci.yml/badge.svg)](https://github.com/mahdidou711/iaq-dashboard-v2/actions/workflows/ci.yml)


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

| File / Folder | Role |
|:--------------|:-----|
| `app.py` | Flask backend, SQLite storage, validation, alerts and API routes. |
| `.env.example` | Fake-only template for local/backend environment variables. |
| `templates/index.html` | Same-origin web dashboard. |
| `templates/infos.html` | Educational page about sensors and health thresholds. |
| `esp32_iaq/esp32_iaq_fusion.ino` | **Active firmware** — verified HTTPS, dual backend, buffering and OTA. |
| `esp32_iaq/esp32_iaq_v2.ino` | Archived trusted-LAN HTTP firmware; not for Internet or production use. |
| `esp32_iaq/esp32_iaq_nini.ino` | Reference HTTPS firmware for the second backend. |
| `esp32_iaq/config_private.example.h` | Fake-only ESP32 private configuration template. |
| `esp32_iaq/root_ca.h` | Public root CA bundle used for HTTPS verification. |
| `esp32_iaq/mq7_calibration.ino` | MQ-7 calibration utility. |
| `tests/` | Backend security regression tests. |
| `requirements.txt` | Pinned Python dependencies. |
| `Procfile` | Gunicorn startup command for Render. |

---

## 3. Server Installation

### 3.1 Local setup

Prerequisite: Python 3.10 or later.

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

3. Create the private local environment file:

```bash
# Linux / macOS
cp .env.example .env

# Windows Command Prompt
copy .env.example .env
```

4. Edit **only** `.env`. Choose two distinct local-only raw keys, then set
   `IAQ_INGEST_API_KEY_SHA256` and `IAQ_ADMIN_API_KEY_SHA256` to their SHA-256
   verifiers (section 3.3). The example placeholders are fake and authenticate nothing.
5. Start the server:

```bash
python app.py
```

6. Open `http://127.0.0.1:5000`.

The application loads `.env` through `python-dotenv`. `.env` and its variants are
ignored by Git. Direct execution binds to loopback by default; set `DEV_HOST=0.0.0.0`
only when you deliberately need LAN access, and use a firewall/trusted network.

### 3.2 Cloud hosting (Render)

Use the existing build command `python -m pip install -r requirements.txt` and the
`Procfile`. Configure secrets in the Render environment, never in tracked files:

| Variable | Production guidance |
|:---------|:--------------------|
| `IAQ_INGEST_API_KEY_SHA256` | Required SHA-256 verifier of the device-ingestion key. |
| `IAQ_ADMIN_API_KEY_SHA256` | Required distinct verifier of the key for `/api/clear` and debug-only `/api/seed`. |
| `DB_PATH` | Persistent SQLite path, for example the mounted Render disk path. |
| `EMAIL_ALERTS_ENABLED` | `true` only after all email variables are configured; otherwise `false`. |
| `EMAIL_SENDER` | Dedicated sender account address. |
| `EMAIL_PASSWORD` | Gmail application password stored only in Render. |
| `EMAIL_RECEIVER` | Alert recipient address. |
| `EMAIL_ALERT_COOLDOWN_SECONDS` | Per-sensor email cooldown; default `900`. |
| `ALLOWED_ORIGINS` | Empty for same-origin; otherwise a comma-separated exact allowlist, never `*`. |
| `FLASK_DEBUG` | `false` in production. |

Render never receives the raw API keys. Production startup fails if either verifier is
missing, if a verifier is not exactly 64 lowercase hexadecimal characters, or if both
verifiers are identical. A malformed verifier is rejected even with `FLASK_DEBUG=true`.
Enabling email alerts with incomplete email configuration also fails safely. Remove any
legacy `IAQ_INGEST_API_KEY` / `IAQ_ADMIN_API_KEY` variables from Render: the backend
ignores them.

### 3.3 API key verifiers

Authentication uses a verifier model:

1. The ESP32 (ingestion) and the administrator (dashboard buttons) keep the **raw** key and
   send it in the `X-API-KEY` header over HTTPS.
2. The backend stores only `SHA-256(raw key)` as lowercase hexadecimal.
3. For each request, the backend hashes the received header and compares the two digests
   with `hmac.compare_digest()`.

Sending the verifier itself as `X-API-KEY` fails with `401`, so reading the Render
environment is not enough to authenticate. SHA-256 is appropriate here because the keys are
long random secrets, not human passwords; do not reuse this model for low-entropy values.

Derive a verifier locally without displaying or storing the raw key in shell history:

```bash
python -c "import getpass, hashlib; print(hashlib.sha256(getpass.getpass('Raw key: ').encode()).hexdigest())"
```

Paste the raw key at the hidden prompt and copy only the printed 64-character digest.
Hash exactly the bytes the client sends: a trailing newline (for example from
`echo key | sha256sum`) produces a different, unusable verifier.

---

## 4. Server Security and API (`app.py`)

### 4.1 Main internal boundaries

| Function | Role |
|:---------|:-----|
| `validate_runtime_configuration()` | Rejects unsafe or incomplete production configuration. |
| `validate_sensor_value()` | Rejects booleans, non-numbers, non-finite values and out-of-range readings. |
| `validate_timestamp()` | Accepts only `YYYY-MM-DD HH:MM:SS`, or creates server time when absent. |
| `send_email_alert_async()` | Sends validated STARTTLS email with bounded workers and per-sensor cooldown. |
| `verifier_alertes()` | Stores threshold alerts and requests deduplicated email dispatch. |
| `require_api_key()` | Hashes the raw `X-API-KEY` with SHA-256 and compares it in constant time with the role's verifier. |
| `sanitize_csv_cell()` | Neutralizes formula-like text before CSV export. |

### 4.2 API routes

| Method | Address | Auth | Rate Limit | Description |
|:-------|:--------|:-----|:-----------|:------------|
| GET | `/` | Public | Global | HTML dashboard. |
| GET | `/infos` | Public | Global | Educational sensor page. |
| GET | `/api/health` | Public | Global | Health check. |
| POST | `/api/mesures` | Ingestion key | 30/min | Single or batch ingestion. |
| GET | `/api/data` | Public | Global | Paginated measurements. |
| GET | `/api/stats` | Public | Global | Aggregate sensor statistics. |
| GET | `/api/alertes` | Public | Global | Alert history. |
| GET | `/api/export` | Public | Global | Sanitized CSV download. |
| POST | `/api/clear` | Admin key | Global | Deletes all data. |
| POST | `/api/seed` | Admin key | Global | Generates test data; `FLASK_DEBUG=true` only. |

Protected routes expect the raw key in `X-API-KEY`; it is verified against
`IAQ_INGEST_API_KEY_SHA256` or `IAQ_ADMIN_API_KEY_SHA256` (section 3.3). Each key is
accepted only by its own role.

The read routes remain public because the current dashboard has no user-account system and
loads them from the same Flask origin. CORS restrictions do **not** make these URLs private:
an Internet deployment exposes its measurements and alert history to anyone who can reach it.
Deploy privately or add a separately reviewed access-control layer if that data is sensitive.

### 4.3 Configuration

All private configuration comes from environment variables; `.env.example` is the canonical
fake-only local template. There is no usable authentication-key default.

| Variable | Default | Purpose |
|:---------|:--------|:--------|
| `IAQ_INGEST_API_KEY_SHA256` | None | Required SHA-256 verifier of the ingestion key (64 lowercase hex). |
| `IAQ_ADMIN_API_KEY_SHA256` | None | Required, distinct SHA-256 verifier of the administrative key. |
| `DB_PATH` | `iaq.db` | SQLite database path. |
| `EMAIL_ALERTS_ENABLED` | `false` | Enables Gmail alerts only when explicitly configured. |
| `EMAIL_SENDER` / `EMAIL_PASSWORD` / `EMAIL_RECEIVER` | None | Private email settings. |
| `EMAIL_ALERT_COOLDOWN_SECONDS` | `900` | Minimum interval between emails for one sensor. |
| `ALLOWED_ORIGINS` | Empty | Same-origin by default; optional comma-separated browser allowlist. |
| `FLASK_DEBUG` | `false` | Enables the debug-only seed route. Never enable in production. |
| `DEV_HOST` | `127.0.0.1` | Direct development-server bind address. |
| `PORT` | `5000` | Direct development-server port. |

Request bodies are limited to 64 KiB and ingestion batches to 100 records.

### 4.4 SQLite database

**`mesures` table:** id, timestamp, co2, tvoc, co, temperature, humidite

**`alertes` table:** id, timestamp, capteur, niveau (warn/alert), valeur, seuil, message

Both tables have an index on `timestamp`.

---

## 5. Full List of Fusion Firmware Functions (`esp32_iaq_fusion.ino`)

The fusion firmware combines the robustness of V2, namely watchdog, OTA, LittleFS, NTP,
ArduinoJson, with 's improvements, namely dynamic `R0` calibration, correct
CCS811 reading, I2C scan, sensitive thresholds, and a local state machine.

### 5.1 Libraries and local headers (14)

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
| `config_private.h` | Ignored local credentials generated from the fake example. |
| `root_ca.h` | Public root certificate for server authentication. |

### 5.2 Functions

| Function | Role |
|:---------|:-----|
| `scanI2C()` | Scans the I2C buses at startup (diagnostics). |
| `setup()` | Init: LittleFS, WDT, 2x I2C, pins, WiFi, verified TLS, OTA, sensors. |
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
| `synchroniserHorlogeTLS()` | Waits for a valid NTP clock before allowing HTTPS. |

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

- **Verified HTTPS**: `WiFiClientSecure.setCACert()` validates the server certificate and
  hostname against the public root CA bundle in `root_ca.h` (GTS Root R1/R4, currently used
  by `*.onrender.com`, plus ISRG Root X1).
- **NTP gate**: production HTTPS requests fail closed until the ESP32 clock is valid;
  this is required for certificate expiry and hostname verification.
- **Private configuration**: Wi-Fi, ingestion and OTA credentials come only from the
  ignored `config_private.h` file.
- **Watchdog (15s)** and **I2C timeout (1s)** limit hardware lockups.
- **LittleFS buffer (50 KB)** preserves measurements while the primary server is offline.
- **Cross-calibration**, finite sensor checks, TVOC fallback and hysteresis improve readings
  and prevent rapid output switching.
- **Local state machine**: IDLE → BUZZING 2s → FAN_ON → back to IDLE.

### 5.5 Private configuration before upload

Never edit credentials into a tracked `.ino` file.

```bash
cp esp32_iaq/config_private.example.h esp32_iaq/config_private.h
```

Edit only `esp32_iaq/config_private.h` and replace every `EXAMPLE_ONLY` placeholder.
This ignored file supplies:

- `IAQ_WIFI_SSID` and `IAQ_WIFI_PASSWORD`;
- `IAQ_INGEST_API_KEY`, the raw ingestion key whose SHA-256 is the backend
  `IAQ_INGEST_API_KEY_SHA256` (HTTPS firmwares only);
- `IAQ_OTA_PASSWORD`;
- `IAQ_LOCAL_INGEST_API_KEY` and the local URLs, used only by the archived V2 LAN firmware.

The archived V2 sketch sends its key over plaintext HTTP, so it reads the separate
`IAQ_LOCAL_INGEST_API_KEY` and never `IAQ_INGEST_API_KEY`. Give it a distinct local-only
value, and configure its SHA-256 as `IAQ_INGEST_API_KEY_SHA256` only on the local test server.
V2 refuses to compile if both keys are identical.

The active fusion firmware keeps its public Render URLs in source. Public service URLs are
not credentials. If a Render endpoint changes certificate authority, update the public CA
bundle after validating the new chain before reflashing.

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

Tested toolchain: board package **`esp32:esp32` 2.0.17** and **ArduinoJson 6.21.x**.
The current firmware does not compile with the ESP32 core 3.x API without source
changes, and ArduinoJson 7 is not the tested major version; pin both versions in the
Boards Manager and Library Manager.

### 10.2 Configuration

1. Copy `config_private.example.h` to the ignored `config_private.h` file.
2. Put the real Wi-Fi, ingestion-key and OTA values only in `config_private.h`.
3. Confirm the backend `IAQ_INGEST_API_KEY_SHA256` is the SHA-256 of the device ingestion key.
4. Open `esp32_iaq/esp32_iaq_fusion.ino` in Arduino IDE.
5. Select `ESP32S3 Dev Module`, choose the serial port, then upload.

Do not rename the example over the private file and do not use `git add -f` on
`config_private.h`. Compilation intentionally fails when the private header is absent.

### 10.3 Verification

Open the Serial Monitor (115200 baud). The ESP32 should display:
- `[LittleFS] OK.`
- `Scan Wire (CCS811)` + `Scan Wire1 (ADS1115)` with detected I2C addresses
- `[ADS1115] OK`
- `[CCS811] OK`
- For 60s: MQ-7 calibration (CO displays `NAN`)
- Then measurements every 2 seconds, sending every 5 seconds.

### 10.4 OTA Updates (Without Cable)

OTA remains enabled in the fusion and archived V2 sketches. Its password is read from
`IAQ_OTA_PASSWORD` in the ignored `config_private.h`; there is no usable default.

After the first USB upload, select the configured OTA hostname in Arduino IDE and upload
normally while the device is on the same trusted network. Rotate the previously exposed OTA
password before reflashing and never place the replacement in source or documentation.

---

## 11. Email Notifications

The server automatically sends an email when a sensor reaches
the **"ALERT"** level, not the "Warning" level.

### 11.1 Gmail Configuration

1. Use a dedicated Gmail account with two-factor authentication.
2. Create an application password in the Google account settings.
3. Store it only in `.env` for local development or in Render environment variables.
4. Set `EMAIL_SENDER`, `EMAIL_PASSWORD`, `EMAIL_RECEIVER`, then set
   `EMAIL_ALERTS_ENABLED=true`.

Email is disabled by default. STARTTLS uses the platform trust store with hostname and
certificate validation. A per-sensor cooldown (default: 15 minutes) and two-worker limit
prevent a measurement batch from creating an unbounded number of SMTP threads.

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

| Control | Status | Detail |
|:--------|:-------|:-------|
| Backend secrets | Environment only | Ingestion, administration and email values are absent from tracked source. |
| API keys at rest | SHA-256 verifiers | The backend stores only `IAQ_INGEST_API_KEY_SHA256` / `IAQ_ADMIN_API_KEY_SHA256`; clients keep the raw keys, and a verifier sent as `X-API-KEY` is rejected. |
| Role separation | Active | Device ingestion and destructive administration require distinct keys. |
| HTTPS firmware | Verified | Fusion and Nini validate the server chain and hostname with the `root_ca.h` bundle after NTP sync. |
| Archived V2 transport | Plaintext LAN only | Uses HTTP intentionally for local reference with the separate `IAQ_LOCAL_INGEST_API_KEY`; never use it over the Internet or with a production key. |
| OTA | Private config | Password comes from ignored `config_private.h`, with no default. |
| Input validation | Active | Strict timestamps, finite numbers, physical ranges, 64 KiB bodies and 100-row batches. |
| Browser rendering | Safe DOM APIs | Alert values are inserted with `textContent`; severity classes are allowlisted. |
| CSV export | Neutralized | Formula-triggering string cells are prefixed before export. |
| CORS / Socket.IO | Same-origin by default | Optional exact origins use `ALLOWED_ORIGINS`; wildcard is rejected. |
| SMTP | Verified STARTTLS | Default trust store validates the Gmail certificate and hostname. |
| Alert resource control | Bounded | Per-sensor cooldown plus at most two simultaneous email workers. |

Important privacy note: the read APIs remain unauthenticated for the current public dashboard
architecture. Same-origin rules limit browser embedding but do not prevent direct requests.

Historical commits still contain previously exposed credentials. Removing them from current
files does not revoke them or erase Git history; rotation and a reviewed history rewrite are
separate mandatory manual phases.

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
