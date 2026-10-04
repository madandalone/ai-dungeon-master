import os
import json
import re
from dotenv import load_dotenv
from openai import OpenAI
from ai_dm.models import TurnRequest, TurnResponse, CampaignStartRequest, CampaignStartResponse
from ai_dm.dnd_api import DnD5eClient

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL", "https://router.huggingface.co/v1"),
    default_headers={
        "HTTP-Referer": "https://github.com/madandalone/ai-dungeon-master",
        "X-Title": "AI Dungeon Master",
    }
)

SYSTEM_PROMPT = """
Ты — AI Dungeon Master (Ведущий) в настольной ролевой игре D&D 5e.
Твоя цель: вести атмосферную игру, следовать правилам SRD 5e и реагировать на действия игроков.
Отвечай СТРОГО валидным JSON-объектом в соответствии с требуемой схемой.
"""

def generate_campaign_start(req: CampaignStartRequest) -> CampaignStartResponse:
    """Генерирует завязку кампании, используя канонические статы монстра из D&D API."""
    monster_stats = DnD5eClient.get_monster(req.monster_encounter)
    stats_text = json.dumps(monster_stats, ensure_ascii=False) if monster_stats else "Базовый противник"

    schema_json = json.dumps(CampaignStartResponse.model_json_schema(), ensure_ascii=False, indent=2)

    prompt = f"""
Создай начало приключения D&D 5e.
Сеттинг / Пожелание: {req.setting_theme}
Персонажи игроков: {json.dumps([c.model_dump() for c in req.characters], ensure_ascii=False)}

КАНОНИЧЕСКИЙ МОНСТР ИЗ D&D 5e API (используй его в сюжете):
{stats_text}

ТРЕБОВАНИЯ К ФОРМАТУ:
Верни JSON по схеме:
{schema_json}
"""
    response = client.chat.completions.create(
        model=os.getenv("MODEL_NAME", "Qwen/Qwen3.8-27B"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.8,
    )
    raw = response.choices[0].message.content.strip()
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    return CampaignStartResponse.model_validate_json(clean)


def process_turn(request: TurnRequest) -> TurnResponse:
    """Обработка одного хода игрока."""
    schema_json = json.dumps(TurnResponse.model_json_schema(), ensure_ascii=False, indent=2)

    user_prompt = f"""
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

ТРЕБОВАНИЕ К ФОРМАТУ ОТВЕТА:
Верни ответ СТРОГО в виде JSON по схеме:
{schema_json}
"""
    response = client.chat.completions.create(
        model=os.getenv("MODEL_NAME", "Qwen/Qwen3.8-27B"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.8,
    )
    raw = response.choices[0].message.content.strip()
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    return TurnResponse.model_validate_json(clean)