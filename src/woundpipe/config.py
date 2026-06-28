"""Runtime configuration (SPEC.md §C.2, spec-architecture §F).

All knobs live here. Secrets are redacted from logs (see logging.py).
Loaded from .env / WP_* env vars / defaults.
"""
from __future__ import annotations

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _HAS_PYDANTIC = True
except ImportError:  # graceful fallback if pydantic-settings absent
    _HAS_PYDANTIC = False


SECRET_FIELDS = {"anthropic_api_key"}


if _HAS_PYDANTIC:
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env", env_prefix="WP_", extra="ignore", protected_namespaces=()
        )

        # secrets
        anthropic_api_key: str | None = None        # WP_ANTHROPIC_API_KEY (never logged)
        # endpoints / storage
        pcc_base_url: str = "https://hackathon.prod.pulsefoundry.ai"
        db_path: str = "data/woundpipe.db"
        export_path: str = "data/export.json"
        runs_dir: str = "data/runs"
        # ingestion
        max_concurrency: int = 8
        http_timeout_s: float = 30.0
        max_attempts: int = 6
        per_call_deadline_s: float = 45.0
        retry_after_cap_s: float = 10.0
        # extraction
        use_llm: bool = True
        model_bulk: str = "claude-haiku-4-5-20251001"
        model_escalate: str = "claude-sonnet-4-6"
        llm_timeout_s: float = 20.0
        llm_concurrency: int = 4                     # bounded parallel Claude calls (Fix 2)
        llm_max_attempts: int = 5                    # retry budget per LLM call
        # routing
        auto_accept_threshold: float = 0.80         # mirrors config table (calibratable)
        format_conf_min: float = 0.70
        measure_tol_cm: float = 0.20
else:
    from dataclasses import dataclass

    @dataclass
    class Settings:  # type: ignore[no-redef]
        anthropic_api_key: str | None = None
        pcc_base_url: str = "https://hackathon.prod.pulsefoundry.ai"
        db_path: str = "data/woundpipe.db"
        export_path: str = "data/export.json"
        runs_dir: str = "data/runs"
        max_concurrency: int = 8
        http_timeout_s: float = 30.0
        max_attempts: int = 6
        per_call_deadline_s: float = 45.0
        retry_after_cap_s: float = 10.0
        use_llm: bool = True
        model_bulk: str = "claude-haiku-4-5-20251001"
        model_escalate: str = "claude-sonnet-4-6"
        llm_timeout_s: float = 20.0
        llm_concurrency: int = 4                     # bounded parallel Claude calls (Fix 2)
        llm_max_attempts: int = 5                    # retry budget per LLM call
        auto_accept_threshold: float = 0.80
        format_conf_min: float = 0.70
        measure_tol_cm: float = 0.20


def load_settings(**overrides) -> Settings:
    s = Settings(**overrides) if not _HAS_PYDANTIC else Settings(**overrides)
    return s
