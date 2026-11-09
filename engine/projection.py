from __future__ import annotations

from collections.abc import Iterable

from engine.events import (
    ActionRejected,
    GameEvent,
    HealthChanged,
    ItemAdded,
    ItemRemoved,
    LocationChanged,
    SkillCheckResolved,
    StatChanged,
    WorldFlagChanged,
)
from engine.models import GameState


def to_public_dict(state: GameState, session_name: str = "", log: tuple[object, ...] | list[object] = ()) -> dict:
    """Return the frontend cache shape. Secrets and engine internals stay out."""
    return {
        "session_id": state.session_id,
        "session_name": session_name,
        "revision": state.revision,
        "turn_number": state.turn_number,
        "location": state.location_id,
        "characters": [_public_character(character) for character in state.characters],
        "log": [_public_log_entry(entry) for entry in log],
    }


def to_public_events(events: Iterable[GameEvent]) -> list[dict]:
    """Project Engine events without exposing private world flags or messages."""
    public = []
    for event in events:
        projected = _public_event(event)
        if projected is not None:
            public.append(projected)
    return public


def _public_character(character) -> dict:
    return {
        "id": character.id,
        "name": character.name,
        "concept": character.concept,
        "hp": character.hp,
        "max_hp": character.max_hp,
        "stats": dict(character.stats),
        "inventory": [
            {"item_id": item.item_id, "name": item.name, "quantity": item.quantity}
            for item in character.inventory
        ],
    }


def _public_log_entry(entry: object) -> dict:
    if isinstance(entry, dict):
        kind = entry.get("kind", "")
        character_id = entry.get("character_id", "")
        text = entry.get("text", "")
    else:
        kind = getattr(entry, "kind", "")
        character_id = getattr(entry, "character_id", "")
        text = getattr(entry, "text", "")
    return {
        "kind": kind,
        "character_id": character_id,
        "text": text,
    }


def _event_base(event: GameEvent, event_type: str) -> dict:
    return {
        "type": event_type,
        "action_id": event.action_id,
        "sequence": event.sequence,
    }


def _public_event(event: GameEvent) -> dict | None:
    if isinstance(event, HealthChanged):
        return {
            **_event_base(event, "health_changed"),
            "character_id": event.character_id,
            "previous_hp": event.previous_hp,
            "new_hp": event.new_hp,
            "delta": event.new_hp - event.previous_hp,
        }
    if isinstance(event, ItemAdded):
        return {
            **_event_base(event, "item_added"),
            "character_id": event.character_id,
            "item_id": event.item_id,
            "quantity": event.quantity,
            "new_quantity": event.new_quantity,
        }
    if isinstance(event, ItemRemoved):
        return {
            **_event_base(event, "item_removed"),
            "character_id": event.character_id,
            "item_id": event.item_id,
            "quantity": event.quantity,
            "new_quantity": event.new_quantity,
        }
    if isinstance(event, LocationChanged):
        return {
            **_event_base(event, "location_changed"),
            "previous_location": event.previous_location_id,
            "location": event.location_id,
        }
    if isinstance(event, StatChanged):
        return {
            **_event_base(event, "stat_changed"),
            "character_id": event.character_id,
            "stat": event.stat,
            "previous_value": event.previous_value,
            "new_value": event.new_value,
            "delta": event.new_value - event.previous_value,
        }
    if isinstance(event, SkillCheckResolved):
        return {
            **_event_base(event, "skill_check_resolved"),
            "character_id": event.character_id,
            "stat": event.stat,
            "roll": event.roll,
            "modifier": event.modifier,
            "total": event.total,
            "difficulty": event.difficulty,
            "success": event.success,
        }
    if isinstance(event, ActionRejected):
        return {
            **_event_base(event, "action_rejected"),
            "code": event.code,
        }
    if isinstance(event, WorldFlagChanged):
        return None
    return None
