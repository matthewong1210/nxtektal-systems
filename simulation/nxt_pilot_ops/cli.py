"""Offline review CLI for repository-native FacilityState captures.

The CLI only reads a completed capture and prints policy evaluations.  It has
no execution, command-bus, robot-control, or upstream mutation surface.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .adapters import AdapterError, read_native_facility_state_capture
from .guardian import BallAvailabilityGuardian
from .serialization import to_primitive


def _parse_midnight(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "simulation midnight must be an ISO-8601 datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "simulation midnight must include a UTC offset"
        )
    if any((parsed.hour, parsed.minute, parsed.second, parsed.microsecond)):
        raise argparse.ArgumentTypeError(
            "simulation midnight must be calendar midnight"
        )
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate a repository-native FacilityState capture in offline "
            "Shadow Ops review mode"
        )
    )
    parser.add_argument("states", type=Path, help="facility_states.jsonl path")
    parser.add_argument(
        "--meta",
        required=True,
        type=Path,
        help="matching stream.meta.json path",
    )
    parser.add_argument(
        "--simulation-midnight",
        required=True,
        type=_parse_midnight,
        help="timezone-aware date midnight corresponding to meta.t_s",
    )
    parser.add_argument(
        "--first-record-sensed-state",
        required=True,
        choices=("valid", "pre-sensor"),
        help=(
            "explicitly declare whether record zero has a valid clean_sensed "
            "reading or precedes the first sensor tick"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        capture = read_native_facility_state_capture(
            args.states,
            args.meta,
            simulation_midnight=args.simulation_midnight,
            first_record_clean_sensed_valid=(
                args.first_record_sensed_state == "valid"
            ),
        )
    except AdapterError as exc:
        parser.error(str(exc))
    guardian = BallAvailabilityGuardian()
    evaluations = tuple(
        guardian.evaluate(snapshot) for snapshot in capture.snapshots
    )
    print(
        json.dumps(
            to_primitive(
                {
                    "mode": "offline_shadow_review",
                    "source": capture.metadata,
                    "evaluations": evaluations,
                }
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
