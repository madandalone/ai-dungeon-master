from typing import Protocol

from ai_dm.models import TurnRequest, TurnResponse


class RulesProvider(Protocol):
    def get_rules(self, query: str) -> str:
        """Return rule text relevant to the query, or an empty string."""


class DungeonMaster(Protocol):
    def process_turn(self, request: TurnRequest) -> TurnResponse:
        """Propose the outcome of one player action. Output is untrusted."""
