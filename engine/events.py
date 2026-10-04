from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from engine.models import ErrorCode, FlagValue


@dataclass(frozen=True)
class HealthChanged:
    action_id: str
    sequence: int
    character_id: str
    previous_hp: int
    new_hp: int
    delta: int


@dataclass(frozen=True)
class ItemAdded:
    action_id: str
    sequence: int
    character_id: str
    item_id: str
    quantity: int
    new_quantity: int


@dataclass(frozen=True)
class ItemRemoved:
    action_id: str
    sequence: int
    character_id: str
    item_id: str
    quantity: int
    new_quantity: int


@dataclass(frozen=True)
class LocationChanged:
    action_id: str
    sequence: int
    previous_location_id: str
    location_id: str


@dataclass(frozen=True)
class StatChanged:
    action_id: str
    sequence: int
    character_id: str
    stat: str
    previous_value: int
    new_value: int
    delta: int


@dataclass(frozen=True)
class WorldFlagChanged:
    action_id: str
    sequence: int
    key: str
    previous_value: FlagValue | None
    value: FlagValue


@dataclass(frozen=True)
class SkillCheckResolved:
    action_id: str
    sequence: int
    character_id: str
    stat: str
    roll: int
    modifier: int
    total: int
    difficulty: int
    success: bool


@dataclass(frozen=True)
class ActionRejected:
    action_id: str
    sequence: int
    code: ErrorCode


GameEvent = Union[
    HealthChanged,
    ItemAdded,
    ItemRemoved,
    LocationChanged,
    StatChanged,
    WorldFlagChanged,
    SkillCheckResolved,
    ActionRejected,
]
