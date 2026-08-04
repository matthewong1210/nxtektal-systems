"""Leaderboard reference: reuse the benchmark report's ranking data.

The viewer's credibility panel shows the published E1 rankings verbatim —
this module extracts them from an existing ``report.json`` produced by
``nxt_range_agent.benchmark`` without recomputing anything.
"""

from __future__ import annotations

from typing import Any

REQUIRED_KEYS = ("disclaimer", "provenance", "config", "seeds", "rankings")
REQUIRED_CONFIG_KEYS = ("policies", "scenarios")


def build_benchmark_ref(report: dict[str, Any]) -> dict[str, Any]:
    """Viewer-ready benchmark reference from a benchmark report dict."""
    missing = [key for key in REQUIRED_KEYS if key not in report]
    if missing:
        raise ValueError(
            f"benchmark report is missing required keys: {', '.join(missing)}"
        )
    config = report["config"]
    missing_config = [
        key
        for key in REQUIRED_CONFIG_KEYS
        if not isinstance(config, dict) or key not in config
    ]
    if missing_config:
        raise ValueError(
            "benchmark report config is missing required keys: "
            f"{', '.join(missing_config)}"
        )
    return {
        "schema": "nxt-range-viewer/benchmark/v1",
        "disclaimer": report["disclaimer"],
        "provenance": report["provenance"],
        "seeds": report["seeds"],
        "policies": report["config"]["policies"],
        "scenarios": report["config"]["scenarios"],
        "rankings": report["rankings"],
    }
