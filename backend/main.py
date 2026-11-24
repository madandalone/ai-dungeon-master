
import os
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from ai_dm.models import (
    CheckResult,
    StateDelta,
    TurnHistoryEntry,
    TurnRequest,
    TurnResponse,
)
from backend.storage import SQLiteSessionStore, StateRevisionConflict
from engine import (
    ability_modifier,
    add_character,
    apply_action,
    resolve_skill_check,
    to_public_dict,
    to_public_events,
)
from engine.commands import AdjustHealth, SkillCheck
from engine.dice import Dice, LocalRandomSource, SystemRandomSource
from engine.events import SkillCheckResolved
from engine.models import ABILITIES, Character, GameState, InventoryItem, PlayerAction
from integration import Campaign, IntegrationError, TurnOrchestrator, TurnOutcome
from integration.factory import build_orchestrator
from integration.turns import IdempotentExecutor, make_player_action


# Plain `uvicorn backend.main:app` must see .env too (run.sh sources it, other launchers do not).
# Variables already set in the environment win.
load_dotenv()

app = FastAPI(title="AI Dungeon Master API")


@app.exception_handler(StateRevisionConflict)
async def handle_revision_conflict(request, exc: StateRevisionConflict):
    return JSONResponse(
        status_code=409,
        content={
            "detail": {
                "code": "STALE_REVISION",
                "message": str(exc),
            }
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5500", "http://localhost:5500"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


DEFAULT_PLOT = "Test campaign"
DEFAULT_THEME = "Приключение в фэнтезийном мире"


class AuthoredCampaign(BaseModel):
    """A campaign written by a person instead of the Dungeon Master (demos, tests, own stories)."""

    title: str = Field(min_length=1, max_length=120)
    hidden_plot: str = Field(min_length=1, max_length=20000)
    starting_location: str = Field(min_length=1, max_length=120)
    room_context: str = Field(min_length=1, max_length=2000)
    intro_narrative: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _not_blank(self) -> "AuthoredCampaign":
        blank = [name for name, value in self.model_dump().items() if not value.strip()]
        if blank:
            raise ValueError("Fields must not be blank: " + ", ".join(blank))
        return self


class CampaignRequest(BaseModel):
    theme: str | None = Field(default=None, min_length=1, max_length=300)
    opening_hook: str | None = Field(default=None, max_length=500)
    monster: str = Field(default="goblin", min_length=1, max_length=60)
    authored: AuthoredCampaign | None = None


class GameSessionCreate(BaseModel):
    name: str
    theme: str = Field(default=DEFAULT_THEME, min_length=1, max_length=300)
    opening_hook: str = Field(default="", max_length=500)
    hidden_plot: str = Field(
        default=DEFAULT_PLOT,
        min_length=1,
        max_length=20000,
    )

class GameSession(BaseModel):
    id: str
    name: str


class PlayerActionRequest(BaseModel):
    character_id: str
    text: str
    health_delta: int


class PlayerTurnRequest(BaseModel):
    character_id: str
    text: str
    action_id: str | None = None
    expected_revision: int | None = None


class RollRequest(BaseModel):
    sequence: int = Field(ge=1, le=3)


class CharacterCreateRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=60)
    concept: str = Field(default="", max_length=400)
    max_hp: int = Field(ge=1, le=999)
    stats: dict[str, int] = Field(default_factory=dict)
    starting_item: str = Field(default="", max_length=60)

    @model_validator(mode="after")
    def _six_abilities(self) -> "CharacterCreateRequest":
        if set(self.stats) != set(ABILITIES):
            raise ValueError("All six D&D abilities are required")
        if any(
            isinstance(value, bool) or not 1 <= value <= 20
            for value in self.stats.values()
        ):
            raise ValueError("Ability scores must be integers from 1 to 20")
        return self


class FakeRulesProvider:
    def get_rules(self, query: str) -> str:
        return "Test rule: the hero loses 1 HP."


class FakeDungeonMaster:
    def process_turn(self, request: TurnRequest) -> TurnResponse:
        character = request.game_state.active_characters[0]

        return TurnResponse(
            narrative=f"{character.name} совершает действие: {request.player_action}",
            state_updates=[
                StateDelta(
                    character_name=character.name,
                    hp_change=-1,
                )
            ],
            new_location=None,
            dm_thoughts="Test turn processed.",
        )


sessions: dict[str, GameSession] = {}
game_states: dict[str, GameState] = {}
game_histories: dict[str, list[TurnHistoryEntry]] = {}
turn_executor = IdempotentExecutor()


@lru_cache(maxsize=1)
def get_orchestrator() -> TurnOrchestrator:
    return build_orchestrator()

@lru_cache(maxsize=1)
def get_session_store() -> SQLiteSessionStore:
    db_path = Path(os.getenv("GAME_DB_PATH", "data/game_sessions.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteSessionStore(db_path)


def ensure_session_loaded(session_id: str) -> None:
    """Restore a session from SQLite if it is missing from memory."""
    if session_id in game_states:
        return

    restored = get_session_store().load_session(session_id)
    if restored is None:
        raise HTTPException(
            status_code=404,
            detail="Session not found",
        )

    name, state, history = restored
    sessions[session_id] = GameSession(
        id=session_id,
        name=name,
    )
    game_states[session_id] = state
    game_histories[session_id] = history


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/sessions", response_model=GameSession)
def create_session(session_data: GameSessionCreate):
    session = GameSession(
        id=str(uuid4()),
        name=session_data.name,
    )

    state = GameState(
        session_id=session.id,
        revision=0,
        turn_number=0,
        location_id="start",
        characters=(),
        world_flags={},
    )

    # Persist first; expose in memory only after successful storage.
    get_session_store().save_session(
        session.id,
        session.name,
        state,
        hidden_plot=session_data.hidden_plot,
        campaign_theme=session_data.theme.strip(),
        opening_hook=session_data.opening_hook.strip(),
    )

    sessions[session.id] = session
    game_states[session.id] = state
    game_histories[session.id] = []

    return session


@app.get("/sessions", response_model=list[GameSession])
def list_sessions():
    """Return saved sessions, including sessions not loaded in memory."""
    return [
        GameSession(id=session_id, name=name)
        for session_id, name in get_session_store().list_sessions()
    ]


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    """Delete a saved session and its related persistent records."""

    def execute_delete():
        deleted = get_session_store().delete_session(session_id)

        if not deleted:
            raise HTTPException(
                status_code=404,
                detail="Session not found",
            )

        sessions.pop(session_id, None)
        game_states.pop(session_id, None)
        game_histories.pop(session_id, None)

        return {
            "status": "deleted",
            "session_id": session_id,
        }

    response, _ = turn_executor.run(
        session_id,
        f"delete:{session_id}",
        execute_delete,
        remember=lambda result: False,
    )
    turn_executor.forget(session_id)
    return response


def _success_chance(modifier: int, difficulty: int) -> int:
    successful = sum(
        1 for roll in range(1, 21)
        if roll + modifier >= difficulty
    )
    return successful * 5


def _pending_public(payload: dict, state: GameState) -> dict:
    check = payload["check"]
    character = next(
        c for c in state.characters
        if c.id == payload["action"]["character_id"]
    )
    score = character.stats[check["ability"]]
    modifier = ability_modifier(score)

    return {
        "status": "awaiting_roll",
        "action_id": payload["action"]["action_id"],
        "character_id": character.id,
        "text": payload["action"]["text"],
        "sequence": check["sequence"],
        "maximum": 3,
        "ability": check["ability"],
        "score": score,
        "modifier": modifier,
        "difficulty": check["difficulty"],
        "chance": _success_chance(modifier, check["difficulty"]),
        "reason": check["reason"],
        "resolved_checks": list(payload.get("checks", [])),
        "can_cancel": not payload.get("checks"),
    }


def _pending_response(payload: dict, state: GameState) -> dict:
    return {
        "status": "awaiting_roll",
        "pending_turn": _pending_public(payload, state),
        "state": to_public_dict(
            state,
            session_name=sessions[state.session_id].name,
            log=[],
        ),
        "revision": state.revision,
        "turn_number": state.turn_number,
    }


def _action_from_payload(payload: dict) -> PlayerAction:
    return PlayerAction(**payload["action"])


def _check_event_from_payload(data: dict) -> SkillCheckResolved:
    return SkillCheckResolved(
        action_id=data["action_id"],
        sequence=data["sequence"],
        character_id=data["character_id"],
        ability=data["ability"],
        roll=data["roll"],
        modifier=data["modifier"],
        total=data["total"],
        difficulty=data["difficulty"],
        success=data["success"],
    )


def _check_result(event: SkillCheckResolved) -> CheckResult:
    return CheckResult(
        ability=event.ability,
        roll=event.roll,
        modifier=event.modifier,
        total=event.total,
        difficulty=event.difficulty,
        success=event.success,
    )


def _turn_response(
    session_id: str,
    action: PlayerAction,
    outcome: TurnOutcome,
    *,
    clear_pending: bool = False,
) -> dict:
    warnings = (
        [{"code": "RULES_UNAVAILABLE"}]
        if outcome.rules_failed else []
    )

    response = {
        "status": "resolved",
        "accepted": outcome.accepted,
        "narration": outcome.narration,
        "state": to_public_dict(
            outcome.state,
            session_name=sessions[session_id].name,
            log=[],
        ),
        "revision": outcome.state.revision,
        "turn_number": outcome.state.turn_number,
        "events": to_public_events(outcome.events),
        "errors": [
            {"code": error.code, "message": error.message}
            for error in outcome.errors
        ],
        "warnings": warnings,
    }

    checks = [
        event for event in outcome.events
        if isinstance(event, SkillCheckResolved)
    ]
    response["check_outcome"] = (
        "success" if all(event.success for event in checks) else "failure"
    ) if checks else None

    if not outcome.accepted:
        return response

    entry = TurnHistoryEntry(
        character_id=action.character_id,
        player_action=action.text,
        narrative=outcome.narration or "",
        dm_notes=outcome.dm_notes or "",
    )

    get_session_store().save_turn(
        session_id,
        outcome.state,
        entry,
        action_id=action.action_id,
        response=response,
        clear_pending=clear_pending,
    )

    game_states[session_id] = outcome.state
    game_histories[session_id].append(entry)

    return response


def public_state(
    session_id: str,
    include_history: bool = True,
) -> dict:
    history = game_histories[session_id]
    log = []

    if include_history:
        for entry in history:
            if entry.player_action.strip():
                log.append({
                    "kind": "player",
                    "character_id": entry.character_id,
                    "text": entry.player_action,
                })

            if entry.narrative.strip():
                log.append({
                    "kind": "master",
                    "character_id": "",
                    "text": entry.narrative,
                })

    public = to_public_dict(
        game_states[session_id],
        session_name=sessions[session_id].name,
        log=log,
    )

    pending = get_session_store().load_pending_turn(session_id)
    public["pending_turn"] = (
        _pending_public(pending, game_states[session_id])
        if pending else None
    )

    campaign = get_session_store().load_campaign(session_id)
    public["campaign"] = {
        "started": campaign["started"],
        "title": campaign["title"],
        "theme": campaign["theme"],
        "opening_hook": campaign["opening_hook"],
    }

    return public


def _start_campaign_locked(
    session_id: str,
    orchestrator: TurnOrchestrator,
    theme: str | None,
    monster: str,
    opening_hook: str | None = None,
    authored: AuthoredCampaign | None = None,
) -> tuple[bool, dict]:
    """Give the session a plot (DM-written, or authored) unless it already has one. Call under the session lock."""
    ensure_session_loaded(session_id)
    store = get_session_store()
    record = store.load_campaign(session_id)
    if record["started"]:
        return False, {"title": record["title"]}

    selected_theme = (theme if theme is not None else record["theme"]).strip()
    selected_hook = (
        opening_hook if opening_hook is not None else record["opening_hook"]
    ).strip()

    state = game_states[session_id]
    if not state.characters:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NO_CHARACTERS",
                "message": "Create at least one character before starting the campaign.",
            },
        )

    try:
        if authored is not None:
            started = orchestrator.use_authored_campaign(
                state,
                Campaign(**authored.model_dump()),
                Dice(SystemRandomSource()),
            )
        else:
            started = orchestrator.start_campaign(
                state, selected_theme, Dice(SystemRandomSource()), monster,
                opening_hook=selected_hook,
            )
    except IntegrationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    campaign = started.campaign
    entry = TurnHistoryEntry(
        player_action="",
        narrative=campaign.intro_narrative,
        dm_notes=f"Начало кампании: {campaign.title}",
    )
    store.save_campaign(
        session_id,
        started.state,
        entry,
        title=campaign.title,
        hidden_plot=campaign.hidden_plot,
        room_context=campaign.room_context,
    )
    game_states[session_id] = started.state
    game_histories[session_id].append(entry)
    return True, {"title": campaign.title, "intro": campaign.intro_narrative}


@app.post("/sessions/{session_id}/campaign")
def start_campaign(
    session_id: str,
    body: CampaignRequest = CampaignRequest(),
    orchestrator: TurnOrchestrator = Depends(get_orchestrator),
):
    """Give the session a hidden plot: written by the Dungeon Master for the current heroes, or `authored`.

    Needs at least one hero. Safe to repeat: a session that already has a campaign answers `created: false`.
    The plot itself never leaves the server.
    """
    if body.authored is None and not orchestrator.can_start_campaign:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CAMPAIGN_UNAVAILABLE",
                "message": "The configured Dungeon Master cannot write campaigns.",
            },
        )

    def execute_campaign():
        created, summary = _start_campaign_locked(
            session_id, orchestrator, body.theme, body.monster,
            body.opening_hook, body.authored,
        )
        return {"created": created, **summary, "state": public_state(session_id)}

    response, _ = turn_executor.run(
        session_id,
        f"campaign:{session_id}",
        execute_campaign,
        remember=lambda result: False,
    )
    return response


@app.get("/sessions/{session_id}/state")
def get_session_state(session_id: str):
    ensure_session_loaded(session_id)
    return public_state(session_id)


@app.post("/sessions/{session_id}/characters")
def create_character(
    session_id: str,
    character_data: CharacterCreateRequest,
):
    operation_id = str(uuid4())

    def execute_creation():
        ensure_session_loaded(session_id)
        pending = get_session_store().load_pending_turn(session_id)
        if pending is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "PENDING_CHECK", "message": "Finish the pending check first."},
            )
        item_name = character_data.starting_item.strip()
        inventory = ()

        if item_name:
            inventory = (
                InventoryItem(
                    item_id=str(uuid4()),
                    name=item_name,
                    quantity=1,
                ),
            )

        character = Character(
            id=str(uuid4()),
            name=character_data.name.strip(),
            concept=character_data.concept.strip(),
            hp=character_data.max_hp,
            max_hp=character_data.max_hp,
            stats={
                name.strip(): value
                for name, value in character_data.stats.items()
            },
            inventory=inventory,
        )

        result = add_character(
            game_states[session_id],
            character,
            character_data.expected_revision,
        )

        if not result.accepted:
            error = result.errors[0]
            status = 409 if error.code == "STALE_REVISION" else 422

            raise HTTPException(
                status_code=status,
                detail={
                    "code": error.code,
                    "message": error.message,
                },
            )

        get_session_store().save_state(session_id, result.state)
        game_states[session_id] = result.state

        return public_state(session_id)

    response, _ = turn_executor.run(
        session_id,
        operation_id,
        execute_creation,
        remember=lambda result: False,
    )

    return response


@app.post("/sessions/{session_id}/actions")
def process_action(
    session_id: str,
    action_data: PlayerActionRequest,
):
    action_id = str(uuid4())

    def execute_action():
        ensure_session_loaded(session_id)
        state = game_states[session_id]

        action = PlayerAction(
            action_id=action_id,
            session_id=session_id,
            character_id=action_data.character_id,
            text=action_data.text,
            expected_revision=state.revision,
        )

        command = AdjustHealth(
            character_id=action_data.character_id,
            delta=action_data.health_delta,
        )

        result = apply_action(
            state=state,
            action=action,
            commands=[command],
            dice=Dice(LocalRandomSource()),
        )

        if result.accepted:
            get_session_store().save_state(session_id, result.state)
            game_states[session_id] = result.state

        return {
            "accepted": result.accepted,
            "state": public_state(
                session_id,
                include_history=False,
            ),
            "revision": result.state.revision,
            "turn_number": result.state.turn_number,
            "errors": [
                {
                    "code": error.code,
                    "message": error.message,
                }
                for error in result.errors
            ],
        }

    response, _ = turn_executor.run(
        session_id,
        action_id,
        execute_action,
        remember=lambda result: False,
    )

    return response


@app.post("/sessions/{session_id}/turns")
def process_turn(
    session_id: str,
    turn_data: PlayerTurnRequest,
    orchestrator: TurnOrchestrator = Depends(get_orchestrator),
):
    action_id = turn_data.action_id or str(uuid4())

    def execute_turn():
        ensure_session_loaded(session_id)
        store = get_session_store()

        saved_response = store.load_processed_turn(
            session_id,
            action_id,
        )
        if saved_response is not None:
            return saved_response

        pending = store.load_pending_turn(session_id)

        if pending is not None:
            if pending["action"]["action_id"] != action_id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "PENDING_CHECK",
                        "message": "Finish the pending check first.",
                    },
                )

            original = pending["action"]

            if (
                original["character_id"] != turn_data.character_id
                or original["text"] != turn_data.text
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "ACTION_ID_REUSED",
                        "message": "Action id belongs to another turn.",
                    },
                )

            return _pending_response(
                pending,
                game_states[session_id],
            )

        campaign = store.load_campaign(session_id)
        expected_revision = turn_data.expected_revision
        if (
            orchestrator.can_start_campaign
            and game_states[session_id].characters
            and not campaign["started"]
            and campaign["hidden_plot"] == DEFAULT_PLOT
        ):
            # First turn of a new game: the DM writes the plot for the heroes that exist now.
            revision_before = game_states[session_id].revision
            _start_campaign_locked(
                session_id, orchestrator, None, "goblin"
            )
            campaign = store.load_campaign(session_id)
            if expected_revision == revision_before:
                expected_revision = game_states[session_id].revision

        state = game_states[session_id]

        action = make_player_action(
            state=state,
            character_id=turn_data.character_id,
            text=turn_data.text,
            action_id=action_id,
            expected_revision=expected_revision,
        )

        try:
            planned = orchestrator.plan_turn(
                state=state,
                action=action,
                hidden_plot=campaign["hidden_plot"],
                dice=Dice(SystemRandomSource()),
                room_context=campaign["room_context"],
                history=game_histories[session_id],
            )
        except IntegrationError as exc:
            raise HTTPException(
                status_code=502,
                detail=str(exc),
            ) from exc

        if isinstance(planned, TurnOutcome):
            return _turn_response(
                session_id,
                action,
                planned,
            )

        check = planned.plan.check

        payload = {
            "action": asdict(action),
            "request": planned.request.model_dump(),
            "check": {
                **check.model_dump(),
                "sequence": 1,
            },
            "checks": [],
            "rules_failed": planned.rules_failed,
        }

        store.save_pending_turn(
            session_id,
            action_id,
            payload,
        )

        return _pending_response(payload, state)

    result, _replayed = turn_executor.run(
        session_id,
        action_id,
        execute_turn,
        remember=lambda response: (
            response.get("status") == "resolved"
            and response.get("accepted", False)
        ),
    )

    if result.get("status") == "awaiting_roll":
        return JSONResponse(
            status_code=202,
            content=result,
        )

    return result


@app.post("/sessions/{session_id}/turns/{action_id}/roll")
def roll_turn(
    session_id: str,
    action_id: str,
    roll_data: RollRequest,
    orchestrator: TurnOrchestrator = Depends(get_orchestrator),
):
    def execute_roll():
        ensure_session_loaded(session_id)
        store = get_session_store()

        saved = store.load_processed_turn(
            session_id,
            action_id,
        )
        if saved is not None:
            return saved

        pending = store.load_pending_turn(session_id)

        if (
            pending is None
            or pending["action"]["action_id"] != action_id
        ):
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "PENDING_CHECK_NOT_FOUND",
                    "message": "Pending check was not found.",
                },
            )

        saved_steps = pending.setdefault("roll_responses", {})
        saved_step = saved_steps.get(str(roll_data.sequence))
        if saved_step is not None:
            return saved_step

        current_sequence = pending["check"]["sequence"]
        if roll_data.sequence != current_sequence:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ROLL_SEQUENCE_MISMATCH",
                    "message": f"Expected roll sequence {current_sequence}.",
                },
            )

        state = game_states[session_id]
        action = _action_from_payload(pending)
        check_data = pending["check"]

        events = [
            _check_event_from_payload(item)
            for item in pending.get("checks", [])
        ]

        sequence = check_data["sequence"]

        if len(events) < sequence:
            event = resolve_skill_check(
                state,
                action,
                SkillCheck(
                    action.character_id,
                    check_data["ability"],
                    check_data["difficulty"],
                ),
                Dice(SystemRandomSource()),
                sequence=sequence - 1,
            )

            events.append(event)
            pending["checks"] = [
                asdict(item) for item in events
            ]

            store.save_pending_turn(
                session_id,
                action_id,
                pending,
            )

        request = TurnRequest.model_validate(
            pending["request"]
        )

        results = [
            _check_result(event)
            for event in events
        ]

        latest = events[-1]
        allow_next = latest.success and len(events) < 3

        try:
            continuation = orchestrator.continue_checked_turn(
                request,
                results,
                allow_next=allow_next,
            )
        except IntegrationError as exc:
            raise HTTPException(
                status_code=502,
                detail=str(exc),
            ) from exc

        if continuation.status == "check":
            if not allow_next:
                raise HTTPException(
                    status_code=502,
                    detail="Dungeon Master exceeded the check limit",
                )

            pending["check"] = {
                **continuation.check.model_dump(),
                "sequence": len(events) + 1,
            }
            response = _pending_response(pending, state)
            saved_steps[str(roll_data.sequence)] = response
            store.save_pending_turn(session_id, action_id, pending)
            return response

        outcome = orchestrator.finalize_turn(
            state,
            action,
            continuation.response,
            Dice(SystemRandomSource()),
            events,
            rules_failed=bool(pending.get("rules_failed")),
            declared_outcome=continuation.outcome,
        )
        if not outcome.accepted:
            codes = ",".join(error.code for error in outcome.errors) or "UNKNOWN"
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "INVALID_DM_RESULT",
                    "message": f"Dungeon Master consequences were rejected by Engine: {codes}",
                },
            )
        return _turn_response(
            session_id, action, outcome, clear_pending=True,
        )

    result, _replayed = turn_executor.run(
        session_id,
        action_id,
        execute_roll,
        remember=lambda response: (
            response.get("status") == "resolved"
            and response.get("accepted", False)
        ),
    )

    if result.get("status") == "awaiting_roll":
        return JSONResponse(
            status_code=202,
            content=result,
        )

    return result


@app.post("/sessions/{session_id}/turns/{action_id}/cancel")
def cancel_turn(session_id: str, action_id: str):
    def execute_cancel():
        ensure_session_loaded(session_id)
        store = get_session_store()

        pending = store.load_pending_turn(session_id)

        if (
            pending is None
            or pending["action"]["action_id"] != action_id
        ):
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "PENDING_CHECK_NOT_FOUND",
                },
            )

        if pending.get("checks"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ROLL_ALREADY_STARTED",
                    "message": "A resolved roll cannot be cancelled.",
                },
            )

        store.delete_pending_turn(
            session_id,
            action_id,
        )

        return {
            "status": "cancelled",
            "action_id": action_id,
        }

    result, _ = turn_executor.run(
        session_id,
        action_id,
        execute_cancel,
        remember=lambda response: False,
    )

    return result
