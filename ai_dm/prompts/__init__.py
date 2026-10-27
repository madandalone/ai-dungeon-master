"""Load prompt templates relative to this package, once per generation operation."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template

from ai_dm.errors import ConfigurationError
from ai_dm.models import CampaignStartRequest, TurnRequest


_TEMPLATE_DIR = Path(__file__).resolve().parent


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

    @classmethod
    def load(cls) -> "PromptTemplates":
        texts = {
            name: _read_template(f"{name}.txt")
            for name in ("system", "campaign", "turn", "request", "repair")
        }
        # These two original messages had no final newline; keep their exact formatting.
        for name in ("request", "repair"):
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

    def turn_prompt(self, request: TurnRequest, history: Sequence[dict[str, str]], count: int) -> str:
        return self._render(
            "turn",
            rules_summary=request.rules_summary,
            hidden_plot=request.hidden_plot,
            history=json.dumps(history[-count:] if count else [], ensure_ascii=False),
            location=request.game_state.location,
            room_context=request.game_state.room_context,
            characters=json.dumps([c.model_dump() for c in request.game_state.active_characters], ensure_ascii=False),
            character_names=json.dumps([c.name for c in request.game_state.active_characters], ensure_ascii=False),
            player_action=request.player_action,
        )
