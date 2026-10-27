class RulesUnavailableError(Exception):
    """The rules source cannot answer right now (model or index not loadable, backing service down).

    A provider raises this for expected unavailability only. Any other exception is a bug and must propagate.
    """
