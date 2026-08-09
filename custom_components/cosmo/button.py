"""Buttons: Request location (with cooldown/lock) and Stop Active Tracking.

All location requests are strictly user-initiated, cooldown protected,
duplicate suppressed for full lifetime (lock + task), bounded, with explicit stop.
Fail closed on errors. Never auto-scheduled.
Use config-entry managed background tasks; cancellation does bounded cleanup + re-raises.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CosmoConfigEntry
from .api import CosmoApiError, CosmoAuthError
from .entity import CosmoEntity

_LOGGER = logging.getLogger(__name__)

# Polling delays for fresh fix after turbo start. User sees progress via polls.
_POLL_DELAYS = (8, 8, 12, 15, 20, 25, 30)  # ~118s max
_ACCEPTABLE_FIX_ACCURACY_METERS = 100
_CLEANUP_TIMEOUT = 5.0  # seconds for bounded stop on cancel


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CosmoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    async_add_entities(
        [
            CosmoLocateButton(
                rt.coordinator, entry.data["name"], entry.data.get("model"), entry
            ),
            CosmoStopActiveTrackingButton(
                rt.coordinator, entry.data["name"], entry.data.get("model"), entry
            ),
        ]
    )


class CosmoLocateButton(CosmoEntity, ButtonEntity):
    """Request a fresh location now (turbo mode). Cooldown protected."""

    _attr_translation_key = "request_location"
    _attr_icon = "mdi:crosshairs-gps"

    def __init__(self, coordinator, name, model, entry: ConfigEntry) -> None:
        super().__init__(coordinator, name, model)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.entry_id}_request_location"
        self._locate_task: asyncio.Task | None = None

    async def async_press(self) -> None:
        # Capture pre-command fix *before* the locate which does its own refresh/readback
        # so that first genuine fresh fix (newer gps_date) can be recognized.
        dev0 = self.coordinator.data
        previous_fix = getattr(dev0, "gps_date", None) if dev0 else None

        try:
            ok = await self.coordinator.async_request_locate()
            if not ok:
                _LOGGER.info("Locate suppressed by cooldown")
                return
        except (CosmoApiError, CosmoAuthError) as err:
            _LOGGER.warning("Cosmo locate request failed (fail-closed): %s", err)
            self.coordinator.last_locate_outcome = f"error:{type(err).__name__}"
            self.coordinator.async_update_listeners()
            return
        except asyncio.CancelledError:
            self.coordinator.last_locate_outcome = "cancelled"
            self.coordinator.async_update_listeners()
            raise

        # Duplicate suppression for the *full* locate workflow lifetime (not just lock)
        if self._locate_task and not self._locate_task.done():
            _LOGGER.debug("locate poll task already active; suppressing duplicate")
            return

        # background re-poll for early stop on good accuracy (bounded)
        # use config-entry-managed task
        self._locate_task = self._entry.async_create_background_task(
            self.hass,
            self._poll_for_fix_and_maybe_stop(previous_fix),
            "cosmo_locate_poll",
        )

    async def _poll_for_fix_and_maybe_stop(self, previous_fix: str | None = None) -> None:
        """Poll until good fix or timeout; stop turbo early if accurate <=100m.

        Only early stop on *newer* fix (different gps_date) with 0 < acc <=100m.
        Duplicate suppressed at caller. Cancellation does bounded cleanup then re-raises.
        Catch *only* expected errors; no blind Exception/pass.
        """
        if previous_fix is None:
            dev0 = self.coordinator.data
            previous_fix = getattr(dev0, "gps_date", None) if dev0 else None

        for delay in _POLL_DELAYS:
            await asyncio.sleep(delay)
            try:
                await self.coordinator.async_request_refresh()
                dev = self.coordinator.data
                if not dev:
                    continue
                current_fix = getattr(dev, "gps_date", None)
                try:
                    acc = float(getattr(dev, "radius", 0) or 0)
                except (TypeError, ValueError):
                    acc = None
                if (
                    current_fix
                    and current_fix != previous_fix
                    and acc is not None
                    and 0 < acc <= _ACCEPTABLE_FIX_ACCURACY_METERS
                ):
                    # early stop only on genuine newer good fix
                    try:
                        await self.coordinator.async_stop_active_tracking()
                    except (CosmoApiError, CosmoAuthError) as err:
                        _LOGGER.warning("early-stop failed (fail-closed): %s", err)
                    return
            except asyncio.CancelledError:
                # bounded cleanup on cancel/unload, then re-raise
                try:
                    await asyncio.wait_for(
                        self.coordinator.async_stop_active_tracking(), timeout=_CLEANUP_TIMEOUT
                    )
                except (asyncio.TimeoutError, CosmoApiError, CosmoAuthError):
                    # bounded, fail closed, no blind pass
                    pass
                raise
            except (CosmoApiError, CosmoAuthError, TimeoutError, asyncio.TimeoutError) as err:
                _LOGGER.debug("poll iteration error (non fatal): %s", err)
            # do not catch broad Exception

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any running locate poll task on entity unload (managed cleanup)."""
        if self._locate_task and not self._locate_task.done():
            self._locate_task.cancel()
            # do not await (may be during unload); task will cleanup bounded


class CosmoStopActiveTrackingButton(CosmoEntity, ButtonEntity):
    """Explicit button to stop Active Tracking (turbo) immediately."""

    _attr_translation_key = "stop_active_tracking"
    _attr_icon = "mdi:stop-circle"

    def __init__(self, coordinator, name, model, entry: ConfigEntry) -> None:
        super().__init__(coordinator, name, model)
        self._entry = entry
        self._attr_unique_id = f"{coordinator.entry_id}_stop_active_tracking"

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_stop_active_tracking()
        except (CosmoApiError, CosmoAuthError) as err:
            _LOGGER.warning("Stop active tracking failed (fail-closed): %s", err)
        except asyncio.CancelledError:
            raise
