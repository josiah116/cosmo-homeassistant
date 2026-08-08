"""Data coordinator: polls /v2/map for the watch's last-known state."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CosmoApiError, CosmoAuthError, CosmoClient
from .const import DOMAIN


class CosmoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls /v2/map (server cache) — never wakes the watch."""

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
        )
        self.client = client
        self.entry_id = entry.entry_id
        self.device_id = device_id


    async def _async_update_data(self) -> dict[str, Any]:
        try:
            device = await self.client.get_device(self.device_id)
        except CosmoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CosmoApiError as err:
            raise UpdateFailed(str(err)) from err
        if device is None:
            raise UpdateFailed(f"device {self.device_id} not found on account")
        return device
