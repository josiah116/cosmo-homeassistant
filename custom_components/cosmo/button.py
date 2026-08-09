"""Buttons: Request location (with cooldown/lock) and Stop Active Tracking.

All location requests are strictly user-initiated, cooldown protected,
duplicate suppressed, bounded, with explicit stop. Fail closed on errors.
Never auto-scheduled.
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

    async def async_press(self) -> None:
        try:
            ok = await self.coordinator.async_request_locate()
            if not ok:
                _LOGGER.info("Locate suppressed by cooldown")
                return
        except (CosmoApiError, CosmoAuthError) as err:
            _LOGGER.warning("Cosmo locate request failed (fail-closed): %s", err)
            self.coordinator.last_locate_outcome = f"error:{type(err).__name__}"
            return
        except asyncio.CancelledError:
            self.coordinator.last_locate_outcome = "cancelled"
            raise

        # background re-poll for early stop on good accuracy (bounded)
        self._entry.async_create_background_task(
            self.hass,
            self._poll_for_fix_and_maybe_stop(),
            "cosmo_locate_poll",
        )

    async def _poll_for_fix_and_maybe_stop(self) -> None:
        """Poll until good fix or timeout; stop turbo early if accurate <=100m.
        Cleanup on unload/cancel handled by task mgmt.
        """
        previous_fix = None  # best effort
        try:
            dev0 = self.coordinator.data
            previous_fix = getattr(dev0, "gps_date", None) if dev0 else None
        except Exception:
            pass

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
                    # early stop
                    try:
                        await self.coordinator.async_stop_active_tracking()
                    except Exception as err:  # fail closed safe
                        _LOGGER.warning("early-stop failed (safe): %s", err)
                    return
            except asyncio.CancelledError:
                # cleanup on cancel/unload
                try:
                    await self.coordinator.async_stop_active_tracking()
                except Exception:
                    pass
                raise
            except Exception as err:
                _LOGGER.debug("poll iteration error (non fatal): %s", err)


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
