"""Tests for coordinator: health timestamps, success/error, unchanged payload behavior.

Uses the narrow dummy HA classes from conftest so real class creation works.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.cosmo.api import (
    CosmoApiError,
    CosmoClient,
    CosmoRateLimitError,
    _reset_governors_for_testing,
)
from custom_components.cosmo.coordinator import CosmoCoordinator
from custom_components.cosmo.models import (
    CosmoDevice,
    normalize_device,
    normalize_settings,
)


def _make_coordinator(
    client_mock,
    device_id="12345",
    *,
    adaptive=False,
    trusted=None,
):
    """Instantiate using dummy base provided by conftest mocks."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "test_entry_123"
    entry.data = {"device_id": device_id}
    hass.states.get.return_value = None
    client_mock.rate_limit_streak = getattr(client_mock, "rate_limit_streak", 0)
    coord = CosmoCoordinator(
        hass,
        entry,
        client_mock,
        device_id,
        timedelta(minutes=2),
        adaptive_enabled=adaptive,
        trusted_zone_entity_ids=trusted,
    )
    return coord


class _TimeoutRequestContext:
    async def __aenter__(self):
        raise TimeoutError("synthetic timeout")

    async def __aexit__(self, *args):
        return False


class _PayloadResponse:
    status = 200

    def __init__(self, payload):
        self.headers = {}
        self.payload = payload

    async def text(self):
        return "synthetic"

    async def json(self):
        return self.payload


class _PayloadRequestContext:
    def __init__(self, payload):
        self.response = _PayloadResponse(payload)

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return False


def test_coordinator_health_on_success(mock_map_payload):
    """Coordinator records last_successful_poll and clears error on success."""
    client = MagicMock()
    dev = normalize_device(mock_map_payload["data"]["Devices"][0])
    client.get_device = AsyncMock(return_value=dev)

    coord = _make_coordinator(client)

    async def _run():
        data = await coord._async_update_data()
        assert isinstance(data, CosmoDevice)
        assert coord.last_successful_poll is not None
        assert coord.last_error is None
        assert coord.last_error_class is None
        return data

    asyncio.run(_run())


def test_coordinator_health_on_error():
    """On auth/api error, records error class and timestamps not updated."""
    from custom_components.cosmo.api import CosmoAuthError

    client = MagicMock()
    client.get_device = AsyncMock(side_effect=CosmoAuthError("bad creds"))

    coord = _make_coordinator(client)

    async def _run():
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()
        assert coord.last_error is not None
        assert coord.last_error_class == "CosmoAuthError"
    asyncio.run(_run())


def test_coordinator_unchanged_payload_behavior():
    """Coordinator configured with always_update=False for unchanged payload skip."""
    client = MagicMock()
    dev = normalize_device({"id": "12345", "batteryLevel": 80})
    client.get_device = AsyncMock(return_value=dev)
    coord = _make_coordinator(client)
    assert coord.always_update is False


def test_startup_active_tracking_init_from_settings_when_map_omits():
    """Startup settings state is authoritative, protected, and lifetime-bounded."""
    client = MagicMock()
    client.get_settings = AsyncMock(
        return_value=normalize_settings({"activeTrackingEnable": True})
    )
    coord = _make_coordinator(client)
    coord.active_tracking = None

    async def _run():
        before = datetime.now(timezone.utc)
        await coord.async_initialize_active_tracking()
        client.get_settings.assert_awaited_once_with("12345")
        assert coord.active_tracking is True
        assert coord._active_readback_protected_until is not None
        assert coord._active_readback_protected_until > before
        assert coord._active_tracking_expires_at is not None
        assert coord._active_tracking_expires_at > before

    asyncio.run(_run())


def test_startup_active_tracking_skips_if_already_set_from_map():
    """If map provided explicit state, do not hit settings at startup (map authority)."""
    client = MagicMock()
    client.get_settings = AsyncMock()
    coord = _make_coordinator(client)
    coord.active_tracking = False

    async def _run():
        await coord.async_initialize_active_tracking()
        client.get_settings.assert_not_awaited()
        assert coord.active_tracking is False

    asyncio.run(_run())


def test_startup_active_tracking_init_swallows_settings_errors():
    """Transient settings failure at startup must not block; state stays None."""
    from custom_components.cosmo.api import CosmoApiError

    client = MagicMock()
    client.get_settings = AsyncMock(side_effect=CosmoApiError("settings down"))
    coord = _make_coordinator(client)
    coord.active_tracking = None

    async def _run():
        await coord.async_initialize_active_tracking()
        assert coord.active_tracking is None
        client.get_settings.assert_awaited_once()

    asyncio.run(_run())


def test_startup_active_tracking_auth_failure_requests_reauthentication():
    """Expired startup credentials must enter native HA reauthentication."""
    from custom_components.cosmo.api import CosmoAuthError

    client = MagicMock()
    client.get_settings = AsyncMock(side_effect=CosmoAuthError("expired"))
    coord = _make_coordinator(client)

    async def _run():
        with pytest.raises(ConfigEntryAuthFailed):
            await coord.async_initialize_active_tracking()
        assert coord.active_tracking is None

    asyncio.run(_run())


@pytest.mark.parametrize(
    "settings",
    [None, normalize_settings({}), normalize_settings({"activeTrackingEnable": "false"})],
)
def test_startup_active_tracking_malformed_state_remains_unknown(settings):
    """Missing or non-boolean critical state must never become false."""
    client = MagicMock()
    client.get_settings = AsyncMock(return_value=settings)
    coord = _make_coordinator(client)

    async def _run():
        await coord.async_initialize_active_tracking()
        assert coord.active_tracking is None
        assert coord._active_readback_protected_until is None
        assert coord._active_tracking_expires_at is None

    asyncio.run(_run())


def test_startup_active_tracking_false_is_stable_without_expiration():
    client = MagicMock()
    client.get_settings = AsyncMock(
        return_value=normalize_settings({"activeTrackingEnable": False})
    )
    coord = _make_coordinator(client)

    async def _run():
        await coord.async_initialize_active_tracking()
        assert coord.active_tracking is False
        assert coord._active_readback_protected_until is not None
        assert coord._active_tracking_expires_at is None

    asyncio.run(_run())


def test_startup_active_tracking_cancellation_propagates():
    client = MagicMock()
    client.get_settings = AsyncMock(side_effect=asyncio.CancelledError())
    coord = _make_coordinator(client)

    async def _run():
        with pytest.raises(asyncio.CancelledError):
            await coord.async_initialize_active_tracking()
        assert coord.active_tracking is None

    asyncio.run(_run())


def test_recurring_map_updates_never_read_settings():
    """The coordinator path remains map-only."""
    client = MagicMock()
    client.get_device = AsyncMock(return_value=CosmoDevice(id="12345"))
    client.get_settings = AsyncMock()
    coord = _make_coordinator(client)

    async def _run():
        await coord._async_update_data()
        await coord._async_update_data()
        assert client.get_device.await_count == 2
        client.get_settings.assert_not_awaited()

    asyncio.run(_run())


def test_transient_backoff_sequence_gate_and_success_reset():
    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(side_effect=CosmoApiError("transient"))
    coord = _make_coordinator(client)

    async def _run():
        expected = [120, 240, 480, 900]
        with (
            patch("custom_components.cosmo.coordinator.random.random", return_value=0),
            patch("custom_components.cosmo.coordinator.time.monotonic", return_value=100),
        ):
            for delay in expected:
                coord._not_before = None
                with pytest.raises(UpdateFailed) as raised:
                    await coord._async_update_data()
                assert raised.value.retry_after == delay
                assert coord.update_interval.total_seconds() == delay
            calls = client.get_device.await_count
            with pytest.raises(UpdateFailed):
                await coord._async_update_data()
            assert client.get_device.await_count == calls
            coord._not_before = None
            client.get_device = AsyncMock(return_value=CosmoDevice(id="synthetic"))
            await coord._async_update_data()
            assert coord.generic_backoff_streak == 0
            assert coord.backoff_class is None
            assert coord.update_interval.total_seconds() == 120

    asyncio.run(_run())


def test_total_timeout_is_wrapped_and_arms_transient_backoff():
    async def _run():
        _reset_governors_for_testing()
        session = MagicMock()
        session.request.return_value = _TimeoutRequestContext()
        client = CosmoClient(session, "synthetic-account", "placeholder")
        client._access = "synthetic"
        coord = _make_coordinator(MagicMock())
        coord.client = client

        with (
            patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()),
            patch("custom_components.cosmo.coordinator.random.random", return_value=0),
            patch("custom_components.cosmo.coordinator.time.monotonic", return_value=100),
            pytest.raises(UpdateFailed) as raised,
        ):
            await coord._async_update_data()

        assert isinstance(raised.value.__cause__, CosmoApiError)
        assert coord.backoff_class == "transient"
        assert coord.generic_backoff_streak == 1
        assert coord.update_interval.total_seconds() == 120

    asyncio.run(_run())


@pytest.mark.parametrize(
    ("invalid_field", "invalid_value"),
    [
        ("radius", float("inf")),
        ("radius", 10**10000),
        ("latitude", 10**10000),
    ],
    ids=["infinite-radius", "overflowing-radius", "overflowing-latitude"],
)
def test_malformed_map_numeric_fails_safe_through_client_and_coordinator(
    invalid_field, invalid_value
):
    async def _run():
        _reset_governors_for_testing()
        now = datetime.now(timezone.utc)
        origin = float(len(""))
        payload = {
            "data": {
                "Devices": [
                    {
                        "id": "synthetic",
                        "gpsDate": now.isoformat(),
                        "latitude": origin,
                        "longitude": origin,
                        "radius": float("inf"),
                    }
                ]
            }
        }
        payload["data"]["Devices"][0][invalid_field] = invalid_value
        session = MagicMock()
        session.request.return_value = _PayloadRequestContext(payload)
        client = CosmoClient(session, "synthetic-account", "placeholder")
        client._access = "synthetic"
        coord = _make_coordinator(
            MagicMock(), device_id="synthetic", adaptive=True
        )
        coord.client = client

        with patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()):
            device = await coord._async_update_data()

        assert device is not None
        assert getattr(device, invalid_field) is None
        assert coord.update_interval.total_seconds() == 120
        assert coord.cadence_name == "baseline"

    asyncio.run(_run())


def test_rate_limit_delay_is_not_capped_by_coordinator():
    client = MagicMock()
    client.rate_limit_streak = 1
    client.get_device = AsyncMock(
        side_effect=CosmoRateLimitError("rate limited", retry_after_seconds=7200)
    )
    coord = _make_coordinator(client)

    async def _run():
        with (
            patch(
                "custom_components.cosmo.coordinator.time.monotonic",
                return_value=100,
            ),
            pytest.raises(UpdateFailed) as raised,
        ):
            await coord._async_update_data()
        assert raised.value.retry_after == 7200
        assert coord.update_interval.total_seconds() == 7200
        assert coord.last_error_class == "rate_limit"

    asyncio.run(_run())


def test_huge_rate_limit_delay_uses_safe_coordinator_slice():
    huge_delay = 10**4000
    client = MagicMock()
    client.rate_limit_streak = 1
    client.get_device = AsyncMock(
        side_effect=CosmoRateLimitError(
            "rate limited", retry_after_seconds=huge_delay
        )
    )
    coord = _make_coordinator(client)

    async def _run():
        with (
            patch(
                "custom_components.cosmo.coordinator.time.monotonic",
                return_value=100,
            ),
            pytest.raises(UpdateFailed) as raised,
        ):
            await coord._async_update_data()
        assert raised.value.retry_after <= 31_536_000
        assert coord.update_interval.total_seconds() <= 31_536_000
        assert coord.last_error_class == "rate_limit"
        client.get_device.assert_awaited_once()

    asyncio.run(_run())


def test_adaptive_disabled_and_away_cadences_use_real_update_path():
    fix_at = datetime.now(timezone.utc)
    origin = float(len(""))
    far = float(len("x"))
    device = normalize_device(
        {
            "id": "synthetic",
            "gpsDate": fix_at.isoformat(),
            "latitude": origin,
            "longitude": origin,
            "radius": len("x"),
        }
    )
    home_state = SimpleNamespace(
        state="0",
        attributes={
            "latitude": far,
            "longitude": far,
            "radius": len("synthetic"),
        },
    )

    async def _run():
        disabled_client = MagicMock()
        disabled_client.rate_limit_streak = 0
        disabled_client.get_device = AsyncMock(return_value=device)
        disabled = _make_coordinator(disabled_client)
        await disabled._async_update_data()
        assert disabled.update_interval.total_seconds() == 120
        assert disabled.cadence_name == "disabled"

        away_client = MagicMock()
        away_client.rate_limit_streak = 0
        away_client.get_device = AsyncMock(return_value=device)
        away = _make_coordinator(away_client, adaptive=True)
        away.hass.states.get.return_value = home_state
        await away._async_update_data()
        assert away.update_interval.total_seconds() == 60
        assert away.cadence_name == "away"

    asyncio.run(_run())


def test_timezone_naive_source_timestamps_cannot_stabilize_home():
    now = datetime.now(timezone.utc)
    origin = float(len(""))
    devices = [
        normalize_device(
            {
                "id": "synthetic",
                "gpsDate": (now - timedelta(minutes=minutes))
                .replace(tzinfo=None)
                .isoformat(),
                "latitude": origin,
                "longitude": origin,
                "radius": len("safe"),
            }
        )
        for minutes in (2, 1)
    ]
    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(side_effect=devices)
    coord = _make_coordinator(client, adaptive=True)
    coord.hass.states.get.return_value = SimpleNamespace(
        state="0",
        attributes={
            "latitude": origin,
            "longitude": origin,
            "radius": len("x" * 100),
        },
    )
    async def _run():
        await coord._async_update_data()
        await coord._async_update_data()
        assert coord.update_interval.total_seconds() == 120
        assert coord.cadence_name == "baseline"
        assert coord._candidate_fix_count == 0
        assert coord._last_fresh_fix_at is None

    asyncio.run(_run())


def test_boolean_location_values_force_baseline_through_real_update_path():
    """Boolean coordinates/accuracy are malformed, never a fresh away fix."""
    device = normalize_device(
        {
            "id": "synthetic",
            "gpsDate": datetime.now(timezone.utc).isoformat(),
            "latitude": True,
            "longitude": False,
            "radius": True,
        }
    )
    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(return_value=device)
    coord = _make_coordinator(client, adaptive=True)

    async def _run():
        await coord._async_update_data()
        assert coord.update_interval.total_seconds() == 120
        assert coord.cadence_name == "baseline"
        assert coord._candidate_fix_count == 0

    asyncio.run(_run())


def test_selected_zone_with_unavailable_geometry_is_unknown():
    """A configured zone cannot be omitted to produce a false-away decision."""
    fix_at = datetime.now(timezone.utc)
    origin = float(len(""))
    home_center = float(len("x"))
    home_state = SimpleNamespace(
        state="0",
        attributes={
            "latitude": home_center,
            "longitude": home_center,
            "radius": len("synthetic"),
        },
    )
    device = normalize_device(
        {
            "id": "synthetic",
            "gpsDate": fix_at.isoformat(),
            "latitude": origin,
            "longitude": origin,
            "radius": len("x"),
        }
    )
    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(return_value=device)
    coord = _make_coordinator(client, adaptive=True, trusted=["zone.synthetic"])
    coord.hass.states.get.side_effect = [home_state, None]

    async def _run():
        await coord._async_update_data()
        assert coord.update_interval.total_seconds() == 120
        assert coord.cadence_name == "baseline"
        assert coord._candidate_fix_count == 0
        assert coord._last_fresh_fix_at == fix_at

    asyncio.run(_run())


@pytest.mark.parametrize("zone_variant", ["unavailable", "malformed"])
def test_selected_zone_uncertainty_is_baseline_through_update_path(zone_variant):
    fix_at = datetime.now(timezone.utc)
    origin = float(len(""))
    far = float(len("x"))
    home_state = SimpleNamespace(
        state="0",
        attributes={
            "latitude": far,
            "longitude": far,
            "radius": len("synthetic"),
        },
    )
    if zone_variant == "unavailable":
        selected_state = SimpleNamespace(
            state="unavailable",
            attributes={
                "latitude": origin,
                "longitude": origin,
                "radius": len("synthetic"),
            },
        )
    else:
        selected_state = SimpleNamespace(
            state="0",
            attributes={"latitude": True, "longitude": origin, "radius": len("x")},
        )
    device = normalize_device(
        {
            "id": "synthetic",
            "gpsDate": fix_at.isoformat(),
            "latitude": origin,
            "longitude": origin,
            "radius": len("x"),
        }
    )
    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(return_value=device)
    coord = _make_coordinator(client, adaptive=True, trusted=["zone.synthetic"])
    coord.hass.states.get.side_effect = [home_state, selected_state]

    async def _run():
        await coord._async_update_data()
        assert coord.update_interval.total_seconds() == 120
        assert coord.cadence_name == "baseline"
        assert coord._candidate_fix_count == 0
        assert coord._last_fresh_fix_at == fix_at

    asyncio.run(_run())


@pytest.mark.parametrize(
    "invalid_radius",
    [float("inf"), 10**10000, 10**300, 1e308],
    ids=["infinite", "overflowing", "huge-integer", "huge-float"],
)
def test_invalid_zone_radius_remains_baseline_through_update_path(invalid_radius):
    now = datetime.now(timezone.utc)
    origin = float(len(""))

    def _device(fix_at):
        return normalize_device(
            {
                "id": "synthetic",
                "gpsDate": fix_at.isoformat(),
                "latitude": origin,
                "longitude": origin,
                "radius": len("x"),
            }
        )

    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(
        side_effect=[
            _device(now - timedelta(minutes=2)),
            _device(now - timedelta(minutes=1)),
        ]
    )
    coord = _make_coordinator(client, adaptive=True)
    coord.hass.states.get.return_value = SimpleNamespace(
        state="0",
        attributes={
            "latitude": origin,
            "longitude": origin,
            "radius": invalid_radius,
        },
    )

    async def _run():
        await coord._async_update_data()
        await coord._async_update_data()
        assert coord.update_interval.total_seconds() == 120
        assert coord.cadence_name == "baseline"
        assert coord._candidate_fix_count == 0

    asyncio.run(_run())


@pytest.mark.parametrize("tangent_side", ["internal", "external"])
def test_exact_tangent_boundaries_remain_baseline_through_update_path(tangent_side):
    now = datetime.now(timezone.utc)
    origin = float(len(""))
    offset = float(len("x")) / len("x" * 1000)
    accuracy = float(len("x" * 25))
    distance = CosmoCoordinator._haversine_meters(origin, origin, offset, origin)
    zone_radius = (
        distance + accuracy if tangent_side == "internal" else distance - accuracy
    )

    def _device(fix_at):
        return normalize_device(
            {
                "id": "synthetic",
                "gpsDate": fix_at.isoformat(),
                "latitude": offset,
                "longitude": origin,
                "radius": accuracy,
            }
        )

    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(
        side_effect=[
            _device(now - timedelta(minutes=2)),
            _device(now - timedelta(minutes=1)),
        ]
    )
    coord = _make_coordinator(client, adaptive=True)
    coord.hass.states.get.return_value = SimpleNamespace(
        state="0",
        attributes={
            "latitude": origin,
            "longitude": origin,
            "radius": zone_radius,
        },
    )

    async def _run():
        await coord._async_update_data()
        await coord._async_update_data()
        assert coord.update_interval.total_seconds() == 120
        assert coord.cadence_name == "baseline"
        assert coord._candidate_fix_count == 0

    asyncio.run(_run())


def test_near_antipodal_haversine_rounding_does_not_raise():
    divisor = len("x" * 10)
    epsilon = float(divisor**-divisor)
    latitude_a = -float(len("x" * 83))
    latitude_b = -latitude_a - epsilon
    longitude_a = float(len(""))
    longitude_b = math.degrees(math.pi) - epsilon

    distance = CosmoCoordinator._haversine_meters(
        latitude_a, longitude_a, latitude_b, longitude_b
    )
    assert math.isfinite(distance)
    assert distance > 0


def test_boolean_zone_geometry_is_invalid():
    state = SimpleNamespace(
        attributes={"latitude": True, "longitude": False, "radius": True}
    )
    assert CosmoCoordinator._zone_geometry(state) is None


def test_overlapping_home_and_trusted_matches_are_unknown_through_update_path():
    """Multiple real zone matches are ambiguous and retain baseline cadence."""
    fix_at = datetime.now(timezone.utc)
    origin = float(len(""))
    zone_state = SimpleNamespace(
        state="0",
        attributes={
            "latitude": origin,
            "longitude": origin,
            "radius": len("synthetic"),
        },
    )
    device = normalize_device(
        {
            "id": "synthetic",
            "gpsDate": fix_at.isoformat(),
            "latitude": origin,
            "longitude": origin,
            "radius": len("x"),
        }
    )
    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(return_value=device)
    coord = _make_coordinator(client, adaptive=True, trusted=["zone.synthetic"])
    coord.hass.states.get.side_effect = [zone_state, zone_state]

    async def _run():
        await coord._async_update_data()
        assert coord.update_interval.total_seconds() == 120
        assert coord.cadence_name == "baseline"
        assert coord._candidate_fix_count == 0
        assert coord._last_fresh_fix_at == fix_at

    asyncio.run(_run())


def test_home_requires_two_distinct_monotonic_fixes_and_cached_does_not_count():
    first_time = datetime.now(timezone.utc) - timedelta(minutes=2)
    second_time = first_time + timedelta(minutes=1)
    origin = float(len(""))

    def _device(fix_at):
        return normalize_device(
            {
                "id": "synthetic",
                "gpsDate": fix_at.isoformat(),
                "latitude": origin,
                "longitude": origin,
                "radius": len("x"),
            }
        )

    first = _device(first_time)
    second = _device(second_time)
    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(side_effect=[first, first, second, second])
    coord = _make_coordinator(client, adaptive=True)
    coord.hass.states.get.return_value = SimpleNamespace(
        state="0",
        attributes={
            "latitude": origin,
            "longitude": origin,
            "radius": len("synthetic"),
        },
    )

    async def _run():
        await coord._async_update_data()
        assert coord.update_interval.total_seconds() == 120
        assert coord.cadence_name == "baseline"
        await coord._async_update_data()
        assert coord.update_interval.total_seconds() == 120
        await coord._async_update_data()
        assert coord.update_interval.total_seconds() == 180
        assert coord.cadence_name == "home"
        await coord._async_update_data()
        assert coord.update_interval.total_seconds() == 180
        assert coord.cadence_name == "home"

    asyncio.run(_run())


def test_cached_fix_cannot_transfer_stabilization_between_zones():
    now = datetime.now(timezone.utc)
    times = [now - timedelta(minutes=4 - offset) for offset in range(4)]
    origin = float(len(""))
    far = float(len("x"))

    def _device(fix_at):
        return normalize_device(
            {
                "id": "synthetic",
                "gpsDate": fix_at.isoformat(),
                "latitude": origin,
                "longitude": origin,
                "radius": len("x"),
            }
        )

    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(
        side_effect=[
            _device(times[0]),
            _device(times[1]),
            _device(times[1]),
            _device(times[2]),
            _device(times[3]),
        ]
    )
    coord = _make_coordinator(client, adaptive=True, trusted=["zone.synthetic"])
    home_near = SimpleNamespace(
        state="0",
        attributes={"latitude": origin, "longitude": origin, "radius": len("synthetic")},
    )
    zone_far = SimpleNamespace(
        state="0",
        attributes={"latitude": far, "longitude": far, "radius": len("synthetic")},
    )
    trusted_near = SimpleNamespace(
        state="0",
        attributes={"latitude": origin, "longitude": origin, "radius": len("synthetic")},
    )
    zone_states = {"zone.home": home_near, "zone.synthetic": zone_far}
    coord.hass.states.get.side_effect = zone_states.get

    async def _run():
        await coord._async_update_data()
        await coord._async_update_data()
        assert coord.cadence_name == "home"
        assert coord._candidate_fix_count == 2

        zone_states["zone.home"] = zone_far
        zone_states["zone.synthetic"] = trusted_near
        await coord._async_update_data()
        assert coord.cadence_name == "baseline"
        assert coord._candidate_fix_count == 0

        await coord._async_update_data()
        assert coord.cadence_name == "baseline"
        assert coord._candidate_fix_count == 1
        await coord._async_update_data()
        assert coord.cadence_name == "trusted"
        assert coord._candidate_fix_count == 2

    asyncio.run(_run())


def test_stale_and_out_of_order_fixes_reset_trusted_candidate():
    now = datetime.now(timezone.utc)
    first_time = now - timedelta(minutes=3)
    older_time = first_time - timedelta(minutes=1)
    stale_time = now - timedelta(minutes=11)
    next_time = first_time + timedelta(minutes=1)
    origin = float(len(""))
    far = float(len("x"))

    def _device(fix_at):
        return normalize_device(
            {
                "id": "synthetic",
                "gpsDate": fix_at.isoformat(),
                "latitude": origin,
                "longitude": origin,
                "radius": len("x"),
            }
        )

    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(
        side_effect=[
            _device(first_time),
            _device(older_time),
            _device(stale_time),
            _device(next_time),
        ]
    )
    coord = _make_coordinator(client, adaptive=True, trusted=["zone.synthetic"])
    home_state = SimpleNamespace(
        state="0",
        attributes={
            "latitude": far,
            "longitude": far,
            "radius": len("synthetic"),
        },
    )
    trusted_state = SimpleNamespace(
        state="0",
        attributes={
            "latitude": origin,
            "longitude": origin,
            "radius": len("synthetic"),
        },
    )
    coord.hass.states.get.side_effect = lambda entity_id: (
        home_state if entity_id == "zone.home" else trusted_state
    )

    async def _run():
        await coord._async_update_data()
        assert coord._candidate_fix_count == 1
        assert coord._last_fresh_fix_at == first_time
        await coord._async_update_data()
        assert coord._candidate_fix_count == 0
        assert coord._last_fresh_fix_at == first_time
        await coord._async_update_data()
        assert coord._candidate_fix_count == 0
        await coord._async_update_data()
        assert coord._candidate_fix_count == 1
        assert coord.cadence_name == "baseline"

    asyncio.run(_run())


def test_missing_fix_does_not_erase_timestamp_high_water():
    """Older fixes cannot stabilize after an unknown sample resets zone confidence."""
    now = datetime.now(timezone.utc)
    high_water = now - timedelta(minutes=2)
    older_first = now - timedelta(minutes=4)
    older_second = now - timedelta(minutes=3)
    origin = float(len(""))
    zone_state = SimpleNamespace(
        state="0",
        attributes={
            "latitude": origin,
            "longitude": origin,
            "radius": len("synthetic"),
        },
    )

    def _device(fix_at: datetime) -> CosmoDevice:
        return normalize_device(
            {
                "id": "synthetic",
                "gpsDate": fix_at.isoformat(),
                "latitude": origin,
                "longitude": origin,
                "radius": len("x"),
            }
        )

    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(
        side_effect=[
            _device(high_water),
            normalize_device({"id": "synthetic"}),
            _device(older_first),
            _device(older_second),
        ]
    )
    coord = _make_coordinator(client, adaptive=True)
    coord.hass.states.get.return_value = zone_state

    async def _run():
        await coord._async_update_data()
        assert coord._candidate_fix_count == 1
        assert coord._last_fresh_fix_at == high_water

        await coord._async_update_data()
        assert coord._candidate_fix_count == 0
        assert coord._last_fresh_fix_at == high_water

        await coord._async_update_data()
        await coord._async_update_data()
        assert coord._candidate_fix_count == 0
        assert coord._last_fresh_fix_at == high_water
        assert coord.update_interval.total_seconds() == 120
        assert coord.cadence_name == "baseline"

    asyncio.run(_run())


def test_boundary_uncertainty_is_baseline_through_update_path():
    fix_at = datetime.now(timezone.utc)
    origin = float(len(""))
    zone_radius = len("synthetic") ** len("xxx")
    boundary_offset = len("x") / (len("synthetic") ** len("xx"))
    device = normalize_device(
        {
            "id": "synthetic",
            "gpsDate": fix_at.isoformat(),
            "latitude": boundary_offset,
            "longitude": origin,
            "radius": zone_radius,
        }
    )
    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(return_value=device)
    coord = _make_coordinator(client, adaptive=True)
    coord.hass.states.get.return_value = SimpleNamespace(
        state="0",
        attributes={
            "latitude": origin,
            "longitude": origin,
            "radius": zone_radius,
        },
    )

    async def _run():
        await coord._async_update_data()
        assert coord.update_interval.total_seconds() == 120
        assert coord.cadence_name == "baseline"
        assert coord._candidate_fix_count == 0
        assert coord._last_fresh_fix_at == fix_at

    asyncio.run(_run())


def test_startup_and_stop_rate_limit_arm_same_coordinator_cooldown():
    client = MagicMock()
    client.rate_limit_streak = 2
    rate_error = CosmoRateLimitError("rate limited", retry_after_seconds=600)
    client.get_settings = AsyncMock(side_effect=rate_error)
    client.set_active_tracking = AsyncMock(side_effect=rate_error)
    coord = _make_coordinator(client)

    async def _run():
        with patch("custom_components.cosmo.coordinator.time.monotonic", return_value=100):
            await coord.async_initialize_active_tracking()
            assert coord.backoff_class == "rate_limit"
            assert coord.update_interval.total_seconds() == 600
            coord._not_before = None
            with pytest.raises(CosmoRateLimitError):
                await coord.async_stop_active_tracking()
            assert coord.backoff_class == "rate_limit"
            assert coord.update_interval.total_seconds() == 600

    asyncio.run(_run())


def test_coordinator_cancellation_propagates_without_backoff():
    client = MagicMock()
    client.rate_limit_streak = 0
    client.get_device = AsyncMock(side_effect=asyncio.CancelledError())
    coord = _make_coordinator(client)

    async def _run():
        with pytest.raises(asyncio.CancelledError):
            await coord._async_update_data()
        assert coord.backoff_class is None

    asyncio.run(_run())
