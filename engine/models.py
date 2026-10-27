from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
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

    def __post_init__(self):
        object.__setattr__(self, "stats", MappingProxyType(dict(self.stats)))
        object.__setattr__(self, "inventory", tuple(self.inventory))

    def __deepcopy__(self, memo):
        return self


@dataclass(frozen=True)
class GameState:
    session_id: str
    revision: int
    turn_number: int
    location_id: str
    characters: tuple[Character, ...]
    world_flags: Mapping[str, FlagValue]

    def __post_init__(self):
        object.__setattr__(self, "characters", tuple(self.characters))
        object.__setattr__(self, "world_flags", MappingProxyType(dict(self.world_flags)))

    def __deepcopy__(self, memo):
        return self


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
