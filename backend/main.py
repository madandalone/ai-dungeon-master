from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel


app = FastAPI(title="AI Dungeon Master API")


class GameSessionCreate(BaseModel):
    name: str


class GameSession(BaseModel):
    id: str
    name: str


sessions: dict[str, GameSession] = {}


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

    return session