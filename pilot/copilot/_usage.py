"""
copilot/_usage.py
Best-effort extraction of token usage from a Responses API response.

The Responses API returns a `usage` object with input/output/total token counts.
Field names have varied across SDK versions, so this reads defensively and
returns a zeroed TokenUsage if nothing is available (telemetry must never break
the pipeline).
"""

from __future__ import annotations

from copilot.contracts import TokenUsage


def _extract_usage(resp: object) -> TokenUsage:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return TokenUsage()

    def _get(*names: str) -> int:
        for n in names:
            v = getattr(usage, n, None)
            if isinstance(v, (int, float)):
                return int(v)
        return 0

    inp = _get("input_tokens", "prompt_tokens")
    out = _get("output_tokens", "completion_tokens")
    tot = _get("total_tokens") or (inp + out)
    return TokenUsage(input_tokens=inp, output_tokens=out, total_tokens=tot)
