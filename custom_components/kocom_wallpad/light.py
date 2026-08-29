"""Light platform for Kocom Wallpad."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity_base import KocomBaseEntity
from .gateway import KocomGateway
from .models import DeviceState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry, 
    async_add_entities: AddEntitiesCallback
) -> bool:
    """Set up Kocom light platform."""
    gateway: KocomGateway = hass.data[DOMAIN][entry.entry_id]

    @callback
    def async_add_light(devices=None):
        """Add light entities."""
        if devices is None:
            devices = gateway.get_devices_from_platform(Platform.LIGHT)

        entities: list[KocomLight] = []
        for dev in devices:
            entity = KocomLight(gateway, dev)
            entities.append(entity)
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, gateway.async_signal_new_device(Platform.LIGHT), async_add_light
        )
    )
    async_add_light()


class KocomLight(KocomBaseEntity, LightEntity):
    """Representation of a Kocom light."""

    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF

    def __init__(self, gateway: KocomGateway, device: DeviceState) -> None:
        """Initialize the light."""
        super().__init__(gateway, device)

    @property
    def is_on(self) -> bool:
        return self._device.state

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_send_or_raise("turn on the light", "turn_on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_send_or_raise("turn off the light", "turn_off")
