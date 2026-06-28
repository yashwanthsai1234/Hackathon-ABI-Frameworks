"""The single HTTP chokepoint (SPEC §Ingestion §1).

Every PCC call goes through `fetch_one`. There is exactly one reused
`httpx.Client` with an EXPLICIT `Timeout` (NEVER None — a call without a timeout
is a failed build, SPEC R9). Retryable conditions are modelled as raised
sentinels (`RetryableHTTP`) so tenacity's `retry_if_exception_type` governs every
retry decision uniformly:

  * 429            -> retry, honor `Retry-After` exactly (int, clamp 1..cap, default 2)
  * 500            -> retry, exponential base 0.5s cap 8s + FULL jitter
  * timeout/transport -> retry, same backoff (network blip)
  * 422 / other 4xx -> FatalHTTP, FAIL-FAST (our bug), never retried
  * 200            -> return parsed JSON (list | dict)

Stop = `stop_after_attempt(max_attempts) | stop_after_delay(per_call_deadline_s)`,
`reraise=True`. `before_sleep` bumps the retry counter; status counters are bumped
at the raise site so 422 (never retried) is still counted. The manifest is
optional so the client is trivially unit-testable.
"""
from __future__ import annotations

import random
import threading

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
)

from woundpipe.errors import FatalHTTP, RetryableHTTP

# short endpoint name -> path. fetch_one also accepts a literal '/path'.
ENDPOINTS = {
    "patients": "/pcc/patients",
    "diagnoses": "/pcc/diagnoses",
    "coverage": "/pcc/coverage",
    "notes": "/pcc/notes",
    "assessments": "/pcc/assessments",
}

_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 8.0
_RETRY_AFTER_DEFAULT = 2.0

_CLIENT: httpx.Client | None = None
_CLIENT_LOCK = threading.Lock()


# ----------------------------------------------------------------- client
def _timeout(settings) -> httpx.Timeout:
    """Explicit per-phase timeout. NEVER None (SPEC R9 / §1)."""
    t = float(getattr(settings, "http_timeout_s", 30.0))
    return httpx.Timeout(t, connect=min(5.0, t))


def get_client(settings) -> httpx.Client:
    """Lazily build and reuse one Client (double-checked under a lock)."""
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = httpx.Client(
                    base_url=settings.pcc_base_url,
                    timeout=_timeout(settings),
                    limits=httpx.Limits(
                        max_connections=int(getattr(settings, "max_concurrency", 8))
                    ),
                    follow_redirects=True,  # /health is a 307; endpoints may redirect
                    headers={"accept": "application/json"},
                )
    return _CLIENT


def close_client() -> None:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            try:
                _CLIENT.close()
            finally:
                _CLIENT = None


# ----------------------------------------------------------------- policy
def _parse_retry_after(value, cap: float) -> float:
    """Parse Retry-After: integer seconds, clamp 1..cap, default 2 (SPEC §1)."""
    if value is None:
        return _RETRY_AFTER_DEFAULT
    try:
        ra = float(int(str(value).strip()))
    except (TypeError, ValueError):
        return _RETRY_AFTER_DEFAULT
    return max(1.0, min(float(cap), ra))


def _raise_for_policy(resp: httpx.Response, settings, manifest):
    """Map an HTTP response to the retry policy. Returns parsed JSON on 200."""
    sc = resp.status_code
    if sc == 200:
        return resp.json()
    if sc == 429:
        if manifest is not None:
            manifest.bump("calls_429")
        ra = _parse_retry_after(
            resp.headers.get("Retry-After"),
            float(getattr(settings, "retry_after_cap_s", 10.0)),
        )
        raise RetryableHTTP(429, retry_after=ra)
    if sc == 500 or 500 < sc < 600:
        if manifest is not None:
            manifest.bump("calls_500")
        raise RetryableHTTP(sc)
    # 422 and every other 4xx -> our bug, fail-fast, never retried.
    if manifest is not None and sc == 422:
        manifest.bump("calls_422")
    raise FatalHTTP(sc, body=resp.text[:500])


def _wait_policy(retry_state) -> float:
    """Honor Retry-After when present; else exponential base 0.5 cap 8 + full jitter."""
    exc = retry_state.outcome.exception()
    if isinstance(exc, RetryableHTTP) and exc.retry_after is not None:
        return float(exc.retry_after)
    base = min(_BACKOFF_CAP, _BACKOFF_BASE * (2 ** (retry_state.attempt_number - 1)))
    return random.uniform(0.0, base)  # full jitter — anti-thundering-herd across workers


def _make_before_sleep(manifest):
    def _before_sleep(retry_state) -> None:
        if manifest is not None:
            manifest.bump("retries")

    return _before_sleep


def _build_retryer(settings, manifest) -> Retrying:
    return Retrying(
        retry=retry_if_exception_type(RetryableHTTP),
        wait=_wait_policy,
        stop=(
            stop_after_attempt(int(getattr(settings, "max_attempts", 6)))
            | stop_after_delay(float(getattr(settings, "per_call_deadline_s", 45.0)))
        ),
        reraise=True,
        before_sleep=_make_before_sleep(manifest),
    )


# ----------------------------------------------------------------- entrypoint
def fetch_one(settings, endpoint: str, params: dict, *, manifest=None, client=None):
    """Fetch one endpoint with the full retry policy.

    Returns parsed JSON (list | dict) on success. Raises FatalHTTP on a 4xx
    (caller marks the task failed and continues) or RetryableHTTP after the
    retry budget is exhausted (`reraise=True`). NEVER swallows — the call site
    owns the fail-closed bookkeeping.
    """
    cl = client if client is not None else get_client(settings)
    path = ENDPOINTS.get(endpoint, endpoint)

    def _attempt():
        if manifest is not None:
            manifest.bump("calls_total")  # counts every send, including retries
        try:
            resp = cl.get(path, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # network-level failure: retryable, no Retry-After -> backoff path
            raise RetryableHTTP(0) from exc
        return _raise_for_policy(resp, settings, manifest)

    return _build_retryer(settings, manifest)(_attempt)
