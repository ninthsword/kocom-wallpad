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
from unittest.mock import patch


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
    climate.HVACMode = climate_const.HVACMode
    components.climate = climate
    for package, attr, value in (
        ("sensor", "SensorDeviceClass", types.SimpleNamespace(TEMPERATURE="temperature")),
        ("binary_sensor", "BinarySensorDeviceClass", types.SimpleNamespace(PROBLEM="problem")),
        ("switch", "SwitchDeviceClass", types.SimpleNamespace(OUTLET="outlet")),
    ):
        module = _module(f"homeassistant.components.{package}")
        setattr(module, attr, value)
        setattr(components, package, module)

    helpers = _module("homeassistant.helpers")
    entity_registry = _module("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda hass: None
    restore_state = _module("homeassistant.helpers.restore_state")
    restore_state.async_get = lambda hass: types.SimpleNamespace(last_states={})
    dispatcher = _module("homeassistant.helpers.dispatcher")
    dispatcher.async_dispatcher_send = lambda *args, **kwargs: None
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
from homeassistant.const import Platform


class _Registry:
    def get(self, key):
        return None


class _ControllerGateway:
    registry = _Registry()

    def on_device_state(self, _state):
        pass


def _thermostat_frame(target: int, current: int) -> PacketFrame:
    raw = bytearray(21)
    raw[5:7] = bytes((0x01, 0x00))
    raw[7:9] = bytes((0x36, 0x01))
    raw[9] = 0x00
    raw[10:18] = bytes((0x10, 0x00, target, 0, current, 0, 0, 0))
    return PacketFrame(bytes(raw))


class _FakeConnection:
    def __init__(self, *_args, **_kwargs):
        self.connected = True
        self.gateway = None
        self.on_send = None

    async def open(self):
        return self.connected

    async def close(self):
        self.connected = False

    def _is_connected(self):
        return self.connected

    def idle_since(self):
        return 99.0

    async def send(self, packet):
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

    async def test_restored_state_is_unavailable_until_live_packet(self):
        state = self._state(True)
        self.gateway._restore_mode = True
        self.gateway.on_device_state(state)
        self.gateway._restore_mode = False
        self.assertFalse(self.gateway.is_device_available(state.key))
        self.gateway.on_device_state(state)
        self.assertTrue(self.gateway.is_device_available(state.key))

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

        self.gateway.on_device_state(state)
        self.assertTrue(self.gateway.is_device_available(state.key))

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


if __name__ == "__main__":
    unittest.main()
