"""SQLite persistence for game sessions, immutable engine state and private turn history."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ai_dm.models import TurnHistoryEntry
from engine.models import Character, GameState, InventoryItem


class SQLiteSessionStore:
    """Small synchronous store; each operation owns its connection and transaction."""

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS game_states (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS game_histories (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (session_id, position)
                );
            """)

    @staticmethod
    def _state_to_json(state: GameState) -> str:
        payload = {
            "session_id": state.session_id,
            "revision": state.revision,
            "turn_number": state.turn_number,
            "location_id": state.location_id,
            "characters": [
                {
                    "id": character.id,
                    "name": character.name,
                    "concept": character.concept,
                    "hp": character.hp,
                    "max_hp": character.max_hp,
                    "stats": dict(character.stats),
                    "inventory": [asdict(item) for item in character.inventory],
                }
                for character in state.characters
            ],
            "world_flags": dict(state.world_flags),
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _state_from_json(raw: str) -> GameState:
        data: dict[str, Any] = json.loads(raw)
        characters = tuple(
            Character(
                id=item["id"],
                name=item["name"],
                concept=item["concept"],
                hp=item["hp"],
                max_hp=item["max_hp"],
                stats=item["stats"],
                inventory=tuple(InventoryItem(**inv) for inv in item["inventory"]),
            )
            for item in data["characters"]
        )
        return GameState(
            session_id=data["session_id"],
            revision=data["revision"],
            turn_number=data["turn_number"],
            location_id=data["location_id"],
            characters=characters,
            world_flags=data["world_flags"],
        )

    def save_session(self, session_id: str, name: str, state: GameState) -> None:
        """Create a session and its initial state atomically."""
        if state.session_id != session_id:
            raise ValueError("Session ID does not match state")
        with self._connect() as connection:
            connection.execute("INSERT INTO sessions (id, name) VALUES (?, ?)", (session_id, name))
            connection.execute(
                "INSERT INTO game_states (session_id, payload) VALUES (?, ?)",
                (session_id, self._state_to_json(state)),
            )

    def load_session(self, session_id: str) -> tuple[str, GameState, list[TurnHistoryEntry]] | None:
        """Return (name, state, private history), or None for unknown sessions."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT s.name, g.payload FROM sessions s "
                "JOIN game_states g ON g.session_id = s.id WHERE s.id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            history_rows = connection.execute(
                "SELECT payload FROM game_histories WHERE session_id = ? ORDER BY position",
                (session_id,),
            ).fetchall()
        history = [TurnHistoryEntry(**json.loads(item[0])) for item in history_rows]
        return row[0], self._state_from_json(row[1]), history

    def save_turn(self, session_id: str, state: GameState, entry: TurnHistoryEntry) -> None:
        """Atomically save the new state and one history entry."""
        if state.session_id != session_id:
            raise ValueError("Session ID does not match state")

        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM game_states WHERE session_id = ?",
                (session_id,),
            ).fetchone()

            if row is None:
                raise KeyError(f"Unknown session: {session_id}")

            previous = self._state_from_json(row[0])
            if state.revision != previous.revision + 1:
                raise ValueError("Unexpected state revision")

            position = connection.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 "
                "FROM game_histories WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]

            connection.execute(
                "UPDATE game_states SET payload = ? WHERE session_id = ?",
                (self._state_to_json(state), session_id),
            )
            connection.execute(
                "INSERT INTO game_histories "
                "(session_id, position, payload) VALUES (?, ?, ?)",
                (
                    session_id,
                    position,
                    json.dumps({
                        "character_id": entry.character_id,
                        "player_action": entry.player_action,
                        "narrative": entry.narrative,
                        "dm_notes": entry.dm_notes,
                    }, ensure_ascii=False),
                ),
            )

    def save_state(self, session_id: str, state: GameState) -> None:
        """Persist a state change without adding a DM history entry."""
        if state.session_id != session_id:
            raise ValueError("Session ID does not match state")

        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE game_states SET payload = ? "
                "WHERE session_id = ? "
                "AND json_extract(payload, '$.revision') = ?",
                (
                    self._state_to_json(state),
                    session_id,
                    state.revision - 1,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Session not found or unexpected revision")
