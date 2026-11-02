
from uuid import uuid4
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from engine import apply_action, to_public_dict
from engine.commands import AdjustHealth
from engine.dice import Dice, LocalRandomSource
from engine.models import Character, GameState, PlayerAction
from integration import IntegrationError, TurnOrchestrator
from ai_dm.models import StateDelta, TurnRequest, TurnResponse
from fastapi.middleware.cors import CORSMiddleware
from ai_dm.models import TurnHistoryEntry
from functools import lru_cache

from backend.storage import SQLiteSessionStore
from fastapi import Depends
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
        raise HTTPException(status_code=404, detail="Session not found")

    name, state, history = restored
    sessions[session_id] = GameSession(id=session_id, name=name)
    game_states[session_id] = state
    game_histories[session_id] = history

@app.get("/sessions/{session_id}/state")
def get_session_state(session_id: str):
    ensure_session_loaded(session_id)
    return to_public_dict(
        game_states[session_id],
        session_name=sessions[session_id].name,
    )


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
            "state": to_public_dict(
                result.state,
                session_name=sessions[session_id].name,
            ),
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
        # Важно читать состояние внутри блокировки сессии.
        ensure_session_loaded(session_id)
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
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        if outcome.accepted:
            entry = TurnHistoryEntry(
                player_action=turn_data.text,
                narrative=outcome.narration or "",
                dm_notes=outcome.dm_notes or "",
            )

            get_session_store().save_turn(
                session_id,
                outcome.state,
                entry,
            )

            game_states[session_id] = outcome.state
            game_histories[session_id].append(entry)

        return {
            "accepted": outcome.accepted,
            "narration": outcome.narration,
            "state": to_public_dict(
                outcome.state,
                session_name=sessions[session_id].name,
            ),
            "revision": outcome.state.revision,
            "turn_number": outcome.state.turn_number,
            "errors": [
                {"code": error.code, "message": error.message}
                for error in outcome.errors
            ],
        }

    result, replayed = turn_executor.run(
        session_id,
        action_id,
        execute_turn,
        remember=lambda response: response["accepted"],
    )

    return result
