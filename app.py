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
import io                           # Pour créer un fichier CSV en mémoire sans écrire sur disque
import os                           # Pour lire les variables d'environnement (DB_PATH, PORT sur Render)
import sqlite3                      # Base de données légère intégrée à Python (aucun serveur requis)
import logging                      # Pour afficher des messages de debug/info/erreur dans la console
from datetime import datetime, timedelta  # Pour manipuler les dates (horodatage, rétention 30j)
from functools import wraps         # Pour créer des décorateurs Python (ex: @require_api_key)
import smtplib                      # Pour envoyer des emails via le protocole SMTP (Gmail)
from email.mime.text import MIMEText           # Pour formater le corps de l'email
from email.mime.multipart import MIMEMultipart # Pour créer un email avec sujet + corps
import threading                    # Pour envoyer les emails EN ARRIÈRE-PLAN sans bloquer Flask

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
Compress(app)           # Active la compression Gzip automatique de toutes les réponses
CORS(app)               # Autorise les requêtes inter-domaines (si le frontal est hébergé ailleurs)
socketio = SocketIO(app, cors_allowed_origins="*")  # Canal WebSocket temps réel (accepte tous les domaines)

# ─── Bouclier anti-spam (Rate Limiter) ────────────────────────────────────────
# Évite qu'un client envoie des milliers de requêtes par minute et sature le serveur.
# Chaque adresse IP est comptée séparément grâce à get_remote_address.
limiter = Limiter(
    get_remote_address,          # Identifie chaque client par son IP
    app=app,
    default_limits=["200 per minute"],  # Règle globale : max 200 requêtes/min pour toutes les routes
    storage_uri="memory://"      # Compteurs stockés en RAM (suffisant pour un seul serveur)
)

# ─── Configuration (modifie directement ici) ──────────────────────────────────
# Sur Render Cloud, DB_PATH est défini comme variable d'environnement
# pour pointer vers le disque persistant (/data/iaq.db).
DATABASE = os.environ.get("DB_PATH", "iaq.db")  # Chemin vers la base de données SQLite
API_KEY = "SECRET_IAQ_2026"        # Clé secrète : l'ESP32 doit l'envoyer dans chaque POST
DEBUG = False                       # False = production. True = active /api/seed + logs détaillés
DATA_RETENTION_DAYS = 30            # Les données > 30 jours sont supprimées automatiquement
SENSOR_OFFLINE_MINUTES = 5          # Si aucune mesure depuis 5 min → capteur affiché "HORS LIGNE"

# ── Configuration Email Gmail ──
# Pour que les emails fonctionnent : générer un "Mot de passe d'application" Google (16 lettres).
# Se créer sur : https://myaccount.google.com/apppasswords
EMAIL_ALERTS_ENABLED = True
EMAIL_SENDER   = "votre.email@gmail.com"                      # <-- REMPLACER : adresse Gmail "robot"
EMAIL_PASSWORD = "votre_mot_de_passe_application_google"      # <-- REMPLACER : mot de passe d'appli (16 lettres)
EMAIL_RECEIVER = "destinataire@gmail.com"                     # <-- REMPLACER : adresse qui reçoit les alertes

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
    if not isinstance(value, (int, float)):
        return None, f"{key}: valeur non numérique"
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


# ─── Envoi d'email d'alerte (en arrière-plan) ─────────────────────────────────

def send_email_alert_async(subject, body):
    """
    Envoie un email Gmail d'alerte SANS BLOQUER le serveur Flask.
    L'envoi SMTP peut prendre 1-3 secondes. On le fait dans un thread séparé
    pour que la réponse HTTP à l'ESP32 reste rapide (< 200ms).
    Si EMAIL_ALERTS_ENABLED = False, la fonction ne fait rien.
    """
    if not EMAIL_ALERTS_ENABLED:
        return

    def send_email():
        try:
            # Construction du message email (format MIME standard)
            msg = MIMEMultipart()
            msg['From']    = EMAIL_SENDER
            msg['To']      = EMAIL_RECEIVER
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain', 'utf-8'))  # Corps en texte brut, encodage UTF-8

            # Connexion au serveur Gmail via SMTP avec chiffrement TLS (port 587)
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()                               # Active le chiffrement
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)      # Authentification Google
            server.send_message(msg)
            server.quit()
            log.info("Email d'alerte envoyé avec succès à %s !", EMAIL_RECEIVER)
        except Exception as e:
            log.error("Erreur critique lors de l'envoi de l'email Gmail : %s", e)

    # daemon=True : le thread est tué automatiquement si le serveur s'arrête
    threading.Thread(target=send_email, daemon=True).start()


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
            send_email_alert_async(sujet, corps)

    db.commit()
    return status


# ─── Décorateur d'authentification par clé API ────────────────────────────────
# Un décorateur Python est une fonction qui "enveloppe" une autre fonction.
# Placer @require_api_key devant une route bloque les requêtes sans le bon header X-API-KEY.
# Si la clé est absente ou fausse → réponse 401 (Non autorisé) immédiate.

def require_api_key(f):
    @wraps(f)  # @wraps conserve le nom de la fonction d'origine (important pour Flask)
    def decorated_function(*args, **kwargs):
        if request.headers.get("X-API-KEY") != API_KEY:
            log.warning("Accès refusé depuis %s (Mauvaise clé API)", request.remote_addr)
            return jsonify({"erreur": "Non autorisé. Clé API manquante ou invalide."}), 401
        return f(*args, **kwargs)
    return decorated_function

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
@require_api_key               # Vérification de la clé API AVANT d'exécuter la fonction
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

    # Utilise le timestamp NTP envoyé par l'ESP32, ou génère l'heure serveur si absent
    ts = data.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
        writer.writerow([r["id"], r["timestamp"],
                         r["co2"], r["tvoc"], r["co"], r["temperature"], r["humidite"]])

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
@require_api_key  # Protégé : seul quelqu'un avec la bonne clé peut tout effacer
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
@require_api_key
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
    # PORT est défini par Render automatiquement. En local → 5000.
    port = int(os.environ.get("PORT", 5000))
    # socketio.run() remplace app.run() pour activer le support WebSocket
    socketio.run(app, host="0.0.0.0", port=port, debug=DEBUG, allow_unsafe_werkzeug=True)
