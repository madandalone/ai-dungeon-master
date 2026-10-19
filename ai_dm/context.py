"""Character budgets for messages; no tokenizer or provider quota assumptions."""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass

from ai_dm.errors import ConfigurationError, ContextBudgetError


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        if value <= 0:
            raise ValueError("Expected a positive integer.")
    except ValueError as exc:
        raise ConfigurationError(f"{name} должен быть положительным целым числом.") from exc
    return value


@dataclass(frozen=True)
class GenerationLimits:
    max_context_chars: int
    max_output_tokens: int

    @classmethod
    def from_env(cls) -> "GenerationLimits":
        return cls(
            max_context_chars=_positive_int("AI_DM_MAX_CONTEXT_CHARS", 24000),
            max_output_tokens=_positive_int("AI_DM_MAX_OUTPUT_TOKENS", 1024),
        )


def message_chars(messages: list[dict[str, str]]) -> int:
    """Count Unicode code points in compact JSON, including roles and escaping."""
    return len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")))


def fit_messages(
    build_messages: Callable[[int], list[dict[str, str]]],
    history_count: int,
    max_chars: int,
    *,
    repair: bool = False,
) -> tuple[list[dict[str, str]], int]:
    """Keep the largest suffix of history that fits; never truncate messages."""
    for count in range(history_count, -1, -1):
        messages = build_messages(count)
        size = message_chars(messages)
        if size <= max_chars:
            return messages, count
    raise ContextBudgetError(size, max_chars, repair=repair)
