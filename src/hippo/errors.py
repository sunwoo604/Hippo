class HippoError(Exception):
    """A user-facing failure: printed without a traceback, exits non-zero.

    `next_steps` are shown under a "Next steps for you" heading, so they stay
    visible even when Claude ran the command and has to relay its output.
    """

    def __init__(self, message: str, next_steps: list[str] | None = None):
        super().__init__(message)
        self.next_steps = next_steps or []
