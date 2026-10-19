"""Failures at the AI Dungeon Master boundary."""


class AIDMError(Exception):
    """The DM could not produce a validated response."""


class ConfigurationError(AIDMError):
    """The LLM client configuration is missing or invalid."""


class ContextBudgetError(AIDMError):
    """Required messages exceed the character budget, even without history."""

    def __init__(self, required_chars: int, limit_chars: int, *, repair: bool = False):
        self.required_chars = required_chars
        self.limit_chars = limit_chars
        self.repair = repair
        stage = "Контекст исправления ответа" if repair else "Обязательный контекст"
        super().__init__(
            f"{stage} превышает бюджет: {required_chars} > {limit_chars} символов. "
            "Увеличьте AI_DM_MAX_CONTEXT_CHARS или сократите входные данные."
        )


class ProviderError(AIDMError):
    """The LLM request failed; no transport retry is performed."""


class InvalidResponseError(AIDMError):
    """The LLM response is still invalid after one repair attempt."""
