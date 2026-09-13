#pragma once

// Copy this file to config_private.h and replace every EXAMPLE_ONLY value.
// config_private.h is ignored by Git; never put real credentials in this example.
#define IAQ_WIFI_SSID "EXAMPLE_ONLY_WIFI_SSID"
#define IAQ_WIFI_PASSWORD "EXAMPLE_ONLY_WIFI_PASSWORD"
#define IAQ_INGEST_API_KEY "EXAMPLE_ONLY_INGEST_KEY"
#define IAQ_OTA_PASSWORD "EXAMPLE_ONLY_OTA_PASSWORD"

// Used only by the archived V2 trusted-LAN HTTP sketch.
// This key travels in plaintext HTTP: it must be a separate local-only value,
// never the production IAQ_INGEST_API_KEY (V2 refuses to compile if they match).
#define IAQ_LOCAL_INGEST_API_KEY "EXAMPLE_ONLY_LOCAL_LAN_INGEST_KEY_NOT_FOR_PRODUCTION"
// 192.0.2.0/24 is reserved for documentation and is intentionally unreachable.
#define IAQ_LOCAL_SERVER_URL "http://192.0.2.10:5000/api/mesures"
#define IAQ_LOCAL_HEALTH_URL "http://192.0.2.10:5000/api/health"
