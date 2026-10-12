"""Failures at the AI Dungeon Master boundary."""


class AIDMError(Exception):
    """The DM could not produce a validated response."""


class ConfigurationError(AIDMError):
    """The LLM client configuration is missing or invalid."""


class ProviderError(AIDMError):
    """The LLM request failed; no transport retry is performed."""


class InvalidResponseError(AIDMError):
    """The LLM response is still invalid after one repair attempt."""
