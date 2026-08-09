"""Constants for the Cosmo (JrTrack kids watch) integration.

The watch's real backend is the FiLIP platform (api.myfilip.com); COSMO is a
white-label of it (whiteLabelId 18). All data + the live-locate ("active
tracking" / turbo mode) go through this API.
"""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "cosmo"
VERSION = "0.5.1"

API_BASE = "https://api.myfilip.com/v2"
WHITE_LABEL_ID = 18
APP_BUILD = "3.4.0.710"

# Endpoints
EP_TOKEN = f"{API_BASE}/token"
EP_TOKEN_REFRESH = f"{API_BASE}/token/refresh"
EP_MAP = f"{API_BASE}/map"


def ep_settings(device_id: int | str) -> str:
    return f"{API_BASE}/settings/{device_id}"


# Poll the server cache (last-known). This does NOT wake the watch. Two minutes
# keeps family dashboards and safety automations reasonably fresh while avoiding
# aggressive private-API traffic.
DEFAULT_SCAN_INTERVAL = timedelta(minutes=2)
# Refresh the access token this long before it expires.
TOKEN_REFRESH_MARGIN = timedelta(minutes=2)

# Active-tracking ("turbo"): wake the watch and have it report frequently.
# Only triggered on-demand by the Request location button — never on a schedule.
ACTIVE_TRACKING_DURATION = 300   # seconds the watch stays in turbo
ACTIVE_TRACKING_FREQUENCY = 10   # seconds between fixes while in turbo

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_DEVICE_ID = "device_id"

MANUFACTURER = "COSMO Together"

# Cooldown after a locate request to prevent hammering (user-initiated only).
LOCATE_COOLDOWN = timedelta(seconds=60)

# Entity keys for new operational/diagnostic sensors
KEY_CLOUD_REACHABLE = "cloud_reachable"
KEY_LAST_SUCCESSFUL_POLL = "last_successful_poll"
KEY_LOCATION_FIX_AGE = "location_fix_age"
KEY_GPS_ACCURACY = "gps_accuracy"
KEY_ACTIVE_TRACKING = "active_tracking"
KEY_LAST_LOCATE = "last_locate"
