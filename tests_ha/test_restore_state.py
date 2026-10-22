"""Restore cached packets through the real Home Assistant entity registry."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, State, callback
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


@pytest.fixture
async def live_thermostat(hass: HomeAssistant):
    """Set up the real climate platform from one received report, without I/O."""
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "wallpad.invalid", "port": 8899})
    entry.add_to_hass(hass)
    packet = bytes.fromhex("aa5530bc00010036010010001500140000005d0d0d")

    async def start(gateway: KocomGateway) -> None:
        gateway._sync_connection_availability()
        gateway.controller.feed(packet)

    with (
        patch.object(KocomGateway, "async_start", start),
        patch.object(KocomGateway, "async_stop", new=AsyncMock()),
        patch.object(KocomGateway, "async_get_entity_registry", new=AsyncMock()),
        patch.object(AsyncConnection, "_is_connected", return_value=True) as connected,
        patch.object(AsyncConnection, "open", new=AsyncMock()) as open_connection,
        patch.object(AsyncConnection, "send", new=AsyncMock()) as send_packet,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        gateway = hass.data[DOMAIN][entry.entry_id]
        key = DeviceKey(DeviceType.THERMOSTAT, 1, 0, SubType.NONE)
        entity_id = er.async_get(hass).async_get_entity_id(
            Platform.CLIMATE, DOMAIN, f"{key.unique_id}:wallpad.invalid"
        )
        assert entity_id is not None
        try:
            yield gateway, entity_id, packet, connected
        finally:
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()
            open_connection.assert_not_awaited()
            send_packet.assert_not_awaited()


@pytest.mark.asyncio
async def test_equal_live_thermostat_reports_refresh_ha_receipt_only(
    hass: HomeAssistant, live_thermostat, freezer
) -> None:
    gateway, entity_id, packet, _connected = live_thermostat
    before = hass.states.get(entity_id)
    assert before is not None and before.state == "heat"
    assert before.attributes["physical_state_confirmed"] is True
    controlled_state = before.state, dict(before.attributes)
    last_changed, last_updated = before.last_changed, before.last_updated
    previous_receipt = before.last_reported
    reports, changes = [], []

    @callback
    def matches(data):
        return data["entity_id"] == entity_id

    @callback
    def reported(event):
        reports.append(event)

    @callback
    def changed(event):
        changes.append(event)

    remove_reported = hass.bus.async_listen("state_reported", reported, matches)
    remove_changed = hass.bus.async_listen("state_changed", changed, matches)
    try:
        for count in (1, 2):
            freezer.tick(60)
            gateway.controller.feed(packet)
            await hass.async_block_till_done()
            after = hass.states.get(entity_id)
            assert after is not None
            assert (after.state, dict(after.attributes)) == controlled_state
            assert after.last_changed == last_changed
            assert after.last_updated == last_updated
            assert after.last_reported > previous_receipt
            assert len(reports) == count
            assert reports[-1].data["old_last_reported"] == previous_receipt
            assert reports[-1].data["last_reported"] == after.last_reported
            previous_receipt = after.last_reported
            assert changes == []
        freezer.tick(60)
        await hass.async_block_till_done()
        idle = hass.states.get(entity_id)
        assert idle is not None and idle.last_reported == previous_receipt
        assert len(reports) == 2
        assert gateway._tx_queue.empty()
        assert gateway._task_bootstrap_queries is None
    finally:
        remove_reported()
        remove_changed()


@pytest.mark.asyncio
async def test_non_live_thermostat_reports_do_not_refresh_confirmed_receipt(
    hass: HomeAssistant, live_thermostat, freezer
) -> None:
    gateway, entity_id, packet, connected = live_thermostat
    confirmed = hass.states.get(entity_id)
    assert confirmed is not None and confirmed.attributes["physical_state_confirmed"] is True
    previous_receipt = confirmed.last_reported
    reports = []

    @callback
    def matches(data):
        return data["entity_id"] == entity_id

    @callback
    def reported(event):
        reports.append(event)

    remove_reported = hass.bus.async_listen("state_reported", reported, matches)
    try:
        freezer.tick(60)
        gateway._restore_mode = True
        try:
            gateway.controller.feed(packet)
        finally:
            gateway._restore_mode = False
        mirror = bytearray(packet)
        mirror[3] = 0xDC
        mirror[5:9] = bytes.fromhex("36010100")
        mirror[18] = sum(mirror[2:18]) % 256
        gateway.controller.feed(bytes(mirror))
        corrupt = bytearray(packet)
        corrupt[18] ^= 1
        gateway.controller.feed(bytes(corrupt))
        await hass.async_block_till_done()
        assert confirmed.last_reported == previous_receipt
        assert reports == []

        connected.return_value = False
        gateway._sync_connection_availability()
        await hass.async_block_till_done()
        disconnected = hass.states.get(entity_id)
        assert disconnected is not None and disconnected.state == "unavailable"
        assert "physical_state_confirmed" not in disconnected.attributes
        disconnected_receipt = disconnected.last_reported
        freezer.tick(60)
        gateway.controller.feed(packet)
        await hass.async_block_till_done()
        assert disconnected.last_reported == disconnected_receipt
        assert reports == []
        key = DeviceKey(DeviceType.THERMOSTAT, 1, 0, SubType.NONE)
        assert not gateway.is_device_state_confirmed(key)
        assert gateway._tx_queue.empty()
        assert gateway._task_bootstrap_queries is None
    finally:
        remove_reported()


@pytest.mark.asyncio
@pytest.mark.parametrize("accessor_mode", ["legacy", "modern"])
async def test_registry_loop_restores_packets_without_confirming_live_state(
    hass: HomeAssistant, accessor_mode: str
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "wallpad.invalid", "port": 8899})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    keys = [DeviceKey(DeviceType.THERMOSTAT, room, 0, SubType.NONE) for room in (1, 2, 3, 4)]
    entities = [
        registry.async_get_or_create(
            Platform.CLIMATE, DOMAIN, f"{key.unique_id}:wallpad.invalid",
            config_entry=entry,
        )
        for key in keys
    ]
    packet = bytes.fromhex("aa5530bc00010036010010001500140000005d0d0d")
    device_storage = {"restored_marker": ["preserved"]}
    mock_restore_cache_with_extra_data(hass, [
        (State(entities[0].entity_id, "heat"), {
            "packet": packet.hex(), "device_storage": device_storage,
        }),
        (State(entities[1].entity_id, "unknown"), {}),
        (State(entities[2].entity_id, "unknown"), {"device_storage": {"ignored": True}}),
    ])
    cache = restore_state.async_get(hass)
    lookup = Mock(side_effect=cache.last_states.get)
    store = cache if accessor_mode == "legacy" else SimpleNamespace(async_get_stored_state=lookup)
    gateway = KocomGateway(hass, entry, "wallpad.invalid", 8899)

    with (
        patch.object(restore_state, "async_get", return_value=store) as get_store,
        patch.object(gateway.conn, "_is_connected", return_value=True),
        patch.object(gateway.conn, "open", new=AsyncMock()) as open_connection,
        patch.object(gateway.conn, "send", new=AsyncMock()) as send_packet,
    ):
        await gateway.async_get_entity_registry()

        assert get_store.call_count == len(entities)
        if accessor_mode == "modern":
            lookup.assert_has_calls([call(entity.entity_id) for entity in entities], any_order=True)
            assert lookup.call_count == len(entities)
        else:
            lookup.assert_not_called()
        restored = gateway.registry.get(keys[0])
        assert restored is not None
        assert restored._packet == packet
        assert isinstance(restored.state, dict)
        assert restored.state["target_temp"] == 21.0
        assert restored.state["current_temp"] == 20.0
        assert gateway.controller._device_storage == device_storage
        assert all(gateway.registry.get(key) is None for key in keys[1:])
        assert gateway._bootstrap_registry_keys == set(keys)
        assert not gateway._restore_mode
        assert gateway._force_register_uid is None
        assert gateway.is_transport_available()
        assert not gateway.is_device_state_confirmed(keys[0])
        assert not gateway.is_device_available(keys[0])

        gateway.controller._dispatch_packet(packet)

        assert gateway.is_device_state_confirmed(keys[0])
        assert gateway.is_device_available(keys[0])
        assert all(not gateway.is_device_state_confirmed(key) for key in keys[1:])
        open_connection.assert_not_awaited()
        send_packet.assert_not_awaited()
