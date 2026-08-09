"""Minimal validated response models for COSMO / FiLIP payloads.

Conservative: tolerate missing/optional fields by setting None.
Critical location fields that are malformed do not silently coerce to plausible
values (e.g. no defaulting latitude to 0.0); entities will be unavailable.
No IMEI, serial, phone, or private fields are modeled or exposed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CosmoDevice:
    """Normalized device state from /v2/map (server cache only)."""

    id: str
    battery_level: int | None = None
    external_battery_level: int | None = None
    gps_date: str | None = None
    firmware_version: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    radius: int | None = None  # accuracy in meters
    emergency_mode: bool | None = None
    shutdown: bool | None = None
    # Active tracking state may come from map or settings; default conservative
    active_tracking_enable: bool | None = None
    active_tracking_duration: int | None = None
    active_tracking_frequency: int | None = None
    # Do not include imei/serial/gsm etc.


@dataclass(frozen=True)
class CosmoSettings:
    """Normalized settings from /v2/settings (for active tracking state)."""

    active_tracking_enable: bool | None = None
    active_tracking_duration: int | None = None
    active_tracking_frequency: int | None = None


def _safe_float(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
        # NaN/inf guard
        if not (f == f and abs(f) < 1e10):  # simple isfinite  # noqa: PLR0124
            return None
        return f
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_int(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        value = int(v)
        return value if abs(value) < 1e10 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_bool(v: Any) -> bool | None:
    """Accept only JSON booleans; ambiguous representations stay unknown."""
    return v if isinstance(v, bool) else None


def normalize_device(raw: dict[str, Any]) -> CosmoDevice:
    """Convert raw map device dict to CosmoDevice. Tolerant of missing fields."""
    if not isinstance(raw, dict):
        raw = {}
    dev_id = str(raw.get("id", "")) or "unknown"
    # Critical geo: do not invent plausible values on bad input
    lat = _safe_float(raw.get("latitude"))
    lon = _safe_float(raw.get("longitude"))
    rad = _safe_int(raw.get("radius"))
    if lat is not None and not -90 <= lat <= 90:
        lat = None
    if lon is not None and not -180 <= lon <= 180:
        lon = None
    if rad is not None and rad < 0:
        rad = None
    # If lat/lon present but invalid type, they become None (caller marks unavailable)
    return CosmoDevice(
        id=dev_id,
        battery_level=_safe_int(raw.get("batteryLevel")),
        external_battery_level=_safe_int(raw.get("externalBatteryLevel")),
        gps_date=raw.get("gpsDate") if isinstance(raw.get("gpsDate"), str) else None,
        firmware_version=raw.get("firmwareVersion") if isinstance(raw.get("firmwareVersion"), str) else None,
        latitude=lat,
        longitude=lon,
        radius=rad,
        emergency_mode=_safe_bool(raw.get("emergencyMode")),
        shutdown=_safe_bool(raw.get("shutdown")),
        active_tracking_enable=_safe_bool(raw.get("activeTrackingEnable")),
        active_tracking_duration=_safe_int(raw.get("activeTrackingDuration")),
        active_tracking_frequency=_safe_int(raw.get("activeTrackingFrequency")),
    )


def normalize_settings(raw: dict[str, Any] | None) -> CosmoSettings:
    if not isinstance(raw, dict):
        raw = {}
    # tolerate wrapped {"status":0, "data": {...}} or direct data
    if "data" in raw and isinstance(raw.get("data"), dict):
        raw = raw["data"]
    return CosmoSettings(
        active_tracking_enable=_safe_bool(raw.get("activeTrackingEnable")),
        active_tracking_duration=_safe_int(raw.get("activeTrackingDuration")),
        active_tracking_frequency=_safe_int(raw.get("activeTrackingFrequency")),
    )
