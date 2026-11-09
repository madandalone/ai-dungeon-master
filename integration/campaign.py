import logging
import time
from dataclasses import dataclass
from uuid import uuid4

from ai_dm.errors import AIDMError
from ai_dm.models import CampaignStartRequest
from engine import apply_action
from engine.commands import SetLocation
from engine.dice import Dice
from engine.models import GameState, PlayerAction
from integration.errors import CampaignRejected, dungeon_master_error
from integration.mapping import to_dm_state
from integration.ports import CampaignMaster

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Campaign:
    """What the DM invented for a session. `hidden_plot` is private: persist it, never send it to a client.

    `room_context` describes the opening scene; the Backend passes it to every turn until the party changes
    location. `intro_narrative` is the only public text.
    """

    title: str
    hidden_plot: str
    starting_location: str
    room_context: str
    intro_narrative: str


@dataclass(frozen=True)
class CampaignStart:
    campaign: Campaign
    state: GameState  # the party standing in the opening location
    events: tuple[object, ...]


def begin_campaign(
    dm: CampaignMaster,
    state: GameState,
    theme: str,
    dice: Dice,
    monster: str = "goblin",
) -> CampaignStart:
    """Ask the DM for a hidden plot built around the party, then move the party into the opening scene.

    The DM is called first, so a DM failure changes nothing. The move goes through the Engine like any other
    change (a SetLocation command), which makes the opening scene one accepted action: revision and
    turn_number each grow by one. Call this once, on a session whose characters are created.
    """
    if not state.characters:
        raise ValueError("A campaign needs at least one character")
    if not theme.strip():
        raise ValueError("A campaign needs a theme")

    request = CampaignStartRequest(
        setting_theme=theme.strip(),
        characters=to_dm_state(state).active_characters,
        monster_encounter=monster,
    )
    started = time.perf_counter()
    try:
        response = dm.generate_campaign_start(request)
    except AIDMError as exc:
        error = dungeon_master_error(exc)
        logger.error("campaign dm failed session=%s category=%s retryable=%s", state.session_id, error.category, error.retryable)
        raise error from exc
    dm_ms = round((time.perf_counter() - started) * 1000)

    campaign = Campaign(
        title=response.campaign_title,
        hidden_plot=response.hidden_plot,
        starting_location=response.starting_location,
        room_context=response.room_context,
        intro_narrative=response.intro_narrative,
    )
    action = PlayerAction(
        action_id=str(uuid4()),
        session_id=state.session_id,
        character_id=state.characters[0].id,
        text=f"Campaign start: {campaign.title}",
        expected_revision=state.revision,
    )
    result = apply_action(state, action, [SetLocation(campaign.starting_location)], dice)
    if not result.accepted:
        codes = ",".join(error.code for error in result.errors)
        logger.error("campaign rejected session=%s errors=%s", state.session_id, codes)
        raise CampaignRejected(f"The Engine refused the opening scene ({codes})", result.errors)

    logger.info("campaign started session=%s characters=%d dm_ms=%d", state.session_id, len(state.characters), dm_ms)
    return CampaignStart(campaign, result.state, result.events)
