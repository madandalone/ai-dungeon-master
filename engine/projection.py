from __future__ import annotations

from engine.models import GameState


def to_public_dict(state: GameState, session_name: str = "", log: tuple[object, ...] | list[object] = ()) -> dict:
    """Return the frontend cache shape. Secrets and engine internals stay out."""
    return {
        "session_id": state.session_id,
        "session_name": session_name,
        "location": state.location_id,
        "characters": [_public_character(character) for character in state.characters],
        "log": [_public_log_entry(entry) for entry in log],
    }


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
