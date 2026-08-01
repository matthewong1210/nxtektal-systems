"""JSON and CSV report writers.

JSON reports carry everything (per-run traces, placeholder inventory,
summary). CSV carries one flat row per run for spreadsheet/pandas analysis;
nested traces are dropped there and override paths become ``override:<path>``
columns.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping


def flatten_record(record: Mapping[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in record.items():
        if key == "overrides" and isinstance(value, Mapping):
            for path, v in value.items():
                flat[f"override:{path}"] = v
        elif isinstance(value, (list, dict)):
            continue  # traces live in the JSON report
        else:
            flat[key] = value
    return flat


def write_json_report(payload: Mapping[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return out


def write_runs_csv(records: list[Mapping[str, Any]], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    flat_rows = [flatten_record(r) for r in records]
    headers: list[str] = []
    seen = set()
    for row in flat_rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                headers.append(key)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, restval="")
        writer.writeheader()
        writer.writerows(flat_rows)
    return out
