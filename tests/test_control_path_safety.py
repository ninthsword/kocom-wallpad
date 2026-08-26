"""Deterministic regression tests for the Kocom control path.

The integration has no Home Assistant test dependency in this repository, so
these tests use only the narrow module shims needed by the protocol gateway.
"""

from __future__ import annotations

import asyncio
from enum import Enum
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


def _module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


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

    const.Platform = Platform
    const.UnitOfTemperature = types.SimpleNamespace(CELSIUS="°C")
    const.ATTR_TEMPERATURE = "temperature"
    const.CONF_HOST = "host"
    const.CONF_PORT = "port"
    const.EVENT_HOMEASSISTANT_STOP = "stop"
    homeassistant.const = const

    core = _module("homeassistant.core")
    core.HomeAssistant = object
    core.Event = object
    core.callback = lambda function: function
    config_entries = _module("homeassistant.config_entries")
    config_entries.ConfigEntry = object

    components = _module("homeassistant.components")
    climate = _module("homeassistant.components.climate")
    climate_const = _module("homeassistant.components.climate.const")
    climate_const.PRESET_NONE = "none"
    climate_const.PRESET_AWAY = "away"
    climate_const.FAN_LOW = "low"
    climate_const.FAN_MEDIUM = "medium"
    climate_const.FAN_HIGH = "high"
    climate_const.FAN_AUTO = "auto"
    climate_const.HVACMode = types.SimpleNamespace(
        HEAT="heat", OFF="off", COOL="cool", FAN_ONLY="fan_only", DRY="dry", AUTO="auto"
    )
    climate_const.HVACAction = types.SimpleNamespace(OFF="off", HEATING="heating", IDLE="idle")
    climate_const.ClimateEntityFeature = types.SimpleNamespace(
        TARGET_TEMPERATURE=1, TURN_OFF=2, TURN_ON=4, FAN_MODE=8, PRESET_MODE=16
    )
    climate.HVACMode = climate_const.HVACMode
    climate.ClimateEntity = object

    class _EntityDescription:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    climate.ClimateEntityDescription = _EntityDescription
    components.climate = climate
    light = _module("homeassistant.components.light")
    light.LightEntityDescription = _EntityDescription
    components.light = light
    for package, attr, value in (
        ("sensor", "SensorDeviceClass", types.SimpleNamespace(TEMPERATURE="temperature")),
        ("binary_sensor", "BinarySensorDeviceClass", types.SimpleNamespace(PROBLEM="problem")),
        ("switch", "SwitchDeviceClass", types.SimpleNamespace(OUTLET="outlet")),
    ):
        module = _module(f"homeassistant.components.{package}")
        setattr(module, attr, value)
        setattr(module, f"{package.title().replace('_', '')}EntityDescription", _EntityDescription)
        setattr(components, package, module)

    fan = _module("homeassistant.components.fan")
    fan.FanEntityDescription = _EntityDescription
    components.fan = fan

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

    entity.DeviceInfo = _DeviceInfo
    entity_registry = _module("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda hass: None
    entity_registry.async_entries_for_config_entry = lambda registry, entry_id: []
    restore_state = _module("homeassistant.helpers.restore_state")
    restore_state.async_get = lambda hass: types.SimpleNamespace(last_states={})
    restore_state.RestoreEntity = _RestoreEntity
    restore_state.RestoredExtraData = lambda value: value
    dispatcher = _module("homeassistant.helpers.dispatcher")
    dispatcher.async_dispatcher_send = lambda *args, **kwargs: None
    dispatcher.async_dispatcher_connect = lambda *_args, **_kwargs: lambda: None
    entity_platform = _module("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    exceptions = _module("homeassistant.exceptions")
    exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    helpers.entity = entity
    helpers.entity_registry = entity_registry
    helpers.restore_state = restore_state
    helpers.dispatcher = dispatcher
    _module("serial_asyncio").open_serial_connection = None


_install_homeassistant_shims()

from custom_components.kocom_wallpad.const import DeviceType, SubType
from custom_components.kocom_wallpad.controller import KocomController, PacketFrame
from custom_components.kocom_wallpad.gateway import KocomGateway, _CmdItem
from custom_components.kocom_wallpad.models import DeviceKey, DeviceState
from custom_components.kocom_wallpad import gateway as gateway_module
from custom_components.kocom_wallpad.transport import AsyncConnection
from custom_components.kocom_wallpad.climate import KocomClimate
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import Platform


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


class _FakeConnection:
    def __init__(self, *_args, **_kwargs):
        self.connected = True
        self.gateway = None
        self.on_send = None
        self.sent = []
        self.idle = True

    async def open(self):
        return self.connected

    async def close(self):
        self.connected = False

    def _is_connected(self):
        return self.connected

    def idle_since(self):
        return 99.0 if self.idle else 0.0

    async def send(self, packet):
        self.sent.append(packet)
        if self.on_send:
            self.on_send(packet)
        return len(packet)

    async def recv(self, *_args):
        return b""


class ControllerSafetyTests(unittest.TestCase):
    def test_thermostat_report_uses_current_packet_without_lag(self):
        controller = KocomController(_ControllerGateway())
        first = controller._handle_thermostat(_thermostat_frame(20, 19))[0]
        second = controller._handle_thermostat(_thermostat_frame(21, 20))[0]
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
        living = next(
            state for state in living_states if state.key.sub_type == SubType.NONE
        )
        self.assertEqual(0, living.key.room_index)


class GatewaySafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_connection = gateway_module.AsyncConnection
        gateway_module.AsyncConnection = _FakeConnection
        self.gateway = KocomGateway(types.SimpleNamespace(), object(), "test", 1)

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
        self.gateway.entry = types.SimpleNamespace(entry_id="entry")
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
        self.gateway.conn.on_send = lambda _packet: self.gateway.on_device_state(self._state(True))
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

        self.gateway.conn.connected = False
        self.gateway._sync_connection_availability()
        self.assertFalse(self.gateway.is_device_available(state.key))

        self.gateway.conn.connected = True
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
            self.gateway.conn.sent,
        )

        self.gateway._sync_connection_availability()
        await asyncio.sleep(0)
        self.assertEqual(3, len(self.gateway.conn.sent))

        self.gateway.conn.connected = False
        self.gateway._sync_connection_availability()
        self.gateway.conn.connected = True
        self.gateway._sync_connection_availability()
        await self._wait_for_bootstrap()
        self.assertEqual(6, len(self.gateway.conn.sent))

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
        self.assertEqual([0, 1, 3], [packet[6] for packet in self.gateway.conn.sent])

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
        self.assertEqual([room], [packet[6] for packet in self.gateway.conn.sent])

    async def test_bootstrap_query_does_not_retry_after_no_response(self):
        state = self._thermostat_state(1)
        self.gateway._restore_mode = True
        self.gateway.on_device_state(state)
        self.gateway._restore_mode = False
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())

        self.gateway._sync_connection_availability()
        await self._wait_for_bootstrap()

        self.assertEqual(1, len(self.gateway.conn.sent))
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
        self.assertEqual(1, len(self.gateway.conn.sent))

        received_eof = False

        async def recv_eof_once(*_args):
            nonlocal received_eof
            if not received_eof:
                received_eof = True
                self.gateway.conn.connected = False
                return b""
            await asyncio.Future()

        async def reopen():
            self.gateway.conn.connected = True
            return True

        self.gateway.conn.recv = recv_eof_once
        self.gateway.conn.open = reopen
        self.gateway._task_reader = asyncio.create_task(self.gateway._read_loop())

        for _ in range(100):
            if (
                self.gateway._connection_generation == initial_generation + 2
                and len(self.gateway.conn.sent) == 2
            ):
                break
            await asyncio.sleep(0.001)

        self.assertEqual(initial_generation + 2, self.gateway._connection_generation)
        self.assertEqual(2, len(self.gateway.conn.sent))

    async def test_stop_cleans_active_status_query_queue_accounting(self):
        state = self._thermostat_state(1)
        self.gateway._connection_generation = 1
        self.gateway.conn.idle = False
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
        self.assertEqual(0, self.gateway._tx_queue._unfinished_tasks)

    async def test_immediate_reconnect_drops_old_active_query_and_runs_new_query(self):
        state = self._thermostat_state(1)
        self.gateway._restore_mode = True
        self.gateway.on_device_state(state)
        self.gateway._restore_mode = False
        self.gateway.conn.idle = False
        self.gateway._task_sender = asyncio.create_task(self.gateway._sender_loop())

        self.gateway._sync_connection_availability()
        for _ in range(20):
            if self.gateway._active_item is not None:
                break
            await asyncio.sleep(0)
        self.assertIsNotNone(self.gateway._active_item)
        old_generation = self.gateway._active_item.connection_generation

        self.gateway.conn.connected = False
        self.gateway._sync_connection_availability()
        self.assertIsNone(self.gateway._task_bootstrap_queries)
        self.gateway.conn.connected = True
        self.gateway._sync_connection_availability()
        replacement = self.gateway._task_bootstrap_queries
        self.assertIsNotNone(replacement)
        self.assertNotEqual(old_generation, self.gateway._connection_generation)

        self.gateway.conn.idle = True
        await asyncio.wait_for(asyncio.shield(replacement), timeout=0.2)
        self.assertIsNone(self.gateway._task_bootstrap_queries)
        self.assertEqual(1, len(self.gateway.conn.sent))
        self.assertEqual(0x3A, self.gateway.conn.sent[0][9])

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
        self.assertEqual([], self.gateway.conn.sent)

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

        self.gateway.conn.connected = False
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
        self.assertEqual(1, len(self.gateway.conn.sent))
        self.assertEqual(room, self.gateway.conn.sent[0][6])

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
        reader = types.SimpleNamespace(read=AsyncMock(return_value=b""))
        wait_closed = AsyncMock()
        connection._reader = reader
        connection._writer = types.SimpleNamespace(
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
        tcp._reader = types.SimpleNamespace(
            read=AsyncMock(side_effect=asyncio.TimeoutError)
        )
        tcp._connected = True
        self.assertEqual(b"", await tcp.recv(512, timeout=0.1))
        self.assertTrue(tcp._is_connected())

        serial = AsyncConnection("/dev/fake", None)
        serial._reader = types.SimpleNamespace(read=AsyncMock(return_value=b""))
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
        gateway = types.SimpleNamespace(
            host="test",
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
        gateway = types.SimpleNamespace(
            host="test",
            is_transport_available=lambda: False,
            is_device_state_confirmed=lambda _key: False,
            async_send_action=AsyncMock(return_value=True),
        )
        climate = KocomClimate(gateway, self._state())

        self.assertFalse(climate.available)
        with self.assertRaises(HomeAssistantError):
            await climate.async_set_temperature(temperature=22)
        gateway.async_send_action.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
