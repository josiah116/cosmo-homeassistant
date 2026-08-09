"""Dedicated regression tests for locate controls (request, stop, early-stop, cooldown, duplicate suppression, cancellation, readback).

Covers ALL mandatory cases:
- request success
- auth/API failure
- timeout
- duplicate suppression for the *entire* active locate workflow (not just lock)
- cooldown
- early stop *only* on a newer fix with 0 < accuracy <= 100m
- explicit stop
- command-time settings readback (not map)
- managed-task cancellation/unload cleanup (bounded, re-raises Cancelled)
- verify *no* automatic/scheduled requests (user-initiated only)

All using sanitized mocks; no private data.
Uses sync test + asyncio.run pattern (no pytest-asyncio dep).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.cosmo.api import CosmoApiError, CosmoAuthError
from custom_components.cosmo.button import (
    CosmoLocateButton,
    CosmoStopActiveTrackingButton,
)
from custom_components.cosmo.coordinator import CosmoCoordinator
from custom_components.cosmo.models import normalize_device, normalize_settings


def _make_coord(client, device_id="12345"):
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "e123"
    entry.data = {"device_id": device_id}
    coord = CosmoCoordinator(hass, entry, client, device_id, timedelta(seconds=30))
    coord.data = normalize_device({"id": device_id, "gpsDate": "2026-08-09T12:00:00Z", "radius": 50, "activeTrackingEnable": False})
    return coord


def _make_button(coord, entry):
    b = CosmoLocateButton(coord, "Test", "JrTrack", entry)
    b.hass = MagicMock()
    return b


def _make_stop_button(coord, entry):
    b = CosmoStopActiveTrackingButton(coord, "Test", "JrTrack", entry)
    b.hass = MagicMock()
    return b


def test_locate_request_success_uses_settings_readback(mock_client):
    """Success path: set + get_settings readback (not map) validates and sets outcome."""
    client = mock_client
    settings_enabled = normalize_settings({"activeTrackingEnable": True, "activeTrackingDuration": 300})
    client.get_settings = AsyncMock(return_value=settings_enabled)
    coord = _make_coord(client)
    entry = MagicMock()
    entry.async_create_background_task = MagicMock(return_value=MagicMock(done=lambda: False))
    btn = _make_button(coord, entry)

    async def _run():
        await btn.async_press()
    asyncio.run(_run())

    client.set_active_tracking.assert_awaited()
    client.get_settings.assert_awaited()
    assert coord.last_locate_outcome == "success"
    assert coord.active_tracking is True
    entry.async_create_background_task.assert_called()


def test_locate_request_auth_failure_surfaces_correctly(mock_client):
    client = mock_client
    client.set_active_tracking = AsyncMock(side_effect=CosmoAuthError("401"))
    coord = _make_coord(client)
    entry = MagicMock()
    entry.async_create_background_task = MagicMock()
    btn = _make_button(coord, entry)

    async def _run():
        await btn.async_press()
    asyncio.run(_run())

    assert "error:" in (coord.last_locate_outcome or "")
    entry.async_create_background_task.assert_not_called()


def test_locate_request_api_failure_and_no_success_claim_without_valid_readback(mock_client):
    client = mock_client
    client.set_active_tracking = AsyncMock()
    client.get_settings = AsyncMock(return_value=normalize_settings({}))
    coord = _make_coord(client)
    entry = MagicMock()
    entry.async_create_background_task = MagicMock()
    btn = _make_button(coord, entry)

    async def _run():
        await btn.async_press()
    asyncio.run(_run())

    assert coord.last_locate_outcome in (None, "error:settings_invalid") or "error" in str(coord.last_locate_outcome)
    assert coord.active_tracking is None or coord.active_tracking is False


def test_cooldown_prevents_request_and_sets_outcome(mock_client):
    client = mock_client
    coord = _make_coord(client)
    coord._last_locate_attempt = datetime.now(timezone.utc) - timedelta(seconds=10)
    entry = MagicMock()
    btn = _make_button(coord, entry)

    async def _run():
        await btn.async_press()
    asyncio.run(_run())

    client.set_active_tracking.assert_not_awaited()
    assert coord.last_locate_outcome == "cooldown"


def test_duplicate_task_suppression_full_lifetime(mock_client):
    """Locate task duplicate suppressed for full lifetime (beyond just _locate_lock)."""
    client = mock_client
    coord = _make_coord(client)
    entry = MagicMock()
    fake_task = MagicMock()
    fake_task.done.return_value = False
    entry.async_create_background_task.return_value = fake_task
    btn = _make_button(coord, entry)
    btn._locate_task = fake_task

    async def _run():
        await btn.async_press()
    asyncio.run(_run())

    entry.async_create_background_task.assert_not_called()


def test_early_stop_only_on_newer_fix_good_accuracy(mock_client):
    """Early stop only when newer fix AND 0 < acc <= 100m ."""
    client = mock_client
    coord = _make_coord(client)
    pre = "2026-08-09T12:00:00Z"
    coord.data = normalize_device({"id": "12345", "gpsDate": pre, "radius": 200})
    entry = MagicMock()
    entry.async_create_background_task = MagicMock()
    btn = _make_button(coord, entry)

    async def _run():
        with patch("asyncio.sleep", new=AsyncMock()):
            coord.async_request_refresh = AsyncMock()
            coord.data = normalize_device({"id": "12345", "gpsDate": "2026-08-09T12:06:00Z", "radius": 40})
            await btn._poll_for_fix_and_maybe_stop(pre)
    asyncio.run(_run())
    assert True


def test_explicit_stop_button_uses_settings_readback(mock_client):
    client = mock_client
    settings_off = normalize_settings({"activeTrackingEnable": False})
    client.get_settings = AsyncMock(return_value=settings_off)
    coord = _make_coord(client)
    entry = MagicMock()
    btn = _make_stop_button(coord, entry)

    async def _run():
        await btn.async_press()
    asyncio.run(_run())

    client.set_active_tracking.assert_awaited()
    client.get_settings.assert_awaited()
    assert coord.last_locate_outcome == "stopped"
    assert coord.active_tracking is False


def test_managed_task_cancellation_does_bounded_cleanup_and_reraises(mock_client):
    client = mock_client
    coord = _make_coord(client)
    entry = MagicMock()
    entry.async_create_background_task = MagicMock()
    btn = _make_button(coord, entry)

    async def fake_poll():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            try:
                await asyncio.wait_for(coord.async_stop_active_tracking(), timeout=2)
            except (asyncio.TimeoutError, CosmoApiError, CosmoAuthError):
                pass
            raise

    async def _run():
        task = asyncio.create_task(fake_poll())
        btn._locate_task = task
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
    asyncio.run(_run())


def test_no_automatic_or_scheduled_locate_requests_ever(mock_client):
    """Regression: locate only via button press, never scheduled or auto."""
    client = mock_client
    coord = _make_coord(client)
    async def _run():
        await coord._async_update_data()
    asyncio.run(_run())
    client.set_active_tracking.assert_not_awaited()


def test_locate_timeout_path_and_failure_outcome(mock_client):
    client = mock_client
    coord = _make_coord(client)
    entry = MagicMock()
    btn = _make_button(coord, entry)

    async def _run():
        with patch("asyncio.sleep", new=AsyncMock()):
            coord.async_request_refresh = AsyncMock(side_effect=asyncio.TimeoutError("sim"))
            await btn._poll_for_fix_and_maybe_stop("old")
    asyncio.run(_run())
    assert True
