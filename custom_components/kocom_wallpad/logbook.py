"""Describe Kocom Wallpad logbook events."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook.const import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_THERMOSTAT_CHANGED

SOURCE_SUFFIX = {
    "integration": " (통합에서 변경)",
    "wallpad": " (월패드에서 변경)",
    "device": " (단말기에서 변경)",
    "unknown": " (변경 주체 알 수 없음)",
}


def _degrees(value: Any) -> str:
    """Show a whole-degree value without a decimal."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def thermostat_change_message(source: str, changes: dict[str, dict[str, Any]]) -> str:
    """Return the Korean logbook message for one labelled thermostat change."""
    parts: list[str] = []
    if target := changes.get("target_temperature"):
        parts.append(f"희망온도 {_degrees(target['old'])} → {_degrees(target['new'])} °C")
    if mode := changes.get("hvac_mode"):
        parts.append("난방 켜짐" if mode["new"] == "heat" else "난방 꺼짐")
    if preset := changes.get("preset_mode"):
        parts.append("외출 설정" if preset["new"] == "away" else "외출 해제")
    return ", ".join(parts) + SOURCE_SUFFIX.get(source, SOURCE_SUFFIX["unknown"])


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, Any]]], None],
) -> None:
    """Describe thermostat change events."""

    @callback
    def async_describe_thermostat_change(event: Event) -> dict[str, Any]:
        data = event.data
        entity_id = data["entity_id"]
        state = hass.states.get(entity_id)
        return {
            LOGBOOK_ENTRY_NAME: state.name if state else entity_id,
            LOGBOOK_ENTRY_MESSAGE: thermostat_change_message(data["source"], data["changes"]),
            LOGBOOK_ENTRY_ENTITY_ID: entity_id,
        }

    async_describe_event(DOMAIN, EVENT_THERMOSTAT_CHANGED, async_describe_thermostat_change)
