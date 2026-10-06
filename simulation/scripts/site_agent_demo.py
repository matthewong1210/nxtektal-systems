"""Pilot Site Agent Service V0 — local fixture-backed service runner.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

Composes the deterministic Pilot Course A service fixture, launches
(or resumes) the ``nxt_site_agent`` service under one runs directory,
and serves the versioned local Manager API plus, when a built console
directory is supplied, the Manager Console — loopback only.

Nothing here connects to a physical device, network transport, or
facility.  There is no Modbus, serial, MQTT, Kafka, OPC-UA, ROS 2,
Nav2, vendor SDK, camera, or cloud path, and no robot, actuator,
motion, charging, or emergency-stop surface.  Manager acceptance stays
workflow evidence only.

Security: local fixture use only.  The server binds loopback, has no
authentication, and is not safe for public or facility-network
exposure; production authentication/enrollment is a separate gate.

Usage (from simulation/):
    uv run --no-sync python -B scripts/site_agent_demo.py \
        --out reports/site-agent --port 8765 \
        --console ../apps/site-agent-console/out
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_site_agent import (  # noqa: E402
    ClockBasis,
    ClockSource,
    LaunchRefusedError,
    SiteAgentApiServer,
    SiteAgentError,
    SiteAgentService,
)
from nxt_workflow_enablement import (  # noqa: E402
    RANGE_OPS_WORKFLOW_ID,
    WorkflowEnablementError,
    canonical_report_json,
    plan_range_ops_launch,
)

from scripts.site_agent_fixture import (  # noqa: E402
    DEPLOYMENT_ID,
    DISCLAIMER,
    SITE_ID,
    broken_service_manifest_payload,
    evaluate_service_enablement,
    fixture_clock,
    service_composition_seam,
    service_enablement_context,
    service_range_ops_evidence,
)


def utcnow() -> datetime:
    """The host's UTC clock, used only when ``--context-clock system`` is chosen."""
    return datetime.now(timezone.utc)

_SECURITY_NOTE = (
    "local fixture use only — loopback binding, no authentication; not "
    "safe for public or facility-network exposure"
)


def _print_refusal_report(out_dir: Path) -> int:
    """Evaluate the broken candidate and show the honest refusal path."""
    payload = broken_service_manifest_payload()
    evaluation, report = evaluate_service_enablement(payload)
    broken_dir = out_dir / "broken"
    broken_dir.mkdir(parents=True, exist_ok=True)
    report_path = broken_dir / "workflow_enablement_report.not_ready.json"
    report_path.write_text(canonical_report_json(report), encoding="utf-8")
    range_ops = next(
        item
        for item in evaluation.workflows
        if item.workflow_id == RANGE_OPS_WORKFLOW_ID
    )
    try:
        plan_range_ops_launch(
            readiness=range_ops,
            shared=evaluation.shared,
            context=service_enablement_context(),
            evidence=service_range_ops_evidence(payload),
        )
    except WorkflowEnablementError as exc:
        refusal = str(exc)
    else:  # pragma: no cover - the planner must fail closed
        raise SystemExit("a NOT_READY workflow must never yield a launch plan")
    print(DISCLAIMER)
    print()
    print("Broken candidate manifest — the service refuses to launch:")
    print(f"  {RANGE_OPS_WORKFLOW_ID}: {range_ops.verdict.value}")
    print(f"  launch plan: refused ({refusal})")
    print(f"  report written: {report_path}")
    print()
    print(DISCLAIMER)
    return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Local fixture-backed Pilot Site Agent service "
            "(synthetic Pilot Course A storyline; loopback only)"
        )
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="runs directory for deterministic service evidence",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="loopback port for the Manager API (default 8765; 0 picks one)",
    )
    parser.add_argument(
        "--console",
        type=Path,
        default=None,
        help=(
            "built Manager Console directory to serve same-origin "
            "(the console's static build output)"
        ),
    )
    parser.add_argument(
        "--advance",
        type=int,
        default=0,
        help="deterministically advance N fixture cycles before serving",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="force a fresh launch into the next empty run directory",
    )
    parser.add_argument(
        "--broken",
        action="store_true",
        help=(
            "evaluate the broken candidate manifest and show the "
            "NOT_READY refusal instead of launching"
        ),
    )
    parser.add_argument(
        "--no-serve",
        action="store_true",
        help="launch, run --advance cycles, print health JSON, and exit",
    )
    parser.add_argument(
        "--context-clock",
        choices=("fixture", "system"),
        default="fixture",
        help=(
            "clock for the Supervisor Snapshot's business context: the declared "
            "fixture instant (default) or the host UTC clock; scenario time is "
            "unaffected"
        ),
    )
    parser.add_argument(
        "--context-as-of",
        default=None,
        help=(
            "override the declared fixture instant (ISO-8601 with offset, e.g. "
            "2026-08-08T10:30:00+00:00); only with --context-clock fixture"
        ),
    )
    args = parser.parse_args(argv)

    if args.broken:
        return _print_refusal_report(args.out)
    if args.advance < 0:
        parser.error("--advance must be non-negative")

    if args.context_clock == "system":
        if args.context_as_of is not None:
            parser.error("--context-as-of applies only to --context-clock fixture")
        clock = ClockSource(read=utcnow, basis=ClockBasis.SYSTEM_UTC)
    elif args.context_as_of is not None:
        try:
            declared = datetime.fromisoformat(args.context_as_of)
        except ValueError:
            parser.error("--context-as-of must be ISO-8601")
        if declared.tzinfo is None:
            parser.error("--context-as-of must carry a UTC offset")
        clock = fixture_clock(declared.astimezone(timezone.utc))
    else:
        clock = fixture_clock()
    seam = service_composition_seam(clock=clock)
    try:
        service = SiteAgentService.launch(
            runs_root=args.out,
            site_id=SITE_ID,
            deployment_id=DEPLOYMENT_ID,
            workflow_id=RANGE_OPS_WORKFLOW_ID,
            seam=seam,
            force_fresh=args.fresh,
        )
    except LaunchRefusedError as exc:
        print(DISCLAIMER)
        print(f"launch refused — {exc.code}: {exc.detail}")
        return 3

    for _ in range(args.advance):
        try:
            outcome = service.advance()
        except SiteAgentError as exc:
            print(f"advance stopped — {exc.code}: {exc.detail}")
            break
        print(
            "advanced: "
            f"outcome={outcome['outcome']} "
            f"sequence={outcome['sequence_number']} "
            f"verdict={outcome['verdict']}"
        )

    if args.no_serve:
        service.stop()
        print(DISCLAIMER)
        print(
            json.dumps(
                service.health_snapshot(),
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
        )
        return 0

    try:
        server = SiteAgentApiServer(
            service,
            host="127.0.0.1",
            port=args.port,
            console_dir=args.console,
        )
    except SiteAgentError as exc:
        print(f"server refused — {exc.code}: {exc.detail}")
        service.stop()
        return 2

    def _shutdown(signum, frame):  # noqa: ARG001 - signal signature
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(DISCLAIMER)
    print()
    print("Pilot Site Agent Service V0 — fixture-backed Shadow Mode")
    print(f"  site:       {SITE_ID}")
    print(f"  deployment: {DEPLOYMENT_ID}")
    print(f"  workflow:   {RANGE_OPS_WORKFLOW_ID}")
    print(f"  evidence:   {service.storage.run_root}")
    print(f"  manager api: {server.url}/api/v0/health")
    print(f"  snapshot:    {server.url}/api/v0/supervisor-snapshot ({clock.basis.value})")
    if args.console is not None:
        print(f"  console:     {server.url}/")
    else:
        print("  console:     not configured (--console <built console dir>)")
    print(f"  security:   {_SECURITY_NOTE}")
    print()
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        service.stop()
        print("stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
