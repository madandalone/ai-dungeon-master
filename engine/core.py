from __future__ import annotations

from dataclasses import replace

from engine.commands import (
    COMMAND_TYPES,
    AddItem,
    AdjustHealth,
    AdjustStat,
    RemoveItem,
    SetLocation,
    SetWorldFlag,
    SkillCheck,
)
from engine.dice import Dice
from engine.events import (
    ActionRejected,
    HealthChanged,
    ItemAdded,
    ItemRemoved,
    LocationChanged,
    SkillCheckResolved,
    StatChanged,
    WorldFlagChanged,
)
from engine.models import (
    Character,
    EngineError,
    EngineResult,
    GameState,
    InventoryItem,
    PlayerAction,
)


def apply_action(
    state: GameState,
    action: PlayerAction,
    commands: tuple[object, ...] | list[object],
    dice: Dice,
) -> EngineResult:
    """Validate one player action and apply its commands atomically.

    The action text is not interpreted. One accepted action advances the turn
    and revision exactly once. Randomness is used only for skill checks, and
    only after every command has been validated.
    """
    action_errors = tuple(_action_errors(state, action))
    if action_errors:
        return _reject(state, action, action_errors)

    preview = state
    for index, command in enumerate(commands):
        preview, _event, error = _step(preview, command, index, action.action_id, dice, commit=False)
        if error is not None:
            return _reject(state, action, (error,))

    current = state
    events = []
    for index, command in enumerate(commands):
        current, event, error = _step(current, command, index, action.action_id, dice, commit=True)
        if error is not None or event is None:
            fallback = error or EngineError("UNKNOWN_COMMAND", "Command could not be applied.", index)
            return _reject(state, action, (fallback,))
        events.append(event)

    accepted_state = replace(
        current,
        revision=current.revision + 1,
        turn_number=current.turn_number + 1,
    )
    return EngineResult(True, accepted_state, tuple(events), ())


def _reject(state: GameState, action: PlayerAction, errors: tuple[EngineError, ...]) -> EngineResult:
    rejected = ActionRejected(action.action_id, 0, errors[0].code)
    return EngineResult(False, state, (rejected,), errors)


def _action_errors(state: GameState, action: PlayerAction) -> list[EngineError]:
    errors: list[EngineError] = []
    if action.session_id != state.session_id:
        errors.append(EngineError("SESSION_MISMATCH", "Action session does not match the game state."))
    if action.expected_revision != state.revision:
        errors.append(EngineError("STALE_REVISION", "Action revision does not match the game state."))
    if action.text.strip() == "":
        errors.append(EngineError("EMPTY_ACTION", "Action text is empty."))
    if _find_character(state, action.character_id) is None:
        errors.append(EngineError("INVALID_CHARACTER", "Acting character was not found in this session."))
    return errors


def _step(state, command, index, action_id, dice, commit):
    if not isinstance(command, COMMAND_TYPES):
        return state, None, EngineError("UNKNOWN_COMMAND", "Command type is not supported.", index)
    if isinstance(command, AdjustHealth):
        return _adjust_health(state, command, index, action_id, commit)
    if isinstance(command, AddItem):
        return _add_item(state, command, index, action_id, commit)
    if isinstance(command, RemoveItem):
        return _remove_item(state, command, index, action_id, commit)
    if isinstance(command, SetLocation):
        return _set_location(state, command, index, action_id, commit)
    if isinstance(command, AdjustStat):
        return _adjust_stat(state, command, index, action_id, commit)
    if isinstance(command, SetWorldFlag):
        return _set_world_flag(state, command, index, action_id, commit)
    if isinstance(command, SkillCheck):
        return _skill_check(state, command, index, action_id, dice, commit)
    return state, None, EngineError("UNKNOWN_COMMAND", "Command type is not supported.", index)


def _adjust_health(state, command, index, action_id, commit):
    character = _find_character(state, command.character_id)
    if character is None:
        return state, None, EngineError("INVALID_TARGET", "Character was not found.", index)
    if not _is_int(command.delta):
        return state, None, EngineError("INVALID_TARGET", "Health delta must be an integer.", index)
    new_hp = max(0, min(character.max_hp, character.hp + command.delta))
    updated = replace(character, hp=new_hp)
    event = None
    if commit:
        event = HealthChanged(action_id, index, character.id, character.hp, new_hp, command.delta)
    return _replace_character(state, updated), event, None


def _add_item(state, command, index, action_id, commit):
    character = _find_character(state, command.character_id)
    if character is None:
        return state, None, EngineError("INVALID_TARGET", "Character was not found.", index)
    item_id = command.item_id.strip()
    if item_id == "" or not _is_int(command.quantity) or command.quantity <= 0:
        return state, None, EngineError("INVALID_ITEM", "Item id and positive quantity are required.", index)
    inventory = []
    new_quantity = command.quantity
    found = False
    for item in character.inventory:
        if item.item_id == item_id:
            new_quantity = item.quantity + command.quantity
            inventory.append(replace(item, quantity=new_quantity))
            found = True
        else:
            inventory.append(item)
    if not found:
        inventory.append(InventoryItem(item_id, item_id, command.quantity))
    updated = replace(character, inventory=tuple(inventory))
    event = None
    if commit:
        event = ItemAdded(action_id, index, character.id, item_id, command.quantity, new_quantity)
    return _replace_character(state, updated), event, None


def _remove_item(state, command, index, action_id, commit):
    character = _find_character(state, command.character_id)
    if character is None:
        return state, None, EngineError("INVALID_TARGET", "Character was not found.", index)
    item_id = command.item_id.strip()
    if item_id == "" or not _is_int(command.quantity) or command.quantity <= 0:
        return state, None, EngineError("INVALID_ITEM", "Item id and positive quantity are required.", index)
    current = next((item for item in character.inventory if item.item_id == item_id), None)
    if current is None or current.quantity < command.quantity:
        return state, None, EngineError("INVALID_ITEM", "Not enough of that item.", index)
    new_quantity = current.quantity - command.quantity
    if new_quantity == 0:
        inventory = tuple(item for item in character.inventory if item.item_id != item_id)
    else:
        inventory = tuple(
            replace(item, quantity=new_quantity) if item.item_id == item_id else item
            for item in character.inventory
        )
    updated = replace(character, inventory=inventory)
    event = None
    if commit:
        event = ItemRemoved(action_id, index, character.id, item_id, command.quantity, new_quantity)
    return _replace_character(state, updated), event, None


def _set_location(state, command, index, action_id, commit):
    location_id = command.location_id.strip()
    if location_id == "":
        return state, None, EngineError("INVALID_LOCATION", "Location id is empty.", index)
    event = None
    if commit:
        event = LocationChanged(action_id, index, state.location_id, location_id)
    return replace(state, location_id=location_id), event, None


def _adjust_stat(state, command, index, action_id, commit):
    character = _find_character(state, command.character_id)
    if character is None:
        return state, None, EngineError("INVALID_TARGET", "Character was not found.", index)
    stat = command.stat.strip()
    if stat == "" or stat not in character.stats or not _is_int(character.stats[stat]):
        return state, None, EngineError("INVALID_STAT", "Stat is not on this character.", index)
    if not _is_int(command.delta):
        return state, None, EngineError("INVALID_TARGET", "Stat delta must be an integer.", index)
    previous = character.stats[stat]
    new_value = previous + command.delta
    stats = dict(character.stats)
    stats[stat] = new_value
    updated = replace(character, stats=stats)
    event = None
    if commit:
        event = StatChanged(action_id, index, character.id, stat, previous, new_value, command.delta)
    return _replace_character(state, updated), event, None


def _set_world_flag(state, command, index, action_id, commit):
    key = command.key.strip()
    if key == "" or not _is_flag_value(command.value):
        return state, None, EngineError("INVALID_TARGET", "World flag key or value is invalid.", index)
    flags = dict(state.world_flags)
    previous = flags.get(key)
    flags[key] = command.value
    event = None
    if commit:
        event = WorldFlagChanged(action_id, index, key, previous, command.value)
    return replace(state, world_flags=flags), event, None


def _skill_check(state, command, index, action_id, dice, commit):
    character = _find_character(state, command.character_id)
    if character is None:
        return state, None, EngineError("INVALID_TARGET", "Character was not found.", index)
    stat = command.stat.strip()
    if stat == "" or stat not in character.stats or not _is_int(character.stats[stat]):
        return state, None, EngineError("INVALID_STAT", "Stat is not on this character.", index)
    if not _is_int(command.difficulty):
        return state, None, EngineError("INVALID_TARGET", "Difficulty must be an integer.", index)
    if not commit:
        return state, None, None
    roll = dice.roll(20)
    modifier = character.stats[stat]
    total = roll + modifier
    event = SkillCheckResolved(
        action_id,
        index,
        character.id,
        stat,
        roll,
        modifier,
        total,
        command.difficulty,
        total >= command.difficulty,
    )
    return state, event, None


def _find_character(state: GameState, character_id: str) -> Character | None:
    for character in state.characters:
        if character.id == character_id:
            return character
    return None


def _replace_character(state: GameState, character: Character) -> GameState:
    characters = tuple(character if current.id == character.id else current for current in state.characters)
    return replace(state, characters=characters)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_flag_value(value: object) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return True
    return isinstance(value, str)
