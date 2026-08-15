"""Errors shared by queue-backed and transport-only execution paths."""


class HardFailure(Exception):
    """A failure which must never be represented as successful work."""


class TreatmentFailure(HardFailure):
    """A hard failure that retains a contract-bound treatment result."""

    def __init__(self, message: str, result: dict) -> None:
        super().__init__(message)
        self.result = result
