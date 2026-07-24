"""Data coordinator: polls /v2/map for the watch's last-known state."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CosmoApiError, CosmoAuthError, CosmoClient
from .const import DOMAIN
from .geocode import reverse_geocode


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
        # Reverse-geocode cache: only call Nominatim when the rounded fix moves.
        self._geo_key: tuple[float, float] | None = None
        self._geo_label: str | None = None
        self._geo_full: str | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            device = await self.client.get_device(self.device_id)
        except CosmoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CosmoApiError as err:
            raise UpdateFailed(str(err)) from err
        if device is None:
            raise UpdateFailed(f"device {self.device_id} not found on account")
        await self._attach_address(device)
        return device

    async def _attach_address(self, device: dict[str, Any]) -> None:
        """Add a human-readable ``address`` to the device dict (cached by fix).

        Geocoding failures are swallowed by reverse_geocode, and we keep the last
        good label, so a bad lookup never blanks the location or breaks the poll.
        """
        lat, lon = device.get("latitude"), device.get("longitude")
        if lat is not None and lon is not None:
            key = (round(float(lat), 4), round(float(lon), 4))  # ~11 m granularity
            if key != self._geo_key:
                label, full = await reverse_geocode(
                    async_get_clientsession(self.hass), lat, lon
                )
                if label:
                    self._geo_key, self._geo_label, self._geo_full = key, label, full
        device["address"] = self._geo_label
        device["address_full"] = self._geo_full
