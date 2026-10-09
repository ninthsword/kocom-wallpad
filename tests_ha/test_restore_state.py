"""Restore cached packets through the real Home Assistant entity registry."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import restore_state
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.kocom_wallpad.const import DOMAIN, DeviceType, SubType
from custom_components.kocom_wallpad.gateway import KocomGateway
from custom_components.kocom_wallpad.models import DeviceKey


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
