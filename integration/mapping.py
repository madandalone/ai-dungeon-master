from collections import Counter

from ai_dm import models as dm
from engine.commands import AddItem, AdjustHealth, RemoveItem, SetLocation
from engine.models import GameState
from integration.errors import UnmappableResponse


def to_dm_state(state: GameState, room_context: str = "") -> dm.GameState:
    """Engine state -> DM context. Only fields the DM contract defines are passed."""
    names = [c.name for c in state.characters]
    if len(set(names)) != len(names):
        raise UnmappableResponse("Character names must be unique: the DM identifies characters by name")
    return dm.GameState(
        location=state.location_id,
        room_context=room_context,
        active_characters=[
            dm.CharacterState(
                name=c.name,
                hp=c.hp,
                max_hp=c.max_hp,
                inventory=[i.name for i in c.inventory for _ in range(i.quantity)],
            )
            for c in state.characters
        ],
    )


def to_commands(state: GameState, response: dm.TurnResponse) -> list[object]:
    """Untrusted DM response -> ordered engine commands. Semantic checks stay in the engine."""
    by_name = {c.name: c for c in state.characters}
    commands: list[object] = []
    for delta in response.state_updates:
        character = by_name.get(delta.character_name)
        if character is None:
            raise UnmappableResponse(f"Unknown character in DM response: {delta.character_name!r}")
        if delta.hp_change:
            commands.append(AdjustHealth(character.id, delta.hp_change))
        for name, qty in Counter(delta.items_added).items():
            commands.append(AddItem(character.id, name, qty))
        item_ids = {**{i.name: i.item_id for i in character.inventory}, **{i.item_id: i.item_id for i in character.inventory}}
        for name, qty in Counter(delta.items_removed).items():
            commands.append(RemoveItem(character.id, item_ids.get(name, name), qty))
    if response.new_location:
        commands.append(SetLocation(response.new_location))
    return commands
