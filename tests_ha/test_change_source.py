"""Thermostat change sources through a real entry with a patched transport."""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.kocom_wallpad import logbook as kocom_logbook
from custom_components.kocom_wallpad.const import (
    DOMAIN,
    EVENT_THERMOSTAT_CHANGED,
    DeviceType,
    SubType,
)
from custom_components.kocom_wallpad.gateway import KocomGateway
from custom_components.kocom_wallpad.models import DeviceKey
from custom_components.kocom_wallpad.transport import AsyncConnection

# Frames observed on 2026-10-23 (room 03), byte for byte.
COMMAND_21 = bytes.fromhex("aa5530bc00360301000011001500000000004c0d0d")
ACK_21 = bytes.fromhex("aa5530dc00010036030011001500000000006c0d0d")
STATUS_21 = bytes.fromhex("aa5530bc0001003603001100150018000000640d0d")
WALLPAD_ACK_21 = bytes.fromhex("aa5530dc0036030100001100150018000000840d0d")
COMMAND_20 = bytes.fromhex("aa5530bc00360301000011001400000000004b0d0d")
ACK_20 = bytes.fromhex("aa5530dc00010036030011001400000000006b0d0d")
STATUS_20 = bytes.fromhex("aa5530bc0001003603001100140018000000630d0d")
WALLPAD_ACK_20 = bytes.fromhex("aa5530dc0036030100001100140018000000830d0d")
QUERY_03 = bytes.fromhex("aa5530bc00360301003a0000000000000000600d0d")
QUERY_ACK_03 = bytes.fromhex("aa5530dc00010036033a0000000000000000800d0d")

WALLPAD_TO_21 = COMMAND_21 + ACK_21 + STATUS_21 + WALLPAD_ACK_21
WALLPAD_TO_20 = COMMAND_20 + ACK_20 + STATUS_20 + WALLPAD_ACK_20
ROOM_3 = DeviceKey(DeviceType.THERMOSTAT, 3, 0, SubType.NONE)


class Wallpad:
    """A loaded entry whose transport answers the observed frames."""

    def __init__(self, hass: HomeAssistant, gateway: KocomGateway, entity_id: str) -> None:
        self.hass = hass
        self.gateway = gateway
        self.entity_id = entity_id
        self.connected: Any = None
        self.sent: list[bytes] = []
        self.events: list[Event] = []
        self.state_changes: list[Event] = []

    def feed(self, data: bytes) -> None:
        self.gateway.controller.feed(data)

    @property
    def last_status(self) -> bytes:
        state = self.gateway.registry.get(ROOM_3)
        assert state is not None and isinstance(state.state, dict)
        return STATUS_21 if state.state["target_temp"] == 21.0 else STATUS_20


@pytest.fixture
async def wallpad(hass: HomeAssistant) -> AsyncIterator[Wallpad]:
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "wallpad.invalid", "port": 8899})
    entry.add_to_hass(hass)
    replies = {
        COMMAND_21: ACK_21 + STATUS_21 + WALLPAD_ACK_21,
        COMMAND_20: ACK_20 + STATUS_20 + WALLPAD_ACK_20,
    }
    holder: list[Wallpad] = []

    async def start(gateway: KocomGateway) -> None:
        gateway._sync_connection_availability()
        gateway._task_sender = hass.async_create_background_task(
            gateway._sender_loop(), "kocom test sender"
        )
        gateway.controller.feed(STATUS_20 + WALLPAD_ACK_20)

    async def send(_conn: AsyncConnection, data: bytes) -> int:
        current = holder[0]
        current.sent.append(data)
        if data == QUERY_03:
            # A bootstrap query is answered with the unchanged report.
            current.feed(QUERY_ACK_03 + current.last_status + WALLPAD_ACK_20)
        elif reply := replies.get(data):
            current.feed(reply)
        return len(data)

    with (
        patch.object(KocomGateway, "async_start", start),
        patch.object(KocomGateway, "async_get_entity_registry", new=AsyncMock()),
        patch.object(AsyncConnection, "_is_connected", return_value=True) as connected,
        patch.object(AsyncConnection, "open", new=AsyncMock()),
        patch.object(AsyncConnection, "close", new=AsyncMock()),
        patch.object(AsyncConnection, "idle_since", return_value=99.0),
        patch.object(AsyncConnection, "send", send),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        gateway = hass.data[DOMAIN][entry.entry_id]
        entity_id = er.async_get(hass).async_get_entity_id(
            Platform.CLIMATE, DOMAIN, f"{ROOM_3.unique_id}:wallpad.invalid"
        )
        assert entity_id is not None
        current = Wallpad(hass, gateway, entity_id)
        current.connected = connected
        holder.append(current)

        @callback
        def changed(event: Event) -> None:
            current.events.append(event)

        @callback
        def state_changed(event: Event) -> None:
            if event.data["entity_id"] == entity_id:
                current.state_changes.append(event)

        remove = [
            hass.bus.async_listen(EVENT_THERMOSTAT_CHANGED, changed),
            hass.bus.async_listen("state_changed", state_changed),
        ]
        try:
            yield current
        finally:
            for unsubscribe in remove:
                unsubscribe()
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


Describer = Callable[[Event], dict[str, Any]]


@pytest.mark.asyncio
async def test_attributes_events_and_translations(hass: HomeAssistant, wallpad: Wallpad) -> None:
    before = hass.states.get(wallpad.entity_id)
    assert before is not None and before.state == "heat"
    assert before.attributes["physical_state_confirmed"] is True
    assert "change_source" not in before.attributes
    assert "change_time" not in before.attributes

    wallpad.feed(STATUS_21 + WALLPAD_ACK_21)
    await hass.async_block_till_done()
    after = hass.states.get(wallpad.entity_id)
    assert after is not None
    assert after.attributes["temperature"] == 21
    assert after.attributes["change_source"] == "device"
    change_time = after.attributes["change_time"]
    assert isinstance(change_time, datetime) and change_time.utcoffset() == UTC.utcoffset(None)
    assert after.attributes["physical_state_confirmed"] is True
    assert [event.data for event in wallpad.events] == [
        {
            "entity_id": wallpad.entity_id,
            "source": "device",
            "changes": {"target_temperature": {"old": 20.0, "new": 21.0}},
        }
    ]

    wallpad.feed(WALLPAD_TO_20)
    await hass.async_block_till_done()
    changed = hass.states.get(wallpad.entity_id)
    assert changed is not None and changed.attributes["change_source"] == "wallpad"
    assert [event.data["source"] for event in wallpad.events] == ["device", "wallpad"]
    assert wallpad.events[1].data["changes"] == {
        "target_temperature": {"old": 21.0, "new": 20.0}
    }

    for language, names in (
        ("ko", ("변경 주체", "변경 시각", ("통합", "월패드", "단말기", "알 수 없음"))),
        ("en", ("Change source", "Change time", ("Integration", "Wallpad", "Thermostat", "Unknown"))),
    ):
        strings = await async_get_translations(hass, language, "entity", [DOMAIN])
        prefix = f"component.{DOMAIN}.entity.climate.thermostat.state_attributes"
        assert strings[f"{prefix}.change_source.name"] == names[0]
        assert strings[f"{prefix}.change_time.name"] == names[1]
        assert [
            strings[f"{prefix}.change_source.state.{source}"]
            for source in ("integration", "wallpad", "device", "unknown")
        ] == list(names[2])


@pytest.mark.asyncio
async def test_logbook_description(hass: HomeAssistant, wallpad: Wallpad) -> None:
    described: dict[tuple[str, str], Describer] = {}

    def collect(domain: str, event_type: str, describe: Describer) -> None:
        described[(domain, event_type)] = describe

    kocom_logbook.async_describe_events(hass, collect)
    describe = described[(DOMAIN, EVENT_THERMOSTAT_CHANGED)]

    wallpad.feed(WALLPAD_TO_21)
    await hass.async_block_till_done()
    entry = describe(wallpad.events[0])
    assert entry["message"] == "희망온도 20 → 21 °C (월패드에서 변경)"
    assert entry["entity_id"] == wallpad.entity_id

    examples = (
        ("device", {"hvac_mode": {"old": "off", "new": "heat"}, "preset_mode": {"old": "away", "new": "none"}},
         "난방 켜짐, 외출 해제 (단말기에서 변경)"),
        ("integration", {"preset_mode": {"old": "none", "new": "away"}, "target_temperature": {"old": 22.0, "new": 23.0}},
         "희망온도 22 → 23 °C, 외출 설정 (통합에서 변경)"),
        ("unknown", {"hvac_mode": {"old": "heat", "new": "off"}}, "난방 꺼짐 (변경 주체 알 수 없음)"),
    )
    for source, changes, message in examples:
        event: Event = Event(
            EVENT_THERMOSTAT_CHANGED,
            {"entity_id": wallpad.entity_id, "source": source, "changes": changes},
        )
        entry = describe(event)
        assert entry["message"] == message
        assert entry["entity_id"] == wallpad.entity_id


@pytest.mark.asyncio
async def test_service_call_context_then_wallpad_change(
    hass: HomeAssistant, wallpad: Wallpad, hass_admin_user: MockUser
) -> None:
    user_id = hass_admin_user.id
    user_context = Context(user_id=user_id)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": wallpad.entity_id, "temperature": 21},
        blocking=True,
        context=user_context,
    )
    await hass.async_block_till_done()
    assert wallpad.sent == [COMMAND_21]
    assert len(wallpad.events) == 1
    integration = wallpad.events[0]
    assert integration.data["source"] == "integration"
    assert integration.context.user_id == user_id
    assert integration.context.id == user_context.id
    state = hass.states.get(wallpad.entity_id)
    assert state is not None and state.attributes["change_source"] == "integration"
    assert state.context.user_id == user_id
    assert wallpad.state_changes[-1].context.user_id == user_id

    # Within 5 seconds of that call, a wallpad change gets its own context.
    wallpad.feed(WALLPAD_TO_20)
    await hass.async_block_till_done()
    assert len(wallpad.events) == 2
    elsewhere = wallpad.events[1]
    assert elsewhere.data["source"] == "wallpad"
    assert elsewhere.context.user_id is None
    assert elsewhere.context.parent_id is None
    assert elsewhere.context.id != user_context.id
    state = hass.states.get(wallpad.entity_id)
    assert state is not None and state.attributes["change_source"] == "wallpad"
    assert state.context.id == elsewhere.context.id
    assert state.context.user_id is None


@pytest.mark.asyncio
async def test_connection_change_repeats_no_label(hass: HomeAssistant, wallpad: Wallpad) -> None:
    wallpad.feed(WALLPAD_TO_21)
    await hass.async_block_till_done()
    labelled = hass.states.get(wallpad.entity_id)
    assert labelled is not None
    attributes = (labelled.attributes["change_source"], labelled.attributes["change_time"])
    assert len(wallpad.events) == 1

    wallpad.connected.return_value = False
    wallpad.gateway._sync_connection_availability()
    await hass.async_block_till_done()
    wallpad.connected.return_value = True
    wallpad.gateway._sync_connection_availability()
    # The bootstrap query runs on the background sender; wait for its answer.
    for _ in range(200):
        if wallpad.gateway.is_device_state_confirmed(ROOM_3):
            break
        await asyncio.sleep(0.01)
    await hass.async_block_till_done()
    assert QUERY_03 in wallpad.sent

    after = hass.states.get(wallpad.entity_id)
    assert after is not None and after.state == "heat"
    assert after.attributes["physical_state_confirmed"] is True
    assert (after.attributes["change_source"], after.attributes["change_time"]) == attributes
    assert len(wallpad.events) == 1
