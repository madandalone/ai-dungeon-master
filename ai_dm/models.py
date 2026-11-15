from typing import List, Literal, Optional
from pydantic import BaseModel, Field, model_validator

# --- Входной контекст от Game Engine и Rules Service ---
class CharacterState(BaseModel):
    name: str
    hp: int
    max_hp: int
    inventory: List[str]
    abilities: dict[str, int] = Field(default_factory=dict)

class GameState(BaseModel):
    location: str
    active_characters: List[CharacterState]
    room_context: str

class TurnHistoryEntry(BaseModel):
    """Один принятый ход; dm_notes доступны только мастеру."""

    character_id: str = ""
    player_action: str
    narrative: str
    dm_notes: str = ""


class TurnRequest(BaseModel):
    rules_summary: str        # Краткие правила игры
    hidden_plot: str          # Скрытый сюжет (видит только Мастер)
    game_state: GameState     # Текущее состояние мира и героев
    player_action: str        # Текст действия игрока
    history: List[TurnHistoryEntry] = Field(default_factory=list)
    # Имя персонажа, совершающего действие; пустая строка — не указан (старые вызовы).
    acting_character: str = ""

    @model_validator(mode="after")
    def _acting_character_is_active(self) -> "TurnRequest":
        names = {character.name for character in self.game_state.active_characters}
        if self.acting_character and self.acting_character not in names:
            raise ValueError("acting_character must be one of game_state.active_characters names")
        return self


# --- Структурированный ответ от AI Dungeon Master ---
class StateDelta(BaseModel):
    character_name: str
    hp_change: int = 0
    items_added: List[str] = Field(default_factory=list)
    items_removed: List[str] = Field(default_factory=list)

class TurnResponse(BaseModel):
    narrative: str = Field(
        description="Художественное описание последствий хода для игрока (атмосферно, от 2-го лица)"
    )
    state_updates: List[StateDelta] = Field(
        description="Численные и инвентарные изменения персонажей для Game Engine"
    )
    new_location: Optional[str] = Field(
        None, description="Новая локация, если игрок переместился"
    )
    dm_thoughts: str = Field(
        description="Скрытые заметки мастера: как изменился скрытый сюжет или отношение окружения"
    )


AbilityName = Literal[
    "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma",
]
DifficultyClass = Literal[5, 10, 15, 20, 25, 30]


class SkillCheckProposal(BaseModel):
    ability: AbilityName
    difficulty: DifficultyClass
    reason: str = Field(min_length=1, max_length=240)


class TurnPlan(BaseModel):
    status: Literal["resolved", "check"]
    response: Optional[TurnResponse] = None
    check: Optional[SkillCheckProposal] = None

    @model_validator(mode="after")
    def _matching_payload(self) -> "TurnPlan":
        if self.status == "resolved":
            if self.response is None:
                raise ValueError("resolved plan requires response")
            self.check = None
        else:
            if self.check is None:
                raise ValueError("check plan requires check")
            # A small local model can echo a speculative response alongside the check.
            # It is deliberately discarded before it can reach Engine.
            self.response = None
        return self


class CheckResult(BaseModel):
    ability: AbilityName
    roll: int = Field(ge=1, le=20)
    modifier: int
    total: int
    difficulty: DifficultyClass
    success: bool


class CheckResolution(BaseModel):
    status: Literal["resolved", "check"]
    outcome: Literal["success", "failure"]
    response: Optional[TurnResponse] = None
    check: Optional[SkillCheckProposal] = None

    @model_validator(mode="after")
    def _matching_payload(self) -> "CheckResolution":
        if self.status == "resolved":
            if self.response is None:
                raise ValueError("resolved check requires response")
            self.check = None
        else:
            if self.check is None:
                raise ValueError("continued check requires check")
            self.response = None
        if self.outcome == "failure" and self.status != "resolved":
            raise ValueError("a failed check must resolve the turn")
        return self


# --- Модели для функции старта кампании ---
class CampaignStartRequest(BaseModel):
    setting_theme: str = Field(description="Тема или пожелание к приключению (напр. 'Заброшенная крипта')")
    characters: List[CharacterState]
    monster_encounter: str = Field("goblin", description="Индекс монстра из D&D 5e API (напр. goblin, skeleton, wolf)")

class CampaignStartResponse(BaseModel):
    campaign_title: str = Field(description="Название приключения")
    hidden_plot: str = Field(description="Скрытая тайна сюжета (известна только Мастеру)")
    starting_location: str = Field(description="Название первой локации")
    room_context: str = Field(description="Описание обстановки стартовой комнаты")
    intro_narrative: str = Field(description="Вводный атмосферный текст для игроков (завязка истории)")
