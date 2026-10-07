from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel
from engine.models import Character, GameState, PlayerAction
from engine import apply_action
from engine.commands import AdjustHealth

app = FastAPI(title="AI Dungeon Master API")


class GameSessionCreate(BaseModel):
    name: str


class GameSession(BaseModel):
    id: str
    name: str


sessions: dict[str, GameSession] = {}
game_states: dict[str, GameState] = {}

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
    characters=(
    Character(
        id="hero",
        name="Hero",
        concept="Adventurer",
        hp=10,
        max_hp=10,
        stats={"strength": 2},
        inventory=(),
    ),
),
    game_states[session.id] = GameState(
    session_id=session.id,
    revision=0,
    turn_number=0,
    location_id="start",
    characters=characters,
    world_flags={},
)
    return session