class WorkflowError(Exception):
    """A classified error; messages must not include credentials or page content."""

    def __init__(self, code: str, *, transient: bool = False):
        self.code = code
        self.transient = transient
        super().__init__(code)


class ConfigError(WorkflowError):
    def __init__(self, fields: list[str]):
        self.fields = sorted(set(fields))
        super().__init__("config_invalid")


class LeaseLost(WorkflowError):
    def __init__(self):
        super().__init__("lease_lost")
