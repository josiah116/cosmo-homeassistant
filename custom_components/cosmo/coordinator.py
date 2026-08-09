"""Data coordinator: polls /v2/map for the watch's last-known state."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CosmoApiError, CosmoAuthError, CosmoClient
from .const import DOMAIN
from .models import CosmoDevice

_SETTINGS_READBACK_MAP_GRACE = timedelta(minutes=3)


class CosmoCoordinator(DataUpdateCoordinator[CosmoDevice | None]):
    """Polls /v2/map (server cache) — never wakes the watch.

    Tracks health timestamps for diagnostics and sensors (cloud reachability,
    last successful poll). Uses always_update=False for unchanged payload behavior.
    Active state initialized conservatively from map data at poll time or explicit
    settings readback after commands. Never polls /settings on the 2min cycle.
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
            name=f"{DOMAIN}_{entry.entry_id}",
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
        # Active tracking control state (updated after commands + readback, or map poll)
        # Starts unknown (None); binary sensor unavailable when unknown/schema-invalid.
        # Initialized once at first poll or explicit readback. Not settings GET every poll.
        self.active_tracking: bool | None = None
        self.last_locate_outcome: str | None = None
        self.last_locate_time: datetime | None = None
        self._locate_lock = asyncio.Lock()  # for duplicate suppression
        self._last_locate_attempt: datetime | None = None
        self._active_readback_protected_until: datetime | None = None

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
            err = UpdateFailed("configured watch not found on account")
            self.last_error = err
            self.last_error_class = "UpdateFailed"
            raise err
        self.last_successful_poll = datetime.now(timezone.utc)
        self.last_error = None
        self.last_error_class = None
        self.update_active_from_data(device)  # pass fresh to avoid stale self.data
        # Force listener update for health timestamps (last_successful_poll, location_fix_age)
        # even when device payload equality would suppress under always_update=False.
        # This keeps diagnostic sensors fresh without changing the payload optimization intent.
        self.async_update_listeners()
        return device

    @property
    def cloud_reachable(self) -> bool:
        """True if last coordinator update succeeded (cloud reachability health)."""
        # `_async_update_data` notifies health listeners before the coordinator base
        # updates `last_update_success`; use our own already-updated health fields so
        # recovery renders correctly even when an unchanged payload suppresses the
        # base listener callback.
        return self.last_successful_poll is not None and self.last_error is None

    @property
    def last_poll_age(self) -> int | None:
        """Seconds since last successful *cloud poll* (distinct from GPS fix age)."""
        if not hasattr(self, "last_successful_poll") or self.last_successful_poll is None:
            return None
        return int((datetime.now(timezone.utc) - self.last_successful_poll).total_seconds())

    def _parse_gps_timestamp(self, ts: str | None) -> datetime | None:
        """Parse GPS fix timestamp safely; return None for invalid/missing."""
        if not ts or not isinstance(ts, str):
            return None
        try:
            dt = dt_util.parse_datetime(ts)
            if dt is None:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError, AttributeError):
            return None

    @property
    def location_fix_age(self) -> int | None:
        """Age (seconds) of the GPS fix from parsed device gps_date.

        Never uses last cloud poll time. Invalid, future or missing -> None.
        Timezone safe.
        """
        if not self.data:
            return None
        gps_ts = getattr(self.data, "gps_date", None)
        fix_dt = self._parse_gps_timestamp(gps_ts)
        if fix_dt is None:
            return None
        now = datetime.now(timezone.utc)
        if fix_dt > now:
            return None
        age = (now - fix_dt).total_seconds()
        if age < 0:
            return None
        return int(age)

    async def async_request_locate(self) -> bool:
        """User-initiated locate: enforce cooldown, no-auto, duplicate suppress, fail closed.

        Command readback uses GET /v2/settings (via client.get_settings) to validate
        active state after PUT. PUT success without validated readback does not claim success.
        """
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
                self.async_update_listeners()
                return False

            self._last_locate_attempt = now
            try:
                await self.client.set_active_tracking(
                    self.device_id, enable=True,
                    duration=ACTIVE_TRACKING_DURATION,
                    frequency=ACTIVE_TRACKING_FREQUENCY,
                )
                # Use settings readback (not map) for validated active state post-PUT
                settings = await self.client.get_settings(self.device_id)
                if settings is None or settings.active_tracking_enable is not True:
                    # No validated state readback -> do not claim success
                    self.last_locate_outcome = "error:tracking_not_enabled"
                    self.last_locate_time = now
                    self.active_tracking = (
                        settings.active_tracking_enable if settings is not None else None
                    )
                    self.async_update_listeners()
                    return False
                self.active_tracking = settings.active_tracking_enable
                self._active_readback_protected_until = (
                    datetime.now(timezone.utc) + _SETTINGS_READBACK_MAP_GRACE
                )
                self.last_locate_outcome = "success"
                self.last_locate_time = now
                self.async_update_listeners()
                # Also refresh map data for location etc (rate ok after command)
                await self.async_request_refresh()
                return True
            except (CosmoApiError, CosmoAuthError) as err:
                self.last_locate_outcome = f"error:{type(err).__name__}"
                self.last_locate_time = now
                self.active_tracking = None
                self.async_update_listeners()
                raise
            except asyncio.CancelledError:
                self.last_locate_outcome = "cancelled"
                self.last_locate_time = now
                self.active_tracking = None
                self.async_update_listeners()
                raise

    async def async_stop_active_tracking(self) -> bool:
        """Explicit stop for active tracking. Fail closed.

        Uses settings readback to validate the stop took effect.
        """
        from .const import ACTIVE_TRACKING_FREQUENCY
        try:
            await self.client.set_active_tracking(
                self.device_id, enable=False, duration=0, frequency=ACTIVE_TRACKING_FREQUENCY
            )
            settings = await self.client.get_settings(self.device_id)
            if settings is None or settings.active_tracking_enable is not False:
                # not validated off
                self.last_locate_outcome = "stop_error:settings_readback_invalid"
                self.last_locate_time = datetime.now(timezone.utc)
                self.active_tracking = None
                self.async_update_listeners()
                return False
            self.active_tracking = settings.active_tracking_enable
            self._active_readback_protected_until = (
                datetime.now(timezone.utc) + _SETTINGS_READBACK_MAP_GRACE
            )
            self.last_locate_outcome = "stopped"
            self.last_locate_time = datetime.now(timezone.utc)
            self.async_update_listeners()
            # refresh map too
            await self.async_request_refresh()
            return True
        except (CosmoApiError, CosmoAuthError) as err:
            self.last_locate_outcome = f"stop_error:{type(err).__name__}"
            self.last_locate_time = datetime.now(timezone.utc)
            self.active_tracking = None
            self.async_update_listeners()
            raise
        except asyncio.CancelledError:
            self.last_locate_outcome = "stop_cancelled"
            self.async_update_listeners()
            raise

    def update_active_from_data(self, device: CosmoDevice | None = None) -> None:
        """Sync active state from provided (or current) poll data.

        Uses map data here; command paths use explicit settings readback.
        Conservative None when unknown.
        A validated command readback temporarily outranks cached map data so the
        immediate refresh cannot undo authoritative command confirmation.
        """
        now = datetime.now(timezone.utc)
        if (
            self._active_readback_protected_until is not None
            and now < self._active_readback_protected_until
        ):
            return
        self._active_readback_protected_until = None
        dev = device or self.data
        if dev:
            val = getattr(dev, "active_tracking_enable", None)
            self.active_tracking = val if val is not None else None
        else:
            self.active_tracking = None
