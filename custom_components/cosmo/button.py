"""Button to request an on-demand fresh GPS fix from the watch.

Enables FiLIP "active tracking" (turbo mode): the watch reports a fix every
~10s for a few minutes. This is the ONLY action that wakes the watch — it is
never triggered on a schedule. After enabling it we re-poll /v2/map a few
times so the tracker reflects the fresh fix as it lands.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CosmoConfigEntry
from .api import CosmoApiError
from .const import ACTIVE_TRACKING_DURATION, ACTIVE_TRACKING_FREQUENCY
from .entity import CosmoEntity

_LOGGER = logging.getLogger(__name__)

# Re-poll the map after enabling turbo so the fresh fix shows up without waiting
# for the next scheduled coordinator update. Runs in the background (see async_press),
# so the window is generous — a slow watch can take well over a minute to report.
# Frequent early (most fixes land in 10-40s), spread out to ~2 minutes total.
_POLL_DELAYS = (8, 8, 12, 15, 20, 25, 30)  # ~118s


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CosmoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    async_add_entities(
        [CosmoLocateButton(rt.coordinator, entry.data["name"], entry.data.get("model"))]
    )


class CosmoLocateButton(CosmoEntity, ButtonEntity):
    """Request a fresh location now (turbo mode)."""

    _attr_translation_key = "request_location"
    _attr_icon = "mdi:crosshairs-gps"

    def __init__(self, coordinator, name, model) -> None:
        super().__init__(coordinator, name, model)
        self._attr_unique_id = f"{coordinator.entry_id}_request_location"

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.set_active_tracking(
                self.coordinator.device_id,
                enable=True,
                duration=ACTIVE_TRACKING_DURATION,
                frequency=ACTIVE_TRACKING_FREQUENCY,
            )
        except CosmoApiError as err:
            _LOGGER.warning("Cosmo locate request failed: %s", err)
            return
        # Re-poll in the background: the fresh fix takes ~40s to land, and we must
        # NOT block the caller that long (a voice agent's whole turn would hang).
        # The press returns now; the tracker/sensors update as the fix arrives.
        self.hass.async_create_task(self._poll_for_fix(), name="cosmo_locate_poll")

    async def _poll_for_fix(self) -> None:
        for delay in _POLL_DELAYS:
            await asyncio.sleep(delay)
            await self.coordinator.async_request_refresh()
