"""
config/loaders.py
Typed loaders for the project's YAML configuration.

Phase-1 scope: only the guardrail config is needed (the trust layer). The
loader validates the file's shape on read and fails loudly if anything the
validator depends on is missing, so misconfiguration can never silently
weaken the guardrails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

# Project root = two levels up from this file (config/ -> project root).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_GUARDRAIL_PATH = _PROJECT_ROOT / "config" / "guardrail_config.yaml"


@dataclass(frozen=True)
class GuardrailConfig:
    """Immutable view of guardrail_config.yaml.

    Frozen on purpose: nothing downstream may mutate the guardrails at runtime.
    """

    allowed_tables: frozenset[str]
    blocked_table_patterns: tuple[str, ...]
    statement_types_allowed: frozenset[str]
    single_statement_only: bool
    enforce_limit: bool
    max_limit: int
    disallow_subquery_to_blocked: bool
    connection_mode: str
    statement_timeout_ms: int
    max_tokens_per_call: int
    target_latency_ms: int

    # Derived convenience set used by the validator for case-insensitive checks.
    allowed_tables_lower: frozenset[str] = field(default=frozenset(), compare=False)


def _require(d: dict, key: str, path: Path):
    if key not in d:
        raise ValueError(f"guardrail config missing required key '{key}' in {path}")
    return d[key]


@lru_cache(maxsize=1)
def load_guardrail_config(path: str | Path | None = None) -> GuardrailConfig:
    """Load and validate the guardrail config.

    Cached: the config is read once per process. Pass an explicit path in tests
    to bypass the cache via load_guardrail_config.cache_clear().
    """
    cfg_path = Path(path) if path is not None else _DEFAULT_GUARDRAIL_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"guardrail config not found: {cfg_path}")

    raw = yaml.safe_load(cfg_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"guardrail config is not a mapping: {cfg_path}")

    allowed = _require(raw, "allowed_tables", cfg_path)
    if not allowed:
        # Fail closed: an empty allowlist means nothing is queryable, which is
        # safe, but almost certainly a misconfiguration we want to surface.
        raise ValueError(f"allowed_tables is empty in {cfg_path}")

    query_rules = _require(raw, "query_rules", cfg_path)
    conn = raw.get("connection", {}) or {}
    budget = raw.get("cost_budget", {}) or {}

    allowed_set = frozenset(str(t).strip() for t in allowed)

    return GuardrailConfig(
        allowed_tables=allowed_set,
        allowed_tables_lower=frozenset(t.lower() for t in allowed_set),
        blocked_table_patterns=tuple(raw.get("blocked_table_patterns", []) or []),
        statement_types_allowed=frozenset(
            str(s).upper() for s in _require(query_rules, "statement_types_allowed", cfg_path)
        ),
        single_statement_only=bool(query_rules.get("single_statement_only", True)),
        enforce_limit=bool(query_rules.get("enforce_limit", True)),
        max_limit=int(query_rules.get("max_limit", 100)),
        disallow_subquery_to_blocked=bool(
            query_rules.get("disallow_subquery_to_blocked", True)
        ),
        connection_mode=str(conn.get("mode", "read_only")),
        statement_timeout_ms=int(conn.get("statement_timeout_ms", 5000)),
        max_tokens_per_call=int(budget.get("max_tokens_per_call", 1500)),
        target_latency_ms=int(budget.get("target_latency_ms", 8000)),
    )


# --------------------------------------------------------------------------- #
# Semantic-layer loaders (entities / metrics / time windows)
# Added for the deterministic resolver stage. Each returns plain parsed YAML;
# the resolvers wrap these into their own typed catalogs.
# --------------------------------------------------------------------------- #

_SEMANTIC_DIR = _PROJECT_ROOT / "semantic"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"semantic file not found: {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"semantic file is not a mapping: {path}")
    return data


@lru_cache(maxsize=1)
def load_entities(path: str | Path | None = None) -> dict:
    return _load_yaml(Path(path) if path else _SEMANTIC_DIR / "entities.yaml")


@lru_cache(maxsize=1)
def load_metrics(path: str | Path | None = None) -> dict:
    return _load_yaml(Path(path) if path else _SEMANTIC_DIR / "metrics.yaml")


@lru_cache(maxsize=1)
def load_time_windows(path: str | Path | None = None) -> dict:
    return _load_yaml(Path(path) if path else _SEMANTIC_DIR / "time_windows.yaml")
