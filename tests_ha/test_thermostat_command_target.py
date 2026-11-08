"""Thermostat preset and mode commands, and the restore after a wallpad exit."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import ExitStack, asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import restore_state
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.kocom_wallpad.const import DOMAIN, DeviceType, SubType
from custom_components.kocom_wallpad.gateway import KocomGateway
from custom_components.kocom_wallpad.models import DeviceKey
from custom_components.kocom_wallpad.transport import AsyncConnection

HOST = "wallpad.invalid"
ROOM_1 = DeviceKey(DeviceType.THERMOSTAT, 1, 0, SubType.NONE)
HEAT, OFF = 0x11, 0x00
NONE, AWAY = 0x00, 0x01
AWAY_TARGET = 10
HOME_TARGET = 22


def _status(mode: int, preset: int, target: int) -> bytes:
    """Return a synthetic live status report of room 1 to the wallpad."""
    body = bytes((0x30, 0xBC, 0x00, 0x01, 0x00, 0x36, 0x01, 0x00,
                  mode, preset, target, 0x00, 20, 0x00, 0x00, 0x00))
    return b"\xaa\x55" + body + bytes((sum(body) % 256, 0x0D, 0x0D))


def _command(data0: int, data1: int, data2: int) -> bytes:
    """Return a command frame to room 1 from the wallpad address."""
    body = bytes((0x30, 0xBC, 0x00, 0x36, 0x01, 0x01, 0x00, 0x00,
                  data0, data1, data2, 0x00, 0x00, 0x00, 0x00, 0x00))
    return b"\xaa\x55" + body + bytes((sum(body) % 256, 0x0D, 0x0D))


assert _status(HEAT, NONE, 22) == bytes.fromhex("aa5530bc00010036010011001600140000005f0d0d")
assert _command(HEAT, NONE, 22) == bytes.fromhex("aa5530bc00360101000011001600000000004b0d0d")
AT_HOME = _status(HEAT, NONE, HOME_TARGET)
AWAY_REPORT = _status(HEAT, AWAY, AWAY_TARGET)
EXIT_REPORT = _status(HEAT, NONE, AWAY_TARGET)
# A wallpad-originated exit: the wallpad's own command, then the room's report.
WALLPAD_EXIT = _command(HEAT, NONE, 0x00) + EXIT_REPORT
RESTORE_22 = _command(HEAT, NONE, HOME_TARGET)


class Wallpad:
    """A loaded entry whose synthetic room 1 applies each command it receives."""

    def __init__(self, hass: HomeAssistant, gateway: KocomGateway, entity_id: str) -> None:
        self.hass = hass
        self.gateway = gateway
        self.entity_id = entity_id
        self.connected: Any = None
        self.idle: Any = None
        self.sent: list[bytes] = []

    def feed(self, data: bytes) -> None:
        self.gateway.controller.feed(data)

    @property
    def commands(self) -> list[bytes]:
        """Command frames (command byte 0x00) written to room 1."""
        return [frame for frame in self.sent if frame[5:7] == b"\x36\x01" and frame[9] == 0x00]

    def hold_sender(self) -> None:
        """Keep the sender in its idle wait (the bus looks busy)."""
        self.idle.return_value = 0.0

    def release_sender(self) -> None:
        self.idle.return_value = 99.0

    async def settle(self) -> None:
        """Wait until the background sender has handled every queued item."""
        for _ in range(300):
            await self.hass.async_block_till_done()
            if self.gateway._tx_queue.empty() and self.gateway._active_item is None:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("sender did not settle")

    async def reconnect(self) -> None:
        self.connected.return_value = False
        self.gateway._sync_connection_availability()
        await self.hass.async_block_till_done()
        self.connected.return_value = True
        self.gateway._sync_connection_availability()
        await self.hass.async_block_till_done()

    async def call(self, service: str, data: dict[str, Any]) -> None:
        await self.hass.services.async_call(
            "climate", service, {"entity_id": self.entity_id, **data}, blocking=True
        )
        await self.settle()


@asynccontextmanager
async def loaded(
    hass: HomeAssistant,
    initial: bytes | None,
    restored: Sequence[tuple[str, SubType, dict[str, Any]]] | None = None,
) -> AsyncIterator[Wallpad]:
    """Set up the entry; ``initial`` is the first live report, if any."""
    entry = MockConfigEntry(domain=DOMAIN, data={"host": HOST, "port": 8899})
    entry.add_to_hass(hass)
    if restored is not None:
        registry = er.async_get(hass)
        cache = []
        for platform, sub_type, extra in restored:
            key = DeviceKey(DeviceType.THERMOSTAT, 1, 0, sub_type)
            entity = registry.async_get_or_create(
                platform, DOMAIN, f"{key.unique_id}:{HOST}", config_entry=entry
            )
            cache.append((State(entity.entity_id, "unknown"), extra))
        mock_restore_cache_with_extra_data(hass, cache)
    holder: list[Wallpad] = []

    async def start(gateway: KocomGateway) -> None:
        gateway._sync_connection_availability()
        gateway._task_sender = hass.async_create_background_task(
            gateway._sender_loop(), "kocom test sender"
        )
        if initial is not None:
            gateway.controller.feed(initial)

    async def send(_conn: AsyncConnection, data: bytes) -> int:
        current = holder[0]
        current.sent.append(data)
        # Status queries stay unanswered; a command is applied as the room does.
        if data[5:7] == b"\x36\x01" and data[9] == 0x00:
            mode, preset, target = data[10], data[11], data[12]
            if preset == AWAY:
                target = AWAY_TARGET
            elif target == 0:
                target = 5
            current.feed(_status(mode, preset, target))
        return len(data)

    idle = Mock(return_value=99.0)
    with ExitStack() as stack:
        stack.enter_context(patch.object(KocomGateway, "async_start", start))
        if restored is None:
            stack.enter_context(
                patch.object(KocomGateway, "async_get_entity_registry", new=AsyncMock())
            )
        connected = stack.enter_context(
            patch.object(AsyncConnection, "_is_connected", return_value=True)
        )
        stack.enter_context(patch.object(AsyncConnection, "open", new=AsyncMock()))
        stack.enter_context(patch.object(AsyncConnection, "close", new=AsyncMock()))
        stack.enter_context(patch.object(AsyncConnection, "idle_since", idle))
        stack.enter_context(patch.object(AsyncConnection, "send", send))
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        gateway = hass.data[DOMAIN][entry.entry_id]
        entity_id = er.async_get(hass).async_get_entity_id(
            Platform.CLIMATE, DOMAIN, f"{ROOM_1.unique_id}:{HOST}"
        )
        assert entity_id is not None
        current = Wallpad(hass, gateway, entity_id)
        current.connected = connected
        current.idle = idle
        holder.append(current)
        try:
            yield current
        finally:
            current.release_sender()
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


def _target(hass: HomeAssistant, wallpad: Wallpad) -> Any:
    state = hass.states.get(wallpad.entity_id)
    assert state is not None
    return state.attributes["temperature"]


@pytest.mark.asyncio
async def test_preset_none_after_away_writes_the_pre_away_target(hass: HomeAssistant) -> None:
    async with loaded(hass, AT_HOME) as wallpad:
        await wallpad.call("set_preset_mode", {"preset_mode": "away"})
        assert _target(hass, wallpad) == AWAY_TARGET
        await wallpad.call("set_preset_mode", {"preset_mode": "none"})
        assert wallpad.commands[-1][12] == HOME_TARGET
        assert wallpad.commands[-1] == RESTORE_22
        assert _target(hass, wallpad) == HOME_TARGET


@pytest.mark.asyncio
async def test_hvac_heat_during_away_writes_the_pre_away_target(hass: HomeAssistant) -> None:
    async with loaded(hass, AT_HOME) as wallpad:
        await wallpad.call("set_preset_mode", {"preset_mode": "away"})
        await wallpad.call("set_hvac_mode", {"hvac_mode": "heat"})
        assert wallpad.commands[-1][12] == HOME_TARGET
        assert _target(hass, wallpad) == HOME_TARGET


@pytest.mark.asyncio
async def test_wallpad_exit_restores_the_pre_away_target_once(hass: HomeAssistant) -> None:
    async with loaded(hass, AT_HOME) as wallpad:
        wallpad.feed(AWAY_REPORT)
        await wallpad.settle()
        wallpad.feed(WALLPAD_EXIT)
        await wallpad.settle()
        assert wallpad.commands == [RESTORE_22]
        assert _target(hass, wallpad) == HOME_TARGET
        wallpad.feed(AT_HOME + AT_HOME)
        await wallpad.settle()
        assert wallpad.commands == [RESTORE_22]


@pytest.mark.asyncio
@pytest.mark.parametrize("reached_by", ["restored state", "reconnection"])
@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("set_preset_mode", {"preset_mode": "away"}),
        ("set_preset_mode", {"preset_mode": "none"}),
        ("set_hvac_mode", {"hvac_mode": "off"}),
        ("set_hvac_mode", {"hvac_mode": "heat"}),
    ],
)
async def test_unconfirmed_room_commands_fail_without_a_command_frame(
    hass: HomeAssistant, reached_by: str, service: str, data: dict[str, str]
) -> None:
    restored = None
    initial: bytes | None = AT_HOME
    if reached_by == "restored state":
        initial = None
        restored = [(Platform.CLIMATE, SubType.NONE, {
            "packet": AT_HOME.hex(), "device_storage": {}, "home_target": HOME_TARGET,
        })]
    async with loaded(hass, initial, restored) as wallpad:
        if reached_by == "reconnection":
            await wallpad.reconnect()
        assert not wallpad.gateway.is_device_state_confirmed(ROOM_1)
        wallpad.sent.clear()
        with pytest.raises(HomeAssistantError):
            await wallpad.call(service, data)
        # Status queries (0x3A) may be written; no command frame is.
        assert wallpad.commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize("newer", [None, "while queued", "during the idle wait"])
async def test_restore_is_dropped_when_a_newer_report_changes_the_setting(
    hass: HomeAssistant, newer: str | None
) -> None:
    newer_report = _status(HEAT, NONE, 12)
    async with loaded(hass, AT_HOME) as wallpad:
        wallpad.feed(AWAY_REPORT)
        await wallpad.settle()
        if newer == "while queued":
            wallpad.feed(WALLPAD_EXIT + newer_report)
        else:
            wallpad.hold_sender()
            wallpad.feed(WALLPAD_EXIT)
            await asyncio.sleep(0.1)
            active = wallpad.gateway._active_item
            assert active is not None and active.restore is not None
            if newer is not None:
                wallpad.feed(newer_report)
            wallpad.release_sender()
        await wallpad.settle()
        if newer is None:
            assert wallpad.commands == [RESTORE_22]
        else:
            assert wallpad.commands == []
            assert _target(hass, wallpad) == 12


@pytest.mark.asyncio
async def test_superseded_restore_is_not_sent_after_an_equal_exit(hass: HomeAssistant) -> None:
    async with loaded(hass, AT_HOME) as wallpad:
        wallpad.feed(AWAY_REPORT)
        await wallpad.settle()
        wallpad.hold_sender()
        wallpad.feed(WALLPAD_EXIT)
        await asyncio.sleep(0.1)
        # A differing report ends that restore; a later exit shows the same setting.
        wallpad.feed(_status(HEAT, NONE, 24) + AWAY_REPORT + WALLPAD_EXIT)
        wallpad.release_sender()
        await wallpad.settle()
        assert wallpad.commands == [_command(HEAT, NONE, 24)]
        assert _target(hass, wallpad) == 24


@pytest.mark.asyncio
async def test_restore_queued_before_a_reconnect_is_not_sent(hass: HomeAssistant) -> None:
    async with loaded(hass, AT_HOME) as wallpad:
        wallpad.feed(AWAY_REPORT)
        await wallpad.settle()
        wallpad.hold_sender()
        wallpad.feed(WALLPAD_EXIT)
        await asyncio.sleep(0.1)
        await wallpad.reconnect()
        # The room shows the exit setting again in the new connection.
        wallpad.feed(EXIT_REPORT)
        wallpad.release_sender()
        await wallpad.settle()
        assert wallpad.commands == []
        assert wallpad.gateway.controller.home_target(1) == AWAY_TARGET


@pytest.mark.asyncio
async def test_home_target_survives_a_startup_with_other_snapshots(hass: HomeAssistant) -> None:
    restored = [
        (Platform.CLIMATE, SubType.NONE, {
            "packet": AWAY_REPORT.hex(), "device_storage": {}, "home_target": HOME_TARGET,
        }),
        (Platform.SENSOR, SubType.HOTTEMP, {
            "packet": AWAY_REPORT.hex(), "device_storage": {"marker": 1}, "home_target": 30,
        }),
        (Platform.SENSOR, SubType.HEATTEMP, {
            "packet": AWAY_REPORT.hex(), "device_storage": {"marker": 2},
        }),
    ]
    async with loaded(hass, AWAY_REPORT, restored) as wallpad:
        assert wallpad.gateway.controller.home_target(1) == HOME_TARGET
        stored = {
            item.state.entity_id: item.extra_data
            for item in restore_state.async_get(hass).async_get_stored_states()
        }
        extra = stored[wallpad.entity_id]
        assert extra is not None and extra.as_dict()["home_target"] == HOME_TARGET
        await wallpad.call("set_preset_mode", {"preset_mode": "none"})
        assert wallpad.commands == [RESTORE_22]


@pytest.mark.asyncio
async def test_own_exit_writes_no_restore(hass: HomeAssistant) -> None:
    async with loaded(hass, AT_HOME) as wallpad:
        await wallpad.call("set_preset_mode", {"preset_mode": "away"})
        await wallpad.call("set_preset_mode", {"preset_mode": "none"})
        await wallpad.settle()
        assert len(wallpad.commands) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial", "exit_frames"),
    [
        (AT_HOME, _command(OFF, NONE, 0x00) + _status(OFF, NONE, AWAY_TARGET)),
        (None, WALLPAD_EXIT),
    ],
    ids=["off mode", "no home target"],
)
async def test_exit_without_a_restore_writes_nothing(
    hass: HomeAssistant, initial: bytes | None, exit_frames: bytes
) -> None:
    async with loaded(hass, initial if initial is not None else AWAY_REPORT) as wallpad:
        if initial is not None:
            wallpad.feed(AWAY_REPORT)
            await wallpad.settle()
        wallpad.feed(exit_frames)
        await wallpad.settle()
        assert wallpad.commands == []
