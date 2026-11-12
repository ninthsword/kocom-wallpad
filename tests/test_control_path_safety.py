"""Deterministic regression tests for the Kocom control path.

These offline tests use narrow module shims for deterministic protocol checks.
The development type checker resolves the real, locked Home Assistant dependency.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import itertools
import json
import sys
import types
import unittest
from collections.abc import Callable
from dataclasses import asdict, fields
from enum import Enum, IntFlag
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call, create_autospec, patch


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

    class _Context:
        def __init__(self, user_id=None, parent_id=None, id=None):
            self.user_id = user_id
            self.parent_id = parent_id
            self.id = id

    _set_attributes(core, Context=_Context)
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
from custom_components.kocom_wallpad import transport as transport_module
from custom_components.kocom_wallpad.climate import KocomClimate
from custom_components.kocom_wallpad.const import DeviceType, SubType
from custom_components.kocom_wallpad.controller import KocomController, PacketFrame
from custom_components.kocom_wallpad.entity_base import KocomBaseEntity
from custom_components.kocom_wallpad.fan import KocomFan
from custom_components.kocom_wallpad.gateway import KocomGateway, _CmdItem
from custom_components.kocom_wallpad.light import KocomLight
from custom_components.kocom_wallpad.models import DeviceKey, DeviceState
from custom_components.kocom_wallpad.switch import KocomSwitch
from custom_components.kocom_wallpad.transport import AsyncConnection


class _Registry:
    def __init__(self) -> None:
        self.states: dict[tuple[int, int, int, int], DeviceState] = {}

    def get(self, key):
        return self.states.get(key.key)


class _ControllerGateway:
    def __init__(self) -> None:
        self.registry = _Registry()
        self.live_keys: set[tuple[int, int, int, int]] = set()
        self.restores: list[tuple[DeviceKey, float, int]] = []

    def on_device_state(self, _state):
        pass

    def is_device_state_confirmed(self, key: DeviceKey) -> bool:
        return key.key in self.live_keys

    def queue_thermostat_restore(self, key: DeviceKey, target: float, restore: int) -> None:
        self.restores.append((key, target, restore))


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
    # Synthetic cutoff report using the existing 0x0EFF/0x65 wire format.
    "cutoff_on": "aa5530bc0001000eff6500000000000000005f0d0d",
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


def _light_report(room: int, values: bytes) -> bytes:
    """Return a synthetic ordinary light status report."""
    body = bytes((0x30, 0xBC, 0, 1, 0, 0x0E, room, 0)) + values
    return b"\xaa\x55" + body + bytes((sum(body) % 256, 13, 13))


_HEAT, _OFF = 0x11, 0x00
_NONE, _AWAY = 0x00, 0x01


def _room_report(mode: int, preset: int, target: int, room: int = 1) -> bytes:
    """Return a synthetic live thermostat status report to the wallpad."""
    body = bytes((0x30, 0xBC, 0x00, 0x01, 0x00, 0x36, room, 0x00,
                  mode, preset, target, 0x00, 20, 0x00, 0x00, 0x00))
    return b"\xaa\x55" + body + bytes((sum(body) % 256, 0x0D, 0x0D))


def _room_command(data0: int, data1: int, data2: int, room: int = 1) -> bytes:
    """Return a thermostat command frame from the wallpad address to a room."""
    body = bytes((0x30, 0xBC, 0x00, 0x36, room, 0x01, 0x00, 0x00,
                  data0, data1, data2, 0x00, 0x00, 0x00, 0x00, 0x00))
    return b"\xaa\x55" + body + bytes((sum(body) % 256, 0x0D, 0x0D))


class ThermostatCommandTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = _ControllerGateway()
        self.gateway.on_device_state = self.record
        self.controller = KocomController(self.gateway)
        self.key = DeviceKey(DeviceType.THERMOSTAT, 1, 0, SubType.NONE)

    def record(self, _state: DeviceState) -> None:
        if _state.platform == Platform.CLIMATE:
            self.gateway.registry.states[_state.key.key] = _state
            self.gateway.live_keys.add(_state.key.key)

    def report(self, mode: int, preset: int, target: int) -> None:
        self.controller.feed(_room_report(mode, preset, target))

    def home(self) -> float | None:
        return self.controller.home_target(1)

    def frame(self, action: str, **kwargs: object) -> bytes:
        packet, _, _ = self.controller.generate_command(self.key, action, **kwargs)
        return packet

    def test_synthetic_frames_match_the_protocol(self) -> None:
        self.assertEqual(
            bytes.fromhex("aa5530bc00010036010011001600140000005f0d0d"),
            _room_report(_HEAT, _NONE, 22),
        )
        self.assertEqual(
            bytes.fromhex("aa5530bc00360101000011001600000000004b0d0d"),
            _room_command(0x11, 0x00, 22),
        )

    def test_home_target_follows_every_live_heat_report_with_preset_none(self) -> None:
        steps = (
            ((_HEAT, _NONE, 22), 22.0),
            ((_HEAT, _NONE, 23), 23.0),
            ((_OFF, _NONE, 18), 23.0),
            ((_HEAT, _NONE, 19), 19.0),
            ((_HEAT, _NONE, 4), 19.0),
            ((_HEAT, _NONE, 41), 19.0),
            ((_HEAT, _AWAY, 10), 19.0),
        )
        for report, expected in steps:
            with self.subTest(report=report):
                self.report(*report)
                self.assertEqual(expected, self.home())
        # A new connection keeps it; its first at-home report sets a new one.
        self.controller.clear_change_record()
        self.assertEqual(19.0, self.home())
        self.report(_HEAT, _NONE, 24)
        self.assertEqual(24.0, self.home())
        # An exit the integration caused (set_temperature during away) is an
        # ordinary at-home report.
        self.report(_HEAT, _AWAY, 10)
        self.controller.note_transmitted(self.frame("set_temperature", target_temp=25.0))
        self.report(_HEAT, _NONE, 25)
        self.assertEqual(25.0, self.home())
        self.assertEqual([], self.gateway.restores)

    def test_reports_while_a_restore_is_pending_keep_the_home_target(self) -> None:
        for later in ((_HEAT, _AWAY, 10), (_OFF, _NONE, 10), (_OFF, _AWAY, 10)):
            with self.subTest(later=later):
                self.setUp()
                self.report(_HEAT, _NONE, 22)
                self.report(_HEAT, _AWAY, 10)
                self.report(_HEAT, _NONE, 10)
                self.assertEqual([(self.key, 22.0, 1)], self.gateway.restores)
                self.assertTrue(self.controller.is_restore_pending(1, 1))
                for _ in range(3):
                    self.report(_HEAT, _NONE, 10)
                self.assertEqual(22.0, self.home())
                self.assertEqual(1, len(self.gateway.restores))
                self.report(*later)
                self.assertFalse(self.controller.is_restore_pending(1, 1))
                self.assertEqual(22.0, self.home())

    def test_failed_restore_then_repeated_exit_reports_keep_the_home_target(self) -> None:
        self.report(_HEAT, _NONE, 22)
        self.report(_HEAT, _AWAY, 10)
        self.report(_HEAT, _NONE, 10)
        # The restore send failed: the room keeps reporting the exit setting.
        for _ in range(3):
            self.report(_HEAT, _NONE, 10)
        self.assertEqual(22.0, self.home())
        self.report(_HEAT, _AWAY, 10)
        self.assertFalse(self.controller.is_restore_pending(1, 1))
        self.report(_HEAT, _NONE, 10)
        self.assertEqual(
            [(self.key, 22.0, 1), (self.key, 22.0, 2)], self.gateway.restores
        )
        self.assertTrue(self.controller.is_restore_pending(1, 2))

    def test_a_new_connection_ends_the_pending_restore(self) -> None:
        self.report(_HEAT, _NONE, 22)
        self.report(_HEAT, _AWAY, 10)
        self.report(_HEAT, _NONE, 10)
        self.controller.clear_change_record()
        self.assertFalse(self.controller.is_restore_pending(1, 1))
        self.report(_HEAT, _NONE, 10)
        self.assertFalse(self.controller.is_restore_pending(1, 1))
        self.assertEqual(10.0, self.home())

    def test_restored_home_target_lasts_until_the_first_live_at_home_report(self) -> None:
        for room, value in enumerate((4, 41, 21.5, True, "22", None, 0.0), start=2):
            with self.subTest(value=value):
                self.controller.restore_home_target(room, value)
                self.assertIsNone(self.controller.home_target(room))
        self.controller.restore_home_target(1, 22.0)
        self.assertEqual(22.0, self.home())
        self.report(_HEAT, _AWAY, 10)
        self.report(_OFF, _NONE, 10)
        self.assertEqual(22.0, self.home())
        self.report(_HEAT, _NONE, 20)
        self.assertEqual(20.0, self.home())
        self.controller.restore_home_target(1, 30.0)
        self.assertEqual(20.0, self.home())

    def test_preset_none_and_hvac_frames_while_away_with_a_home_target(self) -> None:
        self.report(_HEAT, _NONE, 22)
        self.report(_HEAT, _AWAY, 10)
        self.assertEqual(_room_command(0x11, 0x00, 22), self.frame("set_preset", preset_mode="none"))
        self.assertEqual(_room_command(0x11, 0x00, 22), self.frame("set_hvac", hvac_mode="heat"))
        self.assertEqual(_room_command(0x00, 0x00, 22), self.frame("set_hvac", hvac_mode="off"))
        self.assertEqual(
            self.frame("set_temperature", target_temp=22.0),
            self.frame("set_preset", preset_mode="none"),
        )

    def test_preset_none_and_hvac_frames_while_away_without_a_home_target(self) -> None:
        self.report(_HEAT, _AWAY, 10)
        self.assertIsNone(self.home())
        self.assertEqual(_room_command(0x11, 0x00, 10), self.frame("set_preset", preset_mode="none"))
        self.assertEqual(_room_command(0x11, 0x00, 10), self.frame("set_hvac", hvac_mode="heat"))
        self.assertEqual(_room_command(0x00, 0x00, 10), self.frame("set_hvac", hvac_mode="off"))

    def test_preset_none_and_hvac_frames_at_home_carry_the_live_target(self) -> None:
        self.controller.restore_home_target(1, 22.0)
        self.report(_OFF, _NONE, 23)
        self.assertEqual(_room_command(0x11, 0x00, 23), self.frame("set_preset", preset_mode="none"))
        self.assertEqual(_room_command(0x11, 0x00, 23), self.frame("set_hvac", hvac_mode="heat"))
        self.assertEqual(_room_command(0x00, 0x00, 23), self.frame("set_hvac", hvac_mode="off"))

    def test_set_preset_away_frame_is_the_base_frame(self) -> None:
        self.report(_HEAT, _NONE, 22)
        self.assertEqual(
            bytes.fromhex("aa5530bc0036010100001101000000000000360d0d"),
            self.frame("set_preset", preset_mode="away"),
        )

    def test_home_target_is_used_when_the_live_target_is_outside_the_range(self) -> None:
        self.report(_HEAT, _NONE, 22)
        self.report(_HEAT, _NONE, 0)
        self.assertEqual(22.0, self.home())
        self.assertEqual(_room_command(0x11, 0x00, 22), self.frame("set_preset", preset_mode="none"))
        self.assertEqual(_room_command(0x00, 0x00, 22), self.frame("set_hvac", hvac_mode="off"))

    def test_commands_are_refused_without_a_live_confirmed_state(self) -> None:
        cases = (
            ("set_preset", {"preset_mode": "none"}),
            ("set_preset", {"preset_mode": "away"}),
            ("set_hvac", {"hvac_mode": "heat"}),
            ("set_hvac", {"hvac_mode": "off"}),
        )
        for setup in ("no state", "restored only"):
            for action, args in cases:
                with self.subTest(setup=setup, action=action, args=args):
                    self.setUp()
                    if setup == "restored only":
                        self.gateway.registry.states[self.key.key] = DeviceState(
                            self.key, Platform.CLIMATE, {},
                            {"hvac_mode": "heat", "preset_mode": "none", "target_temp": 22.0},
                        )
                        self.controller.restore_home_target(1, 22.0)
                    with self.assertRaises(RuntimeError):
                        self.controller.generate_command(self.key, action, **args)
        with self.assertRaises(ValueError):
            self.controller.generate_command(self.key, "set_fan", fan_mode="low")

    def test_target_outside_the_range_without_a_home_target_is_refused(self) -> None:
        for target in (0, 4, 41):
            for action, args in (
                ("set_preset", {"preset_mode": "none"}),
                ("set_hvac", {"hvac_mode": "heat"}),
                ("set_hvac", {"hvac_mode": "off"}),
            ):
                with self.subTest(target=target, action=action):
                    self.setUp()
                    self.report(_HEAT, _NONE, target)
                    self.assertIsNone(self.home())
                    with self.assertRaises(RuntimeError):
                        self.controller.generate_command(self.key, action, **args)


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

    def test_cutoff_report_has_separate_readonly_identity(self):
        states = self.report("cutoff_on")
        self.assertEqual(1, len(states))
        self.assert_device(states[0], DeviceType.LIGHTCUTOFF, 255, SubType.NONE, Platform.BINARY_SENSOR)
        self.assertIs(states[0].state, True)

    def test_cutoff_does_not_change_normal_light_state_or_confirmation(self):
        self.controller.feed(_light_report(0, bytes((0, 255, 0, 0, 0, 0, 0, 0))))
        lights = list(self.states)
        for state in lights:
            self.gateway.registry.states[state.key.key] = state
        cutoff = self.report("cutoff_on")[0]
        self.gateway.registry.states[cutoff.key.key] = cutoff
        key = DeviceKey(DeviceType.LIGHT, 0, 0, SubType.NONE)
        packet, predicate, _ = self.controller.generate_command(key, "turn_on")
        self.assertEqual("1-0_0-0", key.unique_id)
        self.assertEqual("2-255_0-0", cutoff.key.unique_id)
        self.assertEqual(bytes((255, 255, 0, 0, 0, 0, 0, 0)), packet[10:18])
        self.assertFalse(predicate(cutoff))
        normal = self.gateway.registry.get(key)
        assert normal is not None
        self.assertIs(normal.state, False)
        self.assertEqual(8, len(lights))
        for device_type in (DeviceType.LIGHTCUTOFF, DeviceType.LIGHT):
            for action in ("turn_on", "turn_off", "status_query"):
                with self.subTest(device_type=device_type, action=action), self.assertRaises(ValueError):
                    self.controller.generate_command(DeviceKey(device_type, 255, 0, SubType.NONE), action)

    def test_all_ventilation_tokens_keep_their_encoding(self):
        key = DeviceKey(DeviceType.VENTILATION, 0, 0, SubType.NONE)
        for token, value in (("unknown", 0), ("ventilation", 1), ("auto", 2),
                             ("bypass", 3), ("sleep", 5), ("air purification", 8)):
            with self.subTest(token=token):
                packet, _, _ = self.controller.generate_command(key, "set_preset", preset_mode=token)
                self.assertEqual(value, packet[11])

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
        self.assertTrue(first[0].attribute["feature_preset"])
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


class RestoreStateAccessorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch.object(gateway_module, "AsyncConnection", _FakeConnection):
            self.gateway = KocomGateway(
                create_autospec(HomeAssistant, instance=True),
                create_autospec(ConfigEntry, instance=True, entry_id="entry"),
                "test", 1,
            )
        self.entity_id = "climate.thermostat_1"
        self.key = DeviceKey(DeviceType.THERMOSTAT, 1, 0, SubType.NONE)
        self.packet = _thermostat_frame(21, 20).raw
        self.device_storage = {"restored_marker": ["preserved"]}
        self.stored_state = types.SimpleNamespace(extra_data=types.SimpleNamespace(
            as_dict=lambda: {
                "packet": self.packet.hex(),
                "device_storage": self.device_storage,
            }
        ))

    async def _restore(self, store) -> None:
        entry = types.SimpleNamespace(
            entity_id=self.entity_id, unique_id=f"{self.key.unique_id}:test"
        )
        registry = types.SimpleNamespace(async_get=lambda entity_id: entry)
        with (
            patch.object(gateway_module.er, "async_get", return_value=registry),
            patch.object(
                gateway_module.er, "async_entries_for_config_entry", return_value=[entry]
            ),
            patch.object(
                gateway_module.restore_state, "async_get", return_value=store
            ) as get_store,
        ):
            await self.gateway.async_get_entity_registry()
            get_store.assert_called_once_with(self.gateway.hass)

    def _assert_restored(self) -> DeviceState:
        device = self.gateway.registry.get(self.key)
        assert device is not None
        self.assertEqual(self.packet, device._packet)
        self.assertEqual(
            {"restored_marker": ["preserved"]}, self.gateway.controller._device_storage
        )
        self.assertFalse(self.gateway._restore_mode)
        self.assertIsNone(self.gateway._force_register_uid)
        self.assertTrue(self.gateway.is_transport_available())
        self.assertFalse(self.gateway.is_device_state_confirmed(self.key))
        self.assertFalse(self.gateway.is_device_available(self.key))
        return device

    async def test_modern_accessor_without_legacy_store(self):
        accessor = Mock(return_value=self.stored_state)
        await self._restore(types.SimpleNamespace(async_get_stored_state=accessor))

        accessor.assert_called_once_with(self.entity_id)
        self._assert_restored()
        self.gateway.controller._dispatch_packet(self.packet)
        self.assertTrue(self.gateway.is_device_state_confirmed(self.key))
        self.assertTrue(self.gateway.is_device_available(self.key))

    async def test_legacy_store_restores_identical_packet_and_device_storage(self):
        await self._restore(types.SimpleNamespace(
            last_states={self.entity_id: self.stored_state}
        ))
        legacy = self._assert_restored()
        self.gateway.registry = gateway_module.EntityRegistry()
        self.gateway.controller._device_storage = {}

        await self._restore(types.SimpleNamespace(
            async_get_stored_state=Mock(return_value=self.stored_state)
        ))
        modern = self._assert_restored()
        self.assertEqual(asdict(legacy), asdict(modern))
        self.assertEqual(legacy._packet, modern._packet)

    async def test_noncallable_accessor_uses_legacy_store(self):
        await self._restore(types.SimpleNamespace(
            async_get_stored_state=None,
            last_states={self.entity_id: self.stored_state},
        ))
        self._assert_restored()

    async def test_modern_none_does_not_read_legacy_store(self):
        accessor = Mock(return_value=None)
        legacy = Mock()
        legacy.get.side_effect = AssertionError("Legacy store must not be read")
        await self._restore(types.SimpleNamespace(
            async_get_stored_state=accessor, last_states=legacy
        ))
        accessor.assert_called_once_with(self.entity_id)
        legacy.get.assert_not_called()
        self.assertIsNone(self.gateway.registry.get(self.key))
        self.assertEqual({}, self.gateway.controller._device_storage)

    async def test_modern_error_propagates_without_reading_legacy_store(self):
        error = RuntimeError("Restore accessor failed")
        accessor = Mock(side_effect=error)
        legacy = Mock()
        legacy.get.side_effect = AssertionError("Legacy store must not be read")
        with self.assertRaises(RuntimeError) as raised:
            await self._restore(types.SimpleNamespace(
                async_get_stored_state=accessor, last_states=legacy
            ))
        self.assertIs(error, raised.exception)
        accessor.assert_called_once_with(self.entity_id)
        legacy.get.assert_not_called()
        self.assertFalse(self.gateway._restore_mode)
        self.assertIsNone(self.gateway.registry.get(self.key))

    async def test_missing_extra_data_or_packet_does_not_dispatch(self):
        for stored_state in (
            None,
            types.SimpleNamespace(extra_data=None),
            types.SimpleNamespace(extra_data=types.SimpleNamespace(as_dict=dict)),
            types.SimpleNamespace(extra_data=types.SimpleNamespace(
                as_dict=lambda: {"packet": "", "device_storage": self.device_storage}
            )),
        ):
            with self.subTest(stored_state=stored_state):
                await self._restore(types.SimpleNamespace(
                    async_get_stored_state=Mock(return_value=stored_state)
                ))
                self.assertIsNone(self.gateway.registry.get(self.key))
                self.assertEqual({}, self.gateway.controller._device_storage)
                self.assertFalse(self.gateway.is_device_state_confirmed(self.key))


class GatewaySafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_connection = gateway_module.AsyncConnection
        gateway_module.AsyncConnection = _FakeConnection
        self.gateway = KocomGateway(
            create_autospec(HomeAssistant, instance=True),
            create_autospec(ConfigEntry, instance=True),
            "test", 1,
        )
        self.gateway.entry.data = types.MappingProxyType({})
        connection = self.gateway.conn
        assert isinstance(connection, _FakeConnection)
        self.connection: _FakeConnection = connection
        # kw-boot-1: the bootstrap now waits for each room's live report and
        # re-asks unanswered rooms; patched delays keep these tests fast and
        # the hour-long re-ask interval keeps them to the first round.
        for name, value in (
            ("CMD_CONFIRM_TIMEOUT", 0.001),
            ("SEND_RETRY_GAP", 0.001),
            ("BOOTSTRAP_REQUERY_INTERVAL", 3600.0),
        ):
            patcher = patch.object(gateway_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

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
        # kw-boot-1: answer waits make the task span real (patched) delays.
        deadline = asyncio.get_running_loop().time() + 2.0
        while asyncio.get_running_loop().time() < deadline:
            if self.gateway._task_bootstrap_queries is None:
                return
            await asyncio.sleep(0.001)
        self.fail("bootstrap query task did not finish")

    async def _wait_for_sent(self, count: int) -> None:
        """Wait until the fake transport has sent at least ``count`` frames."""
        # kw-boot-1: unanswered rooms keep their task alive in the re-ask
        # interval, so tests wait for the first round's frames instead.
        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline:
            if len(self.connection.sent) >= count:
                return
            await asyncio.sleep(0.001)
        self.fail(f"expected {count} frames, sent {len(self.connection.sent)}")

    async def _settle(self) -> None:
        """Give a running bootstrap round time for any further frame."""
        await asyncio.sleep(0.05)

    def _live_report(self, room: int, target: int = 21) -> None:
        self.gateway.controller._dispatch_packet(_thermostat_frame(target, 20, room=room).raw)

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

    async def test_cutoff_cannot_confirm_light_and_normal_success_sends_no_release(self):
        self.gateway.controller.feed(_light_report(0, bytes(8)))
        self.gateway.controller.feed(bytes.fromhex(_REPORTS["cutoff_on"]))
        key = DeviceKey(DeviceType.LIGHT, 0, 0, SubType.NONE)
        waiter = self.gateway._register_confirmation(key, lambda dev: dev.state is True)
        self.gateway.controller.feed(bytes.fromhex(_REPORTS["cutoff_on"]))
        self.assertFalse(waiter.future.done())
        self.gateway._pendings.remove(waiter)
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
        for action in ("turn_on", "turn_off"):
            value = 255 if action == "turn_on" else 0
            self.connection.on_send = lambda _packet, value=value: self.gateway.controller.feed(
                _light_report(0, bytes((value, 0, 0, 0, 0, 0, 0, 0)))
            )
            self.assertTrue(await self.gateway.async_send_action(key, action))
        self.assertEqual(2, len(self.connection.sent))
        self.assertTrue(all(packet[5:7] == b"\x0e\x00" for packet in self.connection.sent))

    async def test_equal_live_reports_refresh_age_but_restore_and_invalid_reports_do_not(self):
        key = self._thermostat_state(1).key
        with patch.object(gateway_module, "monotonic", return_value=100.0) as clock:
            self._live_report(1)
            self.assertEqual(100.0, self.gateway._primary_report_times[key.key])
            clock.return_value = 200.0
            self._live_report(1)
            self.assertEqual(200.0, self.gateway._primary_report_times[key.key])
            self.gateway._restore_mode = True
            self._live_report(1)
            self.gateway._restore_mode = False
            invalid = bytearray(_thermostat_frame(21, 20).raw)
            invalid[18] ^= 1
            clock.return_value = 400.0
            self.gateway.controller.feed(bytes(invalid))
            self.gateway.controller.feed(_thermostat_frame(21, 20, mirrored=True).raw)
            self.assertEqual(200.0, self.gateway._primary_report_times[key.key])
            self.assertTrue(self.gateway._is_stale_thermostat(key))
        self.gateway._connection_available = True
        self.connection.connected = False
        self.gateway._sync_connection_availability()
        self.assertEqual({}, self.gateway._primary_report_times)

    async def test_freshness_queries_only_stale_confirmed_primary_rooms(self):
        self.gateway._connection_generation = 3
        with patch.object(gateway_module, "monotonic", return_value=100.0) as clock:
            for room in (1, 2):
                self._live_report(room)
            clock.return_value = 219.9
            await self.gateway._async_refresh_stale_thermostats()
            self.assertTrue(self.gateway._tx_queue.empty())
            clock.return_value = 220.0
            self.gateway._primary_report_times[self._thermostat_state(2).key.key] = 219.0
            unknown = self._thermostat_state(3)
            self.gateway.registry.upsert(unknown)
            virtual = self._thermostat_state(255)
            self.gateway.on_device_state(virtual)
            nonprimary = DeviceState(DeviceKey(DeviceType.THERMOSTAT, 4, 1, SubType.NONE), Platform.CLIMATE, {}, {})
            self.gateway.on_device_state(nonprimary)
            self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
            await self.gateway._async_refresh_stale_thermostats()
            self.assertEqual(1, len(self.connection.sent))
            self.assertEqual(bytes.fromhex("360101003a"), self.connection.sent[0][5:10])
            self.assertEqual(0x3A, self.connection.sent[0][9])
            self.assertEqual([], self.gateway._pendings)
            self.connection.connected = False
            await self.gateway._async_refresh_stale_thermostats()
            self.assertEqual(1, len(self.connection.sent))

    async def test_refresh_rechecks_age_confirmation_epoch_and_cancellation(self):
        self.gateway._connection_generation = 1
        state = self._thermostat_state(1)
        self.gateway.on_device_state(state)
        self.gateway._primary_report_times[state.key.key] = 0.0
        with patch.object(gateway_module, "monotonic", return_value=200.0):
            item = _CmdItem(state.key, "status_query", {}, connection_generation=1, query_intent="refresh")
            self.assertTrue(self.gateway._is_current_status_query(item))
            self.gateway._connection_generation = 2
            self.assertFalse(self.gateway._is_current_status_query(item))
            self.gateway._connection_generation = 1
            self.gateway._live_device_keys.clear()
            self.assertFalse(self.gateway._is_current_status_query(item))
            self.gateway.on_device_state(state)
            self.assertFalse(self.gateway._is_current_status_query(item))
            self.gateway._primary_report_times[state.key.key] = 0.0
            item.future.cancel()
            self.assertFalse(self.gateway._is_current_status_query(item))
            bootstrap = _CmdItem(state.key, "status_query", {}, connection_generation=1)
            self.assertFalse(self.gateway._is_current_status_query(bootstrap))

    async def test_live_report_while_refresh_waits_for_idle_suppresses_send(self):
        self.gateway.on_device_state(self._thermostat_state(1))
        self.gateway._primary_report_times[self._thermostat_state(1).key.key] = 0.0
        self.connection.idle = False
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
        with patch.object(gateway_module, "monotonic", return_value=200.0):
            refresh = asyncio.create_task(self.gateway._async_refresh_stale_thermostats())
            for _ in range(10):
                await asyncio.sleep(0)
                if self.gateway._active_item is not None:
                    break
            self._live_report(1)
            await asyncio.wait_for(refresh, 0.2)
        self.assertEqual([], self.connection.sent)
        await asyncio.wait_for(self.gateway._tx_queue.join(), 0.2)

    async def test_freshness_timer_sweeps_every_120_seconds(self):
        with (
            patch.object(gateway_module.asyncio, "sleep", new=AsyncMock(side_effect=[None, asyncio.CancelledError])) as sleep,
            patch.object(self.gateway, "_async_refresh_stale_thermostats", new=AsyncMock()) as sweep,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await self.gateway._async_freshness_queries()
        self.assertEqual([call(120.0), call(120.0)], sleep.await_args_list)
        sweep.assert_awaited_once_with()

    async def test_stop_cancels_freshness_wait_and_settles_queue(self):
        self.gateway.on_device_state(self._thermostat_state(1))
        self.gateway._primary_report_times[self._thermostat_state(1).key.key] = 0.0
        task = asyncio.create_task(self.gateway._async_refresh_stale_thermostats())
        self.gateway._task_freshness_queries = task
        await asyncio.sleep(0)
        self.assertEqual(1, self.gateway._tx_queue.qsize())
        await self.gateway.async_stop()
        self.assertTrue(task.cancelled())
        self.assertIsNone(self.gateway._task_freshness_queries)
        await asyncio.wait_for(self.gateway._tx_queue.join(), 0.2)
        self.assertEqual([], self.connection.sent)

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

    async def test_equal_live_primary_thermostat_reports_publish_once(self):
        first_packet = _thermostat_frame(21, 20).raw
        self.gateway.controller.feed(first_packet)
        key = self._thermostat_state(1).key
        original = self.gateway.registry.get(key)
        assert original is not None
        unmatched = self.gateway._register_confirmation(
            self._thermostat_state(2).key, lambda _dev: True
        )
        for marker in (1, 2):
            raw = bytearray(first_packet)
            raw[17] = marker
            raw[18] = sum(raw[2:18]) % 256
            packet = bytes(raw)
            order = []
            waiter = self.gateway._register_confirmation(
                key, lambda dev, events=order: events.append(("pending", dev)) is None
            )
            with (
                patch.object(gateway_module, "async_dispatcher_send", side_effect=(
                    lambda _hass, _signal, dev, events=order: events.append(("publish", dev))
                )) as publish,
                patch.object(self.gateway, "on_device_state", wraps=self.gateway.on_device_state) as receive,
            ):
                self.gateway.controller.feed(packet)

            incoming = next(
                call.args[0] for call in receive.call_args_list if call.args[0].key == key
            )
            self.assertIsNot(original, incoming)
            self.assertEqual(original, incoming)
            self.assertEqual(packet, incoming._packet)
            publish.assert_called_once_with(
                self.gateway.hass, self.gateway.async_signal_device_updated(key.unique_id), incoming
            )
            self.assertIs(incoming, publish.call_args.args[-1])
            self.assertEqual([("publish", incoming), ("pending", incoming)], order)
            self.assertIs(incoming, waiter.future.result())
            self.assertFalse(unmatched.future.done())
        self.assertEqual([], self.connection.sent)
        self.assertTrue(self.gateway._tx_queue.empty())
        self.assertIsNone(self.gateway._task_bootstrap_queries)
        self.gateway._resolve_pending(False)

    async def test_non_live_or_other_device_reports_do_not_refresh(self):
        packet = _thermostat_frame(21, 20).raw
        self.gateway.controller.feed(packet)
        key = self._thermostat_state(1).key
        with (
            patch.object(gateway_module, "async_dispatcher_send") as publish,
            patch.object(self.gateway, "_notify_pendings") as notify,
        ):
            self.gateway._restore_mode = True
            self.gateway.controller.feed(packet)
            self.gateway._restore_mode = False
            self.connection.connected = False
            self.gateway.controller.feed(packet)
            self.connection.connected = True
            self.gateway.controller.feed(_thermostat_frame(21, 20, packet_type=0x0D, mirrored=True).raw)
            self.gateway.controller.feed(_thermostat_frame(21, 20, command=0x3A).raw)
            corrupt = bytearray(packet)
            corrupt[18] ^= 1
            self.gateway.controller.feed(bytes(corrupt))
            corrupt = bytearray(packet)
            corrupt[-1] = 0
            self.gateway.controller.feed(bytes(corrupt))
            publish.assert_not_called()
            notify.assert_not_called()

        other_packet = _thermostat_frame(21, 20, room=2).raw
        self.gateway.controller.feed(other_packet)
        with patch.object(gateway_module, "async_dispatcher_send") as publish:
            self.gateway.controller.feed(_thermostat_frame(22, 20, room=2).raw)
            publish.assert_called_once()
            other = publish.call_args.args[-1]
            self.assertEqual(self._thermostat_state(2).key, other.key)
            self.assertNotEqual(key, other.key)
            self.assertEqual(
                self.gateway.async_signal_device_updated(other.key.unique_id),
                publish.call_args.args[1],
            )

        excluded = [
            self._state(True),
            DeviceState(DeviceKey(DeviceType.AIRCONDITIONER, 1, 0, SubType.NONE), Platform.CLIMATE, {}, {}),
            DeviceState(key, Platform.SENSOR, {}, 20),
            *(
                DeviceState(DeviceKey(DeviceType.THERMOSTAT, 3, 0, subtype), platform, {}, 20)
                for subtype, platform in (
                    (SubType.HOTTEMP, Platform.SENSOR),
                    (SubType.HEATTEMP, Platform.SENSOR),
                    (SubType.ERRCODE, Platform.BINARY_SENSOR),
                )
            ),
        ]
        for dev in excluded:
            with self.subTest(key=dev.key, platform=dev.platform):
                self.gateway.on_device_state(dev)
                incoming = DeviceState(dev.key, dev.platform, dict(dev.attribute), dev.state)
                with (
                    patch.object(gateway_module, "async_dispatcher_send") as publish,
                    patch.object(self.gateway, "_notify_pendings") as notify,
                ):
                    self.gateway.on_device_state(incoming)
                    publish.assert_not_called()
                    notify.assert_called_once_with(incoming)
        self.assertEqual([], self.connection.sent)
        self.assertTrue(self.gateway._tx_queue.empty())

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
        # kw-boot-1: nothing answers, so the first round sends every room's
        # first query before any retry, SEND_RETRY_MAX (3) rounds in all.
        await self._wait_for_sent(9)
        await self._settle()
        self.assertEqual(
            [bytes.fromhex("aa5530bc00360001003a00000000000000005d0d0d"),
             bytes.fromhex("aa5530bc00360101003a00000000000000005e0d0d"),
             bytes.fromhex("aa5530bc00360201003a00000000000000005f0d0d")] * 3,
            self.connection.sent,
        )

        self.gateway._sync_connection_availability()
        await asyncio.sleep(0)
        self.assertEqual(9, len(self.connection.sent))

        self.connection.connected = False
        self.gateway._sync_connection_availability()
        self.connection.connected = True
        self.gateway._sync_connection_availability()
        await self._wait_for_sent(18)
        await self._settle()
        self.assertEqual(18, len(self.connection.sent))

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
        # kw-boot-1: no room answers, so each gets SEND_RETRY_MAX first-round tries.
        await self._wait_for_sent(9)
        await self._settle()
        self.assertEqual([0, 1, 3] * 3, [packet[6] for packet in self.connection.sent])

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
        # kw-boot-1: the unanswered room gets SEND_RETRY_MAX first-round tries.
        await self._wait_for_sent(3)
        await self._settle()
        self.assertEqual([room] * 3, [packet[6] for packet in self.connection.sent])

    def _restore_thermostats(self, *rooms: int) -> list[DeviceState]:
        states = [self._thermostat_state(room) for room in rooms]
        self.gateway._restore_mode = True
        for state in states:
            self.gateway.on_device_state(state)
        self.gateway._restore_mode = False
        return states

    async def test_bootstrap_query_retries_and_reasks_unanswered_room(self):
        (state,) = self._restore_thermostats(1)
        interval = 0.05
        sent_at: list[float] = []
        loop = asyncio.get_running_loop()
        self.connection.on_send = lambda _packet: sent_at.append(loop.time())
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())

        with (
            patch.object(gateway_module, "BOOTSTRAP_REQUERY_INTERVAL", interval),
            self.assertNoLogs("custom_components.kocom_wallpad", level="WARNING"),
        ):
            self.gateway._sync_connection_availability()
            generation = self.gateway._connection_generation
            await self._wait_for_sent(3)
            self.assertEqual(3, len(self.connection.sent))
            await self._wait_for_sent(4)
            await self._wait_for_sent(5)
            self.assertEqual(5, len(self.connection.sent))
            self.assertEqual(generation, self.gateway._connection_generation)

        self.assertTrue(all(packet[9] == 0x3A and packet[6] == 1 for packet in self.connection.sent))
        gaps = [later - earlier for earlier, later in itertools.pairwise(sent_at)]
        self.assertTrue(all(gap < interval for gap in gaps[:2]), gaps)
        self.assertTrue(all(gap >= interval * 0.9 for gap in gaps[2:]), gaps)
        self.assertFalse(self.gateway.is_device_state_confirmed(state.key))
        self.assertFalse(self.gateway.is_device_available(state.key))
        task = self.gateway._task_bootstrap_queries
        self.assertIsNotNone(task)

        # A new connection generation ends the re-asking with no further send.
        with patch.object(gateway_module, "BOOTSTRAP_REQUERY_INTERVAL", interval):
            self.gateway._connection_generation += 1
            sent = len(self.connection.sent)
            await asyncio.sleep(interval * 3)
        self.assertEqual(sent, len(self.connection.sent))
        assert task is not None
        self.assertTrue(task.done())
        self.assertFalse(self.gateway.is_device_state_confirmed(state.key))

    async def test_bootstrap_room_answering_first_query_gets_one_frame(self):
        (state,) = self._restore_thermostats(1)
        self.connection.on_send = lambda packet: self._live_report(packet[6])
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())

        self.gateway._sync_connection_availability()
        await self._wait_for_bootstrap()

        self.assertEqual(1, len(self.connection.sent))
        self.assertTrue(self.gateway.is_device_available(state.key))

    async def test_bootstrap_report_between_tries_stops_only_that_room(self):
        first, second = self._restore_thermostats(1, 2)

        def on_send(packet: bytes) -> None:
            # Room 1 reports on its own between its first and second try.
            if packet[6] == 2 and len(self.connection.sent) == 2:
                self._live_report(1)

        self.connection.on_send = on_send
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())

        self.gateway._sync_connection_availability()
        await self._wait_for_sent(4)
        await self._settle()

        self.assertEqual([1, 2, 2, 2], [packet[6] for packet in self.connection.sent])
        self.assertTrue(self.gateway.is_device_available(first.key))
        self.assertFalse(self.gateway.is_device_state_confirmed(second.key))
        self.assertIsNotNone(self.gateway._task_bootstrap_queries)

    async def test_bootstrap_report_between_rounds_ends_reasking(self):
        (state,) = self._restore_thermostats(1)
        interval = 0.05
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())

        with patch.object(gateway_module, "BOOTSTRAP_REQUERY_INTERVAL", interval):
            self.gateway._sync_connection_availability()
            await self._wait_for_sent(3)
            await asyncio.sleep(0.01)
            self._live_report(1)
            await self._wait_for_bootstrap()
            await asyncio.sleep(interval * 3)

        self.assertEqual(3, len(self.connection.sent))
        self.assertTrue(self.gateway.is_device_available(state.key))

    async def test_bootstrap_report_while_query_waits_skips_the_send(self):
        (state,) = self._restore_thermostats(1)
        self.connection.idle = False
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())

        self.gateway._sync_connection_availability()
        for _ in range(20):
            if self.gateway._active_item is not None:
                break
            await asyncio.sleep(0)
        self.assertIsNotNone(self.gateway._active_item)
        self._live_report(1)
        self.connection.idle = True
        await self._wait_for_bootstrap()

        self.assertEqual([], self.connection.sent)
        self.assertTrue(self.gateway.is_device_available(state.key))

    async def test_thermostat_command_is_not_held_by_bootstrap_answer_wait(self):
        silent, commanded = self._restore_thermostats(1, 2)
        confirm_wait = 0.5
        sent_at: list[float] = []
        loop = asyncio.get_running_loop()

        def on_send(packet: bytes) -> None:
            sent_at.append(loop.time())
            if packet[9] != 0x3A and packet[6] == 2:
                self._live_report(2, target=packet[12])

        self.connection.on_send = on_send
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())

        with patch.object(gateway_module, "CMD_CONFIRM_TIMEOUT", confirm_wait):
            self.gateway._sync_connection_availability()
            await self._wait_for_sent(1)
            self.assertEqual((1, 0x3A), (self.connection.sent[0][6], self.connection.sent[0][9]))
            self.assertTrue(await asyncio.wait_for(
                self.gateway.async_send_action(
                    commanded.key, "set_temperature", target_temp=23
                ),
                timeout=confirm_wait / 2,
            ))
            # The room-1 answer wait is still running when the command went out.
            self.assertFalse(self.gateway._task_bootstrap_queries is None)
            self.assertEqual(2, len(self.connection.sent))
            self.assertLess(sent_at[1] - sent_at[0], confirm_wait)
            self.assertNotEqual(0x3A, self.connection.sent[1][9])

            # Room 2 reported through the command answer, so it is never queried.
            await self._wait_for_sent(4)
            await self._settle()
        self.assertEqual([1, 2, 1, 1], [packet[6] for packet in self.connection.sent])
        self.assertTrue(self.gateway.is_device_available(commanded.key))
        self.assertFalse(self.gateway.is_device_state_confirmed(silent.key))

    async def test_disconnect_and_stop_cancel_reasking(self):
        for end in ("disconnect", "stop"):
            with self.subTest(end=end):
                self.connection.sent.clear()
                self.connection.connected = True
                self.gateway._restore_mode = True
                self.gateway.on_device_state(self._thermostat_state(1))
                self.gateway._restore_mode = False
                self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
                with patch.object(gateway_module, "BOOTSTRAP_REQUERY_INTERVAL", 0.05):
                    self.gateway._sync_connection_availability()
                    await self._wait_for_sent(3)
                    task = self.gateway._task_bootstrap_queries
                    assert task is not None
                    if end == "disconnect":
                        self.connection.connected = False
                        self.gateway._sync_connection_availability()
                    else:
                        await self.gateway.async_stop()
                    sent = len(self.connection.sent)
                    await asyncio.sleep(0.2)
                self.assertEqual(sent, len(self.connection.sent))
                self.assertTrue(task.cancelled())
                if end == "disconnect":
                    self.gateway._task_sender.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self.gateway._task_sender
                    self.gateway._task_sender = None

    async def test_eof_reconnect_advances_generation_and_queries_once(self):
        state = self._thermostat_state(1)
        self.gateway._restore_mode = True
        self.gateway.on_device_state(state)
        self.gateway._restore_mode = False
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
        self.gateway._sync_connection_availability()
        # kw-boot-1: the unanswered room gets SEND_RETRY_MAX tries per session.
        await self._wait_for_sent(3)
        await self._settle()
        initial_generation = self.gateway._connection_generation
        self.assertEqual(3, len(self.connection.sent))

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

        # kw-boot-1: the new session's round sends three more frames.
        for _ in range(1000):
            if (
                self.gateway._connection_generation == initial_generation + 2
                and len(self.connection.sent) == 6
            ):
                break
            await asyncio.sleep(0.001)
        await self._settle()

        self.assertEqual(initial_generation + 2, self.gateway._connection_generation)
        self.assertEqual(6, len(self.connection.sent))

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

        # kw-boot-1: the room answers its new-session query, ending the re-asking.
        self.connection.on_send = lambda packet: self._live_report(packet[6])
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
        # kw-boot-1: the unanswered room gets SEND_RETRY_MAX first-round tries.
        await self._wait_for_sent(3)
        await self._settle()
        self.assertEqual(3, len(self.connection.sent))
        self.assertEqual([room] * 3, [packet[6] for packet in self.connection.sent])

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
        self.assertIn("serialx>=1.11.0", manifest["requirements"])
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


# Frames observed on 2026-10-23 (room 03; 0100 is the wallpad and the
# integration's own source address), byte for byte.
_OBSERVED = {
    "command_21": "aa5530bc00360301000011001500000000004c0d0d",
    "ack_21": "aa5530dc00010036030011001500000000006c0d0d",
    "status_21": "aa5530bc0001003603001100150018000000640d0d",
    "wallpad_ack_21": "aa5530dc0036030100001100150018000000840d0d",
    "command_20": "aa5530bc00360301000011001400000000004b0d0d",
    "ack_20": "aa5530dc00010036030011001400000000006b0d0d",
    "status_20": "aa5530bc0001003603001100140018000000630d0d",
    "wallpad_ack_20": "aa5530dc0036030100001100140018000000830d0d",
    "query_00": "aa5530bc00360001003a00000000000000005d0d0d",
    "query_ack_00": "aa5530dc00010036003a00000000000000007d0d0d",
    "status_00": "aa5530bc00010036000011001a001c0000006a0d0d",
    "wallpad_ack_00": "aa5530dc00360001000011001a001c0000008a0d0d",
    "query_03": "aa5530bc00360301003a0000000000000000600d0d",
    "query_ack_03": "aa5530dc00010036033a0000000000000000800d0d",
}


def _frame(name: str) -> bytes:
    return bytes.fromhex(_OBSERVED[name])


_ROOM_3 = DeviceKey(DeviceType.THERMOSTAT, 3, 0, SubType.NONE)


class _StateSink:
    """A gateway stand-in with only on_device_state."""

    def __init__(self) -> None:
        self.states: list[DeviceState] = []

    def on_device_state(self, state: DeviceState) -> None:
        self.states.append(state)


class _RestoringSink(_StateSink):
    """A stand-in gateway that can switch restore mode."""

    _restore_mode = True


def _labels(states: list[DeviceState]) -> list[dict]:
    return [
        label
        for state in states
        if state.key == _ROOM_3
        and (label := getattr(state, "_change_label", None)) is not None
    ]


class ChangeSourceControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sink = _StateSink()
        self.controller = KocomController(self.sink)

    def _feed(self, *names: str) -> None:
        for name in names:
            self.controller.feed(_frame(name))

    def test_observed_frames_have_valid_checksums(self):
        for name in _OBSERVED:
            packet = _frame(name)
            self.assertEqual(21, len(packet), name)
            self.assertEqual(sum(packet[2:18]) % 256, packet[18], name)

    def test_received_command_sequence_is_wallpad(self):
        self._feed("status_20", "wallpad_ack_20")
        self.assertEqual([], _labels(self.sink.states))
        self._feed("command_21", "ack_21", "status_21", "wallpad_ack_21")
        self._feed("command_20", "ack_20", "status_20", "wallpad_ack_20")
        labels = _labels(self.sink.states)
        self.assertEqual(
            [
                {"source": "wallpad", "changes": {"target_temperature": {"old": 20.0, "new": 21.0}}, "context": None},
                {"source": "wallpad", "changes": {"target_temperature": {"old": 21.0, "new": 20.0}}, "context": None},
            ],
            [{k: v for k, v in label.items() if k != "sequence"} for label in labels],
        )
        self.assertLess(labels[0]["sequence"], labels[1]["sequence"])

    def test_status_report_alone_is_device(self):
        self._feed("status_20", "wallpad_ack_20", "status_21", "wallpad_ack_21")
        labels = _labels(self.sink.states)
        self.assertEqual(1, len(labels))
        self.assertEqual("device", labels[0]["source"])
        self.assertEqual({"target_temperature": {"old": 20.0, "new": 21.0}}, labels[0]["changes"])

    def test_received_query_then_changed_report_is_unknown(self):
        self._feed("status_21", "wallpad_ack_21")
        self._feed("query_03", "query_ack_03", "status_20", "wallpad_ack_20")
        labels = _labels(self.sink.states)
        self.assertEqual(["unknown"], [label["source"] for label in labels])
        self.assertEqual({"target_temperature": {"old": 21.0, "new": 20.0}}, labels[0]["changes"])

    def test_received_query_then_unchanged_report_is_no_change(self):
        self._feed("status_20", "wallpad_ack_20")
        self._feed("query_00", "query_ack_00", "status_00", "wallpad_ack_00")
        self._feed("query_03", "query_ack_03", "status_20", "wallpad_ack_20")
        self.assertEqual([], _labels(self.sink.states))

    def test_report_more_than_two_seconds_after_command_is_device(self):
        clock = [100.0]
        with patch("custom_components.kocom_wallpad.controller.time.monotonic", lambda: clock[0]):
            self._feed("status_20")
            self._feed("command_21", "ack_21")
            clock[0] += 2.5
            self._feed("status_21")
            clock[0] += 10.0
            self._feed("command_20", "ack_20")
            clock[0] += 1.9
            self._feed("status_20")
        self.assertEqual(["device", "wallpad"], [label["source"] for label in _labels(self.sink.states)])

    def test_current_temperature_only_is_no_change(self):
        self._feed("status_20")
        changed = bytearray(_frame("status_20"))
        changed[14] = 0x19  # current temperature 25 instead of 24
        changed[18] = sum(changed[2:18]) % 256
        self.controller.feed(bytes(changed))
        self.assertEqual(2, len([s for s in self.sink.states if s.key == _ROOM_3]))
        self.assertEqual([], _labels(self.sink.states))

    def test_first_live_report_after_setup_is_no_change(self):
        self._feed("command_21", "ack_21", "status_21", "wallpad_ack_21")
        self.assertEqual([], _labels(self.sink.states))

    def test_restore_mode_report_and_mirror_are_no_change(self):
        sink = _RestoringSink()
        self.sink = sink
        self.controller = KocomController(sink)
        self._feed("status_20", "wallpad_ack_20")
        restored = [s for s in sink.states if s.key == _ROOM_3]
        self.assertEqual(2, len(restored))
        sink._restore_mode = False
        self._feed("status_21", "wallpad_ack_21")
        self.assertEqual(3, len([s for s in sink.states if s.key == _ROOM_3]))
        self.assertEqual([], _labels(sink.states))

    def test_labelled_state_keeps_dataclass_contract(self):
        self._feed("status_20", "status_21")
        state = next(s for s in self.sink.states if getattr(s, "_change_label", None))
        self.assertEqual(("key", "platform", "attribute", "state"), tuple(f.name for f in fields(state)))
        self.assertNotIn("_change_label", asdict(state))

    def test_received_command_and_query_frames_outside_loop_produce_nothing(self):
        with self.assertRaises(RuntimeError):
            asyncio.get_running_loop()
        for name in ("command_21", "command_20", "query_00", "query_03"):
            self._feed(name)
        self.assertEqual([], self.sink.states)


class ChangeSourceGatewayTests(unittest.IsolatedAsyncioTestCase):
    """Real controller and gateway with the fake transport."""

    async def asyncSetUp(self):
        patcher = patch.object(gateway_module, "AsyncConnection", _FakeConnection)
        patcher.start()
        self.addCleanup(patcher.stop)
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

    async def _wait_for_sent(self, count: int) -> None:
        deadline = asyncio.get_running_loop().time() + 1.0
        while len(self.connection.sent) < count:
            if asyncio.get_running_loop().time() > deadline:
                self.fail(f"expected {count} frames, sent {len(self.connection.sent)}")
            await asyncio.sleep(0.001)

    def _room_state(self) -> DeviceState | None:
        return self.gateway.registry.get(_ROOM_3)

    def _label(self) -> dict | None:
        return getattr(self._room_state(), "_change_label", None)

    def _start(self) -> None:
        self.gateway._sync_connection_availability()
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
        self.gateway.controller.feed(_frame("status_20") + _frame("wallpad_ack_20"))
        self.assertIsNone(self._label())

    async def test_transmitted_command_sequence_is_integration(self):
        self._start()
        context = object()
        task = asyncio.create_task(self.gateway.async_send_action(
            _ROOM_3, "set_temperature", context=context, target_temp=21.0
        ))
        await self._wait_for_sent(1)
        self.assertEqual([_frame("command_21")], self.connection.sent)
        for name in ("ack_21", "status_21", "wallpad_ack_21"):
            self.gateway.controller.feed(_frame(name))
        self.assertTrue(await asyncio.wait_for(task, timeout=1.0))
        label = self._label()
        assert label is not None
        self.assertEqual("integration", label["source"])
        self.assertIs(context, label["context"])
        self.assertEqual({"target_temperature": {"old": 20.0, "new": 21.0}}, label["changes"])

    async def test_same_bytes_received_are_wallpad(self):
        self._start()
        for name in ("command_21", "ack_21", "status_21", "wallpad_ack_21"):
            self.gateway.controller.feed(_frame(name))
        label = self._label()
        assert label is not None
        self.assertEqual("wallpad", label["source"])
        self.assertIsNone(label["context"])
        self.assertEqual([], self.connection.sent)

    async def test_report_fed_from_inside_send_is_integration(self):
        self._start()
        self.connection.on_send = lambda _packet: self.gateway.controller.feed(
            _frame("ack_21") + _frame("status_21") + _frame("wallpad_ack_21")
        )
        self.assertTrue(await asyncio.wait_for(
            self.gateway.async_send_action(_ROOM_3, "set_temperature", target_temp=21.0),
            timeout=1.0,
        ))
        label = self._label()
        assert label is not None
        self.assertEqual("integration", label["source"])
        self.assertIsNone(label["context"])

    async def test_first_live_report_after_reconnection_is_no_change(self):
        self._start()
        self.gateway.controller.feed(_frame("command_21"))
        self.connection.connected = False
        self.gateway._sync_connection_availability()
        self.connection.connected = True
        sender = self.gateway._task_sender
        assert sender is not None
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender
        self.gateway._task_sender = None
        self.gateway._sync_connection_availability()
        self.gateway.controller.feed(_frame("status_21") + _frame("wallpad_ack_21"))
        state = self._room_state()
        assert state is not None and isinstance(state.state, dict)
        self.assertEqual(21.0, state.state["target_temp"])
        self.assertIsNone(self._label())

    async def test_received_command_and_query_frames_leave_state_untouched(self):
        self._start()
        before = self._room_state()
        live_keys = set(self.gateway._live_device_keys)
        waiter = self.gateway._register_confirmation(_ROOM_3, lambda _dev: True)
        other = DeviceKey(DeviceType.THERMOSTAT, 4, 0, SubType.NONE)
        with patch.object(gateway_module, "async_dispatcher_send") as publish:
            for name in ("command_21", "ack_21", "query_03", "query_ack_03", "query_00", "query_ack_00"):
                self.gateway.controller.feed(_frame(name))
        publish.assert_not_called()
        self.assertFalse(waiter.future.done())
        self.assertIs(before, self._room_state())
        self.assertEqual(live_keys, self.gateway._live_device_keys)
        self.assertIsNone(self.gateway.registry.get(other))
        self.assertEqual([], self.connection.sent)
        self.gateway._pendings.remove(waiter)


_RELEASE_FRAME = bytes.fromhex("aa55309c000eff010066ffffffffffffffff380d0d")
_RELEASE_OPTION = "cutoff_release_after_light"
_CUTOFF_KEY = DeviceKey(DeviceType.LIGHTCUTOFF, 255, 0, SubType.NONE)


class CutoffReleaseTests(unittest.IsolatedAsyncioTestCase):
    gateway: KocomGateway
    connection: _FakeConnection
    asyncSetUp = GatewaySafetyTests.asyncSetUp
    asyncTearDown = GatewaySafetyTests.asyncTearDown

    def cutoff(self, value: bool | str = False, *, live=True):
        state = DeviceState(_CUTOFF_KEY, Platform.BINARY_SENSOR, {}, value)
        self.gateway._restore_mode = not live
        self.gateway.on_device_state(state)
        self.gateway._restore_mode = False
        return state

    def start(self):
        self.gateway.entry.data = types.MappingProxyType({_RELEASE_OPTION: True})
        self.gateway._connection_generation = 1
        self.gateway._connection_available = True
        self.cutoff()
        self.key = DeviceKey(DeviceType.LIGHT, 1, 0, SubType.NONE)
        self.connection.on_send = self.acknowledge
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())

    def acknowledge(self, packet):
        if packet != _RELEASE_FRAME:
            self.gateway.on_device_state(DeviceState(
                self.key, Platform.LIGHT, {}, packet[10] == 255,
            ))

    def delay(self):
        entered, release = asyncio.Event(), asyncio.Event()
        real_sleep = asyncio.sleep

        async def sleep(seconds):
            if seconds == 1.0:
                entered.set()
                await release.wait()
            else:
                await real_sleep(0)

        return entered, release, patch.object(gateway_module.asyncio, "sleep", side_effect=sleep)

    async def send(self, action="turn_on"):
        return await self.gateway.async_send_action(self.key, action)

    async def test_literal_frame_after_matching_on_and_off_is_serialized_once(self):
        self.start()
        self.assertEqual(21, len(_RELEASE_FRAME))
        self.assertEqual(sum(_RELEASE_FRAME[2:18]) & 255, _RELEASE_FRAME[18])
        entered, release, sleep_patch = self.delay()
        with sleep_patch:
            first = asyncio.create_task(self.send())
            await asyncio.wait_for(entered.wait(), 0.5)
            self.assertFalse(first.done())
            self.assertEqual(1, len(self.connection.sent))
            second = asyncio.create_task(self.send("turn_off"))
            release.set()
            self.assertTrue(await asyncio.wait_for(first, 0.5))
            self.assertTrue(await asyncio.wait_for(second, 0.5))
        self.assertEqual(4, len(self.connection.sent))
        self.assertEqual(_RELEASE_FRAME, self.connection.sent[1])
        self.assertEqual(_RELEASE_FRAME, self.connection.sent[3])
        self.assertEqual(255, self.connection.sent[0][10])
        self.assertEqual(0, self.connection.sent[2][10])
        self.assertGreater(self.gateway._last_tx_monotonic, 0)

    async def test_disabled_unknown_restored_and_unconfirmed_cutoff_skip_delay(self):
        self.start()
        with patch.object(gateway_module.asyncio, "sleep", new=AsyncMock()) as sleep:
            for option in ({}, {_RELEASE_OPTION: False}, {_RELEASE_OPTION: 1},
                           {_RELEASE_OPTION: "true"}, {_RELEASE_OPTION: None}):
                self.gateway.entry.data = types.MappingProxyType(option)
                self.assertTrue(await self.send())
            self.gateway.entry.data = types.MappingProxyType({_RELEASE_OPTION: True})
            for kind in ("missing", "on", "unknown", "restored", "unconfirmed"):
                self.gateway._live_device_keys.discard(_CUTOFF_KEY.key)
                self.gateway.registry._states.pop(_CUTOFF_KEY.key, None)
                if kind != "missing":
                    self.cutoff(True if kind == "on" else "unknown" if kind == "unknown" else False,
                                live=kind not in ("restored", "unconfirmed"))
                self.assertTrue(await self.send())
            sleep.assert_not_awaited()
        self.assertEqual(10, len(self.connection.sent))
        self.assertNotIn(_RELEASE_FRAME, self.connection.sent)

    async def test_false_waiter_wrong_state_and_old_generation_do_not_qualify(self):
        self.start()
        wrong_key = DeviceKey(DeviceType.LIGHT, 2, 0, SubType.NONE)
        for response in (False, DeviceState(wrong_key, Platform.LIGHT, {}, True),
                         DeviceState(self.key, Platform.LIGHT, {}, False)):
            with patch.object(self.gateway, "_wait_for_confirmation", new=AsyncMock(return_value=response)):
                self.assertFalse(await self.send())
        def reconnect(packet):
            self.acknowledge(packet)
            self.gateway._connection_generation += 1
            self.cutoff()
        self.connection.on_send = reconnect
        self.assertTrue(await self.send())
        self.assertEqual(4, len(self.connection.sent))
        self.assertNotIn(_RELEASE_FRAME, self.connection.sent)

    async def test_delay_revalidates_cutoff_option_generation_connection_and_stop(self):
        self.start()
        for change in ("cutoff", "option", "generation", "connection", "stop", "confirmation"):
            self.gateway.entry.data = types.MappingProxyType({_RELEASE_OPTION: True})
            self.connection.connected = True
            self.gateway._stopping = False
            self.cutoff()
            entered, release, sleep_patch = self.delay()
            with sleep_patch:
                caller = asyncio.create_task(self.send())
                await asyncio.wait_for(entered.wait(), 0.5)
                if change == "cutoff":
                    self.cutoff(True)
                elif change == "option":
                    self.gateway.entry.data = types.MappingProxyType({_RELEASE_OPTION: False})
                elif change == "generation":
                    self.gateway._connection_generation += 1
                elif change == "connection":
                    self.connection.connected = False
                elif change == "stop":
                    self.gateway._stopping = True
                else:
                    self.gateway._live_device_keys.clear()
                release.set()
                self.assertTrue(await asyncio.wait_for(caller, 0.5))
        self.assertEqual(6, len(self.connection.sent))
        self.assertNotIn(_RELEASE_FRAME, self.connection.sent)

    async def test_caller_cancellation_during_delay_skips_and_queue_progresses(self):
        self.start()
        entered, release, sleep_patch = self.delay()
        with sleep_patch:
            caller = asyncio.create_task(self.send())
            await asyncio.wait_for(entered.wait(), 0.5)
            caller.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await caller
            self.gateway.entry.data = types.MappingProxyType({_RELEASE_OPTION: False})
            next_caller = asyncio.create_task(self.send("turn_off"))
            release.set()
            self.assertTrue(await asyncio.wait_for(next_caller, 0.5))
        self.assertEqual(2, len(self.connection.sent))
        await asyncio.wait_for(self.gateway._tx_queue.join(), 0.5)

    async def test_sender_shutdown_retains_acknowledged_true_during_supplement(self):
        self.start()
        entered, _release, sleep_patch = self.delay()
        with sleep_patch:
            caller = asyncio.create_task(self.send())
            await asyncio.wait_for(entered.wait(), 0.5)
            await self.gateway.async_stop()
            self.assertTrue(await asyncio.wait_for(caller, 0.5))
        self.assertTrue(self.gateway._stopping)
        self.assertEqual(1, len(self.connection.sent))
        await asyncio.wait_for(self.gateway._tx_queue.join(), 0.5)

    async def test_stop_flag_precedes_first_shutdown_await(self):
        self.start()
        entered, release = asyncio.Event(), asyncio.Event()
        async def reader():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                entered.set()
                await release.wait()
        self.gateway._task_reader = asyncio.create_task(reader())
        await asyncio.sleep(0)
        stopper = asyncio.create_task(self.gateway.async_stop())
        await asyncio.wait_for(entered.wait(), 0.5)
        self.assertTrue(self.gateway._stopping)
        release.set()
        await stopper

    async def test_busy_admission_rechecks_every_guard_and_skips_persistently_busy_bus(self):
        self.start()
        real_sleep = asyncio.sleep
        for change in ("cutoff", "option", "generation", "connection", "stop", "cancel", "busy"):
            self.connection.idle = True
            self.connection.connected = True
            self.gateway._stopping = False
            self.gateway.entry.data = types.MappingProxyType({_RELEASE_OPTION: True})
            self.cutoff()
            entered, resume = asyncio.Event(), asyncio.Event()
            async def sleep(seconds, *, entered=entered, resume=resume):
                if seconds == 1.0:
                    self.connection.idle = False
                elif seconds == 0.01:
                    entered.set()
                    await resume.wait()
                await real_sleep(0)
            with patch.object(gateway_module.asyncio, "sleep", side_effect=sleep):
                caller = asyncio.create_task(self.send())
                await asyncio.wait_for(entered.wait(), 0.5)
                if change == "cutoff":
                    self.cutoff(True)
                elif change == "option":
                    self.gateway.entry.data = types.MappingProxyType({_RELEASE_OPTION: False})
                elif change == "generation":
                    self.gateway._connection_generation += 1
                elif change == "connection":
                    self.connection.connected = False
                elif change == "stop":
                    self.gateway._stopping = True
                elif change == "cancel":
                    caller.cancel()
                # Advance only the budget clock; avoid a real one-second busy loop.
                clock_start = asyncio.get_running_loop().time() + 2.0
                with patch.object(asyncio.get_running_loop(), "time", side_effect=itertools.count(clock_start, 2)):
                    resume.set()
                    if change == "cancel":
                        with self.assertRaises(asyncio.CancelledError):
                            await caller
                    else:
                        self.assertTrue(await caller)
        self.assertEqual(7, len(self.connection.sent))
        self.assertNotIn(_RELEASE_FRAME, self.connection.sent)

    async def test_supplement_failure_preserves_actual_light_result_and_live_state(self):
        self.start()
        cutoff = self.gateway.registry.get(_CUTOFF_KEY)
        def send(packet):
            if packet == _RELEASE_FRAME:
                raise OSError("synthetic transport failure")
            self.acknowledge(packet)
        self.connection.on_send = send
        entered, release, sleep_patch = self.delay()
        release.set()
        with sleep_patch:
            self.assertTrue(await self.send())
        self.assertTrue(entered.is_set())
        self.assertEqual(2, len(self.connection.sent))
        self.assertIs(cutoff, self.gateway.registry.get(_CUTOFF_KEY))
        self.assertTrue(self.gateway.is_device_state_confirmed(_CUTOFF_KEY))

    async def test_exact_owned_echo_cannot_overwrite_genuine_cutoff_on_and_resets(self):
        self.start()
        def send(packet):
            if packet == _RELEASE_FRAME:
                self.gateway.controller.feed(bytes.fromhex(_REPORTS["cutoff_on"]))
                self.gateway.controller.feed(packet)
            else:
                self.acknowledge(packet)
        self.connection.on_send = send
        entered, release, sleep_patch = self.delay()
        release.set()
        with sleep_patch:
            self.assertTrue(await self.send())
        self.assertTrue(entered.is_set())
        cutoff = self.gateway.registry.get(_CUTOFF_KEY)
        assert cutoff is not None
        self.assertIs(cutoff.state, True)
        waiter = self.gateway._register_confirmation(_CUTOFF_KEY, lambda dev: dev.state is False)
        self.gateway.controller.feed(_RELEASE_FRAME)
        self.assertFalse(waiter.future.done())
        # An ordinary status frame with the same semantic off value is still accepted.
        off = bytearray.fromhex(_REPORTS["cutoff_on"])
        off[9] = 0x66
        off[18] = sum(off[2:18]) & 255
        self.gateway.controller.feed(bytes(off))
        self.assertTrue(waiter.future.done())
        cutoff = self.gateway.registry.get(_CUTOFF_KEY)
        assert cutoff is not None
        self.assertIs(cutoff.state, False)
        self.connection.connected = False
        self.gateway._sync_connection_availability()
        self.connection.connected = True
        self.gateway._sync_connection_availability()
        self.gateway.controller.feed(_RELEASE_FRAME)
        self.assertTrue(self.gateway.is_device_state_confirmed(_CUTOFF_KEY))

    async def test_enabled_reports_and_status_query_only_send_no_supplement(self):
        self.start()
        self.gateway.controller.feed(_light_report(1, bytes((0, 0, 0, 0, 0, 0, 0, 0))))
        await asyncio.sleep(0)
        self.assertEqual([], self.connection.sent)
        key = DeviceKey(DeviceType.THERMOSTAT, 1, 0, SubType.NONE)
        item = _CmdItem(key, "status_query", {}, connection_generation=1)
        self.gateway._tx_queue.put_nowait(item)
        self.assertTrue(await item.future)
        self.assertEqual(1, len(self.connection.sent))
        self.assertEqual(0x3A, self.connection.sent[0][9])
        self.assertNotIn(_RELEASE_FRAME, self.connection.sent)

    async def test_failed_original_and_nonlight_actions_never_supplement(self):
        self.start()
        self.connection.on_send = None
        self.assertFalse(await self.send())
        self.assertEqual(3, len(self.connection.sent))
        self.assertNotIn(_RELEASE_FRAME, self.connection.sent)
        for key, action in ((DeviceKey(DeviceType.OUTLET, 1, 0, SubType.NONE), "turn_on"),
                            (_CUTOFF_KEY, "turn_off"), (self.key, "status_query")):
            item = _CmdItem(key, action, {}, connection_generation=self.gateway._connection_generation)
            self.gateway._tx_queue.put_nowait(item)
            await item.future
        self.assertNotIn(_RELEASE_FRAME, self.connection.sent)


def _sensor_report(pm10: int, pm25: int, *, room: int = 1) -> bytes:
    """Build an air-quality report with all other metrics zero."""
    body = bytes((0x30, 0xBC, 0, 1, 0, 0x98, room, 0, pm10, pm25, 0, 0, 0, 0, 0, 0))
    return b"\xaa\x55" + body + bytes((sum(body) & 255, 13, 13))


class OriginalCommandReliabilityTests(unittest.IsolatedAsyncioTestCase):
    gateway: KocomGateway
    connection: _FakeConnection
    asyncSetUp = GatewaySafetyTests.asyncSetUp
    asyncTearDown = GatewaySafetyTests.asyncTearDown

    def start(self):
        self.key = DeviceKey(DeviceType.LIGHT, 1, 0, SubType.NONE)
        self.gateway._connection_generation = 1
        self.gateway._connection_available = True
        self.connection.on_send = lambda packet: self.gateway.on_device_state(
            DeviceState(self.key, Platform.LIGHT, {}, packet[10] == 255)
        )
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())

    def pause(self, seconds: float):
        entered, release = asyncio.Event(), asyncio.Event()
        real_sleep = asyncio.sleep

        async def sleep(delay):
            if delay == seconds:
                entered.set()
                await release.wait()
            else:
                await real_sleep(0)

        return entered, release, patch.object(gateway_module.asyncio, "sleep", side_effect=sleep)

    async def test_cancel_during_original_idle_sends_nothing_and_queue_progresses(self):
        self.start()
        self.connection.idle = False
        entered, release, paused = self.pause(0.01)
        with paused:
            caller = asyncio.create_task(self.gateway.async_send_action(self.key, "turn_on"))
            await asyncio.wait_for(entered.wait(), 0.5)
            caller.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await caller
            self.connection.idle = True
            release.set()
            await asyncio.wait_for(self.gateway._tx_queue.join(), 0.5)
        self.assertEqual([], self.connection.sent)
        self.assertTrue(await self.gateway.async_send_action(self.key, "turn_on"))
        self.assertEqual(1, len(self.connection.sent))

    async def test_original_lifetime_rechecked_after_idle_and_before_queue_admission(self):
        self.start()
        for change in ("stopping", "disconnect", "reconnect"):
            with self.subTest(change=change):
                self.connection.sent.clear()
                self.connection.idle = False
                entered, release, paused = self.pause(0.01)
                with paused:
                    first = asyncio.create_task(self.gateway.async_send_action(self.key, "turn_on"))
                    await asyncio.wait_for(entered.wait(), 0.5)
                    queued = asyncio.Event()
                    original_put = self.gateway._tx_queue.put

                    async def put(item, original_put=original_put, queued=queued):
                        await original_put(item)
                        queued.set()

                    with patch.object(self.gateway._tx_queue, "put", side_effect=put):
                        second = asyncio.create_task(self.gateway.async_send_action(self.key, "turn_off"))
                        await asyncio.wait_for(queued.wait(), 0.5)
                    if change == "stopping":
                        self.gateway._stopping = True
                    else:
                        self.connection.connected = False
                        self.gateway._sync_connection_availability()
                        if change == "reconnect":
                            self.connection.connected = True
                            self.gateway._sync_connection_availability()
                    self.connection.idle = True
                    release.set()
                    results = await asyncio.wait_for(asyncio.gather(first, second), 0.5)
                self.assertEqual([False, False], results)
                self.assertEqual([], self.connection.sent)
                self.gateway._stopping = False
                self.connection.connected = True
                self.gateway._sync_connection_availability()
                self.assertTrue(await self.gateway.async_send_action(self.key, "turn_on"))

    async def test_cancel_during_retry_does_not_retransmit(self):
        self.start()
        self.connection.on_send = None
        entered, release, paused = self.pause(gateway_module.SEND_RETRY_GAP)
        with paused, patch("custom_components.kocom_wallpad.controller.CMD_CONFIRM_TIMEOUT", 0.001):
            caller = asyncio.create_task(self.gateway.async_send_action(self.key, "turn_on"))
            await asyncio.wait_for(entered.wait(), 0.5)
            caller.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await caller
            release.set()
            await asyncio.wait_for(self.gateway._tx_queue.join(), 0.5)
        self.assertEqual(1, len(self.connection.sent))
        self.assertEqual([], self.gateway._pendings)

    async def test_sender_cancellation_during_send_removes_attempt_waiter(self):
        self.start()
        entered = asyncio.Event()

        async def send(packet):
            entered.set()
            await asyncio.Event().wait()

        with patch.object(self.connection, "send", side_effect=send):
            caller = asyncio.create_task(self.gateway.async_send_action(self.key, "turn_on"))
            await asyncio.wait_for(entered.wait(), 0.5)
            assert self.gateway._task_sender is not None
            self.gateway._task_sender.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await self.gateway._task_sender
            self.assertFalse(await caller)
        self.assertEqual([], self.gateway._pendings)
        self.assertEqual([], self.connection.sent)

    async def test_new_epoch_report_cannot_ack_old_unresolved_command(self):
        self.start()

        def reconnect(packet):
            self.connection.connected = False
            self.gateway._sync_connection_availability()
            self.connection.connected = True
            self.gateway._sync_connection_availability()
            self.gateway.on_device_state(DeviceState(self.key, Platform.LIGHT, {}, True))

        self.connection.on_send = reconnect
        self.assertFalse(await self.gateway.async_send_action(self.key, "turn_on"))
        self.assertEqual(1, len(self.connection.sent))
        self.assertEqual([], self.gateway._pendings)

    async def test_recorded_genuine_ack_survives_epoch_change(self):
        self.start()
        acknowledge = self.connection.on_send
        assert acknowledge is not None

        def reconnect(packet):
            acknowledge(packet)
            self.connection.connected = False
            self.gateway._sync_connection_availability()
            self.connection.connected = True
            self.gateway._sync_connection_availability()

        self.connection.on_send = reconnect
        self.assertTrue(await self.gateway.async_send_action(self.key, "turn_on"))
        self.assertEqual(1, len(self.connection.sent))

    async def test_recorded_ack_survives_sender_cancellation_but_not_caller_cancellation(self):
        self.start()
        for cancel_sender in (True, False):
            with self.subTest(cancel_sender=cancel_sender):
                await self.record_ack_then_cancel(cancel_sender)

    async def record_ack_then_cancel(self, cancel_sender):
        if self.gateway._task_sender is None or self.gateway._task_sender.done():
            self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())
        recorded = asyncio.Event()

        def acknowledge_then_cancel():
            self.gateway.on_device_state(DeviceState(self.key, Platform.LIGHT, {}, True))
            if cancel_sender:
                assert self.gateway._task_sender is not None
                self.gateway._task_sender.cancel()
            else:
                caller.cancel()
            recorded.set()

        def sent(packet):
            asyncio.get_running_loop().call_soon(acknowledge_then_cancel)

        self.connection.on_send = sent
        caller = asyncio.create_task(self.gateway.async_send_action(self.key, "turn_on"))
        await asyncio.wait_for(recorded.wait(), 0.5)
        if cancel_sender:
            self.assertTrue(await asyncio.wait_for(caller, 0.5))
        else:
            with self.assertRaises(asyncio.CancelledError):
                await caller
        await asyncio.wait_for(self.gateway._tx_queue.join(), 0.5)
        self.assertEqual([], self.gateway._pendings)

    async def test_switch_payload_uses_latest_neighbor_after_idle_and_retry(self):
        self.start()
        self.gateway.controller.feed(_light_report(1, bytes(8)))
        entered, release, paused = self.pause(0.01)
        self.connection.idle = False
        with paused:
            caller = asyncio.create_task(self.gateway.async_send_action(self.key, "turn_on"))
            await asyncio.wait_for(entered.wait(), 0.5)
            self.gateway.controller.feed(_light_report(1, bytes((0, 255, 0, 0, 0, 0, 0, 0))))
            self.connection.idle = True
            release.set()
            self.assertTrue(await asyncio.wait_for(caller, 0.5))
        self.assertEqual(bytes((255, 255, 0, 0, 0, 0, 0, 0)), self.connection.sent[0][10:18])

        self.connection.sent.clear()
        self.connection.on_send = None
        entered, release, paused = self.pause(gateway_module.SEND_RETRY_GAP)
        with paused, patch("custom_components.kocom_wallpad.controller.CMD_CONFIRM_TIMEOUT", 0.001):
            caller = asyncio.create_task(self.gateway.async_send_action(self.key, "turn_on"))
            await asyncio.wait_for(entered.wait(), 0.5)
            self.gateway.controller.feed(_light_report(1, bytes((0, 0, 255, 0, 0, 0, 0, 0))))
            self.connection.on_send = lambda packet: self.gateway.on_device_state(
                DeviceState(self.key, Platform.LIGHT, {}, True)
            )
            release.set()
            self.assertTrue(await asyncio.wait_for(caller, 0.5))
        self.assertEqual(bytes((255, 0, 255, 0, 0, 0, 0, 0)), self.connection.sent[1][10:18])

    async def test_thermostat_payload_regenerates_after_idle_and_rejects_lost_state(self):
        self.start()
        self.key = DeviceKey(DeviceType.THERMOSTAT, 1, 0, SubType.NONE)
        for lose_state in (False, True):
            with self.subTest(lose_state=lose_state):
                self.gateway.controller.feed(_room_report(_HEAT, _NONE, 22))
                self.connection.sent.clear()
                self.connection.on_send = lambda packet: self.gateway.controller.feed(
                    _room_report(_HEAT, _NONE, packet[12])
                )
                self.connection.idle = False
                entered, release, paused = self.pause(0.01)
                with paused:
                    caller = asyncio.create_task(self.gateway.async_send_action(
                        self.key, "set_hvac", hvac_mode="heat"
                    ))
                    await asyncio.wait_for(entered.wait(), 0.5)
                    if lose_state:
                        self.gateway.registry._states.pop(self.key.key)
                    else:
                        self.gateway.controller.feed(_room_report(_HEAT, _NONE, 25))
                    self.connection.idle = True
                    release.set()
                    self.assertEqual(not lose_state, await asyncio.wait_for(caller, 0.5))
                if lose_state:
                    self.assertEqual([], self.connection.sent)
                else:
                    self.assertEqual(25, self.connection.sent[0][12])

    async def test_command_echo_does_not_publish_confirm_or_ack_light_and_outlet(self):
        self.start()
        for device_type, wire, platform in ((DeviceType.LIGHT, 0x0E, Platform.LIGHT),
                                            (DeviceType.OUTLET, 0x3B, Platform.SWITCH)):
            with self.subTest(device_type=device_type):
                self.key = DeviceKey(device_type, 2, 0, SubType.NONE)
                self.gateway.registry.upsert(DeviceState(self.key, platform, {}, False))
                echo_seen = asyncio.Event()

                def echo(packet, echo_seen=echo_seen):
                    self.gateway.controller.feed(packet)
                    echo_seen.set()

                self.connection.on_send = echo
                caller = asyncio.create_task(self.gateway.async_send_action(self.key, "turn_on"))
                await asyncio.wait_for(echo_seen.wait(), 0.5)
                state = self.gateway.registry.get(self.key)
                assert state is not None
                self.assertIs(state.state, False)
                self.assertFalse(self.gateway.is_device_state_confirmed(self.key))
                self.assertFalse(caller.done())
                self.assertTrue(self.gateway._pendings)
                # Previously supported type variants remain device reports.
                for packet_type in (0xBC, 0xDC):
                    body = bytes((0x30, packet_type, 0, 1, 0, wire, 2, 0, 255, 0, 0, 0, 0, 0, 0, 0))
                    report = b"\xaa\x55" + body + bytes((sum(body) & 255, 13, 13))
                    self.gateway.controller.feed(report)
                self.assertTrue(await asyncio.wait_for(caller, 0.5))
                self.assertTrue(self.gateway.is_device_state_confirmed(self.key))

    async def test_legacy_switch_mirror_is_restore_only(self):
        self.start()
        for kind, platform in ((DeviceType.LIGHT, Platform.LIGHT), (DeviceType.OUTLET, Platform.SWITCH)):
            key = DeviceKey(kind, 3, 0, SubType.NONE)
            packet, _, _ = self.gateway.controller.generate_command(key, "turn_on")
            self.gateway.controller.feed(packet)
            self.assertIsNone(self.gateway.registry.get(key))
            self.gateway._restore_mode = True
            self.gateway.controller.feed(packet)
            self.gateway._restore_mode = False
            state = self.gateway.registry.get(key)
            assert state is not None
            self.assertEqual(platform, state.platform)
            self.assertIs(state.state, True)
            self.assertFalse(self.gateway.is_device_state_confirmed(key))


class KnownParticulateReliabilityTests(unittest.IsolatedAsyncioTestCase):
    gateway: KocomGateway
    connection: _FakeConnection
    asyncSetUp = GatewaySafetyTests.asyncSetUp
    asyncTearDown = GatewaySafetyTests.asyncTearDown

    def pm_key(self, subtype=SubType.PM10, room=1):
        return DeviceKey(DeviceType.AIRQUALITY, room, 0, subtype)

    async def restore_saved(self, saved, unique_id):
        entry = types.SimpleNamespace(entity_id="sensor.saved_pm", unique_id=unique_id)
        store = types.SimpleNamespace(async_get_stored_state=lambda entity_id: types.SimpleNamespace(
            extra_data=types.SimpleNamespace(as_dict=lambda: saved)
        ))
        with patch.object(gateway_module, "AsyncConnection", _FakeConnection):
            fresh = KocomGateway(self.gateway.hass, self.gateway.entry, "test", 1)
        self.assertEqual({}, fresh.registry._states)
        with (
            patch.object(gateway_module.er, "async_get", return_value=types.SimpleNamespace(async_get=lambda entity_id: entry)),
            patch.object(gateway_module.er, "async_entries_for_config_entry", return_value=[entry]),
            patch.object(gateway_module.restore_state, "async_get", return_value=store),
        ):
            await fresh.async_get_entity_registry()
        return fresh

    async def test_known_pm_zero_updates_without_discovering_other_zero_metrics(self):
        self.gateway.controller.feed(_sensor_report(8, 7))
        self.gateway.controller.feed(_sensor_report(0, 0))
        for subtype in (SubType.PM10, SubType.PM25):
            state = self.gateway.registry.get(self.pm_key(subtype))
            assert state is not None
            self.assertEqual(0, state.state)
            self.assertEqual(_sensor_report(0, 0), state._packet)
        self.gateway.controller.feed(_sensor_report(0, 0, room=2))
        self.assertEqual(2, len(self.gateway.registry._states))
        self.gateway.controller.feed(_sensor_report(9, 0, room=3))
        self.gateway.controller.feed(_sensor_report(0, 0, room=3))
        self.assertIsNone(self.gateway.registry.get(self.pm_key(SubType.PM25, 3)))
        self.assertEqual(3, len(self.gateway.registry._states))
        self.gateway.controller.feed(bytes.fromhex(_REPORTS["airquality_status"]))
        self.gateway.controller.feed(_sensor_report(0, 0))
        for subtype, value in ((SubType.CO2, 600), (SubType.VOC, 300),
                               (SubType.TEMP, 25), (SubType.HUMIDITY, 50)):
            state = self.gateway.registry.get(self.pm_key(subtype))
            assert state is not None
            self.assertEqual(value, state.state)

    async def test_saved_zero_from_real_entity_restores_empty_gateway_exact_identity(self):
        self.gateway.entry.entry_id = "entry"
        self.gateway.controller.feed(_sensor_report(8, 7))
        self.gateway.controller.feed(_sensor_report(0, 0))
        for subtype in (SubType.PM10, SubType.PM25):
            key = self.pm_key(subtype)
            registered = self.gateway.registry.get(key)
            assert registered is not None
            entity = KocomBaseEntity(self.gateway, registered)
            saved = entity.extra_restore_state_data
            self.assertIsInstance(saved, dict)
            fresh = await self.restore_saved(saved, entity.unique_id)
            restored = fresh.registry.get(key)
            assert restored is not None
            self.assertEqual(0, restored.state)
            self.assertEqual([key.key], list(fresh.registry._states))
            self.assertEqual(entity.unique_id, KocomBaseEntity(fresh, restored).unique_id)
            self.assertFalse(fresh.is_device_state_confirmed(key))
            fresh.controller.feed(_sensor_report(0, 0))
            self.assertTrue(fresh.is_device_state_confirmed(key))
            self.assertFalse(fresh._restore_mode)
            self.assertIsNone(fresh._force_register_uid)
            for uid in (None, "wrong:test", f"{self.pm_key(subtype, 2).unique_id}:test",
                        f"{self.pm_key(SubType.CO2).unique_id}:test"):
                rejected = await self.restore_saved(saved, uid)
                self.assertEqual({}, rejected.registry._states)

    async def test_pm_zero_rejects_absent_wrong_stale_and_shadow_identity(self):
        key = self.pm_key()
        for restore, marker in ((True, None), (True, "wrong"),
                                (True, self.pm_key(room=2).unique_id),
                                (True, self.pm_key(SubType.CO2).unique_id),
                                (False, key.unique_id)):
            self.gateway._restore_mode = restore
            self.gateway._force_register_uid = marker
            self.gateway.controller.feed(_sensor_report(0, 0))
            self.assertEqual({}, self.gateway.registry._states)
        self.gateway._restore_mode = False
        self.gateway._force_register_uid = None
        self.gateway.registry._shadow[key.key] = DeviceState(key, Platform.SENSOR, {}, 8)
        self.gateway.controller.feed(_sensor_report(0, 0))
        self.assertEqual({}, self.gateway.registry._states)
        self.assertFalse(self.gateway.is_device_state_confirmed(key))


class BoundedTransportReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def connection(self):
        connection = AsyncConnection("test", 1)
        writer = create_autospec(asyncio.StreamWriter, instance=True)
        connection._reader = create_autospec(asyncio.StreamReader, instance=True)
        connection._writer = writer
        connection._connected = True
        return connection, writer

    async def test_send_drain_timeout_cleans_captured_writer(self):
        connection, writer = self.connection()
        entered = asyncio.Event()

        async def drain():
            entered.set()
            await asyncio.Event().wait()

        writer.drain.side_effect = drain
        with patch.object(transport_module, "TRANSPORT_SEND_TIMEOUT", 0.01, create=True):
            sending = asyncio.create_task(connection.send(b"test"))
            await asyncio.wait_for(entered.wait(), 0.5)
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(sending, 0.5)
        writer.close.assert_called_once()
        self.assertIsNone(connection._writer)
        self.assertIsNone(connection._reader)
        self.assertFalse(connection._is_connected())

    async def test_close_detaches_before_bounded_wait_and_preserves_replacement(self):
        connection, writer = self.connection()
        entered = asyncio.Event()

        async def wait_closed():
            entered.set()
            await asyncio.Event().wait()

        writer.wait_closed.side_effect = wait_closed
        with patch.object(transport_module, "TRANSPORT_CLOSE_TIMEOUT", 0.01, create=True):
            closing = asyncio.create_task(connection.close())
            await asyncio.wait_for(entered.wait(), 0.5)
            self.assertIsNone(connection._writer)
            self.assertIsNone(connection._reader)
            self.assertFalse(connection._is_connected())
            replacement = create_autospec(asyncio.StreamWriter, instance=True)
            connection._writer = replacement
            connection._connected = True
            await asyncio.wait_for(closing, 0.5)
        self.assertIs(replacement, connection._writer)
        self.assertTrue(connection._is_connected())
        replacement.close.assert_not_called()

    async def test_close_errors_are_suppressed_but_cancellation_propagates(self):
        for error in (OSError("close"), RuntimeError("close"), TimeoutError("close")):
            connection, writer = self.connection()
            writer.close.side_effect = error
            await connection.close()
            self.assertIsNone(connection._writer)
            self.assertFalse(connection._is_connected())
        connection, writer = self.connection()
        entered = asyncio.Event()

        async def wait_closed():
            entered.set()
            await asyncio.Event().wait()

        writer.wait_closed.side_effect = wait_closed
        closing = asyncio.create_task(connection.close())
        await asyncio.wait_for(entered.wait(), 0.5)
        closing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        self.assertIsNone(connection._writer)
        self.assertFalse(connection._is_connected())

    async def test_send_failure_preserves_original_error_and_replacement(self):
        connection, writer = self.connection()
        replacement = create_autospec(asyncio.StreamWriter, instance=True)
        original = OSError("original send")

        async def drain():
            connection._writer = replacement
            connection._connected = True
            raise original

        writer.drain.side_effect = drain
        writer.close.side_effect = RuntimeError("cleanup")
        with self.assertRaises(OSError) as caught:
            await connection.send(b"test")
        self.assertIs(original, caught.exception)
        writer.close.assert_called_once()
        replacement.close.assert_not_called()
        self.assertIs(replacement, connection._writer)
        self.assertTrue(connection._is_connected())

    async def test_send_cancellation_detaches_without_waiting_or_background_cleanup(self):
        for replace_writer in (False, True):
            connection, writer = self.connection()
            entered = asyncio.Event()

            async def drain(entered=entered):
                entered.set()
                await asyncio.Event().wait()

            writer.drain.side_effect = drain
            sending = asyncio.create_task(connection.send(b"test"))
            await asyncio.wait_for(entered.wait(), 0.5)
            replacement = create_autospec(asyncio.StreamWriter, instance=True)
            if replace_writer:
                connection._writer = replacement
            sending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await sending
            writer.wait_closed.assert_not_awaited()
            replacement.close.assert_not_called()
            if replace_writer:
                self.assertIs(replacement, connection._writer)
                writer.close.assert_not_called()
            else:
                writer.close.assert_called_once()
                self.assertIsNone(connection._writer)
                self.assertIsNone(connection._reader)
                self.assertFalse(connection._is_connected())


if __name__ == "__main__":
    unittest.main()
