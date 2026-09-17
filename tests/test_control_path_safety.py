"""Deterministic regression tests for the Kocom control path.

These offline tests use narrow module shims for deterministic protocol checks.
The development type checker resolves the real, locked Home Assistant dependency.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import types
import unittest
from collections.abc import Callable
from dataclasses import asdict, fields
from enum import Enum, IntFlag
from pathlib import Path
from unittest.mock import AsyncMock, create_autospec, patch


def _module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def _set_attributes(module: types.ModuleType, **attributes: object) -> None:
    """Populate only the declared fixture surface on dynamically created modules."""
    for name, value in attributes.items():
        setattr(module, name, value)


def _install_homeassistant_shims() -> None:
    if "homeassistant" in sys.modules:
        return
    homeassistant = _module("homeassistant")
    const = _module("homeassistant.const")

    class Platform(Enum):
        LIGHT = "light"
        SWITCH = "switch"
        CLIMATE = "climate"
        FAN = "fan"
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"

    _set_attributes(const, Platform=Platform)
    _set_attributes(const, UnitOfTemperature=types.SimpleNamespace(CELSIUS="°C"))
    _set_attributes(const, ATTR_TEMPERATURE="temperature")
    _set_attributes(const, CONF_HOST="host")
    _set_attributes(const, CONF_PORT="port")
    _set_attributes(const, EVENT_HOMEASSISTANT_STOP="stop")
    _set_attributes(homeassistant, const=const)

    core = _module("homeassistant.core")
    _set_attributes(core, HomeAssistant=object)
    _set_attributes(core, Event=object)
    _set_attributes(core, callback=lambda function: function)
    config_entries = _module("homeassistant.config_entries")
    _set_attributes(config_entries, ConfigEntry=object)

    components = _module("homeassistant.components")
    climate = _module("homeassistant.components.climate")
    climate_const = _module("homeassistant.components.climate.const")
    _set_attributes(climate_const, PRESET_NONE="none")
    _set_attributes(climate_const, PRESET_AWAY="away")
    _set_attributes(climate_const, FAN_LOW="low")
    _set_attributes(climate_const, FAN_MEDIUM="medium")
    _set_attributes(climate_const, FAN_HIGH="high")
    _set_attributes(climate_const, FAN_AUTO="auto")
    _set_attributes(climate_const, HVACMode=types.SimpleNamespace(
        HEAT="heat", OFF="off", COOL="cool", FAN_ONLY="fan_only", DRY="dry", AUTO="auto"
    ))
    _set_attributes(climate_const, HVACAction=types.SimpleNamespace(OFF="off", HEATING="heating", IDLE="idle"))
    _set_attributes(climate_const, ClimateEntityFeature=types.SimpleNamespace(
        TARGET_TEMPERATURE=1, TURN_OFF=2, TURN_ON=4, FAN_MODE=8, PRESET_MODE=16
    ))
    _set_attributes(climate, HVACMode=vars(climate_const)["HVACMode"])
    _set_attributes(climate, ClimateEntity=object)

    class _EntityDescription:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    _set_attributes(climate, ClimateEntityDescription=_EntityDescription)
    _set_attributes(components, climate=climate)
    light = _module("homeassistant.components.light")
    light_const = _module("homeassistant.components.light.const")
    _set_attributes(light, LightEntityDescription=_EntityDescription)
    _set_attributes(light, LightEntity=object)
    _set_attributes(light, ColorMode=types.SimpleNamespace(ONOFF="onoff"))
    _set_attributes(light_const, ColorMode=vars(light)["ColorMode"])
    _set_attributes(components, light=light)
    for package, attr, value in (
        ("sensor", "SensorDeviceClass", types.SimpleNamespace(TEMPERATURE="temperature", CO2="co2", PM10="pm10", PM25="pm25", VOLATILE_ORGANIC_COMPOUNDS="volatile_organic_compounds", HUMIDITY="humidity")),
        ("binary_sensor", "BinarySensorDeviceClass", types.SimpleNamespace(PROBLEM="problem", MOTION="motion")),
        ("switch", "SwitchDeviceClass", types.SimpleNamespace(OUTLET="outlet")),
    ):
        module = _module(f"homeassistant.components.{package}")
        setattr(module, attr, value)
        if package == "switch":
            _set_attributes(module, SwitchEntity=object)
        setattr(module, f"{package.title().replace('_', '')}EntityDescription", _EntityDescription)
        setattr(components, package, module)

    fan = _module("homeassistant.components.fan")
    _set_attributes(fan, FanEntityDescription=_EntityDescription)
    _set_attributes(fan, FanEntity=object)

    class FanEntityFeature(IntFlag):
        SET_SPEED = 1
        TURN_OFF = 2
        TURN_ON = 4
        PRESET_MODE = 8

    _set_attributes(fan, FanEntityFeature=FanEntityFeature)
    _set_attributes(components, fan=fan)

    helpers = _module("homeassistant.helpers")
    entity = _module("homeassistant.helpers.entity")

    class _RestoreEntity:
        def __init__(self):
            pass

        @property
        def unique_id(self):
            return getattr(self, "_attr_unique_id", None)

        def async_write_ha_state(self):
            pass

    class _DeviceInfo(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    _set_attributes(entity, DeviceInfo=_DeviceInfo)
    entity_registry = _module("homeassistant.helpers.entity_registry")
    _set_attributes(entity_registry, async_get=lambda hass: None)
    _set_attributes(entity_registry, async_entries_for_config_entry=lambda registry, entry_id: [])
    device_registry = _module("homeassistant.helpers.device_registry")
    _set_attributes(device_registry, DeviceInfo=_DeviceInfo)
    _set_attributes(device_registry, async_get=lambda hass: None)
    restore_state = _module("homeassistant.helpers.restore_state")
    _set_attributes(restore_state, async_get=lambda hass: types.SimpleNamespace(last_states={}))
    _set_attributes(restore_state, RestoreEntity=_RestoreEntity)
    _set_attributes(restore_state, RestoredExtraData=lambda value: value)
    dispatcher = _module("homeassistant.helpers.dispatcher")
    _set_attributes(dispatcher, async_dispatcher_send=lambda *args, **kwargs: None)
    _set_attributes(dispatcher, async_dispatcher_connect=lambda *_args, **_kwargs: lambda: None)
    entity_platform = _module("homeassistant.helpers.entity_platform")
    _set_attributes(entity_platform, AddEntitiesCallback=object)
    exceptions = _module("homeassistant.exceptions")
    _set_attributes(exceptions, HomeAssistantError=type("HomeAssistantError", (Exception,), {}))
    _set_attributes(helpers, entity=entity)
    _set_attributes(helpers, entity_registry=entity_registry)
    _set_attributes(helpers, device_registry=device_registry)
    _set_attributes(helpers, restore_state=restore_state)
    _set_attributes(helpers, dispatcher=dispatcher)
    _set_attributes(_module("serialx"), open_serial_connection=None)
    util = _module("homeassistant.util")
    percentage = _module("homeassistant.util.percentage")
    _set_attributes(percentage, ordered_list_item_to_percentage=lambda values, value: int(
        values.index(value) * 100 / max(len(values) - 1, 1)
    ))
    _set_attributes(percentage, percentage_to_ordered_list_item=lambda values, value: values[
        round((len(values) - 1) * value / 100)
    ])
    _set_attributes(util, percentage=percentage)


_install_homeassistant_shims()

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

import custom_components.kocom_wallpad as integration_module
from custom_components.kocom_wallpad import async_setup_entry
from custom_components.kocom_wallpad import gateway as gateway_module
from custom_components.kocom_wallpad.climate import KocomClimate
from custom_components.kocom_wallpad.const import DeviceType, SubType
from custom_components.kocom_wallpad.controller import KocomController, PacketFrame
from custom_components.kocom_wallpad.fan import KocomFan
from custom_components.kocom_wallpad.gateway import KocomGateway, _CmdItem
from custom_components.kocom_wallpad.light import KocomLight
from custom_components.kocom_wallpad.models import DeviceKey, DeviceState
from custom_components.kocom_wallpad.switch import KocomSwitch
from custom_components.kocom_wallpad.transport import AsyncConnection


class _Registry:
    def get(self, key):
        return None


class _ControllerGateway:
    registry = _Registry()

    def on_device_state(self, _state):
        pass


def _thermostat_frame(
    target: int,
    current: int,
    *,
    room: int = 1,
    packet_type: int = 0x0B,
    command: int = 0x00,
    dest_device: int = 0x01,
    dest_room: int = 0x00,
    mirrored: bool = False,
) -> PacketFrame:
    raw = bytearray(21)
    raw[:2] = bytes((0xAA, 0x55))
    raw[2:5] = bytes((0x30, (packet_type << 4) | 0x0C, 0x00))
    if mirrored:
        raw[5:7] = bytes((0x36, room))
        raw[7:9] = bytes((0x01, 0x00))
    else:
        raw[5:7] = bytes((dest_device, dest_room))
        raw[7:9] = bytes((0x36, room))
    raw[9] = command
    raw[10:18] = bytes((0x10, 0x00, target, 0, current, 0, 0, 0))
    raw[18] = sum(raw[2:18]) % 256
    raw[19:21] = bytes((0x0D, 0x0D))
    return PacketFrame(bytes(raw))


class _FakeConnection(AsyncConnection):
    def __init__(self, *_args, **_kwargs):
        self.connected = True
        self.gateway = None
        self.on_send: Callable[[bytes], None] | None = None
        self.sent: list[bytes] = []
        self.idle = True

    async def open(self):
        return self.connected

    async def close(self):
        self.connected = False

    def _is_connected(self):
        return self.connected

    def idle_since(self):
        return 99.0 if self.idle else 0.0

    async def send(self, data: bytes) -> int:
        self.sent.append(data)
        if self.on_send:
            self.on_send(data)
        return len(data)

    async def recv(self, nbytes: int, timeout: float = 0.05) -> bytes:
        return b""


class DeviceStateStructureTests(unittest.TestCase):
    def test_dynamic_metadata_preserves_dataclass_contract(self):
        expected_fields = ("key", "platform", "attribute", "state")
        self.assertEqual(expected_fields, tuple(inspect.signature(DeviceState).parameters))
        self.assertEqual(expected_fields, tuple(field.name for field in fields(DeviceState)))
        key = DeviceKey(DeviceType.THERMOSTAT, 1, 0, SubType.NONE)
        state = DeviceState(key, Platform.CLIMATE, {"temp_step": 1.0}, {"target_temp": 21.0})
        expected = {
            "key": asdict(key),
            "platform": Platform.CLIMATE,
            "attribute": {"temp_step": 1.0},
            "state": {"target_temp": 21.0},
        }
        self.assertEqual(expected, asdict(state))
        self.assertFalse(hasattr(state, "_packet"))
        self.assertFalse(hasattr(state, "_is_register"))
        state._packet = b"packet"
        state._is_register = False
        self.assertEqual(b"packet", state._packet)
        self.assertIs(False, state._is_register)
        self.assertEqual(expected, asdict(state))
        self.assertEqual(expected_fields, tuple(field.name for field in fields(state)))


class ControllerSafetyTests(unittest.TestCase):
    def test_thermostat_report_uses_current_packet_without_lag(self):
        controller = KocomController(_ControllerGateway())
        first_states = controller._handle_thermostat(_thermostat_frame(20, 19))
        second_states = controller._handle_thermostat(_thermostat_frame(21, 20))
        assert first_states is not None and second_states is not None
        first, second = first_states[0], second_states[0]
        assert isinstance(first.state, dict) and isinstance(second.state, dict)
        self.assertEqual((20.0, 19.0), (first.state["target_temp"], first.state["current_temp"]))
        self.assertEqual((21.0, 20.0), (second.state["target_temp"], second.state["current_temp"]))
        self.assertEqual(21.0, controller._device_storage[f"{second.key.unique_id}_thermo_target"])
        self.assertEqual(1.0, second.attribute["temp_step"])

    def test_temperature_encoding_is_whole_degree_only(self):
        controller = KocomController(_ControllerGateway())
        key = DeviceKey(DeviceType.THERMOSTAT, 1, 0, SubType.NONE)
        packet, _, _ = controller.generate_command(key, "set_temperature", target_temp=21.0)
        self.assertEqual(21, packet[12])
        with self.assertRaises(ValueError):
            controller.generate_command(key, "set_temperature", target_temp=20.5)

    def test_status_query_has_exact_directed_frame_and_checksum(self):
        controller = KocomController(_ControllerGateway())
        key = DeviceKey(DeviceType.THERMOSTAT, 2, 0, SubType.NONE)
        living_key = DeviceKey(DeviceType.THERMOSTAT, 0, 0, SubType.NONE)

        packet, _, _ = controller.generate_command(key, "status_query")
        living_packet, _, _ = controller.generate_command(living_key, "status_query")

        self.assertEqual(
            bytes.fromhex("aa5530bc00360201003a00000000000000005f0d0d"), packet
        )
        self.assertEqual(21, len(packet))
        self.assertEqual(sum(packet[2:18]) % 256, packet[18])
        self.assertEqual(
            bytes.fromhex("aa5530bc00360001003a00000000000000005d0d0d"),
            living_packet,
        )

    def test_status_query_and_unknown_thermostat_actions_are_rejected(self):
        controller = KocomController(_ControllerGateway())
        thermostat = DeviceKey(DeviceType.THERMOSTAT, 1, 0, SubType.NONE)
        light = DeviceKey(DeviceType.LIGHT, 1, 0, SubType.NONE)

        with self.assertRaises(ValueError):
            controller.generate_command(light, "status_query")
        with self.assertRaises(ValueError):
            controller.generate_command(thermostat, "unsupported")
        with self.assertRaises(ValueError):
            controller.generate_command(
                DeviceKey(DeviceType.THERMOSTAT, 0xFF, 0, SubType.NONE),
                "status_query",
            )

    def test_gasvalve_lock_action_uses_command_and_callable_expectation(self):
        controller = KocomController(_ControllerGateway())
        key = DeviceKey(DeviceType.GASVALVE, 1, 0, SubType.NONE)
        off_state = DeviceState(key, Platform.SWITCH, {}, False)
        on_state = DeviceState(key, Platform.SWITCH, {}, True)

        packet, expect, _ = controller.generate_command(key, "turn_off")

        self.assertEqual(0x02, packet[9])
        self.assertTrue(callable(expect))
        self.assertTrue(expect(off_state))
        self.assertFalse(expect(on_state))

    def test_gasvalve_unsupported_action_is_rejected(self):
        controller = KocomController(_ControllerGateway())
        key = DeviceKey(DeviceType.GASVALVE, 1, 0, SubType.NONE)

        for action in ("turn_on", "set_temperature", "status_query"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                controller.generate_command(key, action)

    def test_only_directed_thermostat_status_reports_are_parsed(self):
        controller = KocomController(_ControllerGateway())

        self.assertFalse(controller._handle_thermostat(
            _thermostat_frame(21, 20, packet_type=0x0D, mirrored=True)
        ))
        self.assertFalse(controller._handle_thermostat(
            _thermostat_frame(21, 20, command=0x3A)
        ))
        self.assertFalse(controller._handle_thermostat(
            _thermostat_frame(21, 20, dest_device=0x36)
        ))
        self.assertFalse(controller._handle_thermostat(
            _thermostat_frame(21, 20, dest_room=0x01)
        ))
        self.assertFalse(controller._handle_thermostat(
            _thermostat_frame(21, 20, room=0xFF)
        ))
        states = controller._handle_thermostat(_thermostat_frame(21, 20))
        assert states is not None
        primary = next(state for state in states if state.key.sub_type == SubType.NONE)
        self.assertEqual(1, primary.key.room_index)

    def test_captured_bc_status_is_accepted_and_dc_mirror_is_rejected(self):
        controller = KocomController(_ControllerGateway())
        captured_status = _thermostat_frame(21, 20)
        captured_mirror = _thermostat_frame(
            21,
            20,
            packet_type=0x0D,
            mirrored=True,
        )

        self.assertEqual(
            bytes.fromhex("aa5530bc00010036010010001500140000005d0d0d"),
            captured_status.raw,
        )
        self.assertEqual(
            bytes.fromhex("aa5530dc00360101000010001500140000007d0d0d"),
            captured_mirror.raw,
        )
        states = controller._handle_thermostat(captured_status)
        self.assertTrue(states)
        self.assertFalse(controller._handle_thermostat(captured_mirror))

        living_status = _thermostat_frame(21, 20, room=0)
        self.assertEqual(
            bytes.fromhex("aa5530bc00010036000010001500140000005c0d0d"),
            living_status.raw,
        )
        living_states = controller._handle_thermostat(living_status)
        assert living_states is not None
        living = next(
            state for state in living_states if state.key.sub_type == SubType.NONE
        )
        self.assertEqual(0, living.key.room_index)


# Literal protocol fixtures specify the existing decoder contract, independent of
# command generation. These synthetic reports are not physical-device captures.
_REPORTS = {
    "ac_on": "aa5530bc000100390200100003001a1600006b0d0d",
    "ac_off": "aa5530bc000100390200000001001b1800005c0d0d",
    "vent_on": "aa5530bc0001004800001102800007230200f40d0d",
    "vent_off": "aa5530bc00010048000000010000060000003c0d0d",
    "elevator_down": "aa5530bc0001004400000182000000000000b40d0d",
    "elevator_arrival": "aa5530bc0001004400000331320000000000970d0d",
    "motion_on": "aa5530bc0001006000040000000000000000510d0d",
    "motion_off": "aa5530bc00010060000000000000000000004d0d0d",
    "airquality_status": "aa5530bc00010098010023110258012c19328c0d0d",
    "airquality_query": "aa5530bc00010098013a050003200000001e060d0d",
}


class ProtocolFixtureTests(unittest.TestCase):
    def setUp(self):
        self.states: list[DeviceState] = []
        self.gateway = _ControllerGateway()
        self.gateway.on_device_state = lambda _state: self.states.append(_state)
        self.controller = KocomController(self.gateway)

    def report(self, name):
        packet = bytes.fromhex(_REPORTS[name])
        self.assertEqual(21, len(packet))
        self.assertEqual(sum(packet[2:18]) & 0xFF, packet[18])
        start = len(self.states)
        self.controller.feed(packet)
        states = self.states[start:]
        for state in states:
            self.assertEqual(packet, state._packet)
        return states

    def assert_device(self, state, device_type, room, subtype, platform):
        self.assertEqual(DeviceKey(device_type, room, 0, subtype), state.key)
        self.assertEqual(platform, state.platform)

    def test_consecutive_airconditioner_reports_replace_values(self):
        for name, expected in (
            ("ac_on", {"hvac_mode": "cool", "fan_mode": "high", "current_temp": 26.0, "target_temp": 22.0}),
            ("ac_off", {"hvac_mode": "off", "fan_mode": "low", "current_temp": 27.0, "target_temp": 24.0}),
        ):
            states = self.report(name)
            self.assertEqual(1, len(states))
            state = states[0]
            self.assert_device(state, DeviceType.AIRCONDITIONER, 2, SubType.NONE, Platform.CLIMATE)
            self.assertEqual(expected, state.state)
            self.assertEqual({
                "hvac_modes": ["cool", "fan_only", "dry", "auto", "off"],
                "fan_modes": ["low", "medium", "high", "auto"],
                "feature_fan": True, "temp_step": 1.0,
            }, state.attribute)
        self.assertEqual(2, len(self.states))

    def test_consecutive_ventilation_reports_and_support_entities(self):
        first = self.report("vent_on")
        second = self.report("vent_off")
        self.assertEqual(3, len(first))
        self.assertEqual(3, len(second))
        for states, expected, co2, error in (
            (first, {"state": True, "preset_mode": "auto", "speed": 128}, 735, True),
            (second, {"state": False, "preset_mode": "ventilation", "speed": 0}, 600, False),
        ):
            self.assert_device(states[0], DeviceType.VENTILATION, 0, SubType.NONE, Platform.FAN)
            self.assertEqual(expected, states[0].state)
            self.assertEqual([64, 128, 192], states[0].attribute["speed_list"])
            self.assert_device(states[1], DeviceType.VENTILATION, 0, SubType.CO2, Platform.SENSOR)
            self.assertEqual(co2, states[1].state)
            self.assertEqual({"device_class": "co2", "unit_of_measurement": "ppm"}, states[1].attribute)
            self.assert_device(states[2], DeviceType.VENTILATION, 0, SubType.ERRCODE, Platform.BINARY_SENSOR)
            self.assertIs(error, states[2].state)
            self.assertEqual("problem", states[2].attribute["device_class"])
        self.assertFalse(first[0].attribute["feature_preset"])
        self.assertTrue(second[0].attribute["feature_preset"])
        self.assertEqual(["ventilation", "auto"], second[0].attribute["preset_modes"])
        self.assertEqual({"error_code": "02"}, first[2].attribute["extra_state"])
        self.assertEqual({"error_code": "00"}, second[2].attribute["extra_state"])

    def test_consecutive_elevator_direction_and_floor_reports(self):
        for name, expected in (
            ("elevator_down", [True, "downward", "B2"]),
            ("elevator_arrival", [False, "arrival", "12"]),
        ):
            states = self.report(name)
            self.assertEqual(expected, [state.state for state in states])
            for state, subtype, platform in zip(states, (SubType.NONE, SubType.DIRECTION, SubType.FLOOR),
                                                 (Platform.SWITCH, Platform.SENSOR, Platform.SENSOR), strict=True):
                self.assert_device(state, DeviceType.ELEVATOR, 0, subtype, platform)
                self.assertEqual({}, state.attribute)

    def test_consecutive_motion_reports(self):
        for name, expected in (("motion_on", True), ("motion_off", False)):
            states = self.report(name)
            self.assertEqual(1, len(states))
            self.assert_device(states[0], DeviceType.MOTION, 0, SubType.NONE, Platform.BINARY_SENSOR)
            self.assertIs(expected, states[0].state)
            self.assertEqual({"device_class": "motion"}, states[0].attribute)

    def test_consecutive_airquality_reports_and_zero_suppression(self):
        first = self.report("airquality_status")
        second = self.report("airquality_query")
        expected = (
            (SubType.PM10, 35, "pm10", "µg/m³"),
            (SubType.PM25, 17, "pm25", "µg/m³"),
            (SubType.CO2, 600, "co2", "ppm"),
            (SubType.VOC, 300, "volatile_organic_compounds", "µg/m³"),
            (SubType.TEMP, 25, "temperature", "°C"),
            (SubType.HUMIDITY, 50, "humidity", "%"),
        )
        self.assertEqual(6, len(first))
        for state, (subtype, value, device_class, unit) in zip(first, expected, strict=True):
            self.assert_device(state, DeviceType.AIRQUALITY, 1, subtype, Platform.SENSOR)
            self.assertEqual(value, state.state)
            self.assertEqual({"device_class": device_class, "unit_of_measurement": unit}, state.attribute)
        self.assertEqual([(SubType.PM10, 5), (SubType.CO2, 800), (SubType.HUMIDITY, 30)],
                         [(state.key.sub_type, state.state) for state in second])

    def test_complete_ac_and_vent_commands_and_confirmation_predicates(self):
        cases = (
            (DeviceType.AIRCONDITIONER, 2, Platform.CLIMATE, "set_hvac", {"hvac_mode": "cool"}, "hvac_mode", "cool", "off", "aa5530bc0039020100001000000000000000380d0d"),
            (DeviceType.AIRCONDITIONER, 2, Platform.CLIMATE, "set_hvac", {"hvac_mode": "off"}, "hvac_mode", "off", "cool", "aa5530bc0039020100000000000000000000280d0d"),
            (DeviceType.AIRCONDITIONER, 2, Platform.CLIMATE, "set_fan", {"fan_mode": "auto"}, "fan_mode", "auto", "low", "aa5530bc00390201000010000400000000003c0d0d"),
            (DeviceType.AIRCONDITIONER, 2, Platform.CLIMATE, "set_temperature", {"target_temp": 23.0}, "target_temp", 23.0, 22.0, "aa5530bc00390201000010000000001700004f0d0d"),
            (DeviceType.VENTILATION, 0, Platform.FAN, "turn_on", {}, "state", True, False, "aa5530bc0048000100001100000000000000460d0d"),
            (DeviceType.VENTILATION, 0, Platform.FAN, "turn_off", {}, "state", False, True, "aa5530bc0048000100000000000000000000350d0d"),
            (DeviceType.VENTILATION, 0, Platform.FAN, "set_preset", {"preset_mode": "bypass"}, "preset_mode", "bypass", "auto", "aa5530bc0048000100001103000000000000490d0d"),
            (DeviceType.VENTILATION, 0, Platform.FAN, "set_percentage", {"speed": 192}, "speed", 192, 128, "aa5530bc0048000100001100c00000000000060d0d"),
        )
        for device_type, room, platform, action, args, field, match, mismatch, hex_packet in cases:
            with self.subTest(device=device_type, action=action, args=args):
                key = DeviceKey(device_type, room, 0, SubType.NONE)
                packet, expect, timeout = self.controller.generate_command(key, action, **args)
                self.assertEqual(bytes.fromhex(hex_packet), packet)
                self.assertEqual(21, len(packet))
                self.assertEqual(sum(packet[2:18]) & 0xFF, packet[18])
                self.assertEqual(1.5 if action == "set_temperature" else 1.0, timeout)
                self.assertTrue(expect(DeviceState(key, platform, {}, {field: match})))
                self.assertFalse(expect(DeviceState(key, platform, {}, {field: mismatch})))
                self.assertFalse(expect(DeviceState(key, platform, {}, {})))
                self.assertFalse(expect(DeviceState(key, platform, {}, True)))
                for other in (
                    DeviceKey(device_type, room + 1, 0, SubType.NONE),
                    DeviceKey(device_type, room, 1, SubType.NONE),
                    DeviceKey(device_type, room, 0, SubType.CO2),
                    DeviceKey(DeviceType.MOTION, room, 0, SubType.NONE),
                ):
                    self.assertFalse(expect(DeviceState(other, platform, {}, {field: match})))


class FramingRegressionTests(unittest.TestCase):
    def collect(self):
        states: list[DeviceState] = []
        gateway = _ControllerGateway()
        gateway.on_device_state = lambda _state: states.append(_state)
        return KocomController(gateway), states

    def test_every_split_offset_for_each_report(self):
        for name, hex_packet in _REPORTS.items():
            packet = bytes.fromhex(hex_packet)
            expected_controller, expected = self.collect()
            expected_controller.feed(packet)
            self.assertTrue(expected)
            for offset in range(len(packet) + 1):
                with self.subTest(report=name, offset=offset):
                    controller, states = self.collect()
                    controller.feed(packet[:offset])
                    self.assertEqual(len(expected) if offset == len(packet) else 0, len(states))
                    controller.feed(packet[offset:])
                    self.assertEqual(expected, states)
                    controller.feed(b"")
                    self.assertEqual(expected, states)
                    self.assertEqual(b"", controller._rx_buf)

    def test_bytewise_and_concatenated_reports_have_no_early_or_duplicate_callbacks(self):
        packets = [bytes.fromhex(_REPORTS[name]) for name in ("ac_on", "ac_off")]
        controller, states = self.collect()
        for count, packet in enumerate(packets):
            for index, byte in enumerate(packet):
                controller.feed(bytes([byte]))
                self.assertEqual(count + (index == len(packet) - 1), len(states))
        joined_controller, joined = self.collect()
        joined_controller.feed(b"".join(packets))
        self.assertEqual(states, joined)
        joined_controller.feed(b"")
        self.assertEqual(2, len(joined))

    def test_garbage_partial_prefixes_and_repeated_prefix_bytes(self):
        packet = bytes.fromhex(_REPORTS["ac_on"])
        for garbage in (b"\x00\x55\x01", b"\xaa\xaa", b"\xaa\x00\x55"):
            with self.subTest(garbage=garbage):
                controller, states = self.collect()
                controller.feed(garbage + packet[:1])
                self.assertEqual([], states)
                self.assertEqual(b"\xaa", controller._rx_buf)
                controller.feed(packet[1:])
                self.assertEqual(1, len(states))
                self.assertEqual(packet, states[0]._packet)
        controller, states = self.collect()
        controller.feed(b"\xaa")
        controller.feed(b"\x00")
        self.assertEqual(b"", controller._rx_buf)
        controller.feed(packet)
        self.assertEqual(1, len(states))

    def test_invalid_suffix_checksum_and_following_fragment_recover(self):
        packet = bytes.fromhex(_REPORTS["ac_on"])
        invalid_suffix = packet[:-1] + b"\x00"
        invalid_checksum = packet[:18] + bytes([packet[18] ^ 1]) + packet[19:]
        for corrupt in (invalid_suffix, invalid_checksum, invalid_suffix + invalid_checksum):
            with self.subTest(corrupt=corrupt):
                controller, states = self.collect()
                controller.feed(corrupt + packet[:1])
                self.assertEqual([], states)
                controller.feed(packet[1:-1])
                self.assertEqual([], states)
                controller.feed(packet[-1:])
                self.assertEqual(1, len(states))
                self.assertEqual(packet, states[0]._packet)
                controller.feed(b"")
                self.assertEqual(1, len(states))


class GatewaySafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_connection = gateway_module.AsyncConnection
        gateway_module.AsyncConnection = _FakeConnection
        self.gateway = KocomGateway(
            create_autospec(HomeAssistant, instance=True),
            create_autospec(ConfigEntry, instance=True),
            "test", 1,
        )
        connection = self.gateway.conn
        assert isinstance(connection, _FakeConnection)
        self.connection: _FakeConnection = connection

    async def asyncTearDown(self):
        if self.gateway._task_sender:
            await self.gateway.async_stop()
        gateway_module.AsyncConnection = self.old_connection

    def _state(self, value=True):
        return DeviceState(
            DeviceKey(DeviceType.LIGHT, 1, 0, SubType.NONE), Platform.LIGHT, {}, value
        )

    def _thermostat_state(self, room: int) -> DeviceState:
        return DeviceState(
            DeviceKey(DeviceType.THERMOSTAT, room, 0, SubType.NONE),
            Platform.CLIMATE,
            {},
            {"hvac_mode": "heat", "target_temp": 21.0, "current_temp": 20.0},
        )

    async def _wait_for_bootstrap(self) -> None:
        for _ in range(20):
            task = self.gateway._task_bootstrap_queries
            if task is None:
                return
            await asyncio.sleep(0)
        self.fail("bootstrap query task did not finish")

    async def _restore_entity_entries(self, entries, last_states=None) -> None:
        self.gateway.entry = create_autospec(ConfigEntry, instance=True, entry_id="entry")
        registry = types.SimpleNamespace()
        restore_store = types.SimpleNamespace(last_states=last_states or {})
        with (
            patch.object(gateway_module.er, "async_get", return_value=registry),
            patch.object(
                gateway_module.er,
                "async_entries_for_config_entry",
                return_value=entries,
            ),
            patch.object(
                gateway_module.restore_state,
                "async_get",
                return_value=restore_store,
            ),
        ):
            await self.gateway.async_get_entity_registry()

    async def test_waiter_is_registered_before_same_turn_reply(self):
        self.connection.on_send = lambda _packet: self.gateway.on_device_state(self._state(True))
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
        self.assertTrue(await asyncio.wait_for(
            self.gateway.async_send_action(self._state().key, "turn_on"), timeout=0.2
        ))
        self.assertEqual([], self.gateway._pendings)

    async def test_timeout_cleans_waiter_and_returns_failure(self):
        self.gateway.controller.generate_command = lambda *_args, **_kwargs: (b"x", lambda _dev: False, 0.001)
        old_retry = gateway_module.SEND_RETRY_MAX
        gateway_module.SEND_RETRY_MAX = 1
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
        try:
            self.assertFalse(await asyncio.wait_for(
                self.gateway.async_send_action(self._state().key, "turn_on"), timeout=0.2
            ))
        finally:
            gateway_module.SEND_RETRY_MAX = old_retry
        self.assertEqual([], self.gateway._pendings)

    async def test_restored_state_is_commandable_but_unconfirmed_until_live_packet(self):
        state = self._state(True)
        self.gateway._restore_mode = True
        self.gateway.on_device_state(state)
        self.gateway._restore_mode = False
        self.assertFalse(self.gateway.is_device_available(state.key))
        self.assertTrue(self.gateway.is_transport_available())
        self.assertFalse(self.gateway.is_device_state_confirmed(state.key))
        self.gateway.on_device_state(state)
        self.assertTrue(self.gateway.is_device_available(state.key))
        self.assertTrue(self.gateway.is_device_state_confirmed(state.key))

    async def test_reconnect_requires_a_new_live_packet(self):
        state = self._state(True)
        self.gateway._sync_connection_availability()
        self.gateway.on_device_state(state)
        self.assertTrue(self.gateway.is_device_available(state.key))

        self.connection.connected = False
        self.gateway._sync_connection_availability()
        self.assertFalse(self.gateway.is_device_available(state.key))

        self.connection.connected = True
        self.gateway._sync_connection_availability()
        self.assertFalse(self.gateway.is_device_available(state.key))
        self.assertTrue(self.gateway.is_transport_available())
        self.assertFalse(self.gateway.is_device_state_confirmed(state.key))

        self.gateway.on_device_state(state)
        self.assertTrue(self.gateway.is_device_available(state.key))

    async def test_live_packet_confirms_only_its_device_key(self):
        first = self._state(True)
        second = DeviceState(
            DeviceKey(DeviceType.LIGHT, 2, 0, SubType.NONE), Platform.LIGHT, {}, True
        )
        self.gateway._restore_mode = True
        self.gateway.on_device_state(first)
        self.gateway.on_device_state(second)
        self.gateway._restore_mode = False

        self.gateway.on_device_state(first)

        self.assertTrue(self.gateway.is_device_state_confirmed(first.key))
        self.assertFalse(self.gateway.is_device_state_confirmed(second.key))
        self.assertTrue(self.gateway.is_transport_available())

    async def test_bootstrap_queries_known_thermostat_rooms_once_per_connection(self):
        first = self._thermostat_state(1)
        second = self._thermostat_state(2)
        global_room = self._thermostat_state(0x00)
        broadcast_room = self._thermostat_state(0xFF)
        self.gateway._restore_mode = True
        self.gateway.on_device_state(first)
        self.gateway.on_device_state(second)
        self.gateway.on_device_state(global_room)
        self.gateway.on_device_state(broadcast_room)
        self.gateway._restore_mode = False
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())

        self.gateway._sync_connection_availability()
        await self._wait_for_bootstrap()
        self.assertEqual(
            [bytes.fromhex("aa5530bc00360001003a00000000000000005d0d0d"),
             bytes.fromhex("aa5530bc00360101003a00000000000000005e0d0d"),
             bytes.fromhex("aa5530bc00360201003a00000000000000005f0d0d")],
            self.connection.sent,
        )

        self.gateway._sync_connection_availability()
        await asyncio.sleep(0)
        self.assertEqual(3, len(self.connection.sent))

        self.connection.connected = False
        self.gateway._sync_connection_availability()
        self.connection.connected = True
        self.gateway._sync_connection_availability()
        await self._wait_for_bootstrap()
        self.assertEqual(6, len(self.connection.sent))

    async def test_registry_only_climates_seed_queries_without_placeholder_state(self):
        oversized_room = types.SimpleNamespace(
            entity_id="climate.oversized_room",
            unique_id=f"5-{'9' * 5000}_0-0:test",
        )
        valid = [
            types.SimpleNamespace(
                entity_id="climate.living",
                unique_id="5-0_0-0:test",
            ),
            types.SimpleNamespace(
                entity_id="climate.thermostat_1",
                unique_id="5-1_0-0:test",
            ),
            types.SimpleNamespace(
                entity_id="climate.thermostat_3",
                unique_id="5-3_0-0:test:host",
            ),
        ]
        invalid = [
            oversized_room,
            types.SimpleNamespace(entity_id="switch.other", unique_id="5-2_0-0:test"),
            types.SimpleNamespace(entity_id="climate.broadcast", unique_id="5-255_0-0:test"),
            types.SimpleNamespace(entity_id="climate.leading_zero", unique_id="5-02_0-0:test"),
            types.SimpleNamespace(entity_id="climate.device", unique_id="4-2_0-0:test"),
            types.SimpleNamespace(entity_id="climate.index", unique_id="5-2_1-0:test"),
            types.SimpleNamespace(entity_id="climate.subtype", unique_id="5-2_0-1:test"),
            types.SimpleNamespace(entity_id="climate.alias", unique_id="5-2_0-0-extra:test"),
            types.SimpleNamespace(entity_id="climate.nondigit", unique_id="5-x_0-0:test"),
            types.SimpleNamespace(entity_id="climate.no_host", unique_id="5-2_0-0"),
            types.SimpleNamespace(entity_id="climate.empty_host", unique_id="5-2_0-0:"),
            types.SimpleNamespace(entity_id="malformed", unique_id="5-2_0-0:test"),
            types.SimpleNamespace(entity_id=None, unique_id="5-2_0-0:test"),
            types.SimpleNamespace(entity_id="climate.none", unique_id=None),
        ]
        last_states = {
            entry.entity_id: types.SimpleNamespace(extra_data=None)
            for entry in valid + invalid
        }

        # The oversized malformed entry precedes valid entries to prove it
        # cannot abort parsing of the remaining entity registry.
        entries = [oversized_room, *valid, *invalid[1:]]
        await self._restore_entity_entries(entries, last_states)

        self.assertEqual([], self.gateway.registry.all_by_platform(Platform.CLIMATE))
        self.assertEqual(
            {0, 1, 3},
            {key.room_index for key in self.gateway._bootstrap_registry_keys},
        )
        for room in (0, 1, 3):
            key = DeviceKey(DeviceType.THERMOSTAT, room, 0, SubType.NONE)
            self.assertFalse(self.gateway.is_device_state_confirmed(key))

        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
        self.gateway._sync_connection_availability()
        await self._wait_for_bootstrap()
        self.assertEqual([0, 1, 3], [packet[6] for packet in self.connection.sent])

        living_key = DeviceKey(DeviceType.THERMOSTAT, 0, 0, SubType.NONE)
        first_key = DeviceKey(DeviceType.THERMOSTAT, 1, 0, SubType.NONE)
        third_key = DeviceKey(DeviceType.THERMOSTAT, 3, 0, SubType.NONE)
        self.gateway.controller._dispatch_packet(
            _thermostat_frame(22, 20, room=0).raw
        )
        self.assertIsNotNone(self.gateway.registry.get(living_key))
        self.assertTrue(self.gateway.is_device_state_confirmed(living_key))
        self.assertIsNone(self.gateway.registry.get(first_key))
        self.assertFalse(self.gateway.is_device_state_confirmed(first_key))
        self.assertIsNone(self.gateway.registry.get(third_key))
        self.assertFalse(self.gateway.is_device_state_confirmed(third_key))

    async def test_packet_and_registry_bootstrap_keys_are_deduplicated(self):
        room = 2
        key = DeviceKey(DeviceType.THERMOSTAT, room, 0, SubType.NONE)
        self.gateway._restore_mode = True
        self.gateway.controller._dispatch_packet(
            _thermostat_frame(
                21,
                20,
                room=room,
                packet_type=0x0D,
                mirrored=True,
            ).raw
        )
        self.gateway._restore_mode = False
        entry = types.SimpleNamespace(
            entity_id="climate.thermostat_2",
            unique_id="5-2_0-0:test",
        )
        await self._restore_entity_entries(
            [entry],
            {entry.entity_id: types.SimpleNamespace(extra_data=None)},
        )

        self.assertIsNotNone(self.gateway.registry.get(key))
        self.assertFalse(self.gateway.is_device_state_confirmed(key))
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
        self.gateway._sync_connection_availability()
        await self._wait_for_bootstrap()
        self.assertEqual([room], [packet[6] for packet in self.connection.sent])

    async def test_bootstrap_query_does_not_retry_after_no_response(self):
        state = self._thermostat_state(1)
        self.gateway._restore_mode = True
        self.gateway.on_device_state(state)
        self.gateway._restore_mode = False
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())

        self.gateway._sync_connection_availability()
        await self._wait_for_bootstrap()

        self.assertEqual(1, len(self.connection.sent))
        self.assertFalse(self.gateway.is_device_state_confirmed(state.key))

    async def test_eof_reconnect_advances_generation_and_queries_once(self):
        state = self._thermostat_state(1)
        self.gateway._restore_mode = True
        self.gateway.on_device_state(state)
        self.gateway._restore_mode = False
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
        self.gateway._sync_connection_availability()
        await self._wait_for_bootstrap()
        initial_generation = self.gateway._connection_generation
        self.assertEqual(1, len(self.connection.sent))

        received_eof = False

        async def recv_eof_once(nbytes: int, timeout: float = 0.05) -> bytes:
            nonlocal received_eof
            if not received_eof:
                received_eof = True
                self.connection.connected = False
                return b""
            return await asyncio.Future[bytes]()

        async def reopen():
            self.connection.connected = True
            return True

        self.connection.recv = recv_eof_once
        self.connection.open = reopen
        self.gateway._task_reader = asyncio.create_task(self.gateway._read_loop())

        for _ in range(100):
            if (
                self.gateway._connection_generation == initial_generation + 2
                and len(self.connection.sent) == 2
            ):
                break
            await asyncio.sleep(0.001)

        self.assertEqual(initial_generation + 2, self.gateway._connection_generation)
        self.assertEqual(2, len(self.connection.sent))

    async def test_stop_cleans_active_status_query_queue_accounting(self):
        state = self._thermostat_state(1)
        self.gateway._connection_generation = 1
        self.connection.idle = False
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
        send_task = asyncio.create_task(
            self.gateway._async_send_status_query(state.key, 1)
        )
        for _ in range(20):
            if self.gateway._active_item is not None:
                break
            await asyncio.sleep(0)
        self.assertIsNotNone(self.gateway._active_item)

        await self.gateway.async_stop()

        self.assertFalse(await asyncio.wait_for(send_task, timeout=0.2))
        self.assertIsNone(self.gateway._active_item)
        await asyncio.wait_for(self.gateway._tx_queue.join(), timeout=0.2)
        self.assertEqual(0, getattr(self.gateway._tx_queue, "_unfinished_tasks", None))

    async def test_immediate_reconnect_drops_old_active_query_and_runs_new_query(self):
        state = self._thermostat_state(1)
        self.gateway._restore_mode = True
        self.gateway.on_device_state(state)
        self.gateway._restore_mode = False
        self.connection.idle = False
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())

        self.gateway._sync_connection_availability()
        for _ in range(20):
            if self.gateway._active_item is not None:
                break
            await asyncio.sleep(0)
        self.assertIsNotNone(self.gateway._active_item)
        active_item = self.gateway._active_item
        assert active_item is not None
        old_generation = active_item.connection_generation

        self.connection.connected = False
        self.gateway._sync_connection_availability()
        self.assertIsNone(self.gateway._task_bootstrap_queries)
        self.connection.connected = True
        self.gateway._sync_connection_availability()
        replacement = self.gateway._task_bootstrap_queries
        self.assertIsNotNone(replacement)
        assert replacement is not None
        self.assertNotEqual(old_generation, self.gateway._connection_generation)

        self.connection.idle = True
        await asyncio.wait_for(asyncio.shield(replacement), timeout=0.2)
        self.assertIsNone(self.gateway._task_bootstrap_queries)
        self.assertEqual(1, len(self.connection.sent))
        self.assertEqual(0x3A, self.connection.sent[0][9])

    async def test_stale_generation_status_item_is_ignored(self):
        state = self._thermostat_state(1)
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
        self.gateway._sync_connection_availability()
        stale = _CmdItem(
            state.key,
            "status_query",
            {},
            connection_generation=self.gateway._connection_generation - 1,
        )

        await self.gateway._tx_queue.put(stale)
        self.assertFalse(await asyncio.wait_for(stale.future, timeout=0.2))
        self.assertEqual([], self.connection.sent)

    async def test_old_bootstrap_finally_does_not_clear_replacement_task(self):
        state = self._thermostat_state(1)
        generation = self.gateway._connection_generation
        old = asyncio.create_task(
            self.gateway._async_bootstrap_queries([state.key], generation)
        )
        self.gateway._task_bootstrap_queries = old
        await asyncio.sleep(0)
        replacement = asyncio.create_task(asyncio.sleep(60))
        self.gateway._task_bootstrap_queries = replacement

        old.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await old
        self.assertIs(replacement, self.gateway._task_bootstrap_queries)

        replacement.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await replacement
        self.gateway._task_bootstrap_queries = None

    async def test_disconnect_cancels_queued_bootstrap_query(self):
        state = self._thermostat_state(1)
        self.gateway._connection_available = True
        task = asyncio.create_task(
            self.gateway._async_bootstrap_queries(
                [state.key], self.gateway._connection_generation
            )
        )
        self.gateway._task_bootstrap_queries = task
        await asyncio.sleep(0)

        self.connection.connected = False
        self.gateway._sync_connection_availability()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.gateway._task_bootstrap_queries)

    async def test_restore_mirror_seeds_query_but_only_bc_response_confirms(self):
        room = 0
        key = DeviceKey(DeviceType.THERMOSTAT, room, 0, SubType.NONE)
        restored_mirror = _thermostat_frame(
            21,
            20,
            room=room,
            packet_type=0x0D,
            mirrored=True,
        )

        self.gateway._restore_mode = True
        self.gateway.controller._dispatch_packet(restored_mirror.raw)
        self.gateway.controller._dispatch_packet(
            _thermostat_frame(
                21,
                20,
                room=0xFF,
                packet_type=0x0D,
                mirrored=True,
            ).raw
        )
        self.gateway._restore_mode = False

        self.assertIsNotNone(self.gateway.registry.get(key))
        self.assertFalse(self.gateway.is_device_state_confirmed(key))
        broadcast_key = DeviceKey(DeviceType.THERMOSTAT, 0xFF, 0, SubType.NONE)
        self.assertIsNone(self.gateway.registry.get(broadcast_key))

        self.gateway.controller._dispatch_packet(restored_mirror.raw)
        self.assertFalse(self.gateway.is_device_state_confirmed(key))

        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
        self.gateway._sync_connection_availability()
        await self._wait_for_bootstrap()
        self.assertEqual(1, len(self.connection.sent))
        self.assertEqual(room, self.connection.sent[0][6])

        self.gateway.controller._dispatch_packet(
            _thermostat_frame(22, 20, room=room).raw
        )
        self.assertTrue(self.gateway.is_device_state_confirmed(key))

    async def test_valid_response_confirms_only_its_matching_thermostat_room(self):
        first = self._thermostat_state(1)
        second = self._thermostat_state(2)
        self.gateway._restore_mode = True
        self.gateway.on_device_state(first)
        self.gateway.on_device_state(second)
        self.gateway._restore_mode = False
        self.gateway._sync_connection_availability()

        self.gateway.controller._dispatch_packet(_thermostat_frame(21, 20, room=1).raw)
        self.assertTrue(self.gateway.is_device_state_confirmed(first.key))
        self.assertFalse(self.gateway.is_device_state_confirmed(second.key))

        self.gateway.controller._dispatch_packet(
            _thermostat_frame(
                22,
                20,
                room=2,
                packet_type=0x0D,
                mirrored=True,
            ).raw
        )
        self.assertFalse(self.gateway.is_device_state_confirmed(second.key))

    async def test_stop_resolves_pending_and_queued_futures(self):
        waiter = self.gateway._register_confirmation(self._state().key, lambda _state: False)
        item = _CmdItem(self._state().key, "turn_on", {})
        await self.gateway._tx_queue.put(item)
        await self.gateway.async_stop()
        self.assertTrue(waiter.future.done())
        self.assertFalse(waiter.future.result())
        self.assertTrue(item.future.done())
        self.assertFalse(item.future.result())


class TransportSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_failure_is_single_attempt_not_recursive(self):
        connection = AsyncConnection("host", 1234)
        with patch("asyncio.open_connection", side_effect=OSError("offline")) as open_connection:
            self.assertFalse(await connection.open())
        self.assertEqual(1, open_connection.call_count)
        self.assertFalse(connection._is_connected())

    async def test_tcp_eof_marks_connection_disconnected(self):
        connection = AsyncConnection("host", 1234)
        reader = create_autospec(asyncio.StreamReader, instance=True, read=AsyncMock(return_value=b""))
        wait_closed = AsyncMock()
        connection._reader = reader
        connection._writer = create_autospec(asyncio.StreamWriter, instance=True,
            close=lambda: None,
            wait_closed=wait_closed,
        )
        connection._connected = True

        self.assertEqual(b"", await connection.recv(512, timeout=0.1))

        reader.read.assert_awaited_once_with(512)
        wait_closed.assert_awaited_once()
        self.assertFalse(connection._is_connected())
        self.assertIsNone(connection._reader)
        self.assertIsNone(connection._writer)

    async def test_timeout_and_serial_empty_read_keep_connection_open(self):
        tcp = AsyncConnection("host", 1234)
        tcp._reader = create_autospec(asyncio.StreamReader, instance=True,
            read=AsyncMock(side_effect=asyncio.TimeoutError)
        )
        tcp._connected = True
        self.assertEqual(b"", await tcp.recv(512, timeout=0.1))
        self.assertTrue(tcp._is_connected())

        serial = AsyncConnection("/dev/fake", None)
        serial._reader = create_autospec(asyncio.StreamReader, instance=True, read=AsyncMock(return_value=b""))
        serial._connected = True
        self.assertEqual(b"", await serial.recv(512, timeout=0.1))
        self.assertTrue(serial._is_connected())


class ClimateBootstrapSafetyTests(unittest.IsolatedAsyncioTestCase):
    def _state(self):
        return DeviceState(
            DeviceKey(DeviceType.THERMOSTAT, 1, 0, SubType.NONE),
            Platform.CLIMATE,
            {"hvac_modes": ["off", "heat"], "temp_step": 1.0},
            {
                "hvac_mode": "heat",
                "current_temp": 20.0,
                "target_temp": 21.0,
                "fan_mode": None,
                "fan_modes": [],
                "preset_mode": "none",
                "preset_modes": [],
            },
        )

    async def test_connected_restored_climate_is_commandable_but_unknown(self):
        gateway = create_autospec(KocomGateway, instance=True,
            host="test",
            device_registry_id="parent-registry-id",
            is_transport_available=lambda: True,
            is_device_state_confirmed=lambda _key: False,
            async_send_action=AsyncMock(return_value=True),
        )
        climate = KocomClimate(gateway, self._state())

        self.assertTrue(climate.available)
        self.assertIsNone(climate.hvac_mode)
        self.assertIsNone(climate.current_temperature)
        self.assertIsNone(climate.target_temperature)
        self.assertEqual({"physical_state_confirmed": False}, climate.extra_state_attributes)
        await climate.async_set_temperature(temperature=22)
        gateway.async_send_action.assert_awaited_once()

    async def test_disconnected_climate_rejects_command(self):
        gateway = create_autospec(KocomGateway, instance=True,
            host="test",
            device_registry_id="parent-registry-id",
            is_transport_available=lambda: False,
            is_device_state_confirmed=lambda _key: False,
            async_send_action=AsyncMock(return_value=True),
        )
        climate = KocomClimate(gateway, self._state())

        self.assertFalse(climate.available)
        with self.assertRaises(HomeAssistantError):
            await climate.async_set_temperature(temperature=22)
        gateway.async_send_action.assert_not_awaited()


class EntityActionSafetyTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _device(device_type, platform):
        key = DeviceKey(device_type, 1, 0, SubType.NONE)
        if platform == Platform.FAN:
            attribute = {
                "feature_preset": True,
                "speed_list": [1, 2],
                "preset_modes": ["sleep"],
            }
            state = {"state": False, "speed": 0, "preset_mode": ""}
        else:
            attribute = {}
            state = False
        return DeviceState(key, platform, attribute, state)

    @staticmethod
    def _gateway(result, available=True):
        return create_autospec(KocomGateway, instance=True,
            host="test",
            device_registry_id="parent-registry-id",
            controller=types.SimpleNamespace(_device_storage={}),
            is_device_available=lambda _key: available,
            async_send_action=AsyncMock(return_value=result),
        )

    def _cases(self):
        return (
            (KocomSwitch, DeviceType.OUTLET, Platform.SWITCH, "async_turn_on", {}),
            (KocomSwitch, DeviceType.OUTLET, Platform.SWITCH, "async_turn_off", {}),
            (KocomLight, DeviceType.LIGHT, Platform.LIGHT, "async_turn_on", {}),
            (KocomLight, DeviceType.LIGHT, Platform.LIGHT, "async_turn_off", {}),
            (KocomFan, DeviceType.VENTILATION, Platform.FAN, "async_turn_on", {}),
            (KocomFan, DeviceType.VENTILATION, Platform.FAN, "async_turn_off", {}),
            (KocomFan, DeviceType.VENTILATION, Platform.FAN, "async_set_percentage", {"percentage": 100}),
            (KocomFan, DeviceType.VENTILATION, Platform.FAN, "async_set_preset_mode", {"preset_mode": "sleep"}),
        )

    async def test_fan_turn_on_accepts_ha_arguments_and_legacy_speed_keyword(self):
        gateway = self._gateway(True)
        device = self._device(DeviceType.VENTILATION, Platform.FAN)
        fan = KocomFan(gateway, device)
        await fan.async_turn_on(50, "sleep")
        gateway.async_send_action.assert_awaited_once_with(device.key, "turn_on")
        gateway.async_send_action.reset_mock()
        await fan.async_turn_on(speed="low", percentage=50, preset_mode="sleep")
        gateway.async_send_action.assert_awaited_once_with(device.key, "turn_on")

    async def test_switch_light_and_fan_actions_propagate_success(self):
        for entity_class, device_type, platform, method, kwargs in self._cases():
            gateway = self._gateway(True)
            entity = entity_class(gateway, self._device(device_type, platform))

            await getattr(entity, method)(**kwargs)

            gateway.async_send_action.assert_awaited_once()

    async def test_switch_light_and_fan_actions_raise_on_failed_send(self):
        for entity_class, device_type, platform, method, kwargs in self._cases():
            gateway = self._gateway(False)
            entity = entity_class(gateway, self._device(device_type, platform))

            with self.assertRaises(HomeAssistantError):
                await getattr(entity, method)(**kwargs)

            gateway.async_send_action.assert_awaited_once()

    async def test_all_actions_raise_without_gateway_call_when_unavailable(self):
        for entity_class, device_type, platform, method, kwargs in self._cases():
            gateway = self._gateway(True, available=False)
            entity = entity_class(gateway, self._device(device_type, platform))

            with self.assertRaises(HomeAssistantError):
                await getattr(entity, method)(**kwargs)

            gateway.async_send_action.assert_not_awaited()


class SetupCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def test_manifest_and_transport_use_serialx(self):
        manifest = json.loads(
            (Path(__file__).parents[1] / "custom_components/kocom_wallpad/manifest.json").read_text()
        )
        self.assertIn("serialx==1.10.0", manifest["requirements"])
        transport = (
            Path(__file__).parents[1] / "custom_components/kocom_wallpad/transport.py"
        ).read_text()
        self.assertIn("import serialx", transport)
        self.assertNotIn("import serial_asyncio\n", transport)

    async def test_parent_device_is_registered_before_platform_forwarding(self):
        events = []

        class FakeGateway:
            def __init__(self, _hass, _entry, host, port):
                self.host = host
                self.port = port
                events.append(("gateway", host, port))

            async def async_get_entity_registry(self):
                events.append("entity_registry")

            async def async_start(self):
                events.append("start")

            async def async_stop(self, _event=None):
                events.append("stop")

        class FakeDeviceRegistry:
            def async_get_or_create(self, **kwargs):
                events.append(("parent", kwargs))
                return types.SimpleNamespace(id="parent-registry-id")

        async def forward(_entry, _platforms):
            events.append("forward")

        entry = create_autospec(ConfigEntry, instance=True,
            entry_id="entry-id",
            data={"host": "wallpad.local", "port": 8899},
            async_on_unload=lambda callback: events.append(("unload", callback)),
        )
        hass = create_autospec(HomeAssistant, instance=True,
            data={},
            bus=types.SimpleNamespace(async_listen_once=lambda *_args: lambda: None),
            config_entries=types.SimpleNamespace(async_forward_entry_setups=forward),
        )

        with (
            patch.object(integration_module, "KocomGateway", FakeGateway),
            patch.object(integration_module.dr, "async_get", return_value=FakeDeviceRegistry()),
        ):
            self.assertTrue(await async_setup_entry(hass, entry))

        parent_events = [event for event in events if isinstance(event, tuple) and event[0] == "parent"]
        self.assertEqual(1, len(parent_events))
        self.assertEqual(
            {
                "config_entry_id": "entry-id",
                "identifiers": {("kocom_wallpad", "wallpad.local")},
                "manufacturer": "KOCOM",
                "model": "Kocom Wallpad",
                "name": "Kocom Wallpad",
            },
            parent_events[0][1],
        )
        self.assertLess(events.index(parent_events[0]), events.index("forward"))
        self.assertEqual("Kocom Wallpad", parent_events[0][1]["name"])
        self.assertNotIn("wallpad.local", parent_events[0][1]["name"])

    async def test_parent_registration_failure_precedes_gateway_setup(self):
        events = []

        class FakeGateway:
            def __init__(self, *_args, **_kwargs):
                events.append("gateway")

            async def async_get_entity_registry(self):
                events.append("entity_registry")

            async def async_start(self):
                events.append("start")

            async def async_stop(self, _event=None):
                events.append("stop")

        class FailingDeviceRegistry:
            def async_get_or_create(self, **_kwargs):
                events.append("parent")
                raise RuntimeError("registry failure")

        async def forward(_entry, _platforms):
            events.append("forward")

        entry = create_autospec(ConfigEntry, instance=True,
            entry_id="entry-id",
            data={"host": "wallpad.local", "port": 8899},
            async_on_unload=lambda callback: events.append(("unload", callback)),
        )
        hass = create_autospec(HomeAssistant, instance=True,
            data={},
            bus=types.SimpleNamespace(async_listen_once=lambda *_args: lambda: None),
            config_entries=types.SimpleNamespace(async_forward_entry_setups=forward),
        )

        with (
            patch.object(integration_module, "KocomGateway", FakeGateway),
            patch.object(
                integration_module.dr,
                "async_get",
                return_value=FailingDeviceRegistry(),
            ),
            self.assertRaises(RuntimeError),
        ):
            await async_setup_entry(hass, entry)

        self.assertEqual(["parent"], events)
        self.assertEqual({}, hass.data)


if __name__ == "__main__":
    unittest.main()
