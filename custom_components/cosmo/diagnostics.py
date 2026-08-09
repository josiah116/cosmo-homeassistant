"""Diagnostics for Cosmo integration (redacted, privacy safe).

Never emits coordinates, tokens, emails, passwords, phones, IMEI, raw payloads
that could identify the child or location history.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import CosmoConfigEntry
from .const import DOMAIN
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
    "data",  # raw would be redacted at top
]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry (redacted)."""
    rt: CosmoConfigEntry = entry  # type guard
    coordinator: CosmoCoordinator = rt.runtime_data.coordinator if rt.runtime_data else None

    diag: dict[str, Any] = {
        "integration": DOMAIN,
        "version": "0.5.0",  # will sync from manifest later if needed
        "entry_id": entry.entry_id,
        "device_id_present": bool(entry.data.get("device_id")),
        "has_client": bool(rt.runtime_data and rt.runtime_data.client),
        "has_coordinator": coordinator is not None,
        "cloud_reachable": getattr(coordinator, "cloud_reachable", None) if coordinator else None,
        "last_successful_poll": str(coordinator.last_successful_poll) if coordinator and coordinator.last_successful_poll else None,
        "last_error_class": getattr(coordinator, "last_error_class", None) if coordinator else None,
        "last_poll_age_seconds": getattr(coordinator, "last_poll_age", None) if coordinator else None,
        "scan_interval": str(getattr(coordinator, "update_interval", None)) if coordinator else None,
        "supported_capabilities": [
            "cloud_polling",
            "active_tracking_on_demand",
            "stop_active_tracking",
            "diagnostics_redacted",
        ],
        "entity_schema_present": True,
        "platforms": ["device_tracker", "sensor", "binary_sensor", "button"],
    }

    # Redact anything sensitive that might sneak in (defensive)
    return async_redact_data(diag, TO_REDACT)
