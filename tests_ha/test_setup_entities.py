"""Real registry parent and entity identities survive setup and reload."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.fan import FanEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.kocom_wallpad import controller as controller_module
from custom_components.kocom_wallpad import gateway as gateway_module
from custom_components.kocom_wallpad.const import DOMAIN, DeviceType, SubType
from custom_components.kocom_wallpad.gateway import KocomGateway
from custom_components.kocom_wallpad.light import KocomLight
from custom_components.kocom_wallpad.models import DeviceKey, DeviceState
from custom_components.kocom_wallpad.sensor import KocomSensor
from custom_components.kocom_wallpad.switch import KocomSwitch
from custom_components.kocom_wallpad.transport import AsyncConnection


@pytest.mark.asyncio
async def test_gateway_registry_identity_and_reload(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "wallpad.invalid", "port": 8899})
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    parent = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "wallpad.invalid")}
    )
    device = DeviceState(DeviceKey(DeviceType.LIGHT, 1, 1, SubType.NONE), Platform.LIGHT, {}, True)
    unique_id = f"{device.key.unique_id}:wallpad.invalid"
    child = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "KOCOM LIGHT")},
        connections={("wallpad.invalid", unique_id)},
        via_device_id=parent.id,
    )

    async def start(gateway):
        assert gateway.device_registry_id == parent.id
        gateway.registry.upsert(device)

    with (
        patch.object(KocomGateway, "async_start", start),
        patch.object(KocomGateway, "async_stop", new=AsyncMock()) as stop,
        patch.object(KocomGateway, "async_get_entity_registry", new=AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        gateway = hass.data[DOMAIN][entry.entry_id]
        info = KocomLight(gateway, device).device_info
        assert info is not None and info.get("via_device_id") == parent.id
        assert "via_device" not in info
        assert info.get("connections") == {("wallpad.invalid", unique_id)}
        entity_id = er.async_get(hass).async_get_entity_id("light", DOMAIN, unique_id)
        assert entity_id is not None
        entity = er.async_get(hass).async_get(entity_id)
        assert entity is not None and entity.device_id == child.id
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        reloaded = er.async_get(hass).async_get(entity_id)
        assert reloaded is not None and reloaded.id == entity.id
        assert reloaded.device_id == child.id
        registered = registry.async_get(child.id)
        assert isinstance(registered, dr.DeviceEntry)
        assert registered.via_device_id == parent.id
        assert hass.data[DOMAIN][entry.entry_id].device_registry_id == parent.id
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.entry_id not in hass.data[DOMAIN]
        assert stop.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("first_mode", [0x00, 0x02])
async def test_fan_first_and_later_known_presets_reach_real_consumer(hass, first_mode):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "wallpad.invalid", "port": 8899})
    entry.add_to_hass(hass)

    def report(mode):
        packet = bytearray.fromhex("aa5530bc0001004800001102800007230200f40d0d")
        packet[11] = mode
        packet[18] = sum(packet[2:18]) % 256
        return bytes(packet)

    async def start(gateway):
        gateway.controller.feed(report(first_mode))

    with (
        patch.object(KocomGateway, "async_start", start),
        patch.object(KocomGateway, "async_stop", new=AsyncMock()),
        patch.object(KocomGateway, "async_get_entity_registry", new=AsyncMock()),
        patch.object(AsyncConnection, "_is_connected", return_value=True),
        patch.object(AsyncConnection, "send", new=AsyncMock()) as send,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        gateway = hass.data[DOMAIN][entry.entry_id]
        key = DeviceKey(DeviceType.VENTILATION, 0, 0, SubType.NONE)
        entity_id = er.async_get(hass).async_get_entity_id("fan", DOMAIN, f"{key.unique_id}:wallpad.invalid")
        assert entity_id is not None
        before = hass.states.get(entity_id)
        assert before is not None and before.state == "on"
        original_flags = before.attributes["supported_features"]
        assert bool(original_flags & FanEntityFeature.PRESET_MODE) == (first_mode == 0x02)
        if first_mode == 0x02:
            assert before.attributes["preset_modes"] == ["ventilation", "auto"]
        for mode, token in ((0x02, "auto"), (0x08, "air purification")):
            gateway.controller.feed(report(mode))
            await hass.async_block_till_done()
            state = hass.states.get(entity_id)
            assert state is not None and state.state == "on"
            assert state.attributes["preset_mode"] == token
            assert token in state.attributes["preset_modes"]
            assert state.attributes["supported_features"] == original_flags | FanEntityFeature.PRESET_MODE
            assert state.attributes["percentage"] == before.attributes["percentage"]
        send.assert_not_awaited()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


ELEVATOR_REQUEST = bytes.fromhex("aa5530bc0001004400010000000000000000320d0d")
ELEVATOR_ARRIVAL = bytes.fromhex("aa5530bc0001004400000331320000000000970d0d")


@asynccontextmanager
async def restored_elevator(hass, packet=ELEVATOR_ARRIVAL):
    """Persist real entity extra data and restore it into a fresh loaded gateway."""
    host = "wallpad.invalid"
    entry = MockConfigEntry(domain=DOMAIN, data={"host": host, "port": 8899})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    source = KocomGateway(hass, entry, host, 8899)
    source._restore_mode = True
    source.controller.feed(bytes.fromhex("aa5530bc0001004400000182000000000000b40d0d"))
    source.controller.feed(packet)
    source._restore_mode = False
    ids, cache = {}, []
    for platform, subtype in ((Platform.SWITCH, SubType.NONE),
                              (Platform.SENSOR, SubType.DIRECTION),
                              (Platform.SENSOR, SubType.FLOOR)):
        key = DeviceKey(DeviceType.ELEVATOR, 0, 0, subtype)
        device = source.registry.get(key)
        assert device is not None
        entity = KocomSwitch(source, device) if platform == Platform.SWITCH else KocomSensor(source, device)
        saved = entity.extra_restore_state_data.as_dict()
        assert saved["packet"] == packet.hex()
        unique_id = entity.unique_id
        assert unique_id is not None
        registered = registry.async_get_or_create(platform, DOMAIN, unique_id, config_entry=entry)
        ids[subtype] = registered.entity_id
        cache.append((State(registered.entity_id, "unknown"), saved))
    mock_restore_cache_with_extra_data(hass, cache)
    sent = []
    response = [ELEVATOR_ARRIVAL]
    holder = []

    async def start(gateway):
        gateway._sync_connection_availability()
        gateway._task_sender = hass.async_create_background_task(gateway._sender_loop(), "elevator test sender")

    async def send(_connection, data):
        sent.append(data)
        holder[0].controller.feed(response[0])
        return len(data)

    with (
        patch.object(KocomGateway, "async_start", start),
        patch.object(AsyncConnection, "_is_connected", return_value=True) as connected,
        patch.object(AsyncConnection, "open", new=AsyncMock()) as opened,
        patch.object(AsyncConnection, "close", new=AsyncMock()),
        patch.object(AsyncConnection, "idle_since", return_value=99.0),
        patch.object(AsyncConnection, "send", send),
        patch.object(controller_module, "CMD_CONFIRM_TIMEOUT", 0.01),
        patch.object(gateway_module, "SEND_RETRY_MAX", 1),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        gateway = hass.data[DOMAIN][entry.entry_id]
        holder.append(gateway)
        assert gateway is not source
        assert sent == []
        opened.assert_not_awaited()
        for subtype, entity_id in ids.items():
            key = DeviceKey(DeviceType.ELEVATOR, 0, 0, subtype)
            assert registry.async_get_entity_id(entity_id.split(".")[0], DOMAIN, f"{key.unique_id}:{host}") == entity_id
            assert not gateway.is_device_state_confirmed(key)
        try:
            yield gateway, ids, sent, response, connected
        finally:
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


async def elevator_service(hass, ids, action):
    await hass.services.async_call("switch", action, {"entity_id": ids[SubType.NONE]}, blocking=True)
    await hass.async_block_till_done()


def elevator_states(hass, ids):
    states = [hass.states.get(ids[subtype]) for subtype in (SubType.NONE, SubType.DIRECTION, SubType.FLOOR)]
    assert all(state is not None for state in states)
    return [state.state for state in states if state is not None]


@pytest.mark.asyncio
async def test_restored_elevator_first_call_reaches_real_switch_service(hass):
    async with restored_elevator(hass) as (gateway, ids, sent, _response, _connected):
        assert elevator_states(hass, ids) == ["unknown", "unknown", "unknown"]
        assert gateway.registry.get(DeviceKey(DeviceType.ELEVATOR, 0, 0, SubType.NONE)).state is False
        await elevator_service(hass, ids, "turn_on")
        assert sent == [ELEVATOR_REQUEST]
        assert elevator_states(hass, ids) == ["off", "arrival", "12"]


@pytest.mark.asyncio
async def test_restored_legacy_elevator_request_packet_allows_first_call(hass):
    async with restored_elevator(hass, ELEVATOR_REQUEST) as (gateway, ids, sent, _response, _connected):
        assert elevator_states(hass, ids) == ["unknown", "unknown", "unknown"]
        assert gateway.registry.get(DeviceKey(DeviceType.ELEVATOR, 0, 0, SubType.NONE))._packet == ELEVATOR_REQUEST
        await elevator_service(hass, ids, "turn_on")
        assert sent == [ELEVATOR_REQUEST]
        assert elevator_states(hass, ids) == ["off", "arrival", "12"]


@pytest.mark.asyncio
async def test_elevator_fresh_arrival_completes_service_with_off_direction_and_floor(hass):
    async with restored_elevator(hass) as (gateway, ids, sent, _response, _connected):
        gateway.controller.feed(ELEVATOR_ARRIVAL)
        await hass.async_block_till_done()
        assert elevator_states(hass, ids) == ["off", "arrival", "12"]
        await elevator_service(hass, ids, "turn_on")
        assert sent == [ELEVATOR_REQUEST]
        assert elevator_states(hass, ids) == ["off", "arrival", "12"]


@pytest.mark.asyncio
async def test_elevator_turn_off_raises_korean_error_without_command_frame(hass, caplog):
    async with restored_elevator(hass) as (_gateway, ids, sent, _response, connected):
        with pytest.raises(HomeAssistantError) as error:
            await elevator_service(hass, ids, "turn_off")
        assert str(error.value) == "엘리베이터는 호출만 지원하며 호출을 취소할 수 없습니다"
        assert sent == []
        connected.return_value = False
        _gateway._sync_connection_availability()
        await hass.async_block_till_done()
        caplog.clear()
        await elevator_service(hass, ids, "turn_off")
        assert any(record.name == "homeassistant.helpers.service" and ids[SubType.NONE] in record.getMessage() for record in caplog.records)
        assert elevator_states(hass, ids) == ["unavailable", "unavailable", "unavailable"]
        assert sent == []


@pytest.mark.asyncio
async def test_elevator_echo_only_call_fails_without_confirming_state(hass):
    async with restored_elevator(hass) as (gateway, ids, sent, response, _connected):
        response[0] = ELEVATOR_REQUEST
        with pytest.raises(HomeAssistantError) as error:
            await elevator_service(hass, ids, "turn_on")
        assert "실패" in str(error.value)
        assert sent == [ELEVATOR_REQUEST]
        assert elevator_states(hass, ids) == ["unknown", "unknown", "unknown"]
        assert not gateway.is_device_state_confirmed(DeviceKey(DeviceType.ELEVATOR, 0, 0, SubType.NONE))
        gateway.controller.feed(ELEVATOR_ARRIVAL)
        await hass.async_block_till_done()
        assert elevator_states(hass, ids) == ["off", "arrival", "12"]
        assert sent == [ELEVATOR_REQUEST]


@pytest.mark.asyncio
async def test_elevator_disconnect_rejects_service_and_reconnect_is_unknown(hass, caplog):
    async with restored_elevator(hass) as (gateway, ids, sent, _response, connected):
        gateway.controller.feed(ELEVATOR_ARRIVAL)
        await hass.async_block_till_done()
        connected.return_value = False
        gateway._sync_connection_availability()
        await hass.async_block_till_done()
        assert elevator_states(hass, ids) == ["unavailable", "unavailable", "unavailable"]
        caplog.clear()
        await elevator_service(hass, ids, "turn_on")
        assert any(record.name == "homeassistant.helpers.service" and ids[SubType.NONE] in record.getMessage() for record in caplog.records)
        assert sent == []
        connected.return_value = True
        gateway._sync_connection_availability()
        await hass.async_block_till_done()
        assert elevator_states(hass, ids) == ["unknown", "unknown", "unknown"]
        await elevator_service(hass, ids, "turn_on")
        assert sent == [ELEVATOR_REQUEST]
        assert elevator_states(hass, ids) == ["off", "arrival", "12"]
