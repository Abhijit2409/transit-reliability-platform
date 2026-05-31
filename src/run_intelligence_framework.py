"""
run_intelligence_framework.py
=============================
Convenience entrypoint that runs the full analysis + geospatial pipeline.

Equivalent to running:
    python src/multimodal_transit_intelligence.py
    python src/geospatial_maps.py

Use this when you want a single command to regenerate every artifact.
For more control over arguments, call the underlying modules directly.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("runner")

SRC = Path(__file__).parent


def run_step(name: str, cmd: list) -> None:
    log.info(f"▶  {name}")
    log.info(f"   $ {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=False)
    if res.returncode != 0:
        log.error(f"✗ {name} failed with code {res.returncode}")
        sys.exit(res.returncode)
    log.info(f"✓ {name} complete")


def main() -> None:
    log.info("=" * 60)
    log.info("TransLink Multimodal Transit Intelligence — Full Run")
    log.info("=" * 60)

    run_step(
        "Analysis (5-layer framework)",
        [sys.executable, str(SRC / "multimodal_transit_intelligence.py")],
    )
    run_step(
        "Geospatial maps",
        [sys.executable, str(SRC / "geospatial_maps.py")],
    )

    log.info("=" * 60)
    log.info("All artifacts regenerated.")
    log.info("  • assets/   — charts (.png) and interactive maps (.html)")
    log.info("  • outputs/  — processed analytical tables (.csv)")
    log.info("  • reports/  — executive findings and map interpretations (.md)")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
