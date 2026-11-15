"""Load prompt templates relative to this package, once per generation operation."""

import json
from dataclasses import dataclass
from pathlib import Path
from string import Template

from ai_dm.errors import ConfigurationError
from ai_dm.models import CampaignStartRequest, TurnRequest
from ai_dm.memory import HistorySelection
from ai_dm.validation import MAX_DIFFICULTY, MIN_DIFFICULTY


_TEMPLATE_DIR = Path(__file__).resolve().parent
_ACTOR_UNKNOWN = "Не указан: определи персонажа по тексту действия."
_CHECK_UNAVAILABLE = (
    "Недоступна: действующий персонаж не указан или у него нет характеристик. "
    "Верни requires_check: false и check: null."
)


def _read_template(name: str) -> str:
    try:
        return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError(f"Не удалось загрузить шаблон промпта: {name}.") from exc


@dataclass(frozen=True)
class PromptTemplates:
    system: str
    campaign: str
    turn: str
    request: str
    repair: str
    check_offer: str
    check_result: str

    @classmethod
    def load(cls) -> "PromptTemplates":
        texts = {
            name: _read_template(f"{name}.txt")
            for name in ("system", "campaign", "turn", "request", "repair", "check_offer", "check_result")
        }
        # These messages are inserted into other text; they carry no final newline of their own.
        for name in ("request", "repair", "check_offer", "check_result"):
            texts[name] = texts[name].removesuffix("\n")
        return cls(**texts)

    def _render(self, name: str, **values: str) -> str:
        try:
            # Substitute once: braces and dollar signs in supplied game data stay literal.
            return Template(getattr(self, name)).substitute(values)
        except (KeyError, ValueError) as exc:
            raise ConfigurationError(f"Некорректный шаблон промпта: {name}.txt.") from exc

    def request_prompt(self, prompt: str, schema: str) -> str:
        return self._render("request", prompt=prompt, schema=schema)

    def repair_prompt(self, details: str) -> str:
        return self._render("repair", details=details)

    def campaign_prompt(self, request: CampaignStartRequest, monster_stats: dict | None) -> str:
        return self._render(
            "campaign",
            setting_theme=request.setting_theme,
            characters=json.dumps([c.model_dump() for c in request.characters], ensure_ascii=False),
            monster_stats=json.dumps(monster_stats, ensure_ascii=False) if monster_stats else "Базовый противник",
        )

    def check_prompt(self, request: TurnRequest) -> str:
        """Second stage reports the roll; first stage offers a check only if Engine can roll it."""
        if request.check_result is not None:
            return self._render(
                "check_result", result=json.dumps(request.check_result.model_dump(), ensure_ascii=False),
            )
        actor = next((character for character in request.game_state.active_characters
                      if character.name == request.acting_character), None)
        if actor is None or not actor.stats:
            return _CHECK_UNAVAILABLE
        return self._render(
            "check_offer",
            stats=json.dumps(actor.stats, ensure_ascii=False),
            min_difficulty=str(MIN_DIFFICULTY),
            max_difficulty=str(MAX_DIFFICULTY),
        )

    def turn_prompt(self, request: TurnRequest, history: HistorySelection, count: int) -> str:
        recent, memories = history.blocks(count)
        return self._render(
            "turn",
            rules_summary=request.rules_summary,
            hidden_plot=request.hidden_plot,
            history=json.dumps(recent, ensure_ascii=False),
            memories=json.dumps(memories, ensure_ascii=False),
            location=request.game_state.location,
            room_context=request.game_state.room_context,
            characters=json.dumps([c.model_dump() for c in request.game_state.active_characters], ensure_ascii=False),
            character_names=json.dumps([c.name for c in request.game_state.active_characters], ensure_ascii=False),
            acting_character=(json.dumps(request.acting_character, ensure_ascii=False)
                              if request.acting_character else _ACTOR_UNKNOWN),
            player_action=request.player_action,
            check=self.check_prompt(request),
        )
