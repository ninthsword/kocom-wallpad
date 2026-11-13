"""Switch platform for Kocom Wallpad."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DeviceType
from .entity_base import KocomBaseEntity
from .gateway import KocomGateway
from .models import DeviceState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kocom switch platform."""
    gateway: KocomGateway = hass.data[DOMAIN][entry.entry_id]

    @callback
    def async_add_switch(devices=None):
        """Add switch entities."""
        if devices is None:
            devices = gateway.get_devices_from_platform(Platform.SWITCH)

        entities: list[KocomSwitch] = []
        for dev in devices:
            entity = KocomSwitch(gateway, dev)
            entities.append(entity)
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, gateway.async_signal_new_device(Platform.SWITCH), async_add_switch
        )
    )
    async_add_switch()


# Preserve the HA MRO mixing cached descriptors and dynamic properties.
class KocomSwitch(KocomBaseEntity, SwitchEntity):
    """Representation of a Kocom switch."""

    def __init__(self, gateway: KocomGateway, device: DeviceState) -> None:
        """Initialize the switch."""
        super().__init__(gateway, device)
        
    @property
    # HA declares a cached descriptor; retain dynamic property semantics.
    def device_class(self) -> SwitchDeviceClass:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._device.attribute.get("device_class", SwitchDeviceClass.SWITCH)

    @property
    # HA declares a cached descriptor; retain dynamic property semantics.
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        if self._device.key.device_type == DeviceType.ELEVATOR:
            return self.gateway.is_transport_available()
        return super().available

    @property
    # HA declares a cached descriptor; retain dynamic property semantics.
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        if self._device.key.device_type == DeviceType.ELEVATOR and not self.gateway.is_device_state_confirmed(self._device.key):
            return None
        return self._device.state

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_send_or_raise("스위치 켜기", "turn_on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._device.key.device_type == DeviceType.ELEVATOR:
            raise HomeAssistantError("엘리베이터는 호출만 지원하며 호출을 취소할 수 없습니다")
        await self._async_send_or_raise("스위치 끄기", "turn_off")
