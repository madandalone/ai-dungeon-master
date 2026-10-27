import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

from ai_dm.errors import AIDMError, ContextBudgetError, ConfigurationError, InvalidResponseError, ProviderError
from ai_dm.models import TurnHistoryEntry, TurnRequest, TurnResponse
from engine import apply_action
from engine.dice import Dice
from engine.models import EngineError, GameState, PlayerAction
from integration.errors import DungeonMasterError, UnmappableResponse
from integration.mapping import to_commands, to_dm_state
from integration.ports import DungeonMaster, RulesProvider
from rules_service.errors import RulesUnavailableError

NO_RULES = "No specific rules found for this action."

logger = logging.getLogger(__name__)


def _dm_error(exc: AIDMError) -> DungeonMasterError:
    if isinstance(exc, ProviderError):
        return DungeonMasterError(str(exc), category=exc.category, retryable=exc.retryable, retry_after=exc.retry_after)
    category = {
        ContextBudgetError: "context_budget",
        ConfigurationError: "configuration",
        InvalidResponseError: "invalid_response",
    }.get(type(exc), "unknown")
    return DungeonMasterError(str(exc), category=category)


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
    rules_failed: bool = False  # the Rules Service reported RulesUnavailableError; played without rules


class TurnOrchestrator:
    """One game turn: rules -> DM -> commands -> engine. Holds no state between turns.

    Failure contract, in both directions only the declared types are handled; anything else is a bug and propagates:
    - rules: RulesUnavailableError -> the turn continues without rules (logged, TurnOutcome.rules_failed);
    - DM: ai_dm.errors.AIDMError -> DungeonMasterError with the DM's safe message and category, nothing committed.
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
        history: Sequence[TurnHistoryEntry] | None = None,
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
            history=list(history or ()),
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
        except RulesUnavailableError:
            logger.warning(
                "rules unavailable session=%s action=%s: playing the turn without rules",
                action.session_id, action.action_id, exc_info=True,
            )
            return NO_RULES, True

    def _ask_dm(self, action: PlayerAction, request: TurnRequest) -> TurnResponse:
        try:
            return self._dm.process_turn(request)
        except AIDMError as exc:
            error = _dm_error(exc)
            logger.error(
                "dm failed session=%s action=%s category=%s retryable=%s",
                action.session_id, action.action_id, error.category, error.retryable,
            )
            raise error from exc

    @staticmethod
    def _rejected_before_dm(action: PlayerAction, result) -> TurnOutcome:
        logger.info(
            "turn session=%s action=%s accepted=False errors=%s (rejected before DM call)",
            action.session_id, action.action_id, ",".join(e.code for e in result.errors) or "-",
        )
        return TurnOutcome(False, result.state, result.events, result.errors, None)
