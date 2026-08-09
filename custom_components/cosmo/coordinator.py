"""Data coordinator: polls /v2/map for the watch's last-known state."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CosmoApiError, CosmoAuthError, CosmoClient
from .const import DOMAIN
from .models import CosmoDevice


class CosmoCoordinator(DataUpdateCoordinator[CosmoDevice | None]):
    """Polls /v2/map (server cache) — never wakes the watch.

    Tracks health timestamps for diagnostics and sensors (cloud reachability,
    last successful poll). Uses always_update=False for unchanged payload behavior.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: CosmoClient,
        device_id: int | str,
        scan_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            logging.getLogger(__name__),
            name=f"{DOMAIN}_{device_id}",
            update_interval=scan_interval,
            config_entry=entry,
            # /v2/map returns stable JSON-derived dicts; skip listener callbacks
            # when the server cache has not changed.
            always_update=False,
        )
        self.client = client
        self.entry_id = entry.entry_id
        self.device_id = device_id
        # Health/diagnostics timestamps (in-memory)
        self.last_successful_poll: datetime | None = None
        self.last_error: Exception | None = None
        self.last_error_class: str | None = None
        # Active tracking control state (updated after commands + readback)
        self.active_tracking: bool = False
        self.last_locate_outcome: str | None = None
        self.last_locate_time: datetime | None = None
        self._locate_lock = asyncio.Lock()  # for duplicate suppression
        self._last_locate_attempt: datetime | None = None



    async def _async_update_data(self) -> CosmoDevice | None:
        try:
            device = await self.client.get_device(self.device_id)
        except CosmoAuthError as err:
            self.last_error = err
            self.last_error_class = "CosmoAuthError"
            raise ConfigEntryAuthFailed(str(err)) from err
        except CosmoApiError as err:
            self.last_error = err
            self.last_error_class = "CosmoApiError"
            raise UpdateFailed(str(err)) from err
        if device is None:
            err = UpdateFailed(f"device {self.device_id} not found on account")
            self.last_error = err
            self.last_error_class = "UpdateFailed"
            raise err
        self.last_successful_poll = datetime.now(timezone.utc)
        self.last_error = None
        self.last_error_class = None
        self.update_active_from_data()
        return device

    @property
    def cloud_reachable(self) -> bool:
        """True if last coordinator update succeeded (cloud reachability health)."""
        return getattr(self, "last_update_success", False)

    @property
    def last_poll_age(self) -> int | None:
        """Seconds since last successful poll (for location fix age / health)."""
        if not hasattr(self, "last_successful_poll") or self.last_successful_poll is None:
            return None
        return int((datetime.now(timezone.utc) - self.last_successful_poll).total_seconds())

    async def async_request_locate(self) -> bool:
        """User-initiated locate: enforce cooldown, no-auto, duplicate suppress, fail closed."""
        from .const import (
            ACTIVE_TRACKING_DURATION,
            ACTIVE_TRACKING_FREQUENCY,
            LOCATE_COOLDOWN,
        )

        async with self._locate_lock:
            now = datetime.now(timezone.utc)
            if self._last_locate_attempt and (now - self._last_locate_attempt) < LOCATE_COOLDOWN:
                self.last_locate_outcome = "cooldown"
                self.last_locate_time = now
                return False

            self._last_locate_attempt = now
            try:
                await self.client.set_active_tracking(
                    self.device_id, enable=True,
                    duration=ACTIVE_TRACKING_DURATION,
                    frequency=ACTIVE_TRACKING_FREQUENCY,
                )
                # immediate readback
                await self.async_request_refresh()
                dev = self.data
                self.active_tracking = getattr(dev, "active_tracking_enable", False) if dev else False
                self.last_locate_outcome = "success"
                self.last_locate_time = now
                return True
            except (CosmoApiError, CosmoAuthError) as err:
                self.last_locate_outcome = f"error:{type(err).__name__}"
                self.last_locate_time = now
                self.active_tracking = False
                raise
            except asyncio.CancelledError:
                self.last_locate_outcome = "cancelled"
                self.last_locate_time = now
                self.active_tracking = False
                raise

    async def async_stop_active_tracking(self) -> bool:
        """Explicit stop for active tracking. Fail closed."""
        from .const import ACTIVE_TRACKING_FREQUENCY
        try:
            await self.client.set_active_tracking(
                self.device_id, enable=False, duration=0, frequency=ACTIVE_TRACKING_FREQUENCY
            )
            await self.async_request_refresh()
            dev = self.data
            self.active_tracking = getattr(dev, "active_tracking_enable", False) if dev else False
            self.last_locate_outcome = "stopped"
            self.last_locate_time = datetime.now(timezone.utc)
            return True
        except (CosmoApiError, CosmoAuthError) as err:
            self.last_locate_outcome = f"stop_error:{type(err).__name__}"
            self.last_locate_time = datetime.now(timezone.utc)
            raise
        except asyncio.CancelledError:
            self.last_locate_outcome = "stop_cancelled"
            raise

    def update_active_from_data(self) -> None:
        """Sync active state from last poll data (for coordinator update)."""
        if self.data:
            self.active_tracking = getattr(self.data, "active_tracking_enable", False)
