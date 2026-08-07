"""Errors shared by queue-backed and transport-only execution paths."""


class HardFailure(Exception):
    """A failure which must never be represented as successful work."""
