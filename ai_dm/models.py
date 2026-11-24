from typing import Annotated, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, RootModel, model_validator


AbilityName = Literal[
    "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma",
]
DifficultyClass = Literal[5, 10, 15, 20, 25, 30]

# --- Входной контекст от Game Engine и Rules Service ---
class CharacterState(BaseModel):
    name: str
    hp: int
    max_hp: int
    inventory: List[str]
    abilities: dict[str, int] = Field(default_factory=dict)
    # Совместимость с первым контрактом AI DM, где передавались готовые модификаторы.
    stats: Dict[str, int] = Field(default_factory=dict)

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


class CheckResult(BaseModel):
    """Неизменяемый итог проверки, выполненной Game Engine."""

    ability: Optional[AbilityName] = None
    stat: Optional[str] = None
    difficulty: int = Field(ge=5, le=30)
    roll: int = Field(ge=1, le=20)
    modifier: int
    total: int
    success: bool

    @model_validator(mode="after")
    def _ability_or_legacy_stat(self) -> "CheckResult":
        if self.ability is None and not self.stat:
            raise ValueError("ability or stat is required")
        if self.stat is None and self.ability is not None:
            self.stat = self.ability
        return self


class TurnRequest(BaseModel):
    rules_summary: str        # Краткие правила игры
    hidden_plot: str          # Скрытый сюжет (видит только Мастер)
    game_state: GameState     # Текущее состояние мира и героев
    player_action: str        # Текст действия игрока
    history: List[TurnHistoryEntry] = Field(default_factory=list)
    # Имя персонажа, совершающего действие; пустая строка — не указан (старые вызовы).
    acting_character: str = ""
    # Второй этап хода: результат уже выполненной проверки; None — проверки ещё не было.
    check_result: Optional[CheckResult] = None

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

class CheckRequest(BaseModel):
    """Запрос проверки характеристики; бросок выполняет Game Engine, а не модель."""

    stat: str = Field(
        description="Название характеристики действующего персонажа, точно как в его stats"
    )
    difficulty: int = Field(description="Сложность проверки: целое число от 5 до 30")


class TurnResponse(BaseModel):
    # Решение о проверке стоит первым: модель принимает его до написания повествования.
    requires_check: bool = Field(
        False, description="true, если исход действия должен решить бросок кубика в Game Engine"
    )
    check: Optional[CheckRequest] = Field(
        None, description="Параметры проверки при requires_check: true, иначе null"
    )
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


class SkillCheckProposal(BaseModel):
    ability: AbilityName
    difficulty: DifficultyClass
    reason: str = Field(min_length=1, max_length=240)


class CheckDecision(BaseModel):
    """First, deliberately small step of planning: does this action need an Engine roll, and which one.

    It carries no narration, no state changes and no nested response, so even a small local model has
    one thing to decide. When `needs_check` is false the optional fields are cleared.
    """

    needs_check: bool = Field(description="true, если исход действия должен решить бросок кубика в Game Engine")
    ability: Optional[AbilityName] = Field(
        None, description="Характеристика действующего персонажа при needs_check: true, иначе null"
    )
    difficulty: Optional[DifficultyClass] = Field(
        None, description="Сложность 5, 10, 15, 20, 25 или 30 при needs_check: true, иначе null"
    )
    reason: str = Field("", max_length=240, description="Одно короткое предложение: почему нужна проверка")

    @model_validator(mode="after")
    def _complete_when_needed(self) -> "CheckDecision":
        if not self.needs_check:
            self.ability, self.difficulty, self.reason = None, None, ""
            return self
        missing = [name for name in ("ability", "difficulty") if getattr(self, name) is None]
        if not self.reason.strip():
            missing.append("reason")
        if missing:
            raise ValueError("needs_check true requires " + ", ".join(missing))
        return self

    def proposal(self) -> "SkillCheckProposal":
        return SkillCheckProposal(ability=self.ability, difficulty=self.difficulty, reason=self.reason.strip())


class ResolvedTurnPlan(BaseModel):
    status: Literal["resolved"]
    response: TurnResponse
    check: Optional[SkillCheckProposal] = None

    @model_validator(mode="after")
    def _discard_check(self) -> "ResolvedTurnPlan":
        self.check = None
        return self


class PendingTurnPlan(BaseModel):
    status: Literal["check"]
    response: Optional[TurnResponse] = None
    check: SkillCheckProposal

    @model_validator(mode="after")
    def _discard_speculative_response(self) -> "PendingTurnPlan":
        # Consequences proposed before Engine rolls are never authoritative.
        self.response = None
        return self


TurnPlanVariant = Annotated[
    ResolvedTurnPlan | PendingTurnPlan,
    Field(discriminator="status"),
]


class TurnPlan(RootModel[TurnPlanVariant]):
    """Strict planning union whose JSON Schema requires the matching payload."""

    def __init__(self, **data):
        super().__init__(root=data)

    @property
    def status(self):
        return self.root.status

    @property
    def response(self):
        return self.root.response

    @response.setter
    def response(self, value):
        self.root.response = value

    @property
    def check(self):
        return self.root.check

    @check.setter
    def check(self, value):
        self.root.check = value


class ResolvedCheckResolution(BaseModel):
    status: Literal["resolved"]
    outcome: Literal["success", "failure"]
    response: TurnResponse
    check: Optional[SkillCheckProposal] = None

    @model_validator(mode="after")
    def _discard_check(self) -> "ResolvedCheckResolution":
        self.check = None
        return self


class ContinuedCheckResolution(BaseModel):
    status: Literal["check"]
    outcome: Literal["success"]
    response: Optional[TurnResponse] = None
    check: SkillCheckProposal

    @model_validator(mode="after")
    def _discard_speculative_response(self) -> "ContinuedCheckResolution":
        self.response = None
        return self


CheckResolutionVariant = Annotated[
    ResolvedCheckResolution | ContinuedCheckResolution,
    Field(discriminator="status"),
]


class CheckResolution(RootModel[CheckResolutionVariant]):
    """Strict post-roll union; a failed check cannot request another check."""

    def __init__(self, **data):
        super().__init__(root=data)

    @property
    def status(self):
        return self.root.status

    @property
    def outcome(self):
        return self.root.outcome

    @property
    def response(self):
        return self.root.response

    @response.setter
    def response(self, value):
        self.root.response = value

    @property
    def check(self):
        return self.root.check

    @check.setter
    def check(self, value):
        self.root.check = value


# --- Модели для функции старта кампании ---
CAMPAIGN_TITLE_MAX_CHARS = 80
HIDDEN_PLOT_MAX_CHARS = 900
STARTING_LOCATION_MAX_CHARS = 100
ROOM_CONTEXT_MAX_CHARS = 350
INTRO_NARRATIVE_MAX_CHARS = 600


class CampaignStartRequest(BaseModel):
    setting_theme: str = Field(description="Тема или пожелание к приключению (напр. 'Заброшенная крипта')")
    opening_hook: str = Field(
        "", max_length=500,
        description="Начальная ситуация или пожелание пользователя к завязке",
    )
    characters: List[CharacterState]
    monster_encounter: str = Field("goblin", description="Индекс монстра из D&D 5e API (напр. goblin, skeleton, wolf)")

class CampaignStartResponse(BaseModel):
    campaign_title: str = Field(
        max_length=CAMPAIGN_TITLE_MAX_CHARS,
        description="Короткое название приключения",
    )
    hidden_plot: str = Field(
        max_length=HIDDEN_PLOT_MAX_CHARS,
        description="Компактная скрытая тайна сюжета, известная только Мастеру",
    )
    starting_location: str = Field(
        max_length=STARTING_LOCATION_MAX_CHARS,
        description="Короткое название первой локации",
    )
    room_context: str = Field(
        max_length=ROOM_CONTEXT_MAX_CHARS,
        description="Краткое описание наблюдаемой обстановки стартовой комнаты",
    )
    intro_narrative: str = Field(
        max_length=INTRO_NARRATIVE_MAX_CHARS,
        description="Короткая завязка истории для игроков",
    )
