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


class UnmappableResponse(IntegrationError):
    """DM response refers to characters that cannot be resolved to engine character IDs."""


class IntegrationConfigError(Exception):
    """Startup misconfiguration. Not an IntegrationError: a per-turn handler must never turn it into a 502."""
