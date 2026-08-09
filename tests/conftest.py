"""Pytest fixtures for Cosmo integration tests.

All payloads are sanitized: no real device IDs, IMEI, phones, emails, real coordinates,
or private data. Uses synthetic values only.

Narrowly-scoped HA stubs to allow importing the integration package under test
without requiring the full Home Assistant package (per task constraints).
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

# --- Narrow HA / dep stubs (module level, before any cosmo import) -------------
# This lets pytest collect tests that do "from custom_components.cosmo.xxx import"
# without HA being installed in the test env. Do not use for runtime behavior.

_ha = MagicMock(name="ha_stub")
sys.modules["homeassistant"] = _ha
sys.modules["homeassistant.config_entries"] = _ha.config_entries
sys.modules["homeassistant.const"] = _ha.const
sys.modules["homeassistant.core"] = _ha.core
sys.modules["homeassistant.exceptions"] = _ha.exceptions
sys.modules["homeassistant.helpers"] = _ha.helpers
sys.modules["homeassistant.helpers.device_registry"] = _ha.helpers.device_registry
sys.modules["homeassistant.helpers.entity_registry"] = _ha.helpers.entity_registry
sys.modules["homeassistant.helpers.update_coordinator"] = _ha.helpers.update_coordinator
sys.modules["homeassistant.helpers.aiohttp_client"] = _ha.helpers.aiohttp_client
sys.modules["homeassistant.helpers.entity_platform"] = _ha.helpers.entity_platform
sys.modules["homeassistant.components"] = _ha.components
sys.modules["homeassistant.components.button"] = _ha.components.button
sys.modules["homeassistant.components.sensor"] = _ha.components.sensor
sys.modules["homeassistant.components.binary_sensor"] = _ha.components.binary_sensor
sys.modules["homeassistant.components.device_tracker"] = _ha.components.device_tracker
sys.modules["homeassistant.util"] = _ha.util
sys.modules["homeassistant.util.dt"] = _ha.util.dt

# voluptuous used in config_flow
if "voluptuous" not in sys.modules:
    sys.modules["voluptuous"] = MagicMock(name="voluptuous_stub")

from unittest.mock import MagicMock  # reimport after sys mods for clarity

import pytest

# Sanitized mock payloads - synthetic, non-identifying
MOCK_MAP_RESPONSE = {
    "status": 0,
    "data": {
        "Devices": [
            {
                "id": "12345",
                "firstName": "TestKid",
                "hardwareName": "JrTrack 5",
                "batteryLevel": 87,
                "externalBatteryLevel": 92,
                "gpsDate": "2026-08-09T12:34:56Z",
                "firmwareVersion": "1.2.3",
                "latitude": 40.7128,
                "longitude": -74.0060,
                "radius": 45,
                "emergencyMode": False,
                "shutdown": False,
                "activeTrackingEnable": False,
                "activeTrackingDuration": 0,
                "activeTrackingFrequency": 10,
                # Note: no imei, no phone, no serial in test data
            }
        ]
    },
}

MOCK_SETTINGS_RESPONSE = {
    "status": 0,
    "data": {
        "activeTrackingEnable": False,
        "activeTrackingDuration": 300,
        "activeTrackingFrequency": 10,
    },
}

MOCK_MAP_RESPONSE_ACTIVE = {
    "status": 0,
    "data": {
        "Devices": [
            {
                "id": "12345",
                "firstName": "TestKid",
                "hardwareName": "JrTrack 5",
                "batteryLevel": 85,
                "externalBatteryLevel": 90,
                "gpsDate": "2026-08-09T12:35:10Z",
                "firmwareVersion": "1.2.3",
                "latitude": 40.7128,
                "longitude": -74.0060,
                "radius": 12,  # good accuracy
                "emergencyMode": False,
                "shutdown": False,
                "activeTrackingEnable": True,
                "activeTrackingDuration": 300,
                "activeTrackingFrequency": 10,
            }
        ]
    },
}


@pytest.fixture
def mock_map_payload():
    return MOCK_MAP_RESPONSE


@pytest.fixture
def mock_settings_payload():
    return MOCK_SETTINGS_RESPONSE


@pytest.fixture
def mock_map_active_payload():
    return MOCK_MAP_RESPONSE_ACTIVE


@pytest.fixture
def mock_client():
    """Mock CosmoClient with async methods."""
    client = MagicMock()
    client.login = AsyncMock()
    client.get_devices = AsyncMock(return_value=MOCK_MAP_RESPONSE["data"]["Devices"])
    client.get_device = AsyncMock(return_value=MOCK_MAP_RESPONSE["data"]["Devices"][0])
    client.get_settings = AsyncMock(return_value=MOCK_SETTINGS_RESPONSE["data"])
    client.set_active_tracking = AsyncMock()
    client._request = AsyncMock()  # for lower level if needed
    return client


@pytest.fixture
def mock_hass():
    """Minimal mock for HomeAssistant in tests that need it."""
    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.async_create_task = MagicMock()
    hass.loop = MagicMock()
    # For background task simulation
    hass.data = {}
    return hass


@pytest.fixture
def mock_entry():
    """Mock config entry."""
    entry = MagicMock()
    entry.entry_id = "test_entry_abc123"
    entry.data = {
        "email": "test@example.com",
        "password": "testpass",
        "device_id": "12345",
        "name": "TestKid",
        "model": "JrTrack 5",
    }
    entry.runtime_data = None
    entry.async_create_background_task = MagicMock()
    return entry

# --- Dummy base classes (injected into mocks so subclassing in cosmo code works) ---
# These are minimal to support import + limited instantiation for unit tests only.

class _DummyDataUpdateCoordinator:
    def __class_getitem__(cls, item):
        return cls  # support DataUpdateCoordinator[Something] annotation

    def __init__(self, *args, **kwargs):
        self.data = None
        self.last_update_success = False
        self.last_exception = None
        self.always_update = kwargs.get("always_update", False)
        self.hass = args[0] if args else None
        self.logger = args[1] if len(args)>1 else None
        self.name = kwargs.get("name")
        self.update_interval = kwargs.get("update_interval")
        self.config_entry = kwargs.get("config_entry")
        self._listeners = []

    async def async_config_entry_first_refresh(self):
        pass

    async def async_request_refresh(self):
        pass


class _DummyCoordinatorEntity:
    def __init__(self, coordinator=None):
        self.coordinator = coordinator


# inject so that "from ... import ..." in modules get usable types
_ha.helpers.update_coordinator.DataUpdateCoordinator = _DummyDataUpdateCoordinator
_ha.helpers.update_coordinator.CoordinatorEntity = _DummyCoordinatorEntity

if hasattr(sys.modules.get("homeassistant.helpers.update_coordinator"), "__dict__"):
    sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = _DummyDataUpdateCoordinator
    sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = _DummyCoordinatorEntity

print("Dummy HA bases injected for tests")

class _DummyConfigEntry:
    def __class_getitem__(cls, item): return cls
    def __init__(self, *a, **k): 
        self.entry_id = k.get("entry_id", "dummy")
        self.data = k.get("data", {})
        self.runtime_data = None
        self.options = {}

_ha.config_entries.ConfigEntry = _DummyConfigEntry
if hasattr(sys.modules.get("homeassistant.config_entries"), "__dict__"):
    sys.modules["homeassistant.config_entries"].ConfigEntry = _DummyConfigEntry

# also make generic subscript work on top mock
_ha.config_entries.__class_getitem__ = lambda cls, item: cls


@pytest.fixture
def mock_coordinator():
    """Mock coordinator with health attrs for diagnostics / sensor tests."""
    coord = MagicMock()
    coord.last_successful_poll = None
    coord.last_error = None
    coord.last_error_class = None
    coord.cloud_reachable = True
    coord.last_poll_age = 120
    coord.update_interval = "0:02:00"
    coord.data = None  # will be set to device in tests
    return coord


# Ensure diagnostics sub-module is mockable for import (narrow scope)
if "homeassistant.components.diagnostics" not in sys.modules:
    _diag = MagicMock(name="diag_stub")
    def _stub_redact(d, keys=None):
        if not isinstance(d, dict):
            return d
        out = {}
        redact_set = set((keys or []) + ["email","password","token","imei","latitude","longitude","data"])
        for k, v in d.items():
            kl = k.lower()
            if kl in redact_set or any(r in str(v).lower() for r in ["secret","token","pass","coord"]):
                out[k] = "**REDACTED**"
            else:
                out[k] = v
        return out
    _diag.async_redact_data = _stub_redact
    sys.modules["homeassistant.components.diagnostics"] = _diag
    _ha = sys.modules.get("homeassistant")
    if _ha:
        _ha.components.diagnostics = _diag
print("diagnostics mock ready")
