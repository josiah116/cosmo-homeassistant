"""Entity-state and model-validation regression tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from custom_components.cosmo.binary_sensor import (
    BINARY_SENSORS,
    CosmoBinarySensor,
)
from custom_components.cosmo.device_tracker import CosmoTracker
from custom_components.cosmo.models import CosmoDevice, normalize_device
from custom_components.cosmo.sensor import SENSORS, CosmoSensor


def _description(descriptions, key):
    return next(description for description in descriptions if description.key == key)


def _coordinator(data: CosmoDevice | object) -> MagicMock:
    coord = MagicMock()
    coord.entry_id = "entry-test"
    coord.data = data
    coord.last_update_success = True
    coord.last_successful_poll = datetime.now(timezone.utc)
    coord.last_error = None
    coord.last_locate_time = None
    coord.last_locate_outcome = None
    return coord


def test_unsupported_sensor_descriptions_are_absent():
    """Do not recreate stale firmware or unsupported charger-battery entities."""
    keys = {description.key for description in SENSORS}
    assert "firmware" not in keys
    assert "charger_battery" not in keys


def test_high_churn_diagnostic_sensors_are_disabled_by_default():
    """Optional diagnostics must not create continuous Recorder churn by default."""
    descriptions = {
        description.key: description for description in SENSORS
    }
    for key in (
        "last_successful_poll",
        "location_fix_age",
        "gps_accuracy",
        "last_locate",
    ):
        assert descriptions[key].entity_registry_enabled_default is False


def test_normalized_dataclass_values_reach_sensor_entities():
    coord = _coordinator(
        normalize_device(
            {
                "id": "watch-test",
                "batteryLevel": 81,
                "externalBatteryLevel": 72,
                "radius": 25,
                "emergencyMode": False,
                "shutdown": False,
            }
        )
    )

    battery = CosmoSensor(
        coord, "Mock Watch", "JrTrack", _description(SENSORS, "battery")
    )
    accuracy = CosmoSensor(
        coord, "Mock Watch", "JrTrack", _description(SENSORS, "gps_accuracy")
    )
    assert battery.native_value == 81
    assert accuracy.native_value == 25


def test_safety_binary_sensors_preserve_unknown_instead_of_false():
    coord = _coordinator(CosmoDevice(id="watch-test"))
    emergency = CosmoBinarySensor(
        coord, "Mock Watch", "JrTrack", _description(BINARY_SENSORS, "emergency")
    )
    powered_off = CosmoBinarySensor(
        coord, "Mock Watch", "JrTrack", _description(BINARY_SENSORS, "powered_off")
    )
    assert emergency.is_on is None
    assert powered_off.is_on is None


def test_safety_binary_sensors_preserve_true_and_false():
    coord = _coordinator(
        CosmoDevice(id="watch-test", emergency_mode=True, shutdown=False)
    )
    emergency = CosmoBinarySensor(
        coord, "Mock Watch", "JrTrack", _description(BINARY_SENSORS, "emergency")
    )
    powered_off = CosmoBinarySensor(
        coord, "Mock Watch", "JrTrack", _description(BINARY_SENSORS, "powered_off")
    )
    assert emergency.is_on is True
    assert powered_off.is_on is False


def test_active_tracking_unknown_remains_unknown():
    coord = _coordinator(CosmoDevice(id="watch-test"))
    coord.active_tracking = None
    active = CosmoBinarySensor(
        coord, "Mock Watch", "JrTrack", _description(BINARY_SENSORS, "active_tracking")
    )
    assert active.is_on is None


def test_cloud_reachability_uses_health_fields():
    coord = _coordinator(CosmoDevice(id="watch-test"))
    cloud = CosmoBinarySensor(
        coord, "Mock Watch", "JrTrack", _description(BINARY_SENSORS, "cloud_reachable")
    )
    coord.cloud_reachable = True
    assert cloud.is_on is True
    coord.cloud_reachable = False
    assert cloud.is_on is False


def test_last_locate_exposes_bounded_outcome_only():
    coord = _coordinator(CosmoDevice(id="watch-test"))
    coord.last_locate_time = datetime.now(timezone.utc)
    coord.last_locate_outcome = "cooldown"
    sensor = CosmoSensor(
        coord, "Mock Watch", "JrTrack", _description(SENSORS, "last_locate")
    )
    assert sensor.native_value == coord.last_locate_time
    assert sensor.extra_state_attributes == {"outcome": "cooldown"}


def test_location_fix_age_uses_gps_timestamp_and_rejects_invalid_or_future():
    from custom_components.cosmo.coordinator import CosmoCoordinator

    entry = MagicMock()
    entry.entry_id = "entry-test"
    coord = CosmoCoordinator(
        MagicMock(), MagicMock(), MagicMock(), "watch-test", timedelta(seconds=30)
    )
    coord.entry_id = entry.entry_id
    coord.data = CosmoDevice(
        id="watch-test",
        gps_date=(datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(),
    )
    age = coord.location_fix_age
    assert age is not None
    assert 59 <= age <= 61

    coord.data = CosmoDevice(id="watch-test", gps_date="invalid")
    assert coord.location_fix_age is None
    coord.data = CosmoDevice(
        id="watch-test",
        gps_date=(datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
    )
    assert coord.location_fix_age is None


@dataclass(frozen=True)
class _SyntheticGeo:
    """Non-numeric stand-in proving availability requires both fields."""

    latitude: object | None
    longitude: object | None
    radius: int | None = 10
    firmware_version: str | None = None


def test_tracker_fails_closed_when_critical_location_is_missing():
    coord = _coordinator(CosmoDevice(id="watch-test"))
    tracker = CosmoTracker(coord, "Mock Watch", "JrTrack")
    assert tracker.available is False


def test_tracker_available_when_both_normalized_location_fields_exist():
    coord = _coordinator(_SyntheticGeo(object(), object()))
    tracker = CosmoTracker(coord, "Mock Watch", "JrTrack")
    assert tracker.available is True


def test_normalizer_rejects_out_of_range_location_and_negative_accuracy():
    device = normalize_device(
        {
            "id": "watch-test",
            "latitude": 999,
            "longitude": -999,
            "radius": -1,
        }
    )
    assert device.latitude is None
    assert device.longitude is None
    assert device.radius is None


def test_normalizer_does_not_coerce_safety_or_tracking_flags():
    device = normalize_device(
        {
            "id": "watch-test",
            "emergencyMode": "false",
            "shutdown": 0,
            "activeTrackingEnable": "true",
        }
    )
    assert device.emergency_mode is None
    assert device.shutdown is None
    assert device.active_tracking_enable is None
