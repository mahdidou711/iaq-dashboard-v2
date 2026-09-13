"""
app.py — Serveur web IAQ (Qualité de l'Air Intérieur)
======================================================
Ce fichier est le CERVEAU du projet. Il fait 4 choses principales :
  1. Reçoit les mesures de l'ESP32 (POST /api/mesures) et les enregistre dans une base de données.
  2. Sert le tableau de bord web (GET /) affiché dans ton navigateur.
  3. Détecte les alertes (dépassement de seuils) et envoie un email Gmail automatiquement.
  4. Expose des routes pour lire les données, les statistiques, les alertes, et l'export CSV.

Architecture complète :
  ESP32 ──HTTPS POST JSON──▶ /api/mesures ──▶ SQLite (iaq.db)
                                                   │
  Navigateur ◀──HTML/JS── GET / ◀──────────────────┘
  Navigateur ◀──JSON────  GET /api/data    (graphiques temps réel)
  Navigateur ◀──JSON────  GET /api/stats   (statistiques min/moy/max)
  Navigateur ◀──JSON────  GET /api/alertes (historique dépassements)
  Navigateur ◀──CSV─────  GET /api/export  (téléchargement tableur)
  Navigateur ◀──Push────  Socket.IO        (mise à jour en temps réel)
  Gmail ◀──Email──────────  si seuil critique dépassé
"""

# ─── IMPORTS : Les "boîtes à outils" utilisées ────────────────────────────────
import csv                          # Pour construire les fichiers CSV (export tableur)
import hashlib                      # SHA-256 de la clé API reçue (le serveur ne stocke que l'empreinte)
import hmac                         # Comparaison sûre des clés API
import io                           # Pour créer un fichier CSV en mémoire sans écrire sur disque
import math                         # Validation des nombres finis (rejette NaN et les infinis)
import os                           # Pour lire les variables d'environnement (DB_PATH, PORT sur Render)
import re                           # Vérifie le format des empreintes SHA-256 configurées
import sqlite3                      # Base de données légère intégrée à Python (aucun serveur requis)
import logging                      # Pour afficher des messages de debug/info/erreur dans la console
import ssl                          # Contexte TLS vérifié pour SMTP STARTTLS
from datetime import datetime, timedelta  # Pour manipuler les dates (horodatage, rétention 30j)
from functools import wraps         # Pour créer des décorateurs Python (ex: @require_api_key)
import smtplib                      # Pour envoyer des emails via le protocole SMTP (Gmail)
from email.mime.text import MIMEText           # Pour formater le corps de l'email
from email.mime.multipart import MIMEMultipart # Pour créer un email avec sujet + corps
import threading                    # Pour envoyer les emails EN ARRIÈRE-PLAN sans bloquer Flask
import time                         # Horloge monotone pour le cooldown des emails
from dotenv import load_dotenv      # type: ignore  # Charge le fichier local .env s'il existe

load_dotenv()


def env_bool(name, default=False):
    """Lit un booléen explicite depuis l'environnement."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} doit valoir true/false, 1/0, yes/no ou on/off")


def env_non_negative_int(name, default):
    """Lit un entier positif ou nul avec une erreur de configuration explicite."""
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} doit être un entier") from exc
    if value < 0:
        raise RuntimeError(f"{name} doit être positif ou nul")
    return value


# ─── Configuration privée fournie uniquement par l'environnement ──────────────
DATABASE = os.environ.get("DB_PATH", "iaq.db")
# Le serveur ne stocke PAS les clés API brutes, seulement leur empreinte SHA-256
# (64 caractères hexadécimaux minuscules). L'ESP32 et l'administrateur gardent la clé brute
# et l'envoient dans X-API-KEY ; le serveur la hache puis compare les deux empreintes.
INGEST_API_KEY_SHA256 = os.environ.get("IAQ_INGEST_API_KEY_SHA256", "")
ADMIN_API_KEY_SHA256 = os.environ.get("IAQ_ADMIN_API_KEY_SHA256", "")
DEBUG = env_bool("FLASK_DEBUG", False)
EMAIL_ALERTS_ENABLED = env_bool("EMAIL_ALERTS_ENABLED", False)
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "")
EMAIL_ALERT_COOLDOWN_SECONDS = env_non_negative_int("EMAIL_ALERT_COOLDOWN_SECONDS", 900)
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
DEV_HOST = os.environ.get("DEV_HOST", "127.0.0.1")


def validate_runtime_configuration():
    """Échoue au démarrage si une production serait lancée sans ses secrets requis."""
    if any("*" in origin for origin in ALLOWED_ORIGINS):
        raise RuntimeError("ALLOWED_ORIGINS doit contenir des origines explicites, jamais '*'")
    verifiers = (
        ("IAQ_INGEST_API_KEY_SHA256", INGEST_API_KEY_SHA256),
        ("IAQ_ADMIN_API_KEY_SHA256", ADMIN_API_KEY_SHA256),
    )
    for name, value in verifiers:
        # Minuscules uniquement : hexdigest() produit des minuscules, une empreinte en
        # majuscules ne correspondrait jamais et refuserait tous les clients en silence.
        if value and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise RuntimeError(f"{name} doit contenir exactement 64 caractères hexadécimaux minuscules")
    if INGEST_API_KEY_SHA256 and ADMIN_API_KEY_SHA256 and hmac.compare_digest(
        INGEST_API_KEY_SHA256.encode("ascii"), ADMIN_API_KEY_SHA256.encode("ascii")
    ):
        raise RuntimeError("IAQ_INGEST_API_KEY_SHA256 et IAQ_ADMIN_API_KEY_SHA256 doivent être distincts")

    missing_auth = [name for name, value in verifiers if not value]
    if missing_auth and not DEBUG:
        raise RuntimeError(
            "Configuration de production incomplète : " + ", ".join(missing_auth)
        )

    if EMAIL_ALERTS_ENABLED:
        missing_email = [
            name for name, value in (
                ("EMAIL_SENDER", EMAIL_SENDER),
                ("EMAIL_PASSWORD", EMAIL_PASSWORD),
                ("EMAIL_RECEIVER", EMAIL_RECEIVER),
            ) if not value
        ]
        if missing_email:
            raise RuntimeError(
                "Alertes email activées sans configuration complète : "
                + ", ".join(missing_email)
            )


validate_runtime_configuration()

# ── Flask : Le mini-serveur web ──
from flask import Flask, request, jsonify, render_template, g, Response # type: ignore
#   Flask            → crée l'application web
#   request          → lit ce que le client (ESP32 ou navigateur) envoie
#   jsonify          → transforme un dictionnaire Python en réponse JSON
#   render_template  → charge un fichier HTML depuis le dossier templates/
#   g                → stockage temporaire lié à UNE requête (ex: connexion BDD)
#   Response         → crée une réponse HTTP personnalisée (ex: CSV, HTML 404)

from flask_limiter import Limiter                # type: ignore  # Anti-spam : limite les requêtes par minute
from flask_limiter.util import get_remote_address  # type: ignore  # Identifie le client par son adresse IP
from flask_cors import CORS                      # type: ignore  # Autorise les requêtes venant d'autres domaines
from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore  # Planificateur (nettoyage BDD à 3h)
import atexit                        # Pour exécuter du code proprement à l'arrêt du serveur
from flask_socketio import SocketIO  # type: ignore  # WebSocket : pousse les mises à jour en temps réel
from flask_compress import Compress  # type: ignore  # Compresse les réponses HTTP (Gzip) pour aller plus vite

# ─── Création de l'application Flask ──────────────────────────────────────────
app = Flask(__name__)   # "__name__" dit à Flask que les templates sont dans le même dossier
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # 64 Kio : suffisant pour un lot de 100 mesures
Compress(app)           # Active la compression Gzip automatique de toutes les réponses
if ALLOWED_ORIGINS:
    CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}})

socketio_options = {}
if ALLOWED_ORIGINS:
    socketio_options["cors_allowed_origins"] = ALLOWED_ORIGINS
socketio = SocketIO(app, **socketio_options)

# ─── Bouclier anti-spam (Rate Limiter) ────────────────────────────────────────
# Évite qu'un client envoie des milliers de requêtes par minute et sature le serveur.
# Chaque adresse IP est comptée séparément grâce à get_remote_address.
limiter = Limiter(
    get_remote_address,          # Identifie chaque client par son IP
    app=app,
    default_limits=["200 per minute"],  # Règle globale : max 200 requêtes/min pour toutes les routes
    storage_uri="memory://"      # Compteurs stockés en RAM (suffisant pour un seul serveur)
)

# ─── Paramètres non secrets ───────────────────────────────────────────────────
DATA_RETENTION_DAYS = 30            # Les données > 30 jours sont supprimées automatiquement
SENSOR_OFFLINE_MINUTES = 5          # Si aucune mesure depuis 5 min → capteur affiché "HORS LIGNE"

# ─── Seuils d'alerte ──────────────────────────────────────────────────────────
# Ces valeurs sont comparées à chaque mesure reçue.
# "warn" = attention (jaune), "alert" = danger (rouge + email envoyé).
THRESHOLDS = {
    "co2":         {"warn": 1000, "alert": 2000},   # CO2 en ppm  (air confiné > 2000)
    "tvoc":        {"warn": 300,  "alert": 600},     # TVOC en ppb (composés organiques volatils)
    "co":          {"warn": 9,    "alert": 35},      # CO en ppm   (> 35 = évacuer !)
    "temperature": {"warn": 28,   "alert": 35},      # Température en °C
    "humidite":    {"warn": 60,   "alert": 75},      # Humidité en %
}

# ─── Plages de valeurs physiquement possibles ─────────────────────────────────
# Si l'ESP32 envoie une valeur hors de ces limites (bug, capteur défaillant), elle est rejetée.
VALID_RANGES = {
    "co2":         (0, 10000),   # CO2 de 50000 ppm dans l'air = capteur défaillant
    "tvoc":        (0, 30000),
    "co":          (0, 500),
    "temperature": (-40, 85),
    "humidite":    (0, 100),
}

# ─── Noms et unités des capteurs ──────────────────────────────────────────────
SENSOR_FIELDS = ["co2", "tvoc", "co", "temperature", "humidite"]  # Ordre des colonnes en BDD

SENSOR_LABELS = {
    "co2": "CO₂", "tvoc": "TVOC", "co": "CO",
    "temperature": "Température", "humidite": "Humidité",
}

SENSOR_UNITS = {
    "co2": "ppm", "tvoc": "ppb", "co": "ppm",
    "temperature": "°C", "humidite": "%",
}

# ─── Logging (messages de debug dans la console) ──────────────────────────────
# En DEBUG=True → tous les messages visibles. En production → INFO et au-dessus seulement.
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",  # Ex: 2026-03-23 14:30:00 [INFO] Mesure reçue
)
log = logging.getLogger("iaq")  # Canal de log nommé "iaq" pour filtrer les messages


# ─── Base de données SQLite ────────────────────────────────────────────────────
# SQLite est une base de données stockée dans UN SEUL FICHIER (iaq.db).
# Elle ne nécessite aucune installation ou serveur séparé.

def get_db():
    """
    Ouvre et retourne une connexion à SQLite.
    La connexion est réutilisée tout au long d'UNE même requête HTTP (stockée dans 'g').
    'g' est un objet Flask spécial qui vit le temps d'une seule requête, puis est détruit.
    """
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row  # Accès par nom : row["co2"] au lieu de row[2]
    return g.db


@app.teardown_appcontext
def close_db(error):
    """
    Ferme automatiquement la connexion BDD à la fin de chaque requête HTTP.
    Appelé par Flask même si une erreur s'est produite.
    """
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """
    Crée les tables de la base de données si elles n'existent pas encore.
    Appelé UNE SEULE FOIS au démarrage du serveur.

    Table 'mesures'  : stocke chaque relevé capteur (co2, tvoc, co, temp, hum + timestamp).
    Table 'alertes'  : stocke chaque dépassement de seuil (capteur, niveau, valeur, message).
    Index 'timestamp': accélère les requêtes filtrées par date (sinon scan complet de la table).
    """
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mesures (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,  -- Identifiant unique auto-incrémenté
                timestamp   TEXT    NOT NULL,                    -- Format "YYYY-MM-DD HH:MM:SS"
                co2         REAL,                                -- NULL si capteur absent ou invalide
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
                capteur     TEXT    NOT NULL,  -- ex: "co2", "co", "temperature"
                niveau      TEXT    NOT NULL,  -- "warn" (attention jaune) ou "alert" (danger rouge)
                valeur      REAL,              -- Valeur mesurée au moment de l'alerte
                seuil       REAL,              -- Seuil qui a été dépassé
                message     TEXT               -- ex: "ALERTE CO2 : 2350 ppm (seuil : 2000 ppm)"
            )
        """)
        # Les index accélèrent considérablement les requêtes "WHERE timestamp >= ..."
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mesures_timestamp ON mesures(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alertes_timestamp ON alertes(timestamp)")
        conn.commit()
    log.info("Database initialized: %s", DATABASE)


def cleanup_old_data():
    """
    Supprime les données plus vieilles que DATA_RETENTION_DAYS (30 jours par défaut).
    Appelée automatiquement chaque jour à 3h00 du matin par le planificateur.
    Évite que la base de données grossisse indéfiniment sur le disque.
    """
    cutoff = (datetime.now() - timedelta(days=DATA_RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DATABASE) as conn:
        d1 = conn.execute("DELETE FROM mesures WHERE timestamp < ?", (cutoff,)).rowcount
        d2 = conn.execute("DELETE FROM alertes WHERE timestamp < ?", (cutoff,)).rowcount
        conn.commit()
    if d1 + d2 > 0:
        log.info("Cleaned up %d mesures + %d alertes (before %s)", d1, d2, cutoff)


# ─── Validation des données reçues ────────────────────────────────────────────
# Ces fonctions vérifient que les valeurs envoyées par l'ESP32 sont cohérentes.
# Elles protègent la base de données contre les erreurs capteur et les injections.

def validate_sensor_value(key, value):
    """
    Valide UNE valeur capteur (ex: co2=1500).
    Retourne (valeur_nettoyée, None) si OK.
    Retourne (None, "message d'erreur") si la valeur est invalide.
    """
    if value is None:
        return None, None  # Valeur absente = autorisé (capteur non branché)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, f"{key}: valeur non numérique"
    # Seuls les float peuvent être NaN/infini ; un entier JSON géant ferait planter isfinite().
    if isinstance(value, float) and not math.isfinite(value):
        return None, f"{key}: valeur non finie"
    lo, hi = VALID_RANGES[key]
    if value < lo or value > hi:
        return None, f"{key}: {value} hors plage [{lo}, {hi}]"
    return float(value), None  # Conversion en float pour uniformiser (int 1500 → float 1500.0)


def validate_measurement(data):
    """
    Valide les 5 champs capteur d'un JSON reçu.
    Retourne (dict_nettoyé, liste_erreurs).
    Les champs absents ou invalides sont mis à None (stockés comme NULL en BDD).
    """
    cleaned = {}
    errors = []
    for key in SENSOR_FIELDS:
        raw = data.get(key)           # Lit la valeur du JSON (None si absente)
        val, err = validate_sensor_value(key, raw)
        cleaned[key] = val
        if err:
            errors.append(err)
    return cleaned, errors


_MISSING = object()
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
TIMESTAMP_LENGTH = 19


def validate_timestamp(value=_MISSING):
    """Valide l'horodatage ESP32 ou génère celui du serveur si le champ est absent."""
    if value is _MISSING:
        return datetime.now().strftime(TIMESTAMP_FORMAT), None
    if not isinstance(value, str):
        return None, "timestamp: chaîne attendue au format YYYY-MM-DD HH:MM:SS"
    if len(value) != TIMESTAMP_LENGTH:
        return None, "timestamp: longueur ou format invalide"
    try:
        parsed = datetime.strptime(value, TIMESTAMP_FORMAT)
    except ValueError:
        return None, "timestamp: date invalide, format attendu YYYY-MM-DD HH:MM:SS"
    if parsed.strftime(TIMESTAMP_FORMAT) != value:
        return None, "timestamp: format non canonique"
    return value, None


# ─── Envoi d'email d'alerte (en arrière-plan) ─────────────────────────────────

_EMAIL_WORKER_LIMIT = 2
_email_slots = threading.BoundedSemaphore(_EMAIL_WORKER_LIMIT)
_email_state_lock = threading.Lock()
_last_email_by_key = {}


def send_email_alert_async(subject, body, dedup_key):
    """
    Envoie un email Gmail d'alerte SANS BLOQUER le serveur Flask.
    L'envoi SMTP peut prendre 1-3 secondes. On le fait dans un thread séparé
    pour que la réponse HTTP à l'ESP32 reste rapide (< 200ms).
    Si EMAIL_ALERTS_ENABLED = False, la fonction ne fait rien.
    """
    if not EMAIL_ALERTS_ENABLED:
        return False

    now = time.monotonic()
    with _email_state_lock:
        last_sent = _last_email_by_key.get(dedup_key)
        if last_sent is not None and now - last_sent < EMAIL_ALERT_COOLDOWN_SECONDS:
            log.info("Email d'alerte ignoré pendant le cooldown pour %s", dedup_key)
            return False
        if not _email_slots.acquire(blocking=False):
            log.warning("Email d'alerte ignoré : limite de %d envois simultanés", _EMAIL_WORKER_LIMIT)
            return False
        _last_email_by_key[dedup_key] = now

    def send_email():
        try:
            # Construction du message email (format MIME standard)
            msg = MIMEMultipart()
            msg['From']    = EMAIL_SENDER
            msg['To']      = EMAIL_RECEIVER
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain', 'utf-8'))  # Corps en texte brut, encodage UTF-8

            # STARTTLS avec validation du certificat et du nom d'hôte.
            tls_context = ssl.create_default_context()
            with smtplib.SMTP('smtp.gmail.com', 587, timeout=10) as server:
                server.ehlo()
                server.starttls(context=tls_context)
                server.ehlo()
                server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                server.send_message(msg)
            log.info("Email d'alerte envoyé avec succès.")
        except Exception as e:
            log.error("Erreur critique lors de l'envoi de l'email Gmail : %s", e)
        finally:
            _email_slots.release()

    # daemon=True : le thread est tué automatiquement si le serveur s'arrête
    try:
        threading.Thread(target=send_email, daemon=True).start()
    except Exception:
        with _email_state_lock:
            if _last_email_by_key.get(dedup_key) == now:
                _last_email_by_key.pop(dedup_key, None)
        _email_slots.release()
        log.exception("Impossible de démarrer le thread d'alerte email")
        return False
    return True


# ─── Vérification des seuils d'alerte ─────────────────────────────────────────

def verifier_alertes(cleaned, ts):
    """Vérifie les seuils et enregistre les alertes dans la base."""
    # Compare chaque valeur capteur aux seuils définis dans THRESHOLDS.
    # Pour chaque dépassement : insère en BDD + envoie email si niveau "alert".
    # Retourne un dict de statuts renvoyé à l'ESP32 pour déclencher buzzer/ventilateur.
    db = get_db()
    status = {}

    for key in SENSOR_FIELDS:
        val = cleaned[key]
        if val is None:
            continue  # Capteur absent ou invalide → on ignore
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
            continue  # Valeur normale → pas d'alerte à enregistrer

        # Construction du message lisible (stocké en BDD et affiché dans le tableau de bord)
        label     = SENSOR_LABELS[key]
        unit      = SENSOR_UNITS[key]
        niveau_fr = "ALERTE" if niveau == "alert" else "ATTENTION"
        message   = f"{niveau_fr} {label} : {val} {unit} (seuil : {seuil} {unit})"

        db.execute(
            """INSERT INTO alertes (timestamp, capteur, niveau, valeur, seuil, message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ts, key, niveau, val, seuil, message),
        )

        # Email uniquement pour les alertes rouges (pas pour les warnings jaunes)
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
            send_email_alert_async(sujet, corps, key)

    db.commit()
    return status


# ─── Décorateur d'authentification par clé API ────────────────────────────────
# Un décorateur Python est une fonction qui "enveloppe" une autre fonction.
# Placer @require_api_key devant une route bloque les requêtes sans le bon header X-API-KEY.
# Si la clé est absente ou fausse → réponse 401 (Non autorisé) immédiate.

def require_api_key(expected_sha256, role):
    """Crée un décorateur lié à l'empreinte SHA-256 du rôle demandé (ingestion ou administration)."""
    def decorator(f):
        @wraps(f)  # @wraps conserve le nom de la fonction d'origine (important pour Flask)
        def decorated_function(*args, **kwargs):
            if not expected_sha256:
                log.error("Empreinte de clé API %s absente de la configuration", role)
                return jsonify({"erreur": "Service temporairement indisponible."}), 503

            supplied_key = request.headers.get("X-API-KEY")
            # On hache la clé BRUTE reçue avant de comparer. Envoyer l'empreinte elle-même
            # échoue donc : lire la variable Render ne suffit pas pour s'authentifier.
            if supplied_key is None or not hmac.compare_digest(
                hashlib.sha256(supplied_key.encode("utf-8")).hexdigest().encode("ascii"),
                expected_sha256.encode("ascii"),
            ):
                log.warning("Accès %s refusé depuis %s", role, request.remote_addr)
                return jsonify({"erreur": "Non autorisé. Clé API manquante ou invalide."}), 401
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ─── Routes (les "pages" du serveur) ──────────────────────────────────────────
# Chaque @app.route("...") définit une URL que le serveur sait gérer.
# GET = lecture (navigateur ou ESP32 qui lit des données)
# POST = écriture (ESP32 qui envoie des mesures)

@app.route("/")
def index():
    """
    Page principale : sert le tableau de bord HTML.
    Flask charge templates/index.html et injecte les variables Python (Jinja2).
    Les seuils, labels et unités sont injectés pour que le JS du navigateur les connaisse.
    """
    return render_template(
        "index.html",
        thresholds=THRESHOLDS,            # Seuils pour les lignes d'annotation sur les graphiques
        offline_minutes=SENSOR_OFFLINE_MINUTES,  # Délai avant "HORS LIGNE"
        sensor_labels=SENSOR_LABELS,      # Noms affichés : "CO₂", "Température"...
        sensor_units=SENSOR_UNITS,        # Unités : "ppm", "°C"...
        debug=DEBUG,                      # True → affiche le bouton "Données test"
    )


@app.route("/api/health")
def health():
    """
    Route de "ping" : vérifie que le serveur est en ligne.
    L'ESP32 appelle cette URL avant chaque envoi. Si le serveur ne répond pas,
    les données sont sauvegardées dans LittleFS pour envoi ultérieur.
    """
    return jsonify({"statut": "ok", "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


@app.route("/api/mesures", methods=["POST"])
@require_api_key(INGEST_API_KEY_SHA256, "ingestion")
@limiter.limit("30 per minute")  # Max 30 POST par minute par IP (protection anti-spam)
def recevoir_mesures():
    """
    Route principale : reçoit les mesures de l'ESP32 en JSON.
    Accepte deux formats :
      - Objet unique  : {"co2": 1200, "tvoc": 150, ...}
      - Tableau batch : [{"co2": 1200, ...}, {"co2": 1300, ...}]
    Le mode batch est utilisé quand l'ESP32 renvoie ses données bufferisées (LittleFS).
    """
    data = request.get_json(silent=True)  # silent=True = ne plante pas si le JSON est malformé
    if not data:
        return jsonify({"erreur": "Corps JSON manquant"}), 400

    if isinstance(data, list):
        return _insert_batch(data)    # Tableau → insertion en lot
    if not isinstance(data, dict):
        return jsonify({"erreur": "Le JSON doit être un objet ou un tableau d'objets"}), 400
    return _insert_single(data)       # Objet unique → insertion simple


def _insert_single(data):
    """
    Insère UNE mesure dans la BDD.
    Séquence : validation → insertion → vérification alertes → push WebSocket → réponse 201.
    """
    if not any(k in data for k in SENSOR_FIELDS):
        return jsonify({"erreur": "Aucun champ reconnu dans le JSON"}), 400

    cleaned, errors = validate_measurement(data)
    if errors:
        log.warning("Validation errors from %s: %s", request.remote_addr, errors)
        return jsonify({"erreur": "Valeurs invalides", "details": errors}), 400

    # Utilise le timestamp NTP envoyé par l'ESP32, ou génère l'heure serveur si absent.
    ts, timestamp_error = validate_timestamp(data.get("timestamp", _MISSING))
    if timestamp_error:
        return jsonify({"erreur": "Timestamp invalide", "details": [timestamp_error]}), 400

    db = get_db()
    db.execute(
        """INSERT INTO mesures (timestamp, co2, tvoc, co, temperature, humidite)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ts, cleaned["co2"], cleaned["tvoc"], cleaned["co"],
         cleaned["temperature"], cleaned["humidite"]),
    )
    db.commit()

    # Vérification des seuils → peut insérer dans 'alertes' et envoyer un email
    status = verifier_alertes(cleaned, ts)

    # Notification WebSocket : le navigateur rafraîchit les graphiques immédiatement
    socketio.emit('update_needed', namespace='/')

    log.info("Mesure reçue de %s", request.remote_addr)
    return jsonify({"statut": "ok", "timestamp": ts, "alertes": status}), 201


def _insert_batch(data_list):
    """
    Insère un LOT de mesures (buffer LittleFS de l'ESP32 après reconnexion WiFi).
    Maximum 100 mesures par lot pour ne pas saturer la RAM.
    Les mesures invalides sont ignorées avec un message d'erreur dans la réponse.
    """
    if len(data_list) > 100:
        return jsonify({"erreur": "Lot trop grand, max 100 mesures"}), 400

    # Une erreur d'horodatage invalide tout le lot afin d'éviter une insertion partielle ambiguë.
    timestamp_errors = []
    for i, item in enumerate(data_list):
        if isinstance(item, dict):
            _, timestamp_error = validate_timestamp(item.get("timestamp", _MISSING))
            if timestamp_error:
                timestamp_errors.append(f"Element {i}: {timestamp_error}")
    if timestamp_errors:
        return jsonify({"erreur": "Timestamp invalide", "details": timestamp_errors}), 400

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

        ts, _ = validate_timestamp(data.get("timestamp", _MISSING))

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

    # Notification WebSocket si au moins une mesure a été insérée
    if inserted > 0:
        socketio.emit('update_needed', namespace='/')

    result = {"statut": "ok", "lignes_inserees": inserted}
    if errors:
        return jsonify({"statut": "ok", "lignes_inserees": inserted, "erreurs": errors}), 201
    return jsonify({"statut": "ok", "lignes_inserees": inserted}), 201


@app.route("/api/data")
def lire_donnees():
    """
    Retourne les mesures brutes pour les graphiques Chart.js.
    Paramètres URL optionnels :
      ?n=2880        → nombre de points max (défaut: 2880 = 48h à 1 pt/min)
      ?from=2026-03-20&to=2026-03-23  → filtre par plage de dates
      ?page=2&per_page=1000           → pagination pour les grands exports
    Les données sont retournées du plus ancien au plus récent (ordre chronologique).
    """
    n = request.args.get("n", 2880, type=int) # 48h par defaut (1 pt/min)
    per_page = request.args.get("per_page", n, type=int)
    per_page = min(max(per_page, 1), 50000)  # Entre 1 et 50000 points max

    page = request.args.get("page", 1, type=int)
    page = max(page, 1)
    offset = (page - 1) * per_page  # Décalage pour la pagination

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
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"  # DESC + reversed ci-dessous = ordre chrono final
    params.extend([per_page, offset])

    rows = list(reversed(db.execute(query, params).fetchall()))  # Inversé pour ordre chronologique

    # Format attendu par Chart.js : {"labels": [...timestamps], "co2": [...valeurs], ...}
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
    """
    Retourne les statistiques (moyenne, min, max) par capteur pour une période donnée.
    Paramètres URL optionnels :
      ?from=2026-03-20&to=2026-03-23  → filtre par dates (défaut: dernières 24h)
    Utilisé par l'onglet "Statistiques" du tableau de bord.
    """
    date_from = request.args.get("from")
    date_to = request.args.get("to")

    # Si pas de filtre → dernières 24 heures par défaut
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

    # Construction dynamique de la requête SQL pour tous les capteurs
    # Génère : "AVG(co2) as co2_avg, MIN(co2) as co2_min, MAX(co2) as co2_max, AVG(tvoc)..."
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
    """
    Retourne l'historique des alertes, filtrable par capteur, niveau et date.
    Paramètres URL optionnels :
      ?n=50          → nombre max de résultats (défaut: 50, max: 500)
      ?capteur=co2   → filtre par capteur
      ?niveau=alert  → filtre par niveau ("warn" ou "alert")
      ?from=...&to=... → filtre par dates
    """
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

    return jsonify([dict(r) for r in rows])  # Convertit chaque Row SQLite en dictionnaire Python


# ─── Export CSV ───────────────────────────────────────────────────────────────

CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def sanitize_csv_cell(value):
    """Neutralise les cellules texte qu'un tableur pourrait interpréter comme une formule."""
    if isinstance(value, str) and value:
        significant = value.lstrip(" \t\r\n")
        if value[0] in ("\t", "\r", "\n") or significant.startswith(CSV_FORMULA_PREFIXES):
            return "'" + value
    return value


@app.route("/api/export")
def export_data():
    """
    Exporte toutes les mesures au format CSV (compatible Excel et LibreOffice Calc).
    Le fichier est généré en mémoire et envoyé directement au navigateur pour téléchargement.
    Paramètres URL optionnels : ?from=...&to=... pour filtrer par dates.
    """
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
    query += " ORDER BY timestamp ASC"  # Ordre chronologique dans le CSV

    rows = db.execute(query, params).fetchall()

    # io.StringIO() crée un "fichier" en mémoire RAM, pas sur disque
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "timestamp", "co2", "tvoc", "co", "temperature", "humidite"])  # En-tête
    for r in rows:
        writer.writerow([sanitize_csv_cell(cell) for cell in r])

    # Nom de fichier avec date/heure pour éviter les écrasements
    filename = f"iaq_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ─── Infos capteurs ──────────────────────────────────────────────────────────

@app.route("/infos")
def infos():
    """Page éducative : explique chaque capteur et ses seuils de santé."""
    return render_template("infos.html")


# ─── Vider la base ───────────────────────────────────────────────────────────

@app.route("/api/clear", methods=["POST"])
@require_api_key(ADMIN_API_KEY_SHA256, "administration")
def clear_data():
    """
    Supprime TOUTES les mesures et alertes de la base de données.
    Utilisé par le bouton "Vider la base" du tableau de bord.
    Retourne le nombre de lignes supprimées pour confirmation.
    """
    db = get_db()
    d1 = db.execute("DELETE FROM mesures").rowcount
    d2 = db.execute("DELETE FROM alertes").rowcount
    db.commit()
    log.info("Database cleared: %d mesures + %d alertes", d1, d2)
    return jsonify({"statut": "ok", "mesures_supprimees": d1, "alertes_supprimees": d2})


# ─── Données de test ─────────────────────────────────────────────────────────

@app.route("/api/seed", methods=["POST"])
@require_api_key(ADMIN_API_KEY_SHA256, "administration")
def seed():
    """
    Génère 1440 mesures de test (= 24h à 1 mesure/min) avec données réalistes simulées.
    Uniquement disponible si DEBUG = True (désactivé en production sur Render).
    Utilisé par le bouton "Données test" pour tester le dashboard sans ESP32.
    Inclut un pic de CO2 entre 10h et 11h et un pic de température entre 15h et 16h.
    """
    if not DEBUG:
        return jsonify({"erreur": "Endpoint désactivé en production"}), 403

    import random

    db = get_db()

    # On vide avant de remplir pour éviter les doublons
    db.execute("DELETE FROM mesures")
    db.execute("DELETE FROM alertes")

    base_time = datetime.now().timestamp() - 86400  # Il y a 24h exactement
    for i in range(1440):
        ts = datetime.fromtimestamp(base_time + i * 60).strftime("%Y-%m-%d %H:%M:%S")

        # Valeurs simulées avec bruit gaussien + pics réalistes
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

        # Génération des alertes correspondantes (pour peupler l'onglet alertes)
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
    socketio.emit('update_needed', namespace='/')  # Notifie le navigateur pour rafraîchir
    return jsonify({"statut": "ok", "lignes_inserees": 1440})


# ─── 404 ─────────────────────────────────────────────────────────────────────

@app.errorhandler(413)
def request_too_large(error):
    """Retourne une erreur JSON claire quand un payload dépasse 64 Kio."""
    return jsonify({"erreur": "Corps de requête trop volumineux (maximum 64 Kio)."}), 413


@app.errorhandler(404)
def not_found(error):
    """
    Gestionnaire d'erreur 404 (page non trouvée).
    Retourne du JSON pour les routes /api/* (utile pour l'ESP32 et les outils de test).
    Retourne une page HTML minimaliste pour toutes les autres URLs.
    """
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


# ─── Démarrage du serveur ──────────────────────────────────────────────────────

# 1. Crée les tables SQLite au premier lancement (ou vérifie qu'elles existent)
init_db()

# 2. Planificateur automatique : nettoie les vieilles données chaque nuit à 3h00
#    daemon=True → le planificateur est tué proprement si le serveur s'arrête
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(func=cleanup_old_data, trigger="cron", hour=3, minute=0)
scheduler.start()
def shutdown_scheduler():
    scheduler.shutdown()
atexit.register(shutdown_scheduler) # type: ignore  # Appelé automatiquement à l'arrêt du serveur

if __name__ == "__main__":
    # Nettoyage immédiat au démarrage (au cas où des données trop vieilles traîneraient)
    cleanup_old_data()
    # PORT est défini par Render automatiquement. En local → 5000 sur loopback.
    port = int(os.environ.get("PORT", 5000))
    # Définir explicitement DEV_HOST=0.0.0.0 pour exposer le serveur de développement au LAN.
    socketio.run(app, host=DEV_HOST, port=port, debug=DEBUG)
