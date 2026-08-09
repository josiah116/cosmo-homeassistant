"""Behavioral regression tests for user-initiated locate controls.

All payloads are synthetic and contain no credentials, personal identifiers, or
coordinates. Tests invoke production coordinator/button methods directly.
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


class _ManagedEntry:
    """Minimal config-entry task manager that creates real asyncio tasks."""

    def __init__(self) -> None:
        self.entry_id = "entry-test"
        self.tasks: list[asyncio.Task] = []

    def async_create_background_task(self, hass, coro, name):
        task = asyncio.create_task(coro, name=name)
        self.tasks.append(task)
        return task


def _make_coord(client, device_id="watch-test") -> CosmoCoordinator:
    entry = MagicMock()
    entry.entry_id = "entry-test"
    coord = CosmoCoordinator(
        MagicMock(), entry, client, device_id, timedelta(seconds=30)
    )
    coord.data = normalize_device(
        {
            "id": device_id,
            "gpsDate": "2026-08-09T12:00:00Z",
            "radius": 50,
            "activeTrackingEnable": False,
        }
    )
    coord.async_update_listeners = MagicMock()
    return coord


def _make_button(coord, entry=None) -> CosmoLocateButton:
    button = CosmoLocateButton(coord, "Mock Watch", "JrTrack", entry or _ManagedEntry())
    button.hass = MagicMock()
    return button


def _make_stop_button(coord, entry=None) -> CosmoStopActiveTrackingButton:
    button = CosmoStopActiveTrackingButton(
        coord, "Mock Watch", "JrTrack", entry or _ManagedEntry()
    )
    button.hass = MagicMock()
    return button


def test_request_success_requires_true_settings_readback(mock_client):
    async def _run():
        client = mock_client
        client.get_settings = AsyncMock(
            return_value=normalize_settings({"activeTrackingEnable": True})
        )
        coord = _make_coord(client)
        coord.async_request_refresh = AsyncMock()

        assert await coord.async_request_locate() is True
        client.set_active_tracking.assert_awaited_once()
        client.get_settings.assert_awaited_once_with("watch-test")
        coord.async_request_refresh.assert_awaited_once()
        assert coord.active_tracking is True
        assert coord.last_locate_outcome == "success"

    asyncio.run(_run())


def test_request_false_readback_fails_closed(mock_client):
    async def _run():
        client = mock_client
        client.get_settings = AsyncMock(
            return_value=normalize_settings({"activeTrackingEnable": False})
        )
        coord = _make_coord(client)
        coord.async_request_refresh = AsyncMock()

        assert await coord.async_request_locate() is False
        assert coord.active_tracking is False
        assert coord.last_locate_outcome == "error:tracking_not_enabled"
        coord.async_request_refresh.assert_not_awaited()

    asyncio.run(_run())


@pytest.mark.parametrize("error", [CosmoAuthError("auth"), CosmoApiError("api")])
def test_request_errors_are_classified_and_reraised(mock_client, error):
    async def _run():
        client = mock_client
        client.set_active_tracking = AsyncMock(side_effect=error)
        coord = _make_coord(client)

        with pytest.raises(type(error)):
            await coord.async_request_locate()
        assert coord.last_locate_outcome == f"error:{type(error).__name__}"
        assert coord.active_tracking is None

    asyncio.run(_run())


def test_cooldown_blocks_put(mock_client):
    async def _run():
        coord = _make_coord(mock_client)
        coord._last_locate_attempt = datetime.now(timezone.utc) - timedelta(seconds=10)

        assert await coord.async_request_locate() is False
        mock_client.set_active_tracking.assert_not_awaited()
        assert coord.last_locate_outcome == "cooldown"

    asyncio.run(_run())


def test_duplicate_workflow_is_suppressed_before_battery_affecting_request(mock_client):
    async def _run():
        coord = _make_coord(mock_client)
        coord.async_request_locate = AsyncMock(return_value=True)
        button = _make_button(coord)
        blocker = asyncio.Event()
        task = asyncio.create_task(blocker.wait())
        button._locate_task = task
        try:
            await button.async_press()
            coord.async_request_locate.assert_not_awaited()
            assert coord.last_locate_outcome == "duplicate"
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(_run())


def test_button_passes_pre_command_fix_to_managed_poll_task(mock_client):
    async def _run():
        coord = _make_coord(mock_client)
        coord.async_request_locate = AsyncMock(return_value=True)
        entry = _ManagedEntry()
        button = _make_button(coord, entry)
        button._poll_for_fix_and_maybe_stop = AsyncMock()

        await button.async_press()
        await entry.tasks[0]
        button._poll_for_fix_and_maybe_stop.assert_awaited_once_with(
            "2026-08-09T12:00:00Z"
        )

    asyncio.run(_run())


def test_early_stop_requires_new_fix_and_good_accuracy(mock_client):
    async def _run():
        coord = _make_coord(mock_client)
        coord.async_stop_active_tracking = AsyncMock(return_value=True)

        async def _refresh():
            coord.data = normalize_device(
                {
                    "id": "watch-test",
                    "gpsDate": "2026-08-09T12:01:00Z",
                    "radius": 40,
                }
            )

        coord.async_request_refresh = AsyncMock(side_effect=_refresh)
        button = _make_button(coord)
        with patch("custom_components.cosmo.button._POLL_DELAYS", (0,)):
            await button._poll_for_fix_and_maybe_stop("2026-08-09T12:00:00Z")
        coord.async_stop_active_tracking.assert_awaited_once()

    asyncio.run(_run())


@pytest.mark.parametrize(
    ("fix", "accuracy"),
    [
        ("2026-08-09T12:00:00Z", 40),
        ("2026-08-09T12:01:00Z", 0),
        ("2026-08-09T12:01:00Z", 101),
        ("2026-08-09T12:01:00Z", None),
    ],
)
def test_early_stop_rejects_old_or_inaccurate_fix(mock_client, fix, accuracy):
    async def _run():
        coord = _make_coord(mock_client)
        coord.data = normalize_device(
            {"id": "watch-test", "gpsDate": fix, "radius": accuracy}
        )
        coord.async_request_refresh = AsyncMock()
        coord.async_stop_active_tracking = AsyncMock(return_value=True)
        button = _make_button(coord)

        with patch("custom_components.cosmo.button._POLL_DELAYS", (0,)):
            await button._poll_for_fix_and_maybe_stop("2026-08-09T12:00:00Z")
        coord.async_stop_active_tracking.assert_not_awaited()
        assert coord.last_locate_outcome == "timeout"

    asyncio.run(_run())


def test_explicit_stop_requires_false_settings_readback(mock_client):
    async def _run():
        client = mock_client
        client.get_settings = AsyncMock(
            return_value=normalize_settings({"activeTrackingEnable": False})
        )
        coord = _make_coord(client)
        coord.async_request_refresh = AsyncMock()
        button = _make_stop_button(coord)

        await button.async_press()
        client.set_active_tracking.assert_awaited_once()
        client.get_settings.assert_awaited_once_with("watch-test")
        coord.async_request_refresh.assert_awaited_once()
        assert coord.active_tracking is False
        assert coord.last_locate_outcome == "stopped"

    asyncio.run(_run())


def test_cancel_during_sleep_attempts_cleanup_and_reraises(mock_client):
    async def _run():
        coord = _make_coord(mock_client)
        coord.async_stop_active_tracking = AsyncMock(return_value=True)
        button = _make_button(coord)

        with patch("custom_components.cosmo.button._POLL_DELAYS", (3600,)):
            task = asyncio.create_task(
                button._poll_for_fix_and_maybe_stop("2026-08-09T12:00:00Z")
            )
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        coord.async_stop_active_tracking.assert_awaited_once()

    asyncio.run(_run())


def test_entity_removal_awaits_cancelled_workflow_cleanup(mock_client):
    async def _run():
        coord = _make_coord(mock_client)
        coord.async_stop_active_tracking = AsyncMock(return_value=True)
        button = _make_button(coord)

        with patch("custom_components.cosmo.button._POLL_DELAYS", (3600,)):
            task = asyncio.create_task(
                button._poll_for_fix_and_maybe_stop("2026-08-09T12:00:00Z")
            )
            button._locate_task = task
            await asyncio.sleep(0)
            await button.async_will_remove_from_hass()
        assert task.done()
        assert task.cancelled()
        coord.async_stop_active_tracking.assert_awaited_once()

    asyncio.run(_run())


def test_timeout_consumes_bounded_retries_and_records_outcome(mock_client):
    async def _run():
        coord = _make_coord(mock_client)
        coord.async_request_refresh = AsyncMock(side_effect=asyncio.TimeoutError)
        button = _make_button(coord)

        with patch("custom_components.cosmo.button._POLL_DELAYS", (0, 0, 0)):
            await button._poll_for_fix_and_maybe_stop("2026-08-09T12:00:00Z")
        assert coord.async_request_refresh.await_count == 3
        assert coord.last_locate_outcome == "timeout"
        assert coord.last_locate_time is not None

    asyncio.run(_run())


def test_normal_poll_never_starts_active_tracking(mock_client):
    async def _run():
        coord = _make_coord(mock_client)
        mock_client.get_device = AsyncMock(
            return_value=normalize_device({"id": "watch-test"})
        )
        await coord._async_update_data()
        mock_client.set_active_tracking.assert_not_awaited()
        mock_client.get_settings.assert_not_awaited()

    asyncio.run(_run())
