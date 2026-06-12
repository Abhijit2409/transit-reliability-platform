"""
copilot/validator.py
The trust layer. Turns CandidateSQL into a ValidatedQuery or a rejection.

Philosophy: FAIL CLOSED. The validator only approves SQL it can positively
prove is a single, read-only SELECT touching exclusively allowlisted tables,
with an enforced LIMIT. Anything it cannot prove safe — unparseable input, an
unrecognized construct, a table it cannot resolve — is rejected.

Validation order (cheapest / most decisive first):
  1. non-empty
  2. parses, and is exactly ONE statement
  3. the statement is a SELECT (not DML/DDL/PRAGMA/ATTACH/COPY/...)
  4. no forbidden constructs (no SQL injection-style stacking already caught by #2)
  5. every referenced table is allowlisted AND matches no blocked pattern
  6. LIMIT present and <= max; injected if absent
"""

from __future__ import annotations

import fnmatch

import sqlglot
from sqlglot import exp

from config.loaders import GuardrailConfig, load_guardrail_config
from copilot.contracts import (
    _VALIDATION_SENTINEL,
    CandidateSQL,
    RejectionReason,
    ValidatedQuery,
    ValidationResult,
)

_DIALECT = "duckdb"

# Expression types that must never appear, even nested. SELECT is allowed;
# anything that writes, changes state, attaches files, or reaches the OS is not.
_FORBIDDEN_EXPR_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,        # PRAGMA, SET, CALL, VACUUM, etc. parse as Command
    exp.Set,
    exp.Use,
)


def _reject(reason: RejectionReason, detail: str = "") -> ValidationResult:
    return ValidationResult(ok=False, reason=reason, detail=detail)


def _collect_table_names(tree: exp.Expression) -> list[str]:
    """Return the bare table names referenced anywhere in the tree (incl. CTEs,
    subqueries). CTE-defined names are excluded from the allowlist check because
    they are local aliases, not physical tables."""
    cte_names = {
        cte.alias_or_name.lower()
        for cte in tree.find_all(exp.CTE)
    }
    tables: list[str] = []
    for t in tree.find_all(exp.Table):
        name = t.name
        if name and name.lower() not in cte_names:
            tables.append(name)
    return tables


def validate(
    candidate: CandidateSQL | str,
    config: GuardrailConfig | None = None,
) -> ValidationResult:
    """Validate candidate SQL against the guardrail config. Pure / no I/O."""
    cfg = config or load_guardrail_config()
    sql = candidate.sql if isinstance(candidate, CandidateSQL) else candidate

    # 1. non-empty
    if sql is None or not sql.strip():
        return _reject(RejectionReason.EMPTY, "empty SQL")
    sql = sql.strip()

    # 2. parse + single statement
    try:
        statements = sqlglot.parse(sql, read=_DIALECT)
    except Exception as e:  # sqlglot raises ParseError and subclasses
        return _reject(RejectionReason.PARSE_ERROR, str(e))

    statements = [s for s in statements if s is not None]
    if len(statements) == 0:
        return _reject(RejectionReason.PARSE_ERROR, "no parseable statement")
    if cfg.single_statement_only and len(statements) > 1:
        return _reject(
            RejectionReason.MULTIPLE_STATEMENTS,
            f"{len(statements)} statements found; only one allowed",
        )

    tree = statements[0]

    # 3. must be a SELECT at the top level
    #    (a top-level SELECT may be wrapped in parens / be a UNION of selects)
    top = tree
    if not isinstance(top, (exp.Select, exp.Union, exp.Subquery)):
        return _reject(
            RejectionReason.NOT_A_SELECT,
            f"top-level statement is {type(top).__name__}, not SELECT",
        )

    # Enforce the configured allowed statement types as a second gate.
    if "SELECT" not in cfg.statement_types_allowed:
        return _reject(
            RejectionReason.DISALLOWED_STATEMENT_TYPE,
            "SELECT not in allowed statement types",
        )

    # 4. no forbidden constructs anywhere in the tree
    for node in tree.walk():
        expr = node[0] if isinstance(node, tuple) else node
        if isinstance(expr, _FORBIDDEN_EXPR_TYPES):
            return _reject(
                RejectionReason.FORBIDDEN_CONSTRUCT,
                f"forbidden construct: {type(expr).__name__}",
            )

    # 5. table allowlist + blocked patterns
    referenced = _collect_table_names(tree)
    for name in referenced:
        lname = name.lower()
        # blocked patterns take precedence and are explicit defence-in-depth
        for pat in cfg.blocked_table_patterns:
            if fnmatch.fnmatch(lname, pat.lower()):
                return _reject(
                    RejectionReason.TABLE_BLOCKED_PATTERN,
                    f"table '{name}' matches blocked pattern '{pat}'",
                )
        if lname not in cfg.allowed_tables_lower:
            return _reject(
                RejectionReason.TABLE_NOT_ALLOWLISTED,
                f"table '{name}' is not in the allowlist",
            )

    # 6. LIMIT enforcement (only meaningful on a Select; Union handled too)
    enforced_limit = cfg.max_limit
    if cfg.enforce_limit:
        limit_node = tree.args.get("limit") if isinstance(tree, exp.Select) else None
        if isinstance(tree, exp.Select):
            existing = _read_limit(tree)
            if existing is None:
                tree.set("limit", exp.Limit(expression=exp.Literal.number(cfg.max_limit)))
                enforced_limit = cfg.max_limit
            elif existing > cfg.max_limit:
                return _reject(
                    RejectionReason.LIMIT_EXCEEDS_MAX,
                    f"LIMIT {existing} exceeds max {cfg.max_limit}",
                )
            else:
                enforced_limit = existing
        else:
            # Wrap non-Select selectables (UNION etc.) in an outer LIMIT.
            tree = exp.select("*").from_(tree.subquery()).limit(cfg.max_limit)
            enforced_limit = cfg.max_limit

    normalized_sql = tree.sql(dialect=_DIALECT)

    vq = ValidatedQuery(
        sql=normalized_sql,
        referenced_tables=tuple(sorted(set(referenced))),
        enforced_limit=enforced_limit,
        _marker=_VALIDATION_SENTINEL,
    )
    return ValidationResult(ok=True, query=vq)


def _read_limit(select: exp.Select) -> int | None:
    """Return an integer LIMIT if present and literal, else None."""
    limit = select.args.get("limit")
    if limit is None:
        return None
    expr = limit.expression if isinstance(limit, exp.Limit) else limit
    try:
        return int(expr.name)
    except (AttributeError, ValueError, TypeError):
        # Non-literal LIMIT (e.g. parameter) — treat as absent so we enforce max.
        return None
