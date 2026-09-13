import hashlib
import math
import os
from pathlib import Path
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


TEST_DIRECTORY = tempfile.TemporaryDirectory()
# Les clients gardent la clé brute ; le serveur ne reçoit que son empreinte SHA-256.
TEST_INGEST_KEY = "EXAMPLE_TEST_INGEST_KEY"
TEST_ADMIN_KEY = "EXAMPLE_TEST_ADMIN_KEY"
TEST_INGEST_SHA256 = hashlib.sha256(TEST_INGEST_KEY.encode("utf-8")).hexdigest()
TEST_ADMIN_SHA256 = hashlib.sha256(TEST_ADMIN_KEY.encode("utf-8")).hexdigest()
os.environ["IAQ_INGEST_API_KEY_SHA256"] = TEST_INGEST_SHA256
os.environ["IAQ_ADMIN_API_KEY_SHA256"] = TEST_ADMIN_SHA256
os.environ["EMAIL_ALERTS_ENABLED"] = "false"
os.environ["DB_PATH"] = str(Path(TEST_DIRECTORY.name) / "test-iaq.db")
os.environ["FLASK_DEBUG"] = "true"
os.environ["ALLOWED_ORIGINS"] = ""

import app as iaq  # noqa: E402  (l'environnement doit être défini avant l'import)


class SecurityRegressionTests(unittest.TestCase):
    def setUp(self):
        iaq.app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
        self.client = iaq.app.test_client()
        with sqlite3.connect(iaq.DATABASE) as connection:
            connection.execute("DELETE FROM alertes")
            connection.execute("DELETE FROM mesures")

    @staticmethod
    def measurement(**overrides):
        data = {
            "co2": 800,
            "tvoc": 100,
            "co": 4,
            "temperature": 22,
            "humidite": 45,
        }
        data.update(overrides)
        return data

    def post_measurement(self, data, key=TEST_INGEST_KEY):
        return self.client.post(
            "/api/mesures",
            json=data,
            headers={"X-API-KEY": key},
        )

    def count_measurements(self):
        with sqlite3.connect(iaq.DATABASE) as connection:
            return connection.execute("SELECT COUNT(*) FROM mesures").fetchone()[0]

    def test_ingestion_and_admin_keys_are_not_interchangeable(self):
        denied_ingest = self.post_measurement(
            self.measurement(), key=TEST_ADMIN_KEY
        )
        self.assertEqual(denied_ingest.status_code, 401)

        accepted = self.post_measurement(self.measurement())
        self.assertEqual(accepted.status_code, 201)

        denied_clear = self.client.post(
            "/api/clear", headers={"X-API-KEY": TEST_INGEST_KEY}
        )
        self.assertEqual(denied_clear.status_code, 401)

        accepted_clear = self.client.post(
            "/api/clear", headers={"X-API-KEY": TEST_ADMIN_KEY}
        )
        self.assertEqual(accepted_clear.status_code, 200)

    def test_raw_keys_authenticate_only_their_own_role(self):
        self.assertEqual(self.post_measurement(self.measurement()).status_code, 201)
        self.assertEqual(
            self.client.post("/api/clear", headers={"X-API-KEY": TEST_ADMIN_KEY}).status_code,
            200,
        )

    def test_missing_or_wrong_key_returns_401(self):
        for headers in ({}, {"X-API-KEY": ""}, {"X-API-KEY": "EXAMPLE_WRONG_KEY"}):
            with self.subTest(headers=headers):
                ingest = self.client.post("/api/mesures", json=self.measurement(), headers=headers)
                self.assertEqual(ingest.status_code, 401)
                clear = self.client.post("/api/clear", headers=headers)
                self.assertEqual(clear.status_code, 401)
        self.assertEqual(self.count_measurements(), 0)

    def test_configured_sha256_verifier_is_not_a_valid_api_key(self):
        for verifier in (TEST_INGEST_SHA256, TEST_INGEST_SHA256.upper()):
            with self.subTest(verifier=verifier[:8]):
                response = self.post_measurement(self.measurement(), key=verifier)
                self.assertEqual(response.status_code, 401)
        for verifier in (TEST_ADMIN_SHA256, TEST_ADMIN_SHA256.upper()):
            with self.subTest(verifier=verifier[:8]):
                response = self.client.post("/api/clear", headers={"X-API-KEY": verifier})
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.count_measurements(), 0)

    def test_production_configuration_rejects_missing_malformed_or_identical_verifiers(self):
        original = (
            iaq.DEBUG, iaq.INGEST_API_KEY_SHA256, iaq.ADMIN_API_KEY_SHA256, iaq.ALLOWED_ORIGINS
        )
        try:
            iaq.DEBUG = False
            iaq.INGEST_API_KEY_SHA256 = TEST_INGEST_SHA256
            iaq.ADMIN_API_KEY_SHA256 = TEST_ADMIN_SHA256
            iaq.validate_runtime_configuration()  # configuration valide : aucune exception

            for ingest, admin in (("", ""), (TEST_INGEST_SHA256, ""), ("", TEST_ADMIN_SHA256)):
                with self.subTest(missing=(bool(ingest), bool(admin))):
                    iaq.INGEST_API_KEY_SHA256, iaq.ADMIN_API_KEY_SHA256 = ingest, admin
                    with self.assertRaises(RuntimeError):
                        iaq.validate_runtime_configuration()

            iaq.INGEST_API_KEY_SHA256 = TEST_INGEST_SHA256
            iaq.ADMIN_API_KEY_SHA256 = TEST_INGEST_SHA256
            with self.assertRaises(RuntimeError):
                iaq.validate_runtime_configuration()

            iaq.ADMIN_API_KEY_SHA256 = TEST_ADMIN_SHA256
            iaq.ALLOWED_ORIGINS = ["https://*.example.invalid"]
            with self.assertRaises(RuntimeError):
                iaq.validate_runtime_configuration()
        finally:
            (
                iaq.DEBUG, iaq.INGEST_API_KEY_SHA256, iaq.ADMIN_API_KEY_SHA256, iaq.ALLOWED_ORIGINS
            ) = original

    def test_malformed_verifiers_are_rejected_even_in_debug(self):
        malformed = (
            TEST_INGEST_SHA256[:-1],            # 63 caractères
            TEST_INGEST_SHA256 + "0",           # 65 caractères
            TEST_INGEST_SHA256.upper(),         # majuscules
            "g" * 64,                           # non hexadécimal
            " " + TEST_INGEST_SHA256[1:],       # espace parasite
            TEST_INGEST_KEY,                    # clé brute collée par erreur
        )
        original = (iaq.DEBUG, iaq.INGEST_API_KEY_SHA256, iaq.ADMIN_API_KEY_SHA256)
        try:
            iaq.DEBUG = True
            for value in malformed:
                with self.subTest(value=value[:8], length=len(value)):
                    iaq.INGEST_API_KEY_SHA256 = value
                    iaq.ADMIN_API_KEY_SHA256 = TEST_ADMIN_SHA256
                    with self.assertRaises(RuntimeError):
                        iaq.validate_runtime_configuration()
                    iaq.INGEST_API_KEY_SHA256 = TEST_INGEST_SHA256
                    iaq.ADMIN_API_KEY_SHA256 = value
                    with self.assertRaises(RuntimeError):
                        iaq.validate_runtime_configuration()
        finally:
            iaq.DEBUG, iaq.INGEST_API_KEY_SHA256, iaq.ADMIN_API_KEY_SHA256 = original

    def test_production_startup_fails_closed_on_invalid_verifiers(self):
        # Démarrage réel du module dans un processus séparé, comme gunicorn sur Render.
        # Les variables sont définies à "" (et non supprimées) pour qu'un éventuel .env
        # local ne puisse pas les compléter via load_dotenv().
        cases = {
            "missing_ingest": ("", TEST_ADMIN_SHA256),
            "missing_admin": (TEST_INGEST_SHA256, ""),
            "malformed_ingest": ("abc123", TEST_ADMIN_SHA256),
            "malformed_admin": (TEST_INGEST_SHA256, TEST_ADMIN_SHA256.upper()),
            "identical": (TEST_INGEST_SHA256, TEST_INGEST_SHA256),
        }
        for name, (ingest, admin) in cases.items():
            with self.subTest(case=name):
                env = dict(
                    os.environ,
                    FLASK_DEBUG="false",
                    IAQ_INGEST_API_KEY_SHA256=ingest,
                    IAQ_ADMIN_API_KEY_SHA256=admin,
                    DB_PATH=str(Path(TEST_DIRECTORY.name) / f"startup-{name}.db"),
                )
                result = subprocess.run(
                    [sys.executable, "-c", "import app"],
                    cwd=iaq.app.root_path,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("RuntimeError", result.stderr)
                self.assertIn("API_KEY_SHA256", result.stderr)

    def test_backend_no_longer_reads_raw_api_key_variables(self):
        source = Path(iaq.app.root_path, "app.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r'"IAQ_(INGEST|ADMIN)_API_KEY"')

    def test_absent_timestamp_gets_server_value(self):
        response = self.post_measurement(self.measurement())
        self.assertEqual(response.status_code, 201)
        timestamp = response.get_json()["timestamp"]
        self.assertEqual(len(timestamp), iaq.TIMESTAMP_LENGTH)
        self.assertIsNone(iaq.validate_timestamp(timestamp)[1])

    def test_supplied_invalid_timestamps_return_400_and_are_not_stored(self):
        invalid_values = [
            None,
            "",
            "2026-02-30 12:00:00",
            "<img src=x onerror=alert(1)>",
            "=1+1",
            123,
        ]
        for value in invalid_values:
            with self.subTest(value=value):
                response = self.post_measurement(self.measurement(timestamp=value))
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.count_measurements(), 0)

    def test_batch_with_invalid_timestamp_is_rejected_before_any_insert(self):
        response = self.post_measurement([
            self.measurement(timestamp="2026-09-13 12:00:00"),
            self.measurement(timestamp="not-a-timestamp"),
        ])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.count_measurements(), 0)

    def test_batch_size_limit_is_enforced(self):
        response = self.post_measurement([self.measurement()] * 101)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.count_measurements(), 0)

    def test_batch_keeps_existing_partial_success_behavior(self):
        response = self.post_measurement([
            self.measurement(),
            self.measurement(co2=True),
        ])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["lignes_inserees"], 1)
        self.assertEqual(self.count_measurements(), 1)

    def test_numeric_validation_rejects_bool_nan_and_infinity(self):
        for value in (True, False, math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                cleaned, error = iaq.validate_sensor_value("co2", value)
                self.assertIsNone(cleaned)
                self.assertIsNotNone(error)

        cleaned, error = iaq.validate_sensor_value("co2", 800)
        self.assertEqual(cleaned, 800.0)
        self.assertIsNone(error)

    def test_oversized_json_integer_is_rejected_without_server_error(self):
        huge_integer = "1" + "0" * 400
        for body in ('{"co2": %s}' % huge_integer, '[{"co2": %s}]' % huge_integer):
            with self.subTest(body=body[:20]):
                response = self.client.post(
                    "/api/mesures",
                    data=body,
                    content_type="application/json",
                    headers={"X-API-KEY": TEST_INGEST_KEY},
                )
                self.assertIn(response.status_code, (201, 400))
        self.assertEqual(self.count_measurements(), 0)

    def test_socketio_rejects_cross_origin_handshake_by_default(self):
        cross_origin = self.client.get(
            "/socket.io/?EIO=4&transport=polling",
            headers={"Origin": "https://untrusted.example.invalid"},
        )
        self.assertEqual(cross_origin.status_code, 400)

        same_origin = self.client.get(
            "/socket.io/?EIO=4&transport=polling",
            headers={"Origin": "http://localhost"},
        )
        self.assertEqual(same_origin.status_code, 200)

    def test_archived_http_firmware_uses_separate_local_ingestion_key(self):
        firmware = Path(iaq.app.root_path, "esp32_iaq")
        v2_source = (firmware / "esp32_iaq_v2.ino").read_text(encoding="utf-8")
        self.assertIn("const char* API_KEY = IAQ_LOCAL_INGEST_API_KEY;", v2_source)
        self.assertNotIn("= IAQ_INGEST_API_KEY;", v2_source)

        example = (firmware / "config_private.example.h").read_text(encoding="utf-8")
        self.assertRegex(example, r'#define IAQ_LOCAL_INGEST_API_KEY "EXAMPLE_ONLY_[A-Z_]+"')

        for sketch in ("esp32_iaq_fusion.ino", "esp32_iaq_nini.ino"):
            with self.subTest(sketch=sketch):
                source = (firmware / sketch).read_text(encoding="utf-8")
                self.assertIn("setCACert(IAQ_ROOT_CA)", source)
                self.assertNotIn("http://", source)

    def test_csv_export_neutralizes_legacy_formula_cells(self):
        with sqlite3.connect(iaq.DATABASE) as connection:
            connection.execute(
                """INSERT INTO mesures
                   (timestamp, co2, tvoc, co, temperature, humidite)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("=1+1", 800, 100, 4, 22, 45),
            )

        response = self.client.get("/api/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("'=1+1", response.get_data(as_text=True))

        for value in ("+SUM(A1:A2)", " -10+20", "@command", "\t=1+1", "\r=1+1"):
            with self.subTest(value=value):
                self.assertTrue(iaq.sanitize_csv_cell(value).startswith("'"))

    def test_oversized_json_returns_413(self):
        response = self.client.post(
            "/api/mesures",
            data=b'{' + b'"padding":"' + (b"x" * 70000) + b'"}',
            content_type="application/json",
            headers={"X-API-KEY": TEST_INGEST_KEY},
        )
        self.assertEqual(response.status_code, 413)

    def test_default_api_response_has_no_cross_origin_header(self):
        response = self.client.get(
            "/api/health", headers={"Origin": "https://untrusted.example.invalid"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_same_origin_dashboard_and_public_read_routes_remain_available(self):
        for path in ("/", "/infos", "/api/health", "/api/data", "/api/stats", "/api/alertes", "/api/export"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_email_uses_validating_tls_and_deduplicates(self):
        tls_contexts = []

        class ImmediateThread:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                self.target()

        class FakeSMTP:
            def __init__(self, host, port, timeout):
                self.timeout = timeout

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def ehlo(self):
                return None

            def starttls(self, context):
                tls_contexts.append(context)

            def login(self, sender, password):
                return None

            def send_message(self, message):
                return None

        original = (
            iaq.EMAIL_ALERTS_ENABLED,
            iaq.EMAIL_SENDER,
            iaq.EMAIL_PASSWORD,
            iaq.EMAIL_RECEIVER,
        )
        try:
            iaq.EMAIL_ALERTS_ENABLED = True
            iaq.EMAIL_SENDER = "sender@example.invalid"
            iaq.EMAIL_PASSWORD = "EXAMPLE_TEST_PASSWORD"
            iaq.EMAIL_RECEIVER = "receiver@example.invalid"
            iaq._last_email_by_key.clear()
            with mock.patch.object(iaq.threading, "Thread", ImmediateThread), mock.patch.object(
                iaq.smtplib, "SMTP", FakeSMTP
            ):
                self.assertTrue(iaq.send_email_alert_async("subject", "body", "co2"))
                self.assertFalse(iaq.send_email_alert_async("subject", "body", "co2"))
        finally:
            (
                iaq.EMAIL_ALERTS_ENABLED,
                iaq.EMAIL_SENDER,
                iaq.EMAIL_PASSWORD,
                iaq.EMAIL_RECEIVER,
            ) = original
            iaq._last_email_by_key.clear()

        self.assertEqual(len(tls_contexts), 1)
        self.assertIsInstance(tls_contexts[0], ssl.SSLContext)
        self.assertTrue(tls_contexts[0].check_hostname)
        self.assertEqual(tls_contexts[0].verify_mode, ssl.CERT_REQUIRED)

    def test_email_worker_count_is_bounded(self):
        pending = []

        class DeferredThread:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                pending.append(self.target)

        original = iaq.EMAIL_ALERTS_ENABLED
        try:
            iaq.EMAIL_ALERTS_ENABLED = True
            iaq._last_email_by_key.clear()
            with mock.patch.object(iaq.threading, "Thread", DeferredThread):
                self.assertTrue(iaq.send_email_alert_async("subject", "body", "co2"))
                self.assertTrue(iaq.send_email_alert_async("subject", "body", "tvoc"))
                self.assertFalse(iaq.send_email_alert_async("subject", "body", "co"))
            self.assertEqual(len(pending), iaq._EMAIL_WORKER_LIMIT)
        finally:
            for _ in pending:
                iaq._email_slots.release()
            iaq.EMAIL_ALERTS_ENABLED = original
            iaq._last_email_by_key.clear()

    def test_alert_template_uses_text_nodes_for_backend_values(self):
        template = Path(iaq.app.root_path, "templates", "index.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("list.innerHTML = data.map", template)
        self.assertIn("timestamp.textContent", template)
        self.assertIn("message.textContent", template)


if __name__ == "__main__":
    unittest.main()
