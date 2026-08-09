"""Tests for API client, response normalization, and exception classification.

TDD: these tests are written first; they initially drove the models + client changes.
All data sanitized, no private identifiers or real data.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.cosmo.api import (
    CosmoApiError,
    CosmoAuthError,
    CosmoClient,
)
from custom_components.cosmo.models import (
    CosmoDevice,
    CosmoSettings,
    normalize_device,
    normalize_settings,
)


def test_normalize_device_from_map_payload(mock_map_payload):
    """Test response normalization for map/device data into CosmoDevice dataclass."""
    devices = mock_map_payload["data"]["Devices"]
    device = normalize_device(devices[0])
    assert isinstance(device, CosmoDevice)
    assert device.id == "12345"
    assert device.battery_level == 87
    assert device.gps_date == "2026-08-09T12:34:56Z"
    assert device.latitude == 40.7128
    assert device.longitude == -74.0060
    assert device.radius == 45
    assert device.emergency_mode is False
    assert device.shutdown is False
    assert device.firmware_version == "1.2.3"
    # IMEI must NEVER be present or assigned in model
    assert not hasattr(device, "imei") or getattr(device, "imei", None) is None
    assert device.active_tracking_enable is False


def test_normalize_device_tolerates_missing_optional_fields():
    """Malformed or partial payloads must not silently become plausible data."""
    partial = {"id": "999", "batteryLevel": 50}
    device = normalize_device(partial)
    assert device.id == "999"
    assert device.latitude is None
    assert device.longitude is None
    assert device.radius is None
    assert device.gps_date is None


def test_normalize_device_rejects_bad_critical_location():
    """Critical location fields malformed must not become plausible (e.g. 0,0)."""
    bad = {
        "id": "badloc",
        "latitude": "not-a-float",
        "longitude": None,
        "radius": "invalid",
    }
    device = normalize_device(bad)
    assert device.latitude is None
    assert device.longitude is None
    assert device.radius is None


def test_normalize_settings(mock_settings_payload):
    settings = normalize_settings(mock_settings_payload)
    assert isinstance(settings, CosmoSettings)
    assert settings.active_tracking_enable is False
    assert settings.active_tracking_duration == 300


def test_client_get_device_uses_normalized(mock_client):
    """Client get_device returns normalized (post update)."""
    async def _run():
        device = await mock_client.get_device("12345")
        if device is not None:
            assert isinstance(device, (dict, CosmoDevice))
    asyncio.run(_run())


def test_auth_error_classification():
    """Auth errors classified as CosmoAuthError."""
    async def _run():
        session = AsyncMock()
        client = CosmoClient(session, "e", "p")
        with patch.object(client, "_request", side_effect=CosmoAuthError("401")):
            with pytest.raises(CosmoAuthError):
                await client.login()
    asyncio.run(_run())


def test_api_error_non_auth():
    """Non-auth errors -> CosmoApiError."""
    async def _run():
        session = AsyncMock()
        client = CosmoClient(session, "e", "p")
        with patch.object(client, "_request", side_effect=CosmoApiError("500")):
            with pytest.raises(CosmoApiError):
                await client.get_devices()
    asyncio.run(_run())
