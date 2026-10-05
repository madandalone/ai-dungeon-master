from dataclasses import dataclass

from ai_dm.models import TurnRequest
from engine import apply_action
from engine.dice import Dice
from engine.models import EngineError, GameState, PlayerAction
from integration.errors import DungeonMasterError
from integration.mapping import to_commands, to_dm_state
from integration.ports import DungeonMaster, RulesProvider

NO_RULES = "No specific rules found for this action."


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
    ) -> TurnOutcome:
        precheck = apply_action(state, action, [], dice)
        if not precheck.accepted:
            return TurnOutcome(False, precheck.state, precheck.events, precheck.errors, None)

        request = TurnRequest(
            rules_summary=self._rules.get_rules(action.text) or NO_RULES,
            hidden_plot=hidden_plot,
            game_state=to_dm_state(state, room_context),
            player_action=action.text,
        )
        try:
            response = self._dm.process_turn(request)
        except Exception as exc:
            raise DungeonMasterError(f"Dungeon Master failed: {exc}") from exc

        result = apply_action(state, action, to_commands(state, response), dice)
        if not result.accepted:
            return TurnOutcome(False, result.state, result.events, result.errors, None)
        return TurnOutcome(True, result.state, result.events, (), response.narrative, response.dm_thoughts)
