import json
import math
import os
import re
from typing import TypeVar

import httpx
from dotenv import load_dotenv
from openai import APIError, OpenAI
from pydantic import BaseModel, ValidationError

from ai_dm.dnd_api import DnD5eClient
from ai_dm.errors import ConfigurationError, InvalidResponseError, ProviderError
from ai_dm.models import (
    CampaignStartRequest,
    CampaignStartResponse,
    TurnRequest,
    TurnResponse,
)

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)

SYSTEM_PROMPT = """
Ты — AI Dungeon Master (Ведущий) в настольной ролевой игре D&D 5e.
Твоя цель: вести атмосферную игру, следовать правилам SRD 5e и реагировать на действия игроков.
Отвечай СТРОГО валидным JSON-объектом в соответствии с требуемой схемой.
"""


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

    A supplied client belongs to the caller, including its timeout/retry policy.
    The default client uses AI_DM_TIMEOUT_SECONDS and disables SDK retries.
    """

    def __init__(self, client: OpenAI | None = None):
        self._client = client

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = _create_client()
        return self._client

    def _generate(self, prompt: str, response_model: type[ResponseModel]) -> ResponseModel:
        client = self._get_client()
        model = os.getenv("MODEL_NAME", "Qwen/Qwen3.8-27B")
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False, indent=2)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{prompt}\nВерни JSON по схеме:\n{schema}"},
        ]
        for attempt in range(2):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.8,
                )
            except (APIError, httpx.HTTPError, TimeoutError, ConnectionError) as exc:
                # Do not include provider payloads or private prompts in the public message.
                raise ProviderError("Dungeon Master provider request failed.") from exc

            raw = None
            try:
                raw = _response_text(response)
                return response_model.model_validate_json(_strip_json_fence(raw))
            except ValueError as exc:
                if attempt == 1:
                    raise InvalidResponseError(
                        "Dungeon Master returned an invalid response after one repair attempt."
                    ) from exc
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
                    "content": (
                        "Исправь формат предыдущего ответа. Ошибки проверки: "
                        f"{details}\nВерни только полный JSON-объект по исходной схеме."
                    ),
                })
                messages = [*messages, *repair_messages]

        raise AssertionError("The bounded generation loop must return or raise.")

    def generate_campaign_start(self, req: CampaignStartRequest) -> CampaignStartResponse:
        """Генерирует завязку кампании с каноническими статами монстра."""
        # Fail on missing configuration before making the optional D&D API request.
        self._get_client()
        monster_stats = DnD5eClient.get_monster(req.monster_encounter)
        stats_text = json.dumps(monster_stats, ensure_ascii=False) if monster_stats else "Базовый противник"
        prompt = f"""
Создай начало приключения D&D 5e.
Сеттинг / Пожелание: {req.setting_theme}
Персонажи игроков: {json.dumps([c.model_dump() for c in req.characters], ensure_ascii=False)}

КАНОНИЧЕСКИЙ МОНСТР ИЗ D&D 5e API (используй его в сюжете):
{stats_text}
"""
        return self._generate(prompt, CampaignStartResponse)

    def process_turn(self, request: TurnRequest) -> TurnResponse:
        """Предлагает последствия хода; применяет их только Game Engine."""
        prompt = f"""
### ПРАВИЛА:
{request.rules_summary}

### ТАЙНА МАСТЕРА (Скрытый сюжет):
{request.hidden_plot}

### ТЕКУЩЕЕ СОСТОЯНИЕ:
- Локация: {request.game_state.location}
- Окружение: {request.game_state.room_context}
- Персонажи: {json.dumps([c.model_dump() for c in request.game_state.active_characters], ensure_ascii=False)}

### ДЕЙСТВИЕ ИГРОКА:
"{request.player_action}"
"""
        return self._generate(prompt, TurnResponse)


_default_dm = AIDungeonMaster()


def generate_campaign_start(req: CampaignStartRequest) -> CampaignStartResponse:
    return _default_dm.generate_campaign_start(req)


def process_turn(request: TurnRequest) -> TurnResponse:
    return _default_dm.process_turn(request)
