from ai_dm.errors import AIDMError, ConfigurationError, ContextBudgetError, InvalidResponseError, ProviderError


class IntegrationError(Exception):
    """Turn could not be processed. Nothing was committed; Backend should answer 5xx/502."""


class DungeonMasterError(IntegrationError):
    """The DM reported a failure (ai_dm.errors.AIDMError). Nothing was committed.

    `message` and the metadata come from the DM's own safe error contract, so the Backend may show them.
    `category` lets the Backend choose a response: e.g. "rate_limit" -> 429/503 with retry_after,
    "authentication"/"configuration" -> operator problem, "invalid_response" -> retry the turn.
    The original exception stays in __cause__ for logs.
    """

    def __init__(self, message: str, *, category: str = "unknown", retryable: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.retry_after = retry_after


def dungeon_master_error(exc: AIDMError) -> DungeonMasterError:
    """Translate the DM's own error into the integration error, keeping its safe message and metadata."""
    if isinstance(exc, ProviderError):
        return DungeonMasterError(str(exc), category=exc.category, retryable=exc.retryable, retry_after=exc.retry_after)
    category = {
        ContextBudgetError: "context_budget",
        ConfigurationError: "configuration",
        InvalidResponseError: "invalid_response",
    }.get(type(exc), "unknown")
    return DungeonMasterError(str(exc), category=category)


class UnmappableResponse(IntegrationError):
    """DM response refers to characters that cannot be resolved to engine character IDs."""


class CampaignRejected(IntegrationError):
    """The Engine refused to enter the opening scene. Nothing was committed; `errors` holds the Engine's codes."""

    def __init__(self, message: str, errors: tuple = ()):
        super().__init__(message)
        self.errors = errors


class IntegrationConfigError(Exception):
    """Startup misconfiguration. Not an IntegrationError: a per-turn handler must never turn it into a 502."""
