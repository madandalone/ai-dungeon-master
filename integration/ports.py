from typing import Protocol

from ai_dm.models import CampaignStartRequest, CampaignStartResponse, TurnRequest, TurnResponse


class RulesProvider(Protocol):
    def get_rules(self, query: str) -> str:
        """Return rule text relevant to the query, or an empty string.

        Raise rules_service.errors.RulesUnavailableError if the source cannot answer right now;
        the turn then continues without rules. Other exceptions are treated as bugs and propagate.
        """


class DungeonMaster(Protocol):
    def process_turn(self, request: TurnRequest) -> TurnResponse:
        """Propose the outcome of one player action. Output is untrusted.

        Raise ai_dm.errors.AIDMError (or a subclass) for expected failures; other exceptions propagate as bugs.
        """


class CampaignMaster(Protocol):
    def generate_campaign_start(self, request: CampaignStartRequest) -> CampaignStartResponse:
        """Create the hidden plot and the opening scene for the given characters.

        Raise ai_dm.errors.AIDMError (or a subclass) for expected failures; other exceptions propagate as bugs.
        """
