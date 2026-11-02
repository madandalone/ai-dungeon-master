import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Generic, TypeVar
from uuid import uuid4

from engine.models import GameState, PlayerAction

T = TypeVar("T")

DEFAULT_REMEMBERED_PER_SESSION = 256


def make_player_action(
    state: GameState,
    character_id: str,
    text: str,
    action_id: str | None = None,
    expected_revision: int | None = None,
) -> PlayerAction:
    """Build the action from what the client sent, so retries and stale clients are visible to the Engine.

    A client-supplied action_id is kept (it identifies a retry); without one a new id is generated.
    A client-supplied expected_revision is kept, so a stale client is rejected as STALE_REVISION; legacy
    clients that send none are assumed to be up to date.
    """
    return PlayerAction(
        action_id=action_id or str(uuid4()),
        session_id=state.session_id,
        character_id=character_id,
        text=text,
        expected_revision=state.revision if expected_revision is None else expected_revision,
    )


class IdempotentExecutor(Generic[T]):
    """Runs turn work one at a time per session and replays the stored result of a repeated action.

    `run(session, key, fn)` calls `fn` while holding the session's lock, so everything inside it (load the
    state, play the turn, save the state) is atomic with respect to other turns of that session; different
    sessions never block each other. If `key` already produced a remembered result, `fn` is not called and
    that result is returned with replayed=True.

    Only results for which `remember(result)` is true are stored: pass "was the turn committed". A rejected
    turn changed nothing and may legitimately succeed on retry; an exception stores nothing and releases the lock.
    Remembered results are bounded per session (oldest forgotten first) and live in this process's memory.
    """

    def __init__(self, max_remembered: int = DEFAULT_REMEMBERED_PER_SESSION):
        if max_remembered < 1:
            raise ValueError("max_remembered must be at least 1")
        self._max = max_remembered
        self._registry_lock = threading.Lock()
        self._session_locks: dict[str, threading.Lock] = {}
        self._remembered: dict[str, OrderedDict[str, T]] = {}

    def run(
        self, session_id: str, key: str, fn: Callable[[], T], remember: Callable[[T], bool] = lambda result: True
    ) -> tuple[T, bool]:
        with self._lock_for(session_id):
            results = self._remembered.setdefault(session_id, OrderedDict())
            if key in results:
                return results[key], True
            result = fn()
            if remember(result):
                results[key] = result
                while len(results) > self._max:
                    results.popitem(last=False)
            return result, False

    def forget(self, session_id: str) -> None:
        """Drop everything kept for a deleted session."""
        with self._registry_lock:
            lock = self._session_locks.pop(session_id, None)
        if lock is not None:
            with lock:
                self._remembered.pop(session_id, None)

    def _lock_for(self, session_id: str) -> threading.Lock:
        with self._registry_lock:
            return self._session_locks.setdefault(session_id, threading.Lock())
