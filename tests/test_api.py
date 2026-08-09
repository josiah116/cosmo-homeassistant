"""Tests for API client, response normalization, and exception classification.

TDD: these tests are written first; they initially drove the models + client changes.
All data sanitized, no private identifiers or real data.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.cosmo.api import (
    CosmoApiError,
    CosmoAuthError,
    CosmoClient,
    CosmoRateLimitError,
    _reset_governors_for_testing,
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
    assert device.latitude is None
    assert device.longitude is None
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


def test_normalize_models_reject_boolean_numeric_fields():
    """JSON booleans must never coerce to numeric device or settings values."""
    device = normalize_device(
        {
            "id": "synthetic",
            "latitude": True,
            "longitude": False,
            "radius": True,
            "batteryLevel": True,
            "externalBatteryLevel": False,
            "activeTrackingDuration": True,
            "activeTrackingFrequency": False,
        }
    )
    assert device.latitude is None
    assert device.longitude is None
    assert device.radius is None
    assert device.battery_level is None
    assert device.external_battery_level is None
    assert device.active_tracking_duration is None
    assert device.active_tracking_frequency is None

    settings = normalize_settings(
        {
            "activeTrackingDuration": True,
            "activeTrackingFrequency": False,
        }
    )
    assert settings.active_tracking_duration is None
    assert settings.active_tracking_frequency is None


def test_normalize_settings(mock_settings_payload):
    settings = normalize_settings(mock_settings_payload)
    assert isinstance(settings, CosmoSettings)
    assert settings.active_tracking_enable is False
    assert settings.active_tracking_duration == 300


def test_client_get_device_uses_normalized(mock_client):
    """Client get_device returns normalized (post update)."""
    async def _run():
        device = await mock_client.get_device("12345")
        assert isinstance(device, CosmoDevice)

    asyncio.run(_run())


def test_auth_error_classification():
    """Auth errors classified as CosmoAuthError."""
    async def _run():
        session = AsyncMock()
        client = CosmoClient(session, "e", "p")
        with patch.object(client, "_request", side_effect=CosmoAuthError("401")), pytest.raises(CosmoAuthError):
            await client.login()
    asyncio.run(_run())


def test_api_error_non_auth():
    """Non-auth errors -> CosmoApiError."""
    async def _run():
        session = AsyncMock()
        client = CosmoClient(session, "e", "p")
        with patch.object(client, "_request", side_effect=CosmoApiError("500")), pytest.raises(CosmoApiError):
            await client.get_devices()
    asyncio.run(_run())


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"data": []},
        {"data": {}},
        {"data": {"Devices": {}}},
        {"data": {"Devices": ["invalid"]}},
    ],
)
def test_map_schema_drift_is_classified(response):
    async def _run():
        client = CosmoClient(AsyncMock(), "account", "placeholder")
        with (
            patch.object(client, "_request", AsyncMock(return_value=response)),
            pytest.raises(CosmoApiError, match="schema invalid"),
        ):
            await client.get_devices()

    asyncio.run(_run())


def test_settings_critical_state_requires_boolean():
    async def _run():
        client = CosmoClient(AsyncMock(), "account", "placeholder")
        response = {"data": {"activeTrackingEnable": "false"}}
        with (
            patch.object(client, "_request", AsyncMock(return_value=response)),
            pytest.raises(CosmoApiError, match="activeTrackingEnable"),
        ):
            await client.get_settings("watch-test")

    asyncio.run(_run())


def test_invalid_token_expiry_is_auth_error():
    client = CosmoClient(AsyncMock(), "account", "placeholder")
    with pytest.raises(CosmoAuthError, match="invalid expiry"):
        client._store_tokens(
            {
                "accessToken": "synthetic-token",
                "expDate": "not-a-timestamp",
            }
        )


def test_private_device_id_is_redacted_from_error_endpoint():
    client = CosmoClient(AsyncMock(), "account", "placeholder")
    safe_endpoint = client._safe_endpoint(
        "https://api.myfilip.com/v2/settings/private-device-reference"
    )
    assert safe_endpoint == "/settings/<device>"
    assert "private-device-reference" not in safe_endpoint


class _FakeResponse:
    def __init__(self, status, *, headers=None, payload=None):
        self.status = status
        self.headers = headers or {}
        self._payload = payload or {}
        self.text_calls = 0
        self.json_calls = 0

    async def text(self):
        self.text_calls += 1
        return "synthetic-success-body"

    async def json(self, content_type=None):
        self.json_calls += 1
        return self._payload


class _FakeRequestContext:
    def __init__(self, response=None, enter_error=None):
        self.response = response
        self.enter_error = enter_error

    async def __aenter__(self):
        if self.enter_error is not None:
            raise self.enter_error
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _fake_session(response=None, enter_error=None):
    session = MagicMock()
    session.request.return_value = _FakeRequestContext(response, enter_error)
    return session


class _BlockingRequestContext:
    def __init__(self, response, entered, release):
        self.response = response
        self.entered = entered
        self.release = release

    async def __aenter__(self):
        self.entered.set()
        await self.release.wait()
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_same_account_healthy_requests_are_serialized():
    async def _run():
        _reset_governors_for_testing()
        entered = asyncio.Event()
        release = asyncio.Event()
        first_session = MagicMock()
        first_session.request.return_value = _BlockingRequestContext(
            _FakeResponse(200, payload={}), entered, release
        )
        second_session = _fake_session(_FakeResponse(200, payload={}))
        first = CosmoClient(first_session, "shared-account", "placeholder")
        second = CosmoClient(second_session, "shared-account", "placeholder")

        first_task = asyncio.create_task(
            first._request("GET", "https://api.myfilip.com/v2/map", auth=False)
        )
        await entered.wait()
        second_task = asyncio.create_task(
            second._request("GET", "https://api.myfilip.com/v2/map", auth=False)
        )
        await asyncio.sleep(0)
        second_session.request.assert_not_called()
        release.set()
        assert await first_task == {}
        assert await second_task == {}
        second_session.request.assert_called_once()

    asyncio.run(_run())


def test_queued_request_rechecks_cooldown_after_lock_acquisition():
    async def _run():
        _reset_governors_for_testing()
        entered = asyncio.Event()
        release = asyncio.Event()
        first_session = MagicMock()
        first_session.request.return_value = _BlockingRequestContext(
            _FakeResponse(429), entered, release
        )
        second_session = _fake_session(_FakeResponse(200, payload={}))
        first = CosmoClient(first_session, "shared-account", "placeholder")
        second = CosmoClient(second_session, "shared-account", "placeholder")

        with patch("custom_components.cosmo.api.random.random", return_value=0):
            first_task = asyncio.create_task(
                first._request(
                    "GET", "https://api.myfilip.com/v2/map", auth=False
                )
            )
            await entered.wait()
            second_task = asyncio.create_task(
                second._request(
                    "GET", "https://api.myfilip.com/v2/map", auth=False
                )
            )
            await asyncio.sleep(0)
            second_session.request.assert_not_called()
            release.set()

            with pytest.raises(CosmoRateLimitError):
                await first_task
            with pytest.raises(CosmoRateLimitError):
                await second_task

        second_session.request.assert_not_called()
        assert second.rate_limit_streak == 1

    asyncio.run(_run())


def test_active_cooldown_fails_before_waiting_for_contended_account_lock():
    async def _run():
        _reset_governors_for_testing()
        first = CosmoClient(
            _fake_session(_FakeResponse(429)), "shared-account", "placeholder"
        )
        second_session = _fake_session(_FakeResponse(200, payload={}))
        second = CosmoClient(second_session, "shared-account", "placeholder")
        governor = first._get_governor()

        with (
            patch("custom_components.cosmo.api.random.random", return_value=0),
            patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()),
            pytest.raises(CosmoRateLimitError),
        ):
            await first._request(
                "GET", "https://api.myfilip.com/v2/map", auth=False
            )

        lock_entered = asyncio.Event()
        release_lock = asyncio.Event()

        async def _hold_lock():
            async with governor._rlock():
                lock_entered.set()
                await release_lock.wait()

        holder = asyncio.create_task(_hold_lock())
        await lock_entered.wait()
        try:
            with pytest.raises(CosmoRateLimitError):
                await asyncio.wait_for(
                    second._request(
                        "GET", "https://api.myfilip.com/v2/map", auth=False
                    ),
                    timeout=0.05,
                )
        finally:
            release_lock.set()
            await holder

        second_session.request.assert_not_called()
        assert second.rate_limit_streak == 1

    asyncio.run(_run())


def test_nested_login_and_following_map_request_each_apply_spacing():
    async def _run():
        _reset_governors_for_testing()
        request_starts: list[float] = []
        responses = iter(
            [
                _FakeResponse(
                    200,
                    payload={"data": {"accessToken": "synthetic"}},
                ),
                _FakeResponse(200, payload={"data": {"Devices": []}}),
            ]
        )
        session = MagicMock()

        def _request(*args, **kwargs):
            request_starts.append(time.monotonic())
            return _FakeRequestContext(next(responses))

        session.request.side_effect = _request
        client = CosmoClient(session, "shared-account", "placeholder")

        assert await client.get_devices() == []
        assert len(request_starts) == 2
        assert request_starts[1] - request_starts[0] >= 0.07

    asyncio.run(_run())


@pytest.mark.parametrize("header", [None, "", "malformed"])
def test_http_429_missing_or_malformed_retry_after_uses_five_minute_floor(header):
    async def _run():
        _reset_governors_for_testing()
        headers = {} if header is None else {"Retry-After": header}
        response = _FakeResponse(429, headers=headers)
        client = CosmoClient(_fake_session(response), "account", "placeholder")
        with (
            patch("custom_components.cosmo.api.random.random", return_value=0),
            patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()),
            pytest.raises(CosmoRateLimitError) as raised,
        ):
            await client._request("GET", "https://api.myfilip.com/v2/map", auth=False)
        assert raised.value.retry_after_seconds is not None
        assert raised.value.retry_after_seconds >= 300
        assert response.text_calls == 0
        assert response.json_calls == 0

    asyncio.run(_run())


def test_headerless_rate_limit_escalates_exact_local_sequence():
    async def _run():
        _reset_governors_for_testing()
        response = _FakeResponse(429)
        client = CosmoClient(_fake_session(response), "account", "placeholder")
        delays = []
        with (
            patch("custom_components.cosmo.api.random.random", return_value=0),
            patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()),
        ):
            for _ in range(5):
                client._get_governor()._cooldown_until = None
                with pytest.raises(CosmoRateLimitError) as raised:
                    await client._request(
                        "GET",
                        "https://api.myfilip.com/v2/map",
                        auth=False,
                    )
                delays.append(raised.value.retry_after_seconds)
        assert delays == [301, 901, 1801, 3601, 3601]

    asyncio.run(_run())


def test_http_429_honors_delta_date_and_long_server_floor():
    async def _one(header, minimum):
        _reset_governors_for_testing()
        response = _FakeResponse(429, headers={"Retry-After": header})
        client = CosmoClient(_fake_session(response), "account", "placeholder")
        with (
            patch("custom_components.cosmo.api.random.random", return_value=0),
            patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()),
            pytest.raises(CosmoRateLimitError) as raised,
        ):
            await client._request("GET", "https://api.myfilip.com/v2/map", auth=False)
        assert raised.value.retry_after_seconds is not None
        assert raised.value.retry_after_seconds >= minimum

    async def _run():
        await _one("7200", 7200)
        future = format_datetime(datetime.now(timezone.utc) + timedelta(minutes=10))
        await _one(future, 590)

    asyncio.run(_run())


def test_huge_retry_after_delta_arms_governor_without_overflow():
    async def _run():
        _reset_governors_for_testing()
        header = "9" * 4000
        server_floor = int(header)
        response = _FakeResponse(429, headers={"Retry-After": header})
        session = _fake_session(response)
        client = CosmoClient(session, "account", "placeholder")
        with (
            patch("custom_components.cosmo.api.random.random", return_value=0),
            patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()),
            pytest.raises(CosmoRateLimitError) as raised,
        ):
            await client._request("GET", "https://api.myfilip.com/v2/map", auth=False)
        assert raised.value.retry_after_seconds is not None
        assert raised.value.retry_after_seconds >= server_floor
        assert session.request.call_count == 1

        with pytest.raises(CosmoRateLimitError) as locally_blocked:
            await client._request("GET", "https://api.myfilip.com/v2/map", auth=False)
        assert locally_blocked.value.retry_after_seconds is not None
        assert locally_blocked.value.retry_after_seconds >= server_floor
        assert session.request.call_count == 1

    asyncio.run(_run())


@pytest.mark.parametrize("status", [401, 403])
def test_auth_status_precedes_retry_after(status):
    async def _run():
        _reset_governors_for_testing()
        response = _FakeResponse(status, headers={"Retry-After": "7200"})
        client = CosmoClient(_fake_session(response), "account", "placeholder")
        with (
            patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()),
            pytest.raises(CosmoAuthError),
        ):
            await client._request("GET", "https://api.myfilip.com/v2/map", auth=False)
        assert response.text_calls == 0
        assert response.json_calls == 0

    asyncio.run(_run())


def test_refresh_rate_or_transport_failure_does_not_fallback_to_login():
    async def _run(error):
        client = CosmoClient(AsyncMock(), "account", "placeholder")
        client._refresh = "synthetic"
        client.login = AsyncMock()
        with (
            patch.object(client, "_request", AsyncMock(side_effect=error)),
            pytest.raises(type(error)),
        ):
            await client._refresh_token()
        client.login.assert_not_awaited()

    asyncio.run(_run(CosmoRateLimitError("rate limited", retry_after_seconds=300)))
    asyncio.run(_run(CosmoApiError("transport")))


def test_account_governor_blocks_new_same_account_client_without_http():
    async def _run():
        _reset_governors_for_testing()
        first_response = _FakeResponse(429)
        first = CosmoClient(_fake_session(first_response), "shared-account", "placeholder")
        with (
            patch("custom_components.cosmo.api.random.random", return_value=0),
            patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()),
            pytest.raises(CosmoRateLimitError),
        ):
            await first._request("GET", "https://api.myfilip.com/v2/map", auth=False)

        second_session = _fake_session(_FakeResponse(200, payload={}))
        second = CosmoClient(second_session, "shared-account", "placeholder")
        with (
            patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()),
            pytest.raises(CosmoRateLimitError) as raised,
        ):
            await second._request("GET", "https://api.myfilip.com/v2/map", auth=False)
        assert raised.value.retry_after_seconds is not None
        assert raised.value.retry_after_seconds > 0
        second_session.request.assert_not_called()

    asyncio.run(_run())


def test_only_valid_map_success_resets_shared_rate_streak():
    async def _run():
        _reset_governors_for_testing()
        rate_response = _FakeResponse(429)
        client = CosmoClient(_fake_session(rate_response), "account", "placeholder")
        with (
            patch("custom_components.cosmo.api.random.random", return_value=0),
            patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()),
            pytest.raises(CosmoRateLimitError),
        ):
            await client._request("GET", "https://api.myfilip.com/v2/map", auth=False)
        assert client.rate_limit_streak == 1

        governor = client._get_governor()
        governor._cooldown_until = None
        client._access = "synthetic"

        settings = _FakeResponse(
            200,
            payload={"data": {"activeTrackingEnable": False}},
        )
        client._session = _fake_session(settings)
        with patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()):
            await client.get_settings("synthetic")
        assert client.rate_limit_streak == 1

        invalid_map = _FakeResponse(200, payload={"data": {"Devices": "invalid"}})
        client._session = _fake_session(invalid_map)
        with (
            patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()),
            pytest.raises(CosmoApiError),
        ):
            await client.get_devices()
        assert client.rate_limit_streak == 1

        valid = _FakeResponse(200, payload={"data": {"Devices": []}})
        client._session = _fake_session(valid)
        with patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()):
            assert await client.get_devices() == []
        assert client.rate_limit_streak == 0

    asyncio.run(_run())


def test_request_cancellation_propagates():
    async def _run():
        _reset_governors_for_testing()
        client = CosmoClient(
            _fake_session(enter_error=asyncio.CancelledError()),
            "account",
            "placeholder",
        )
        with (
            patch("custom_components.cosmo.api.asyncio.sleep", new=AsyncMock()),
            pytest.raises(asyncio.CancelledError),
        ):
            await client._request("GET", "https://api.myfilip.com/v2/map", auth=False)

    asyncio.run(_run())
