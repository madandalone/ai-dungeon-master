from typing import List, Optional
from pydantic import BaseModel, Field

# --- Входной контекст от Game Engine и Rules Service ---
class CharacterState(BaseModel):
    name: str
    hp: int
    max_hp: int
    inventory: List[str]

class GameState(BaseModel):
    location: str
    active_characters: List[CharacterState]
    room_context: str

class TurnRequest(BaseModel):
    rules_summary: str        # Краткие правила игры
    hidden_plot: str          # Скрытый сюжет (видит только Мастер)
    game_state: GameState     # Текущее состояние мира и героев
    player_action: str        # Текст действия игрока


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