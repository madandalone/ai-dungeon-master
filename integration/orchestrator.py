import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ai_dm.models import TurnHistoryEntry, TurnRequest, TurnResponse
from engine import apply_action
from engine.dice import Dice
from engine.models import EngineError, GameState, PlayerAction
from integration.errors import DungeonMasterError, UnmappableResponse
from integration.mapping import to_commands, to_dm_state
from integration.ports import DungeonMaster, RulesProvider

NO_RULES = "No specific rules found for this action."

logger = logging.getLogger(__name__)


def _normalize_history(history: Sequence[Any]) -> list[TurnHistoryEntry]:
    entries = []
    for entry in history:
        if isinstance(entry, TurnHistoryEntry):
            entries.append(entry)
        elif isinstance(entry, dict):
            entries.append(TurnHistoryEntry(**entry))
        else:
            raise TypeError(f"History entry must be TurnHistoryEntry or dict, got {type(entry).__name__}")
    return entries


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


@dataclass(frozen=True)
class TurnOutcome:
    accepted: bool
    state: GameState
    events: tuple[object, ...]
    errors: tuple[EngineError, ...]
    narration: str | None
    dm_notes: str | None = None  # private: Backend persists it, never sends it to the Frontend
    rules_failed: bool = False  # the Rules Service raised; the turn was played without rules


class TurnOrchestrator:
    """One game turn: rules -> DM -> commands -> engine. Holds no state between turns.

    Rules are auxiliary: if the Rules Service fails, the turn continues without them (logged, flagged in
    TurnOutcome.rules_failed). A DM failure is fatal for the turn (DungeonMasterError, nothing committed).
    Logs carry identifiers, outcome codes and timings only - never player text, narration or the hidden plot.
    """

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
            return self._rejected_before_dm(action, precheck)

        started = time.perf_counter()
        rules, rules_failed = self._fetch_rules(action)
        rules_ms = _elapsed_ms(started)

        request = TurnRequest(
            rules_summary=rules,
            hidden_plot=hidden_plot,
            game_state=to_dm_state(state, room_context),
            player_action=action.text,
            history=_normalize_history(history or ()),
        )
        started = time.perf_counter()
        response = self._ask_dm(action, request)
        dm_ms = _elapsed_ms(started)

        try:
            commands = to_commands(state, response)
        except UnmappableResponse:
            logger.error("turn unmappable session=%s action=%s dm_ms=%d", action.session_id, action.action_id, dm_ms)
            raise
        result = apply_action(state, action, commands, dice)

        logger.info(
            "turn session=%s action=%s accepted=%s errors=%s commands=%d rules_failed=%s rules_ms=%d dm_ms=%d",
            action.session_id, action.action_id, result.accepted,
            ",".join(e.code for e in result.errors) or "-", len(commands), rules_failed, rules_ms, dm_ms,
        )
        if not result.accepted:
            return TurnOutcome(False, result.state, result.events, result.errors, None, rules_failed=rules_failed)
        return TurnOutcome(
            True, result.state, result.events, (), response.narrative, response.dm_thoughts, rules_failed
        )

    def _fetch_rules(self, action: PlayerAction) -> tuple[str, bool]:
        try:
            return self._rules.get_rules(action.text) or NO_RULES, False
        except Exception:
            logger.warning(
                "rules unavailable session=%s action=%s: playing the turn without rules",
                action.session_id, action.action_id, exc_info=True,
            )
            return NO_RULES, True

    def _ask_dm(self, action: PlayerAction, request: TurnRequest) -> TurnResponse:
        try:
            return self._dm.process_turn(request)
        except Exception as exc:
            logger.error(
                "dm failed session=%s action=%s error=%s", action.session_id, action.action_id, type(exc).__name__
            )
            raise DungeonMasterError(f"Dungeon Master failed ({type(exc).__name__})") from exc

    @staticmethod
    def _rejected_before_dm(action: PlayerAction, result) -> TurnOutcome:
        logger.info(
            "turn session=%s action=%s accepted=False errors=%s (rejected before DM call)",
            action.session_id, action.action_id, ",".join(e.code for e in result.errors) or "-",
        )
        return TurnOutcome(False, result.state, result.events, result.errors, None)
