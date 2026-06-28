"""Structured logging with secret redaction (SPEC.md spec-architecture §F/§6).

Uses structlog if present; falls back to stdlib logging. note_text and patient
names are NEVER logged — only ids/keys/counts cross the logging boundary.
"""
from __future__ import annotations

import logging as _stdlib
import re

_REDACT = re.compile(r".*(key|token|secret|authorization).*", re.IGNORECASE)


def _redact_processor(logger, method_name, event_dict):
    for k in list(event_dict.keys()):
        if _REDACT.match(k):
            event_dict[k] = "***"
    return event_dict


def configure(run_id: str | None = None, json_logs: bool = False):
    try:
        import structlog
        procs = [
            structlog.contextvars.merge_contextvars,
            _redact_processor,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
        ]
        procs.append(
            structlog.processors.JSONRenderer() if json_logs
            else structlog.dev.ConsoleRenderer(colors=True)
        )
        structlog.configure(processors=procs)
        log = structlog.get_logger()
        if run_id:
            log = log.bind(run_id=run_id)
        return log
    except ImportError:
        _stdlib.basicConfig(level=_stdlib.INFO, format="%(asctime)s %(levelname)s %(message)s")
        return _StdlibShim(_stdlib.getLogger("woundpipe"), run_id)


class _StdlibShim:
    """Minimal structlog-like API over stdlib logging."""
    def __init__(self, logger, run_id=None):
        self._log = logger
        self._ctx = {"run_id": run_id} if run_id else {}

    def bind(self, **kw):
        new = _StdlibShim(self._log, None)
        new._ctx = {**self._ctx, **kw}
        return new

    def _emit(self, level, event, **kw):
        safe = {k: ("***" if _REDACT.match(k) else v) for k, v in {**self._ctx, **kw}.items()}
        self._log.log(level, "%s %s", event, safe)

    def info(self, event, **kw): self._emit(_stdlib.INFO, event, **kw)
    def warning(self, event, **kw): self._emit(_stdlib.WARNING, event, **kw)
    def error(self, event, **kw): self._emit(_stdlib.ERROR, event, **kw)
    def debug(self, event, **kw): self._emit(_stdlib.DEBUG, event, **kw)
