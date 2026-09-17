"""Real registry parent and entity identities survive setup and reload."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kocom_wallpad.const import DOMAIN, DeviceType, SubType
from custom_components.kocom_wallpad.gateway import KocomGateway
from custom_components.kocom_wallpad.light import KocomLight
from custom_components.kocom_wallpad.models import DeviceKey, DeviceState


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
