"""Failures at the AI Dungeon Master boundary."""

from typing import Literal


ProviderCategory = Literal[
    "authentication", "permission", "quota", "rate_limit", "unavailable",
    "invalid_request", "timeout", "connection", "unknown",
]

_PROVIDER_MESSAGES = {
    "authentication": "Провайдер отклонил ключ доступа. Проверьте OPENAI_API_KEY.",
    "permission": "Нет доступа к выбранной модели. Проверьте права ключа и MODEL_NAME.",
    "quota": "Исчерпана квота или требуется оплата. Проверьте лимиты и баланс у провайдера.",
    "rate_limit": "Превышен лимит запросов или токенов у провайдера. Попробуйте позже.",
    "unavailable": "Сервис модели временно недоступен. Попробуйте позже.",
    "invalid_request": "Провайдер отклонил запрос. Проверьте модель, адрес API и параметры генерации.",
    "timeout": "Истекло время ожидания ответа модели. Таймаут автоматически не повторяется.",
    "connection": "Не удалось связаться с провайдером модели. Проверьте подключение.",
    "unknown": "Не удалось выполнить запрос к провайдеру модели.",
}


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
    """Provider failure with safe metadata; the original exception stays in __cause__."""

    def __init__(
        self, message: str | None = None, *, category: ProviderCategory = "unknown",
        status_code: int | None = None, retry_after: float | None = None,
        retryable: bool = False,
    ):
        self.category = category
        self.status_code = status_code
        self.retry_after = retry_after
        self.retryable = retryable
        if message is None:
            message = _PROVIDER_MESSAGES[category]
            if status_code is not None:
                message += f" HTTP {status_code}."
            if retry_after is not None and retry_after > 0:
                message += f" Рекомендованное провайдером ожидание: {retry_after:g} с."
        super().__init__(message)


class InvalidResponseError(AIDMError):
    """The LLM response is still invalid after one repair attempt."""
