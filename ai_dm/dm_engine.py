import os
import json
import re
from dotenv import load_dotenv
from openai import OpenAI
from ai_dm.models import TurnRequest, TurnResponse

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
    default_headers={
        "HTTP-Referer": "https://github.com/madandalone/ai-dungeon-master",
        "X-Title": "AI Dungeon Master",
    }
)

SYSTEM_PROMPT = """
Ты — AI Dungeon Master (Ведущий) в настольной ролевой игре.
Твоя задача — вести захватывающее повествование, соблюдать правила и реагировать на действия игроков.

Правила поведения:
1. Опирайся на скрытый сюжет, но никогда не раскрывай его напрямую — давай намёки через окружение и детали.
2. Будь справедливым: за рискованные или глупые действия наказывай уроном (HP) или потерей предметов.
3. Отвечай СТРОГО в формате JSON без лишних пояснений до или после него.
"""

def process_turn(request: TurnRequest) -> TurnResponse:
    # Получаем схему JSON напрямую из модели Pydantic
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

### ТРЕБОВАНИЕ К ФОРМАТУ ОТВЕТА:
Верни ответ СТРОГО в виде JSON-объекта, соответствующего следующей JSON-схеме:
{schema_json}
Не оборачивай ответ ни во что, кроме чистого JSON (или блока ```json ... ```).
"""

    response = client.chat.completions.create(
        model=os.getenv("MODEL_NAME", "qwen/qwen3.8-27b:free"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
    )

    raw_content = response.choices[0].message.content.strip()

    # Очищаем ответ от markdown-блоков ```json ... ```, если модель их добавила
    clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_content, flags=re.MULTILINE).strip()

    # Pydantic сам валидирует JSON и превращает его в объект TurnResponse
    return TurnResponse.model_validate_json(clean_json)