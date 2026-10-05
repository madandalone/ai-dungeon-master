class IntegrationError(Exception):
    """Turn could not be processed. Nothing was committed; Backend should answer 5xx/502."""


class DungeonMasterError(IntegrationError):
    """DM call failed or returned output that does not match the TurnResponse contract."""


class UnmappableResponse(IntegrationError):
    """DM response refers to characters that cannot be resolved to engine character IDs."""
