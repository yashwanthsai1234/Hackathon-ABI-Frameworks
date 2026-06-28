"""Exception hierarchy (SPEC.md spec-architecture §H).

transient  -> retryable; permanent -> fail-fast per-record, never per-run.
Only a corrupt final export aborts the whole run.
"""
from __future__ import annotations

from dataclasses import dataclass


class WoundpipeError(Exception):
    """Root of all pipeline errors."""


# ---- transient (retryable) ----
class TransientError(WoundpipeError):
    pass


class RetryableHTTP(TransientError):
    """429 / 500 — retry. retry_after only set for 429."""
    def __init__(self, status: int, retry_after: float | None = None):
        super().__init__(f"retryable HTTP {status}")
        self.status = status
        self.retry_after = retry_after


# ---- permanent (fail-fast, never retried) ----
class PermanentError(WoundpipeError):
    pass


class FatalHTTP(PermanentError):
    """422 / other 4xx — our bug, never retried."""
    def __init__(self, status: int, body: str = ""):
        super().__init__(f"fatal HTTP {status}")
        self.status = status
        self.body = body


class NormalizationError(PermanentError):
    pass


class ExtractionError(PermanentError):
    pass


@dataclass
class StageError:
    """Non-fatal per-record failure, collected into a StageResult."""
    stage: str
    key: str
    kind: str
    message: str
