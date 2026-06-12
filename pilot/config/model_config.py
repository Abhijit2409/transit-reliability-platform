"""
config/model_config.py
Single source of truth for the model snapshot and sampling settings.

Pinned to a dated snapshot so the benchmark is reproducible: a floating alias
like 'gpt-4.1' can change under you and silently move your published accuracy
number. Override per-stage via environment variables if needed.
"""

from __future__ import annotations

import os

# Pinned dated snapshot (not the floating 'gpt-4.1' alias). Override with
# TIC_SQL_MODEL / TIC_NARRATOR_MODEL only when intentionally testing a new model.
_PINNED_MODEL = "gpt-4.1-2025-04-14"

SQL_MODEL = os.environ.get("TIC_SQL_MODEL", _PINNED_MODEL)
NARRATOR_MODEL = os.environ.get("TIC_NARRATOR_MODEL", _PINNED_MODEL)

SQL_TEMPERATURE = float(os.environ.get("TIC_SQL_TEMPERATURE", "0"))
NARRATOR_TEMPERATURE = float(os.environ.get("TIC_NARRATOR_TEMPERATURE", "0"))
