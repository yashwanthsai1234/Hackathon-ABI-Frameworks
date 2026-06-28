"""Lane 2 — Claude structured extraction (OWNS prose/ambiguity).

SPEC spec-extraction §2.3. Calls Claude with a strict tool schema when an API
key is configured; otherwise degrades to no-op (regex lane is the floor).

Scaling (Fix 2): the lane is content-addressed-cached, called through a bounded
CONCURRENT pre-pass, and every call has its own retry/backoff policy — so the
LLM is paid at most once per *distinct* note (across patients and runs) and a
429 storm degrades gracefully to the regex floor instead of aborting.

The orchestrator (Claude Code) can also fulfil the LLM need directly: see
`apply_llm_results()` to inject externally-produced structured wounds.
"""
from __future__ import annotations

import hashlib
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from woundpipe.config import Settings

PROMPT_VERSION = "v1"   # bump to invalidate every cache entry on a prompt/tool change

EXTRACT_TOOL = {
    "name": "extract_wounds",
    "description": "Extract every wound's clinical fields. Copy each evidence_span as a "
                   "verbatim substring BEFORE its value. Null if not stated; never infer numbers.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "primary_reasoning": {"type": "string"},
            "wounds": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "is_primary": {"type": "boolean"},
                        "type_evidence_span": {"type": ["string", "null"]},
                        "wound_type": {"type": ["string", "null"],
                                       "enum": ["pressure_ulcer", "diabetic_foot_ulcer",
                                                "venous_leg_ulcer", "arterial_ulcer",
                                                "surgical_wound", "trauma_wound", "other", None]},
                        "location_evidence_span": {"type": ["string", "null"]},
                        "location": {"type": ["string", "null"]},
                        "length_evidence_span": {"type": ["string", "null"]},
                        "length_cm": {"type": ["number", "null"]},
                        "width_evidence_span": {"type": ["string", "null"]},
                        "width_cm": {"type": ["number", "null"]},
                        "depth_evidence_span": {"type": ["string", "null"]},
                        "depth_cm": {"type": ["number", "null"]},
                        "stage_evidence_span": {"type": ["string", "null"]},
                        "stage": {"type": ["string", "null"],
                                  "enum": ["1", "2", "3", "4", "unstageable",
                                           "deep_tissue_injury", "not_applicable", None]},
                        "drainage_evidence_span": {"type": ["string", "null"]},
                        "drainage": {"type": ["string", "null"],
                                     "enum": ["none", "light", "moderate", "heavy", None]},
                    },
                    "required": ["is_primary", "wound_type", "location",
                                 "length_cm", "width_cm", "depth_cm", "stage", "drainage"],
                },
            },
        },
        "required": ["primary_reasoning", "wounds"],
    },
}

_SYSTEM = (
    "You extract wound clinical fields from a clinician's note for Medicare billing triage.\n"
    "- Copy each *_evidence_span as a LITERAL verbatim substring of the NOTE, before its value.\n"
    "- If a field is NOT explicitly stated, set value and span to null. Never infer a number.\n"
    "- Copy numbers exactly; do not compute or round. Use the enums only.\n"
    "- List EVERY distinct wound; mark exactly one is_primary=true; explain in primary_reasoning."
)


def span_gate(value, evidence_span: str | None, note: str) -> bool:
    """Drop any measurement whose span is not a literal substring of the note."""
    if value is None:
        return True
    if not evidence_span:
        return False
    import re
    norm = lambda s: re.sub(r"\s*(cm|mm)\b", r"\1", re.sub(r"\s+", " ", str(s).lower().replace("×", "x"))).strip()
    hay, span_n, val_n = norm(note), norm(evidence_span), norm(value)
    return span_n in hay and (val_n in span_n or val_n in hay)


# ----------------------------------------------------------------- reused client + backoff
_CLIENT = None
_CLIENT_LOCK = threading.Lock()


def _get_client(settings: Settings):
    """Lazily build and reuse ONE Anthropic client (thread-safe for concurrent
    requests). Returns None if the SDK or key is unavailable (regex floor)."""
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                try:
                    import anthropic
                except ImportError:
                    return None
                _CLIENT = anthropic.Anthropic(
                    api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_s
                )
    return _CLIENT


def _is_retryable(exc) -> bool:
    """429 / 5xx / 529-overloaded / timeout / connection are transient; a 4xx
    request error is OUR bug and fails fast (mirrors the ingest HTTP policy)."""
    try:
        import anthropic
    except ImportError:
        return False
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    return status in (429, 500, 502, 503, 504, 529)


def _retry_after(exc, attempt: int) -> float:
    """Honor a Retry-After header when present; else exp backoff base 0.5 cap 8 + full jitter."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            ra = float(int(resp.headers.get("retry-after")))
            return max(1.0, min(10.0, ra))
        except (TypeError, ValueError):
            pass
    return random.uniform(0.0, min(8.0, 0.5 * (2 ** (attempt - 1))))


def _call_claude(text: str, fmt: str, settings: Settings) -> list[dict]:
    """The real Claude call with retry/backoff. Returns span-gated wounds, or []
    on permanent failure (fail-closed to the regex floor — never raises out)."""
    client = _get_client(settings)
    if client is None:
        return []
    last = None
    for attempt in range(1, int(getattr(settings, "llm_max_attempts", 5)) + 1):
        try:
            resp = client.messages.create(
                model=settings.model_bulk,
                max_tokens=1024,
                temperature=0,
                system=_SYSTEM,
                tools=[EXTRACT_TOOL],
                tool_choice={"type": "tool", "name": "extract_wounds"},
                messages=[{"role": "user",
                           "content": f'NOTE (format={fmt}):\n"""{text}"""\nCall extract_wounds.'}],
            )
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    return _gate_measurements(block.input.get("wounds", []), text)
            return []
        except Exception as exc:  # noqa: BLE001
            if not _is_retryable(exc) or attempt >= int(getattr(settings, "llm_max_attempts", 5)):
                return []
            _time_sleep(_retry_after(exc, attempt))
    return []


def _time_sleep(seconds: float) -> None:  # indirection so tests can monkeypatch
    import time
    time.sleep(seconds)


# ----------------------------------------------------------------- cache
def cache_key(text: str, settings: Settings) -> str:
    raw = f"{settings.model_bulk}\x00{PROMPT_VERSION}\x00{text or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_get(con, ck: str) -> list[dict] | None:
    row = con.execute("SELECT wounds_json FROM llm_cache WHERE cache_key=?", (ck,)).fetchone()
    return json.loads(row[0]) if row else None


def cache_put(con, ck: str, wounds: list[dict], model: str, now: str) -> None:
    con.execute(
        "INSERT INTO llm_cache (cache_key, wounds_json, model, created_at) VALUES (?,?,?,?) "
        "ON CONFLICT(cache_key) DO NOTHING",
        (ck, json.dumps(wounds, default=str), model, now),
    )


def cached_wounds(con, text: str, settings: Settings) -> list[dict]:
    """LLM wounds for a note from the cache (filled by prefill_cache); [] on miss."""
    return cache_get(con, cache_key(text, settings)) or []


# ----------------------------------------------------------------- concurrent pre-pass
def prefill_cache(con, notes, settings: Settings, *, caller=None) -> dict:
    """Concurrently extract every DISTINCT, not-yet-cached note via Claude and
    land the results in ``llm_cache``. ``notes`` is an iterable of (text, fmt).
    ``caller`` defaults to the real Claude call (injectable for tests).

    Cache writes happen on THIS thread (SQLite single-writer); the network calls
    fan out across a bounded pool. Returns a small summary."""
    from woundpipe.ingest.checkpoint import now_iso
    caller = caller or _call_claude
    # dedup by cache_key, skip anything already cached
    pending: dict[str, tuple[str, str]] = {}
    for text, fmt in notes:
        if not text:
            continue
        ck = cache_key(text, settings)
        if ck in pending or cache_get(con, ck) is not None:
            continue
        pending[ck] = (text, fmt)
    if not pending:
        return {"called": 0, "cached_hits": 0, "failed": 0}

    now = now_iso()
    n_ok = n_fail = 0
    workers = max(1, int(getattr(settings, "llm_concurrency", 4)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(caller, text, fmt, settings): ck
                for ck, (text, fmt) in pending.items()}
        for fut in as_completed(futs):
            ck = futs[fut]
            try:
                wounds = fut.result() or []
                n_ok += 1
            except Exception:  # noqa: BLE001 - a failed note never aborts the pre-pass
                wounds, n_fail = [], n_fail + 1
            cache_put(con, ck, wounds, settings.model_bulk, now)
    con.commit()
    return {"called": len(pending), "ok": n_ok, "failed": n_fail}


def _gate_measurements(wounds: list[dict], note: str) -> list[dict]:
    for w in wounds:
        for f, span_key in (("length_cm", "length_evidence_span"),
                            ("width_cm", "width_evidence_span"),
                            ("depth_cm", "depth_evidence_span")):
            if not span_gate(w.get(f), w.get(span_key), note):
                w[f] = None  # hallucination guard
    return wounds


def apply_llm_results(wounds: list[dict], note: str) -> list[dict]:
    """Inject externally-produced (orchestrator-fulfilled) LLM wounds through the span gate."""
    return _gate_measurements(wounds, note)
