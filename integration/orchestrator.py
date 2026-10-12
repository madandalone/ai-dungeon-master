from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ai_dm.models import TurnRequest
from engine import apply_action
from engine.dice import Dice
from engine.models import EngineError, GameState, PlayerAction
from integration.errors import DungeonMasterError
from integration.mapping import to_commands, to_dm_state
from integration.ports import DungeonMaster, RulesProvider

NO_RULES = "No specific rules found for this action."


def _turn_request_supports_history() -> bool:
    """Check whether the installed ai_dm.models.TurnRequest has a history field."""
    fields = getattr(TurnRequest, "model_fields", None) or getattr(TurnRequest, "__fields__", None) or {}
    return "history" in fields


def _normalize_history(history: Sequence[Any]) -> list[Any]:
    """Convert dict entries to TurnHistoryEntry; raise if history is not supported."""
    if not _turn_request_supports_history():
        raise ValueError("TurnRequest does not support history in this version of ai_dm.models")
    from ai_dm.models import TurnHistoryEntry

    normalized = []
    for entry in history:
        if isinstance(entry, TurnHistoryEntry):
            normalized.append(entry)
        elif isinstance(entry, dict):
            normalized.append(TurnHistoryEntry(**entry))
        else:
            raise TypeError(f"History entry must be TurnHistoryEntry or dict, got {type(entry)}")
    return normalized


@dataclass(frozen=True)
class TurnOutcome:
    accepted: bool
    state: GameState
    events: tuple[object, ...]
    errors: tuple[EngineError, ...]
    narration: str | None
    dm_notes: str | None = None  # private: Backend persists it, never sends it to the Frontend


class TurnOrchestrator:
    """One game turn: rules -> DM -> commands -> engine. Holds no state between turns."""

    def __init__(self, dm: DungeonMaster, rules: RulesProvider):
        self._dm = dm
        self._rules = rules

    def play_turn(
        self,
        state: GameState,
        action: PlayerAction,
        hidden_plot: str,
        dice: Dice,
        room_context: str = "",
        history: Sequence[Any] | None = None,
    ) -> TurnOutcome:
        precheck = apply_action(state, action, [], dice)
        if not precheck.accepted:
            return TurnOutcome(False, precheck.state, precheck.events, precheck.errors, None)

        request_kwargs = {
            "rules_summary": self._rules.get_rules(action.text) or NO_RULES,
            "hidden_plot": hidden_plot,
            "game_state": to_dm_state(state, room_context),
            "player_action": action.text,
        }
        if history is not None:
            request_kwargs["history"] = _normalize_history(history)

        request = TurnRequest(**request_kwargs)
        try:
            response = self._dm.process_turn(request)
        except Exception as exc:
            raise DungeonMasterError(f"Dungeon Master failed: {exc}") from exc

        result = apply_action(state, action, to_commands(state, response), dice)
        if not result.accepted:
            return TurnOutcome(False, result.state, result.events, result.errors, None)
        return TurnOutcome(True, result.state, result.events, (), response.narrative, response.dm_thoughts)
