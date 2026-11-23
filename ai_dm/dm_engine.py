import json
import logging
import math
import os
import re
import time
from collections.abc import Callable
from functools import partial
from typing import TypeVar

import httpx
from dotenv import load_dotenv
from openai import APIError, OpenAI
from pydantic import BaseModel, ValidationError

from ai_dm.context import GenerationLimits, fit_messages
from ai_dm.dnd_api import DnD5eClient
from ai_dm.errors import ConfigurationError, InvalidResponseError
from ai_dm.models import (
    CampaignStartRequest,
    CampaignStartResponse,
    CheckDecision,
    CheckResolution,
    CheckResult,
    TurnRequest,
    TurnPlan,
    TurnResponse,
)
from ai_dm.provider import RetryPolicy, provider_error, without_sdk_retries
from ai_dm.prompts import PromptTemplates
from ai_dm.memory import select_history
from ai_dm.validation import (
    validate_campaign,
    validate_check_decision,
    validate_check_resolution,
    validate_turn,
    validate_turn_plan,
)

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
logger = logging.getLogger(__name__)
_ERROR_LIMIT = 400


def _summarize_error(exc: Exception) -> str:
    """One short line about why a model answer was rejected; never the answer itself."""
    if isinstance(exc, ValidationError):
        text = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
    else:
        text = str(exc)
    return " ".join(text.split())[:_ERROR_LIMIT]


def _log_raw_enabled() -> bool:
    """AI_DM_LOG_RAW=1 logs full prompts and raw answers. For local debugging only: they contain the hidden plot."""
    return os.getenv("AI_DM_LOG_RAW", "").strip() == "1"


def _create_client() -> OpenAI:
    """Read configuration only when an LLM operation is requested."""
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ConfigurationError("OPENAI_API_KEY is required to call the Dungeon Master.")
    try:
        timeout = float(os.getenv("AI_DM_TIMEOUT_SECONDS", "60"))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Timeout must be positive and finite.")
    except ValueError as exc:
        raise ConfigurationError("AI_DM_TIMEOUT_SECONDS must be a positive finite number.") from exc

    try:
        return OpenAI(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://router.huggingface.co/v1"),
            timeout=timeout,
            max_retries=0,
            default_headers={
                "HTTP-Referer": "https://github.com/madandalone/ai-dungeon-master",
                "X-Title": "AI Dungeon Master",
            },
        )
    except (ValueError, TypeError, httpx.InvalidURL) as exc:
        raise ConfigurationError("Could not configure the Dungeon Master client.") from exc


def _response_text(response) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError("The model returned no response choices.")
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("The model returned empty response content.")
    return content.strip()


def _strip_json_fence(text: str) -> str:
    # Remove only a fence enclosing the whole response, not text inside JSON strings.
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


class AIDungeonMaster:
    """Stateless game logic with a lazy, reusable LLM client.

    A supplied client belongs to the caller, including its timeout and transport.
    SDK retries are disabled; one provider retry and one response repair are allowed.
    """

    def __init__(self, client: OpenAI | None = None):
        self._client = client

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = _create_client()
        return self._client

    def _generate(
        self,
        prompt: str | Callable[[int], str],
        response_model: type[ResponseModel],
        validate_response: Callable[[ResponseModel], ResponseModel] | None = None,
        *,
        templates: PromptTemplates,
        history_count: int = 0,
        operation: str = "generate",
    ) -> ResponseModel:
        client = without_sdk_retries(self._get_client())
        limits = GenerationLimits.from_env()
        retry_policy = RetryPolicy.from_env()
        model = os.getenv("MODEL_NAME", "Qwen/Qwen3.8-27B")
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False, indent=2)
        repair_messages = []
        provider_retry_used = False
        repair_used = False

        def build_messages(count: int) -> list[dict[str, str]]:
            text = prompt(count) if callable(prompt) else prompt
            return [
                {"role": "system", "content": templates.system},
                {"role": "user", "content": templates.request_prompt(text, schema)},
                *repair_messages,
            ]

        for attempt in range(3):
            messages, history_count = fit_messages(
                build_messages, history_count, limits.max_context_chars, repair=repair_used,
            )
            if _log_raw_enabled():
                logger.info("AI REQUEST operation=%s attempt=%d messages=%s", operation, attempt + 1,
                            json.dumps(messages, ensure_ascii=False))
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    # Constrain compatible providers (including Ollama) with the same
                    # schema that Pydantic validates at the application boundary.
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": response_model.__name__,
                            "strict": True,
                            "schema": response_model.model_json_schema(),
                        },
                    },
                    temperature=0.2,
                    max_tokens=limits.max_output_tokens,
                )
            except (APIError, httpx.HTTPError, TimeoutError, ConnectionError) as exc:
                error = provider_error(exc)
                delay = retry_policy.delay_for(error)
                if not provider_retry_used and attempt < 2 and delay is not None:
                    provider_retry_used = True
                    time.sleep(delay)
                    continue
                raise error from exc

            raw = None
            try:
                raw = _response_text(response)
                if _log_raw_enabled():
                    logger.info("RAW AI RESPONSE operation=%s attempt=%d finish_reason=%s raw=%s", operation,
                                attempt + 1, getattr(response.choices[0], "finish_reason", None), raw)
                if getattr(response.choices[0], "finish_reason", None) == "length":
                    raise ValueError("Достигнут лимит токенов ответа. Сократи текст и верни полный JSON.")
                result = response_model.model_validate_json(_strip_json_fence(raw))
                if validate_response is not None:
                    result = validate_response(result)
                return result
            except ValueError as exc:
                details = (
                    json.dumps(
                        exc.errors(include_input=False, include_url=False),
                        ensure_ascii=False,
                        default=str,
                    )
                    if isinstance(exc, ValidationError)
                    else str(exc)
                )
                logger.warning(
                    "invalid AI response operation=%s model=%s schema=%s attempt=%d repair=%s error=%s",
                    operation, model, response_model.__name__, attempt + 1,
                    "failed" if repair_used else "next", _summarize_error(exc),
                )
                if repair_used or attempt == 2:
                    raise InvalidResponseError(
                        "Dungeon Master returned an invalid response after one repair attempt."
                    ) from exc
                repair_used = True
                repair_messages = []
                if raw is not None:
                    repair_messages.append({"role": "assistant", "content": raw})
                repair_messages.append({
                    "role": "user",
                    "content": templates.repair_prompt(details),
                })

        raise AssertionError("The bounded generation loop must return or raise.")

    def generate_campaign_start(self, req: CampaignStartRequest) -> CampaignStartResponse:
        """Генерирует завязку кампании с каноническими статами монстра."""
        # Fail on missing configuration before making the optional D&D API request.
        self._get_client()
        templates = PromptTemplates.load()
        monster_stats = DnD5eClient.get_monster(req.monster_encounter)
        return self._generate(
            templates.campaign_prompt(req, monster_stats), CampaignStartResponse,
            validate_response=partial(validate_campaign, request=req), templates=templates,
            operation="campaign",
        )

    def process_turn(self, request: TurnRequest) -> TurnResponse:
        """Предлагает последствия хода; применяет их только Game Engine."""
        templates = PromptTemplates.load()
        history = select_history(request)
        return self._generate(
            partial(templates.turn_prompt, request, history), TurnResponse,
            validate_response=partial(validate_turn, request=request),
            templates=templates, history_count=history.count, operation="turn",
        )

    def plan_turn(self, request: TurnRequest) -> TurnPlan:
        """Either resolve a safe action or request one Engine-owned ability check.

        Two small requests instead of one large union: first only the decision "does this action need a
        roll, and which" (no history, no plot, no nested response), then, if no roll is needed, the narration
        with the roll question already answered. A model that must fill a nested `check` object next to a
        full `response` object in one schema tends to name a status and forget the payload.
        """
        templates = PromptTemplates.load()
        decision = self._generate(
            templates.decision_prompt(request), CheckDecision,
            validate_response=partial(validate_check_decision, request=request),
            templates=templates, operation="plan.decision",
        )
        if decision.needs_check:
            return TurnPlan(status="check", check=decision.proposal())

        history = select_history(request)

        def validate_narration(response: TurnResponse) -> TurnResponse:
            response = validate_turn(response, request=request)
            if response.requires_check or response.check is not None:
                raise ValueError(
                    "Проверка в этом ходе уже не требуется. Верни requires_check: false и check: null."
                )
            return response

        response = self._generate(
            partial(templates.turn_prompt, request, history, check_block=templates.no_check),
            TurnResponse,
            validate_response=validate_narration,
            templates=templates, history_count=history.count, operation="plan.narration",
        )
        return TurnPlan(status="resolved", response=response)

    def resolve_check(
        self, request: TurnRequest, checks: list[CheckResult], *, allow_next: bool,
    ) -> CheckResolution:
        """Continue or finish a turn using immutable Engine check results."""
        templates = PromptTemplates.load()
        history = select_history(request)
        expected = "success" if all(check.success for check in checks) else "failure"
        block = templates.outcome_block([check.model_dump() for check in checks], expected, allow_next)

        return self._generate(
            partial(templates.turn_prompt, request, history, check_block=block), CheckResolution,
            validate_response=partial(
                validate_check_resolution, request=request, expected_outcome=expected,
            ),
            templates=templates, history_count=history.count, operation="resolve_check",
        )


_default_dm = AIDungeonMaster()


def generate_campaign_start(req: CampaignStartRequest) -> CampaignStartResponse:
    return _default_dm.generate_campaign_start(req)


def process_turn(request: TurnRequest) -> TurnResponse:
    return _default_dm.process_turn(request)
