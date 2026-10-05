from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Union


ErrorCode = Literal[
    "INVALID_CHARACTER",
    "SESSION_MISMATCH",
    "STALE_REVISION",
    "EMPTY_ACTION",
    "UNKNOWN_COMMAND",
    "INVALID_TARGET",
    "INVALID_STAT",
    "INVALID_ITEM",
    "INVALID_LOCATION",
]

FlagValue = Union[bool, int, str]


@dataclass(frozen=True)
class InventoryItem:
    item_id: str
    name: str
    quantity: int


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    concept: str
    hp: int
    max_hp: int
    stats: Mapping[str, int]
    inventory: tuple[InventoryItem, ...]


@dataclass(frozen=True)
class GameState:
    session_id: str
    revision: int
    turn_number: int
    location_id: str
    characters: tuple[Character, ...]
    world_flags: Mapping[str, FlagValue]


@dataclass(frozen=True)
class PlayerAction:
    action_id: str
    session_id: str
    character_id: str
    text: str
    expected_revision: int


@dataclass(frozen=True)
class EngineError:
    code: ErrorCode
    message: str
    command_index: int | None = None


@dataclass(frozen=True)
class EngineResult:
    accepted: bool
    state: GameState
    events: tuple[object, ...]
    errors: tuple[EngineError, ...]
