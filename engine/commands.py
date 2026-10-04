from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class AdjustHealth:
    character_id: str
    delta: int


@dataclass(frozen=True)
class AddItem:
    character_id: str
    item_id: str
    quantity: int


@dataclass(frozen=True)
class RemoveItem:
    character_id: str
    item_id: str
    quantity: int


@dataclass(frozen=True)
class SetLocation:
    location_id: str


@dataclass(frozen=True)
class AdjustStat:
    character_id: str
    stat: str
    delta: int


@dataclass(frozen=True)
class SetWorldFlag:
    key: str
    value: object


@dataclass(frozen=True)
class SkillCheck:
    character_id: str
    stat: str
    difficulty: int


GameCommand = Union[
    AdjustHealth,
    AddItem,
    RemoveItem,
    SetLocation,
    AdjustStat,
    SetWorldFlag,
    SkillCheck,
]

COMMAND_TYPES = (
    AdjustHealth,
    AddItem,
    RemoveItem,
    SetLocation,
    AdjustStat,
    SetWorldFlag,
    SkillCheck,
)
