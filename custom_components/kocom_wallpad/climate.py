"""Climate platform for Kocom Wallpad."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, Platform, UnitOfTemperature
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoredExtraData

from .const import DOMAIN, EVENT_THERMOSTAT_CHANGED, DeviceType, SubType
from .entity_base import KocomBaseEntity
from .gateway import KocomGateway
from .models import DeviceState

# Core 2026.11 declares the native_ names as cached descriptors and older Cores do not
# declare them, so no single pyright ignore fits every supported Core; typing the
# decorator as Any keeps these overrides dynamic and valid on all of them.
_dynamic_property: Any = property


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kocom climate platform."""
    gateway: KocomGateway = hass.data[DOMAIN][entry.entry_id]

    @callback
    def async_add_climate(devices=None):
        """Add climate entities."""
        if devices is None:
            devices = gateway.get_devices_from_platform(Platform.CLIMATE)

        entities: list[KocomClimate] = []
        for dev in devices:
            entity = KocomClimate(gateway, dev)
            entities.append(entity)
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, gateway.async_signal_new_device(Platform.CLIMATE), async_add_climate
        )
    )
    async_add_climate()


class KocomClimate(KocomBaseEntity, ClimateEntity):
    """Representation of a Kocom climate."""
    
    _attr_should_poll = False
    _enable_turn_on_off_backwards_compatibility = False

    _attr_min_temp = 5
    _attr_max_temp = 40

    def __init__(self, gateway: KocomGateway, device: DeviceState) -> None:
        """Initialize the climate."""
        super().__init__(gateway, device)
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE |
            ClimateEntityFeature.TURN_OFF |
            ClimateEntityFeature.TURN_ON
        )
        if device.attribute.get("feature_fan", False):
            self._attr_supported_features |= ClimateEntityFeature.FAN_MODE
        if device.attribute.get("feature_preset", False):
            self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE
        key = device.key
        self._is_primary_thermostat = (
            key.device_type == DeviceType.THERMOSTAT
            and key.sub_type == SubType.NONE
            and key.device_index == 0
            and 0x00 <= key.room_index <= 0xFE
        )
        self._change_sequence = 0
        self._change_source: str | None = None
        self._change_time: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Subscribe, then restore the room's home target from this entity's snapshot."""
        await super().async_added_to_hass()
        if not self._is_primary_thermostat:
            return
        last = await self.async_get_last_extra_data()
        if last is not None:
            self.gateway.controller.restore_home_target(
                self._device.key.room_index, last.as_dict().get("home_target")
            )

    @property
    def extra_restore_state_data(self) -> RestoredExtraData:
        """Add the room's home target to a primary thermostat's restore data."""
        data = super().extra_restore_state_data
        if not self._is_primary_thermostat:
            return data
        home = self.gateway.controller.home_target(self._device.key.room_index)
        if home is None:
            return data
        return RestoredExtraData({**data.as_dict(), "home_target": home})

    @callback
    def update_from_state(self) -> None:
        """Write state; record a newly labelled thermostat change once."""
        label = getattr(self._device, "_change_label", None)
        if (
            not self._is_primary_thermostat
            or label is None
            or label["sequence"] <= self._change_sequence
        ):
            self.async_write_ha_state()
            return
        self._change_sequence = label["sequence"]
        self._change_source = label["source"]
        self._change_time = datetime.now(UTC)
        context = label["context"]
        if label["source"] != "integration" or not isinstance(context, Context):
            context = Context()
        self.async_set_context(context)
        self.async_write_ha_state()
        self.hass.bus.async_fire(
            EVENT_THERMOSTAT_CHANGED,
            {
                "entity_id": self.entity_id,
                "source": label["source"],
                "changes": label["changes"],
            },
            context=context,
        )

    def _send_context(self) -> Context | None:
        """Return the service call context sent with a primary thermostat command."""
        if not self._is_primary_thermostat:
            return None
        return getattr(self, "_context", None)

    @property
    # HA declares a cached descriptor; retain dynamic property semantics.
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return whether a confirmation-bound climate command can be sent."""
        return self.gateway.is_transport_available()

    @property
    # HA declares a cached descriptor; retain dynamic property semantics.
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Expose physical confirmation and the last labelled change."""
        attributes: dict[str, Any] = {
            "physical_state_confirmed": self.gateway.is_device_state_confirmed(
                self._device.key
            )
        }
        if self._change_source is not None:
            attributes["change_source"] = self._change_source
            attributes["change_time"] = self._change_time
        return attributes

    @property
    def _state_is_confirmed(self) -> bool:
        return self.gateway.is_device_state_confirmed(self._device.key)

    @property
    # HA declares a cached descriptor; retain dynamic property semantics.
    def hvac_mode(self) -> HVACMode | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        if not self._state_is_confirmed:
            return None
        return self._device.state["hvac_mode"]
    
    @property
    # HA declares a cached descriptor; retain dynamic property semantics.
    def hvac_modes(self) -> list[HVACMode]:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._device.attribute["hvac_modes"]
    
    # ----------------------------------------------------------------
    # [추가됨] 난방 중/유휴 상태 표시를 위한 hvac_action 속성 추가
    # ----------------------------------------------------------------
    @property
    # HA declares a cached descriptor; retain dynamic property semantics.
    def hvac_action(self) -> HVACAction | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the current running hvac operation."""
        if not self._state_is_confirmed:
            return None
        # 1. 시스템이 꺼져 있으면 '꺼짐(Off)' 표시
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        # 2. 난방 모드일 때 동작 상태 판단
        if self.hvac_mode == HVACMode.HEAT:
            current = self.native_current_temperature
            target = self.native_target_temperature
            
            # controller.py에서 값을 float로 넘겨주지만, 혹시 모를 None 체크
            if current is not None and target is not None:
                # 설정 온도가 현재 온도보다 높으면 '난방 중(Heating)'
                if target > current:
                    return HVACAction.HEATING
                # 설정 온도가 낮거나 같으면 '대기(Idle)'
                else:
                    return HVACAction.IDLE
        
        # 그 외의 경우 대기 상태
        return HVACAction.IDLE
    # ----------------------------------------------------------------
    
    @property
    # HA declares a cached descriptor; retain dynamic property semantics.
    def fan_mode(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        if not self._state_is_confirmed:
            return None
        return self._device.state["fan_mode"]
    
    @property
    # HA declares a cached descriptor; retain dynamic property semantics.
    def fan_modes(self) -> list[str]:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._device.attribute["fan_modes"]

    @property
    # HA declares a cached descriptor; retain dynamic property semantics.
    def preset_mode(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        if not self._state_is_confirmed:
            return None
        return self._device.state["preset_mode"]
    
    @property
    # HA declares a cached descriptor; retain dynamic property semantics.
    def preset_modes(self) -> list[str]:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._device.attribute["preset_modes"]

    @_dynamic_property
    def native_current_temperature(self) -> float | None:
        if not self._state_is_confirmed:
            return None
        return self._device.state["current_temp"]

    @_dynamic_property
    def native_target_temperature(self) -> float | None:
        if not self._state_is_confirmed:
            return None
        return self._device.state["target_temp"]

    @_dynamic_property
    def native_temperature_unit(self) -> str:
        return UnitOfTemperature.CELSIUS

    @property
    # HA declares a cached descriptor; retain dynamic property semantics.
    def target_temperature_step(self) -> float:  # pyright: ignore[reportIncompatibleVariableOverride]
        return self._device.attribute["temp_step"]
    
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        args = {"hvac_mode": hvac_mode}
        await self._async_send_or_raise(
            "set HVAC mode", "set_hvac", context=self._send_context(), **args
        )
        
    async def async_set_fan_mode(self, fan_mode: str) -> None:
        args = {"fan_mode": fan_mode}
        await self._async_send_or_raise("set fan mode", "set_fan", **args)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        args = {"preset_mode": preset_mode}
        await self._async_send_or_raise(
            "set preset mode", "set_preset", context=self._send_context(), **args
        )

    async def async_set_temperature(self, **kwargs) -> None:
        args = {"target_temp": float(kwargs[ATTR_TEMPERATURE])}
        await self._async_send_or_raise(
            "set temperature", "set_temperature", context=self._send_context(), **args
        )

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Opt this class's added/removed hooks in to in-place entity_id changes."""
        super().async_entity_id_changed(old_entity_id)


if not hasattr(ClimateEntity, "native_current_temperature"):
    # Cores before 2026.11 read only the unprefixed names. Assigned here, not in the class
    # body, because Core 2026.11 declares them Final and warns on a subclass defining them.
    for _native_name in (
        "native_current_temperature",
        "native_target_temperature",
        "native_temperature_unit",
    ):
        setattr(KocomClimate, _native_name.removeprefix("native_"), getattr(KocomClimate, _native_name))
