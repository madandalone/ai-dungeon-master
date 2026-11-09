
import os
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ai_dm.models import StateDelta, TurnHistoryEntry, TurnRequest, TurnResponse
from backend.storage import SQLiteSessionStore
from engine import add_character, apply_action, to_public_dict
from engine.commands import AdjustHealth
from engine.dice import Dice, LocalRandomSource
from engine.models import Character, GameState, InventoryItem, PlayerAction
from integration import IntegrationError, TurnOrchestrator
from integration.factory import build_orchestrator
from integration.turns import IdempotentExecutor, make_player_action

app = FastAPI(title="AI Dungeon Master API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

class GameSessionCreate(BaseModel):
    name: str


class GameSession(BaseModel):
    id: str
    name: str


class PlayerActionRequest(BaseModel):
    character_id: str
    text: str
    health_delta: int


class PlayerTurnRequest(BaseModel):
    character_id: str
    text: str
    action_id: str | None = None
    expected_revision: int | None = None


class CharacterCreateRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=60)
    concept: str = Field(default="", max_length=120)
    max_hp: int = Field(ge=1, le=999)
    stats: dict[str, int] = Field(default_factory=dict)
    starting_item: str = Field(default="", max_length=60)


class FakeRulesProvider:
    def get_rules(self, query: str) -> str:
        return "Test rule: the hero loses 1 HP."


class FakeDungeonMaster:
    def process_turn(self, request: TurnRequest) -> TurnResponse:
        character = request.game_state.active_characters[0]

        return TurnResponse(
            narrative=f"{character.name} совершает действие: {request.player_action}",
            state_updates=[
                StateDelta(
                    character_name=character.name,
                    hp_change=-1,
                )
            ],
            new_location=None,
            dm_thoughts="Test turn processed.",
        )


sessions: dict[str, GameSession] = {}
game_states: dict[str, GameState] = {}
game_histories: dict[str, list[TurnHistoryEntry]] = {}
turn_executor = IdempotentExecutor()

@lru_cache(maxsize=1)
def get_orchestrator() -> TurnOrchestrator:
    return build_orchestrator()

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/sessions", response_model=GameSession)
def create_session(session_data: GameSessionCreate):
    session = GameSession(
        id=str(uuid4()),
        name=session_data.name,
    )

    sessions[session.id] = session

    characters = (
        Character(
            id="hero",
            name="Hero",
            concept="Adventurer",
            hp=10,
            max_hp=10,
            stats={"strength": 2},
            inventory=(),
        ),
    )

    game_states[session.id] = GameState(
        session_id=session.id,
        revision=0,
        turn_number=0,
        location_id="start",
        characters=characters,
        world_flags={},
    )
    game_histories[session.id] = []
    get_session_store().save_session(
        session.id,
        session.name,
        game_states[session.id],
    )
    return session


@lru_cache(maxsize=1)
def get_session_store() -> SQLiteSessionStore:
    db_path = Path(os.getenv("GAME_DB_PATH", "data/game_sessions.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteSessionStore(db_path)


def ensure_session_loaded(session_id: str) -> None:
    """Restore a session from SQLite if it is missing from memory."""
    if session_id in game_states:
        return

    restored = get_session_store().load_session(session_id)
    if restored is None:
        raise HTTPException(
            status_code=404,
            detail="Session not found",
        )

    name, state, history = restored
    sessions[session_id] = GameSession(
        id=session_id,
        name=name,
    )
    game_states[session_id] = state
    game_histories[session_id] = history


def public_state(session_id: str, include_history: bool = True) -> dict:
    history = game_histories[session_id]
    log = []
    if include_history:
        for entry in history:
            log.append({
                "kind": "player",
                "character_id": entry.character_id,
                "text": entry.player_action,
            })
            if entry.narrative.strip():
                log.append({
                    "kind": "master",
                    "character_id": "",
                    "text": entry.narrative,
                })
    return to_public_dict(
        game_states[session_id],
        session_name=sessions[session_id].name,
        log=log,
    )

@app.get("/sessions/{session_id}/state")
def get_session_state(session_id: str):
    ensure_session_loaded(session_id)
    return public_state(session_id)


@app.post("/sessions/{session_id}/characters")
def create_character(session_id: str, character_data: CharacterCreateRequest):
    operation_id = str(uuid4())

    def execute_creation():
        ensure_session_loaded(session_id)
        item_name = character_data.starting_item.strip()
        inventory = ()
        if item_name:
            inventory = (
                InventoryItem(
                    item_id=str(uuid4()),
                    name=item_name,
                    quantity=1,
                ),
            )
        character = Character(
            id=str(uuid4()),
            name=character_data.name.strip(),
            concept=character_data.concept.strip(),
            hp=character_data.max_hp,
            max_hp=character_data.max_hp,
            stats={name.strip(): value for name, value in character_data.stats.items()},
            inventory=inventory,
        )
        result = add_character(
            game_states[session_id],
            character,
            character_data.expected_revision,
        )
        if not result.accepted:
            error = result.errors[0]
            status = 409 if error.code == "STALE_REVISION" else 422
            raise HTTPException(
                status_code=status,
                detail={"code": error.code, "message": error.message},
            )

        get_session_store().save_state(session_id, result.state)
        game_states[session_id] = result.state
        return public_state(session_id)

    response, _ = turn_executor.run(
        session_id,
        operation_id,
        execute_creation,
        remember=lambda result: False,
    )
    return response


@app.post("/sessions/{session_id}/actions")
def process_action(session_id: str, action_data: PlayerActionRequest):
    action_id = str(uuid4())

    def execute_action():
        ensure_session_loaded(session_id)
        state = game_states[session_id]

        action = PlayerAction(
            action_id=action_id,
            session_id=session_id,
            character_id=action_data.character_id,
            text=action_data.text,
            expected_revision=state.revision,
        )

        command = AdjustHealth(
            character_id=action_data.character_id,
            delta=action_data.health_delta,
        )

        result = apply_action(
            state=state,
            action=action,
            commands=[command],
            dice=Dice(LocalRandomSource()),
        )

        if result.accepted:
            get_session_store().save_state(session_id, result.state)
            game_states[session_id] = result.state

        return {
            "accepted": result.accepted,
            "state": public_state(session_id, include_history=False),
            "revision": result.state.revision,
            "turn_number": result.state.turn_number,
            "errors": [
                {"code": error.code, "message": error.message}
                for error in result.errors
            ],
        }

    response, _ = turn_executor.run(
        session_id,
        action_id,
        execute_action,
        remember=lambda result: False,
    )
    return response

@app.post("/sessions/{session_id}/turns")
def process_turn(
    session_id: str,
    turn_data: PlayerTurnRequest,
    orchestrator: TurnOrchestrator = Depends(get_orchestrator),
):
    action_id = turn_data.action_id or str(uuid4())

    def execute_turn():
        # Загружаем состояние внутри блокировки сессии.
        ensure_session_loaded(session_id)

        # Проверяем, не был ли этот ход выполнен ранее.
        # Работает даже после перезапуска Backend.
        saved_response = get_session_store().load_processed_turn(
            session_id,
            action_id,
        )
        if saved_response is not None:
            return saved_response

        state = game_states[session_id]

        action = make_player_action(
            state=state,
            character_id=turn_data.character_id,
            text=turn_data.text,
            action_id=action_id,
            expected_revision=turn_data.expected_revision,
        )

        try:
            outcome = orchestrator.play_turn(
                state=state,
                action=action,
                hidden_plot="Test campaign",
                dice=Dice(LocalRandomSource()),
                history=game_histories[session_id],
            )
        except IntegrationError as exc:
            raise HTTPException(
                status_code=502,
                detail=str(exc),
            ) from exc

        if outcome.accepted:
            entry = TurnHistoryEntry(
                character_id=turn_data.character_id,
                player_action=turn_data.text,
                narrative=outcome.narration or "",
                dm_notes=outcome.dm_notes or "",
            )

            # Формируем ответ для сохранения в SQLite.
            response = {
                "accepted": True,
                "narration": outcome.narration,
                "state": to_public_dict(
                    outcome.state,
                    session_name=sessions[session_id].name,
                    log=[],
                ),
                "revision": outcome.state.revision,
                "turn_number": outcome.state.turn_number,
                "errors": [],
            }

            # Сохраняем состояние, историю и результат хода
            # в одной транзакции.
            get_session_store().save_turn(
                session_id,
                outcome.state,
                entry,
                action_id=action_id,
                response=response,
            )

            # Обновляем состояние в оперативной памяти
            # только после успешного сохранения в SQLite.
            game_states[session_id] = outcome.state
            game_histories[session_id].append(entry)

            return response

        # Отклонённый ход не сохраняем в processed_turns:
        # его можно будет повторить.
        return {
            "accepted": False,
            "narration": outcome.narration,
            "state": public_state(session_id, include_history=False),
            "revision": outcome.state.revision,
            "turn_number": outcome.state.turn_number,
            "errors": [
                {"code": error.code, "message": error.message}
                for error in outcome.errors
            ],
        }

    result, _replayed = turn_executor.run(
        session_id,
        action_id,
        execute_turn,
        remember=lambda response: response["accepted"],
    )

    return result