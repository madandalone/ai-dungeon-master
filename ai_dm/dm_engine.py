import json
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
    TurnRequest,
    TurnResponse,
)
from ai_dm.provider import RetryPolicy, provider_error, without_sdk_retries
from ai_dm.prompts import PromptTemplates
from ai_dm.validation import validate_campaign, validate_turn

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
HISTORY_LIMIT = 10


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
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.8,
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
                if getattr(response.choices[0], "finish_reason", None) == "length":
                    raise ValueError("Достигнут лимит токенов ответа. Сократи текст и верни полный JSON.")
                result = response_model.model_validate_json(_strip_json_fence(raw))
                if validate_response is not None:
                    result = validate_response(result)
                return result
            except ValueError as exc:
                if repair_used or attempt == 2:
                    raise InvalidResponseError(
                        "Dungeon Master returned an invalid response after one repair attempt."
                    ) from exc
                repair_used = True
                details = (
                    json.dumps(exc.errors(include_input=False, include_url=False), ensure_ascii=False)
                    if isinstance(exc, ValidationError)
                    else str(exc)
                )
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
        )

    def process_turn(self, request: TurnRequest) -> TurnResponse:
        """Предлагает последствия хода; применяет их только Game Engine."""
        templates = PromptTemplates.load()
        history = [entry.model_dump() for entry in request.history[-HISTORY_LIMIT:]]
        return self._generate(
            partial(templates.turn_prompt, request, history), TurnResponse,
            validate_response=partial(validate_turn, request=request),
            templates=templates, history_count=len(history),
        )


_default_dm = AIDungeonMaster()


def generate_campaign_start(req: CampaignStartRequest) -> CampaignStartResponse:
    return _default_dm.generate_campaign_start(req)


def process_turn(request: TurnRequest) -> TurnResponse:
    return _default_dm.process_turn(request)
