"""Diagnostics for Cosmo integration (redacted, privacy safe).

Never emits coordinates, tokens, emails, passwords, phones, IMEI, raw payloads
that could identify the child or location history.
For v0.5.3: only broad adaptive, cadence, interval, backoff class/streak, trusted-configured bool.
No zone IDs/names/counts, coords, account/device/config-entry new identifiers, Retry-After, payloads.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import CosmoConfigEntry
from .const import DOMAIN, VERSION
from .coordinator import CosmoCoordinator

TO_REDACT = [
    # explicit: never these
    "email",
    "password",
    "access",
    "refresh",
    "token",
    "imei",
    "serial_number",
    "latitude",
    "longitude",
    "gpsDate",
    "radius",  # accuracy + rough location
    "firstName",
    "lastName",
    "gsmNumber",
    "phone",
    "phoneNumber",
    "message",
    "messages",
    "call",
    "calls",
    "data",  # raw would be redacted at top
    "entry_id",
]


def _interval_seconds(coordinator: CosmoCoordinator | None) -> int | None:
    """Return a safe interval only when the coordinator exposes a duration."""
    interval = getattr(coordinator, "update_interval", None) if coordinator else None
    total_seconds = getattr(interval, "total_seconds", None)
    if not callable(total_seconds):
        return None
    seconds = total_seconds()
    if not isinstance(seconds, (int, float)):
        return None
    return max(0, int(seconds))


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CosmoConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry (redacted)."""
    runtime = entry.runtime_data
    coordinator: CosmoCoordinator | None = runtime.coordinator if runtime else None

    diag: dict[str, Any] = {
        "integration": DOMAIN,
        "version": VERSION,
        "device_id_present": bool(entry.data.get("device_id")),
        "has_client": bool(runtime and runtime.client),
        "has_coordinator": coordinator is not None,
        "cloud_reachable": getattr(coordinator, "cloud_reachable", None) if coordinator else None,
        "last_successful_poll": str(coordinator.last_successful_poll) if coordinator and coordinator.last_successful_poll else None,
        "last_error_class": getattr(coordinator, "last_error_class", None) if coordinator else None,
        "last_poll_age_seconds": getattr(coordinator, "last_poll_age", None) if coordinator else None,
        "scan_interval": str(getattr(coordinator, "update_interval", None)) if coordinator else None,
        # v0.5.3 privacy-safe subset only (broad, no IDs/coords/retry/raw)
        "adaptive_enabled": getattr(coordinator, "adaptive_enabled", False) if coordinator else False,
        "cadence": getattr(coordinator, "cadence_name", None) if coordinator else None,
        "current_interval_seconds": _interval_seconds(coordinator),
        "backoff_class": getattr(coordinator, "backoff_class", None) if coordinator else None,
        "backoff_streak": getattr(coordinator, "backoff_streak", 0) if coordinator else 0,
        "trusted_configured": bool(getattr(coordinator, "trusted_zone_entity_ids", [])) if coordinator else False,
        "supported_capabilities": [
            "cloud_polling",
            "active_tracking_on_demand",
            "stop_active_tracking",
            "diagnostics_redacted",
            "adaptive_polling_opt_in",
        ],
        "entity_schema_present": True,
        "platforms": ["device_tracker", "sensor", "binary_sensor", "button"],
    }

    # Redact anything sensitive that might sneak in (defensive)
    return async_redact_data(diag, TO_REDACT)
