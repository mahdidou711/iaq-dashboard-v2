"""
app.py — Serveur Flask pour le tableau de bord IAQ (Qualité de l'air intérieur)
Architecture : ESP32 → POST /api/mesures → SQLite → GET /api/data → Chart.js
"""

import csv
import io
import os
import sqlite3
import logging
from datetime import datetime, timedelta
from functools import wraps
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading
from flask import Flask, request, jsonify, render_template, g, Response # type: ignore
from flask_limiter import Limiter # type: ignore
from flask_limiter.util import get_remote_address # type: ignore
from flask_cors import CORS # type: ignore
from apscheduler.schedulers.background import BackgroundScheduler # type: ignore
import atexit
from flask_socketio import SocketIO # type: ignore
from flask_compress import Compress # type: ignore

app = Flask(__name__)
Compress(app)
CORS(app)  # Autorise les requêtes inter-domaines (si le frontal est hébergé ailleurs)
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialisation du Rate Limiter (Bouclier anti-spam)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per minute"],
    storage_uri="memory://"
)

# ─── Configuration (modifie directement ici) ─────────────────────────────────
DATABASE = os.environ.get("DB_PATH", "iaq.db") # Utilise le disque Render s'il existe, sinon local
API_KEY = "SECRET_IAQ_2026"        # Clé secrète d'authentification
DEBUG = True                       # True = active /api/seed + logs détaillés
DATA_RETENTION_DAYS = 30           # Jours avant suppression des vieilles données
SENSOR_OFFLINE_MINUTES = 5         # Minutes sans données → "capteur hors ligne"

# --- Configuration Email (Gmail) ---
EMAIL_ALERTS_ENABLED = True
EMAIL_SENDER = "votre.email@gmail.com"
# ATTENTION: Il faut générer un "Mot de passe d'application" Google (16 lettres) si vous avez la double-authentification
EMAIL_PASSWORD = "votre_mot_de_passe_application_google" 
EMAIL_RECEIVER = "destinataire@gmail.com"

# Seuils d'alerte
THRESHOLDS = {
    "co2":         {"warn": 1000, "alert": 2000},   # ppm
    "tvoc":        {"warn": 300,  "alert": 600},     # ppb
    "co":          {"warn": 9,    "alert": 35},      # ppm
    "temperature": {"warn": 28,   "alert": 35},      # °C
    "humidite":    {"warn": 60,   "alert": 75},      # %
}

VALID_RANGES = {
    "co2":         (0, 10000),
    "tvoc":        (0, 30000),
    "co":          (0, 500),
    "temperature": (-40, 85),
    "humidite":    (0, 100),
}

SENSOR_FIELDS = ["co2", "tvoc", "co", "temperature", "humidite"]

SENSOR_LABELS = {
    "co2": "CO₂", "tvoc": "TVOC", "co": "CO",
    "temperature": "Température", "humidite": "Humidité",
}

SENSOR_UNITS = {
    "co2": "ppm", "tvoc": "ppb", "co": "ppm",
    "temperature": "°C", "humidite": "%",
}

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("iaq")


# ─── Base de données ──────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mesures (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                co2         REAL,
                tvoc        REAL,
                co          REAL,
                temperature REAL,
                humidite    REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alertes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                capteur     TEXT    NOT NULL,
                niveau      TEXT    NOT NULL,
                valeur      REAL,
                seuil       REAL,
                message     TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mesures_timestamp ON mesures(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alertes_timestamp ON alertes(timestamp)")
        conn.commit()
    log.info("Database initialized: %s", DATABASE)


def cleanup_old_data():
    cutoff = (datetime.now() - timedelta(days=DATA_RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DATABASE) as conn:
        d1 = conn.execute("DELETE FROM mesures WHERE timestamp < ?", (cutoff,)).rowcount
        d2 = conn.execute("DELETE FROM alertes WHERE timestamp < ?", (cutoff,)).rowcount
        conn.commit()
    if d1 + d2 > 0:
        log.info("Cleaned up %d mesures + %d alertes (before %s)", d1, d2, cutoff)


# ─── Validation ───────────────────────────────────────────────────────────────

def validate_sensor_value(key, value):
    if value is None:
        return None, None
    if not isinstance(value, (int, float)):
        return None, f"{key}: valeur non numérique"
    lo, hi = VALID_RANGES[key]
    if value < lo or value > hi:
        return None, f"{key}: {value} hors plage [{lo}, {hi}]"
    return float(value), None


def validate_measurement(data):
    cleaned = {}
    errors = []
    for key in SENSOR_FIELDS:
        raw = data.get(key)
        val, err = validate_sensor_value(key, raw)
        cleaned[key] = val
        if err:
            errors.append(err)
    return cleaned, errors


# ─── Envoi d'Email Asynchrone ─────────────────────────────────────────────────

def send_email_alert_async(subject, body):
    if not EMAIL_ALERTS_ENABLED:
        return
    
    def send_email():
        try:
            msg = MIMEMultipart()
            msg['From'] = EMAIL_SENDER
            msg['To'] = EMAIL_RECEIVER
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
            server.quit()
            log.info("Email d'alerte envoyé avec succès à %s !", EMAIL_RECEIVER)
        except Exception as e:
            log.error("Erreur critique lors de l'envoi de l'email Gmail : %s", e)

    # Lancer l'envoi en arrière-plan pour ne jamais ralentir ou bloquer le routeur Flask
    threading.Thread(target=send_email, daemon=True).start()


# ─── Vérification des alertes ─────────────────────────────────────────────────

def verifier_alertes(cleaned, ts):
    """Vérifie les seuils et enregistre les alertes dans la base."""
    db = get_db()
    status = {}

    for key in SENSOR_FIELDS:
        val = cleaned[key]
        if val is None:
            continue
        t = THRESHOLDS[key]
        if val >= t["alert"]:
            status[key] = "alert"
            niveau = "alert"
            seuil = t["alert"]
        elif val >= t["warn"]:
            status[key] = "warn"
            niveau = "warn"
            seuil = t["warn"]
        else:
            status[key] = "ok"
            continue

        label = SENSOR_LABELS[key]
        unit = SENSOR_UNITS[key]
        niveau_fr = "ALERTE" if niveau == "alert" else "ATTENTION"
        message = f"{niveau_fr} {label} : {val} {unit} (seuil : {seuil} {unit})"

        db.execute(
            """INSERT INTO alertes (timestamp, capteur, niveau, valeur, seuil, message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ts, key, niveau, val, seuil, message),
        )

        if niveau == "alert":
            sujet = f"⚠️ ALERTE IAQ : Danger {label} Élevé !"
            corps = (
                f"Alerte de Qualité de l'Air Intérieur (IAQ)\n"
                f"Date : {ts}\n\n"
                f"Le capteur a détecté un niveau potentiellement toxique ou dangereux :\n"
                f"- Capteur concerné : {label}\n"
                f"- Valeur Actuelle  : {val} {unit}\n"
                f"- Seuil d'Alerte   : {seuil} {unit}\n\n"
                f"Veuillez vérifier l'aérateur ou aérer la pièce immédiatement.\n"
                f"Ce message est généré automatiquement par le serveur IAQ."
            )
            send_email_alert_async(sujet, corps)

    db.commit()
    return status


# ─── Routes ───────────────────────────────────────────────────────────────────

def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.headers.get("X-API-KEY") != API_KEY:
            log.warning("Accès refusé depuis %s (Mauvaise clé API)", request.remote_addr)
            return jsonify({"erreur": "Non autorisé. Clé API manquante ou invalide."}), 401
        return f(*args, **kwargs)
    return decorated_function

@app.route("/")
def index():
    return render_template(
        "index.html",
        thresholds=THRESHOLDS,
        offline_minutes=SENSOR_OFFLINE_MINUTES,
        sensor_labels=SENSOR_LABELS,
        sensor_units=SENSOR_UNITS,
        debug=DEBUG,
    )


@app.route("/api/health")
def health():
    return jsonify({"statut": "ok", "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


@app.route("/api/mesures", methods=["POST"])
@require_api_key
@limiter.limit("30 per minute")
def recevoir_mesures():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"erreur": "Corps JSON manquant"}), 400

    if isinstance(data, list):
        return _insert_batch(data)
    return _insert_single(data)


def _insert_single(data):
    if not any(k in data for k in SENSOR_FIELDS):
        return jsonify({"erreur": "Aucun champ reconnu dans le JSON"}), 400

    cleaned, errors = validate_measurement(data)
    if errors:
        log.warning("Validation errors from %s: %s", request.remote_addr, errors)
        return jsonify({"erreur": "Valeurs invalides", "details": errors}), 400

    ts = data.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db = get_db()
    db.execute(
        """INSERT INTO mesures (timestamp, co2, tvoc, co, temperature, humidite)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ts, cleaned["co2"], cleaned["tvoc"], cleaned["co"],
         cleaned["temperature"], cleaned["humidite"]),
    )
    db.commit()

    status = verifier_alertes(cleaned, ts)

    socketio.emit('update_needed', namespace='/')

    log.info("Mesure reçue de %s", request.remote_addr)
    return jsonify({"statut": "ok", "timestamp": ts, "alertes": status}), 201


def _insert_batch(data_list):
    if len(data_list) > 100:
        return jsonify({"erreur": "Lot trop grand, max 100 mesures"}), 400

    db = get_db()
    inserted = 0
    errors = []

    for i, data in enumerate(data_list):
        if not isinstance(data, dict):
            errors.append(f"Element {i}: pas un objet JSON")
            continue
        if not any(k in data for k in SENSOR_FIELDS):
            errors.append(f"Element {i}: aucun champ reconnu")
            continue

        cleaned, val_errors = validate_measurement(data)
        if val_errors:
            errors.append(f"Element {i}: {val_errors}")
            continue

        ts = data.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        db.execute(
            """INSERT INTO mesures (timestamp, co2, tvoc, co, temperature, humidite)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ts, cleaned["co2"], cleaned["tvoc"], cleaned["co"],
             cleaned["temperature"], cleaned["humidite"]),
        )
        verifier_alertes(cleaned, ts)
        inserted += 1

    db.commit()
    log.info("Batch insert: %d/%d mesures insérées", inserted, len(data_list))

    if inserted > 0:
        socketio.emit('update_needed', namespace='/')

    result = {"statut": "ok", "lignes_inserees": inserted}
    if errors:
        return jsonify({"statut": "ok", "lignes_inserees": inserted, "erreurs": errors}), 201
    return jsonify({"statut": "ok", "lignes_inserees": inserted}), 201


@app.route("/api/data")
def lire_donnees():
    n = request.args.get("n", 2880, type=int) # 48h par defaut (1 pt/min)
    per_page = request.args.get("per_page", n, type=int)
    per_page = min(max(per_page, 1), 50000)

    page = request.args.get("page", 1, type=int)
    page = max(page, 1)
    offset = (page - 1) * per_page

    date_from = request.args.get("from")
    date_to = request.args.get("to")

    db = get_db()
    query = "SELECT timestamp, co2, tvoc, co, temperature, humidite FROM mesures"
    conditions = []
    params = []

    if date_from:
        conditions.append("timestamp >= ?")
        params.append(date_from + " 00:00:00")
    if date_to:
        conditions.append("timestamp <= ?")
        params.append(date_to + " 23:59:59")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([per_page, offset])

    rows = list(reversed(db.execute(query, params).fetchall()))

    return jsonify({
        "labels":      [r["timestamp"] for r in rows],
        "co2":         [r["co2"]         for r in rows],
        "tvoc":        [r["tvoc"]        for r in rows],
        "co":          [r["co"]          for r in rows],
        "temperature": [r["temperature"] for r in rows],
        "humidite":    [r["humidite"]    for r in rows],
    })


# ─── Stats ────────────────────────────────────────────────────────────────────

@app.route("/api/stats")
def stats():
    date_from = request.args.get("from")
    date_to = request.args.get("to")

    if not date_from:
        date_from = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        date_from += " 00:00:00"
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        date_to += " 23:59:59"

    db = get_db()
    conditions = ["timestamp >= ?", "timestamp <= ?"]
    params = [date_from, date_to]
    where = " AND ".join(conditions)

    agg_parts = []
    for field in SENSOR_FIELDS:
        agg_parts.append(f"AVG({field}) as {field}_avg")
        agg_parts.append(f"MIN({field}) as {field}_min")
        agg_parts.append(f"MAX({field}) as {field}_max")

    query = f"SELECT COUNT(*) as total, {', '.join(agg_parts)} FROM mesures WHERE {where}"
    row = db.execute(query, params).fetchone()

    capteurs_dict = {}
    for field in SENSOR_FIELDS:
        avg_val = row[f"{field}_avg"]
        avg_val_rounded = round(float(avg_val), 1) if avg_val is not None else None # type: ignore
        capteurs_dict[field] = {
            "label": SENSOR_LABELS[field],
            "unite": SENSOR_UNITS[field],
            "moyenne": avg_val_rounded,
            "min": row[f"{field}_min"],
            "max": row[f"{field}_max"],
        }

    result = {
        "periode": {"de": date_from, "a": date_to},
        "total_mesures": row["total"],
        "capteurs": capteurs_dict,
    }

    return jsonify(result)


# ─── Historique d'alertes ─────────────────────────────────────────────────────

@app.route("/api/alertes")
def lire_alertes():
    n = request.args.get("n", 50, type=int)
    n = min(max(n, 1), 500)
    capteur = request.args.get("capteur")
    niveau = request.args.get("niveau")
    date_from = request.args.get("from")
    date_to = request.args.get("to")

    conditions = []
    params = []

    if capteur:
        conditions.append("capteur = ?")
        params.append(capteur)
    if niveau:
        conditions.append("niveau = ?")
        params.append(niveau)
    if date_from:
        conditions.append("timestamp >= ?")
        params.append(date_from + " 00:00:00")
    if date_to:
        conditions.append("timestamp <= ?")
        params.append(date_to + " 23:59:59")

    query = "SELECT * FROM alertes"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(n)

    db = get_db()
    rows = db.execute(query, params).fetchall()

    return jsonify([dict(r) for r in rows])


# ─── Export CSV ───────────────────────────────────────────────────────────────

@app.route("/api/export")
def export_data():
    date_from = request.args.get("from")
    date_to = request.args.get("to")

    db = get_db()
    conditions = []
    params = []

    if date_from:
        conditions.append("timestamp >= ?")
        params.append(date_from + " 00:00:00")
    if date_to:
        conditions.append("timestamp <= ?")
        params.append(date_to + " 23:59:59")

    query = "SELECT id, timestamp, co2, tvoc, co, temperature, humidite FROM mesures"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY timestamp ASC"

    rows = db.execute(query, params).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "timestamp", "co2", "tvoc", "co", "temperature", "humidite"])
    for r in rows:
        writer.writerow([r["id"], r["timestamp"],
                         r["co2"], r["tvoc"], r["co"], r["temperature"], r["humidite"]])

    filename = f"iaq_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ─── Infos capteurs ──────────────────────────────────────────────────────────

@app.route("/infos")
def infos():
    return render_template("infos.html")


# ─── Vider la base ───────────────────────────────────────────────────────────

@app.route("/api/clear", methods=["POST"])
@require_api_key
def clear_data():
    db = get_db()
    d1 = db.execute("DELETE FROM mesures").rowcount
    d2 = db.execute("DELETE FROM alertes").rowcount
    db.commit()
    log.info("Database cleared: %d mesures + %d alertes", d1, d2)
    return jsonify({"statut": "ok", "mesures_supprimees": d1, "alertes_supprimees": d2})


# ─── Données de test ─────────────────────────────────────────────────────────

@app.route("/api/seed", methods=["POST"])
@require_api_key
def seed():
    if not DEBUG:
        return jsonify({"erreur": "Endpoint désactivé en production"}), 403

    import random

    db = get_db()

    # Vider avant de remplir
    db.execute("DELETE FROM mesures")
    db.execute("DELETE FROM alertes")

    base_time = datetime.now().timestamp() - 86400
    for i in range(1440):
        ts = datetime.fromtimestamp(base_time + i * 60).strftime("%Y-%m-%d %H:%M:%S")
        co2_val = round(float(700 + random.gauss(0, 80) + (150 if 600 <= i <= 660 else 0)), 1) # type: ignore
        tvoc_val = round(float(120 + random.gauss(0, 30)), 1) # type: ignore
        co_val = round(float(5 + random.gauss(0, 1.5)), 2) # type: ignore
        temp_val = round(float(23 + random.gauss(0, 1.5) + (3 if 900 <= i <= 960 else 0)), 1) # type: ignore
        hum_val = round(float(50 + random.gauss(0, 5)), 1) # type: ignore

        db.execute(
            """INSERT INTO mesures (timestamp, co2, tvoc, co, temperature, humidite)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ts, co2_val, tvoc_val, co_val, temp_val, hum_val),
        )

        for key, val in [("co2", co2_val), ("tvoc", tvoc_val), ("co", co_val),
                         ("temperature", temp_val), ("humidite", hum_val)]:
            th = THRESHOLDS[key]
            if val >= th["alert"]:
                db.execute(
                    "INSERT INTO alertes (timestamp, capteur, niveau, valeur, seuil, message) VALUES (?,?,?,?,?,?)",
                    (ts, key, "alert", val, th["alert"],
                     f"ALERTE {SENSOR_LABELS[key]} : {val} {SENSOR_UNITS[key]}"),
                )
            elif val >= th["warn"]:
                db.execute(
                    "INSERT INTO alertes (timestamp, capteur, niveau, valeur, seuil, message) VALUES (?,?,?,?,?,?)",
                    (ts, key, "warn", val, th["warn"],
                     f"ATTENTION {SENSOR_LABELS[key]} : {val} {SENSOR_UNITS[key]}"),
                )

    db.commit()
    socketio.emit('update_needed', namespace='/')
    return jsonify({"statut": "ok", "lignes_inserees": 1440})


# ─── 404 ─────────────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"):
        return jsonify({"erreur": "Endpoint non trouvé"}), 404
    return Response(
        '<!DOCTYPE html><html><head><meta charset="UTF-8"><title>404</title>'
        "<style>body{background:#0d1117;color:#8b949e;font-family:'Courier New',monospace;"
        "display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh}"
        "a{color:#58a6ff}</style></head><body>"
        "<h1>404</h1><p>Page non trouvée</p>"
        '<p><a href="/">Retour au tableau de bord</a></p></body></html>',
        status=404, content_type="text/html",
    )


# ─── Démarrage ────────────────────────────────────────────────────────────────

init_db()

# Planification du nettoyage automatique de la BDD (tous les jours à 3h00)
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(func=cleanup_old_data, trigger="cron", hour=3, minute=0)
scheduler.start()
def shutdown_scheduler():
    scheduler.shutdown()
atexit.register(shutdown_scheduler) # type: ignore

if __name__ == "__main__":
    cleanup_old_data()
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=DEBUG, allow_unsafe_werkzeug=True)
