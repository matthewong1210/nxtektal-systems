"""Pure human-led ball-supply scenarios within the existing policy owner.

The supplied inventory remains an opening count at its original bucket origin.
These conditional calculations are advice, never committed arrivals or state.
"""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timedelta

from .contracts import InboundBallBatch
from .planning_contracts import PlanningError, utc, utc_text, validate_plan_request
from .projection import project_stockout_minutes
from .serialization import stable_digest


RULES_VERSION = "human-led-ball-supply/v1"
_GLOBAL_FIELDS = (
    "inventory_clean_balls", "demand", "safety_stock_balls", "buffer_minutes",
    "operations_allowed", "washer_available",
)
_ZONE_FIELDS = ("collection_allowed", "clean_yield_balls", "cycle_minutes")


def _after(origin: datetime, minutes: float) -> datetime:
    try:
        return origin + timedelta(minutes=minutes)
    except (OverflowError, ValueError):
        raise PlanningError("planning_invalid_request", "duration exceeds representable UTC time") from None


def _minutes(value: datetime, origin: datetime) -> float:
    return (value - origin).total_seconds() / 60.0


def _value(item: dict, field: str):
    return None if item[field] is None else item[field]["value"]


def _project(inventory: float, demand: dict, level: str, horizon: float,
             batches: tuple[InboundBallBatch, ...] = ()) -> float | None:
    return project_stockout_minutes(
        initial_clean_balls=inventory,
        demand_rates_balls_per_minute=tuple(demand[level]),
        demand_bucket_minutes=demand["bucket_minutes"],
        inbound_batches=batches,
        horizon_minutes=horizon,
    )


def _batches(candidate: dict | None, origin: datetime) -> tuple[InboundBallBatch, ...]:
    if candidate is None or candidate["supply_at_utc"] is None or candidate["clean_yield_balls"] is None:
        return ()
    amount = candidate["clean_yield_balls"]["low"]
    eta = _minutes(utc(candidate["supply_at_utc"]), origin)
    if amount <= 0 or eta < 0:
        return ()
    return (InboundBallBatch(
        source_id=f"counterfactual:{candidate['zone_id']}:{candidate['robot_id']}",
        eta_minutes=eta,
        balls=amount,
        provenance="human-led-ball-supply/v1:conditional-low-yield",
    ),)


def evaluate_plan(input_record: dict, request: dict, now: str | datetime,
                  previous_plan: dict | None = None) -> dict:
    """Evaluate one immutable plan version using an explicitly supplied clock.

    The workflow validates and stores inputs, owns compare-and-swap and confirmed
    plan immutability. This function still verifies its revision/version links.
    """
    now = utc(now)
    request = validate_plan_request(request, now)
    if request["input_revision"] != input_record["revision"]:
        raise PlanningError("planning_conflict", "plan must reference the supplied input revision")
    if previous_plan is None:
        if request["plan_id"] is not None or request["expected_plan_version"] != 0:
            raise PlanningError("planning_conflict", "new plan cannot name an existing version")
        plan_id = "plan-" + stable_digest({"request_id": request["request_id"]})[:24]
        version = 1
    else:
        if (request["plan_id"] != previous_plan["plan_id"] or
                request["expected_plan_version"] != previous_plan["version"]):
            raise PlanningError("planning_conflict", "plan version changed")
        plan_id, version = previous_plan["plan_id"], previous_plan["version"] + 1

    origin = utc(input_record["effective_at_utc"])
    input_end = utc(input_record["valid_until_utc"])
    operating_start = utc(input_record["operating_window"]["start_at_utc"])
    operating_end = utc(input_record["operating_window"]["end_at_utc"])
    expiry = min(input_end, utc(request["valid_until_utc"]), operating_end)
    demand = _value(input_record, "demand")
    inventory = _value(input_record, "inventory_clean_balls")
    safety = _value(input_record, "safety_stock_balls")
    buffer = _value(input_record, "buffer_minutes")
    horizon = None if demand is None else len(demand["high"]) * demand["bucket_minutes"]
    if horizon is not None:
        expiry = min(expiry, _after(origin, horizon))

    missing = [field for field in _GLOBAL_FIELDS if input_record[field] is None]
    global_missing = bool(missing)
    expired_fields = [field for field in _GLOBAL_FIELDS if input_record[field] is not None
                      and utc(input_record[field]["valid_until_utc"]) <= now]
    if not input_record["zones"]:
        missing.append("zones")
    baseline = {}
    thresholds = {}
    if inventory is not None and demand is not None:
        for level in ("low", "typical", "high"):
            baseline[level] = _project(inventory, demand, level, horizon)
            if safety is not None:
                thresholds[level] = (0.0 if inventory <= safety else
                                     _project(inventory - safety, demand, level, horizon))

    manual = request["selection"]
    zone_pairs = {(item["zone_id"], item["robot_id"]) for item in input_record["zones"]}
    if manual is not None and (manual["zone_id"], manual["robot_id"]) not in zone_pairs:
        raise PlanningError("planning_invalid_request", "selection is absent from the input zone/robot pairs")

    earliest = max(now, origin, operating_start)

    def candidate_for(zone: dict, start: datetime) -> dict:
        path = f"zones[{zone['zone_id']},{zone['robot_id']}]"
        reasons = []
        for field in _ZONE_FIELDS:
            if zone[field] is None:
                name = f"{path}.{field}"
                if name not in missing:
                    missing.append(name)
                reasons.append(f"missing:{field}")
            elif utc(zone[field]["valid_until_utc"]) <= now:
                reasons.append(f"expired:{field}")
        if global_missing:
            reasons.append("missing:planning_inputs")
        if expired_fields or now >= expiry:
            reasons.append("expired:planning_inputs")
        for field in ("operations_allowed", "washer_available"):
            if _value(input_record, field) is False:
                reasons.append(f"blocked:{field}")
        if _value(zone, "collection_allowed") is False:
            reasons.append("blocked:collection_allowed")
        yields = _value(zone, "clean_yield_balls")
        durations = _value(zone, "cycle_minutes")
        cycle = None if durations is None else sum(durations.values())
        supply = None if cycle is None or global_missing else _after(start, cycle)
        latest = None
        if not global_missing and cycle is not None and buffer is not None and "high" in thresholds:
            threshold = thresholds["high"]
            deadline = _after(origin, horizon if threshold is None else threshold)
            latest = min(_after(deadline, -cycle - buffer),
                         _after(operating_end, -cycle), _after(expiry, -cycle))
        if start < earliest:
            reasons.append("start_before_current_operating_window")
        if start >= expiry or start >= operating_end:
            reasons.append("start_outside_validity")
        if supply is not None and (supply >= expiry or supply > operating_end):
            reasons.append("full_replenishment_outside_validity")
        if latest is not None and start >= latest:
            reasons.append("safety_stock_deadline_missed")
        if yields is not None and yields["low"] == 0:
            reasons.append("no_conservative_clean_yield")
        candidate = {
            "zone_id": zone["zone_id"], "robot_id": zone["robot_id"],
            "eligible": not reasons, "exclusion_reasons": reasons,
            "start_at_utc": None if global_missing else utc_text(start),
            "latest_start_at_utc": None if latest is None else utc_text(latest),
            "supply_at_utc": None if supply is None else utc_text(supply),
            "cycle_minutes": cycle,
            "clean_yield_balls": deepcopy(yields),
            "protects_high_demand_horizon": None,
        }
        if inventory is not None and demand is not None and supply is not None and yields is not None:
            candidate["protects_high_demand_horizon"] = (
                _project(inventory, demand, "high", horizon, _batches(candidate, origin)) is None
            )
        return candidate

    def ranking(candidate: dict) -> tuple:
        return (
            not candidate["eligible"],
            candidate["protects_high_demand_horizon"] is not True,
            candidate["supply_at_utc"] or "~",
            -(candidate["clean_yield_balls"]["low"] if candidate["clean_yield_balls"] else 0),
            candidate["zone_id"], candidate["robot_id"],
        )

    system_candidates = sorted((candidate_for(zone, earliest) for zone in input_record["zones"]), key=ranking)
    system_candidate = next((item for item in system_candidates if item["eligible"]), None)

    def selection_of(candidate: dict | None) -> dict | None:
        if candidate is None:
            return None
        return {field: candidate[field] for field in ("zone_id", "robot_id", "start_at_utc")}

    system_selection = selection_of(system_candidate)
    selection = deepcopy(manual if manual is not None else system_selection)
    candidates = system_candidates
    selected = system_candidate
    if manual is not None:
        zones_by_pair = {(zone["zone_id"], zone["robot_id"]): zone for zone in input_record["zones"]}
        candidates = sorted([
            candidate_for(zones_by_pair[(candidate["zone_id"], candidate["robot_id"])],
                          utc(manual["start_at_utc"]))
            if (candidate["zone_id"], candidate["robot_id"]) == (manual["zone_id"], manual["robot_id"])
            else candidate
            for candidate in system_candidates
        ], key=ranking)
        selected = next(item for item in candidates
                        if (item["zone_id"], item["robot_id"]) == (manual["zone_id"], manual["robot_id"]))

    scenarios = []
    if baseline:
        batches = _batches(selected, origin)
        supplied = sum(batch.balls for batch in batches if batch.eta_minutes <= horizon)
        for level in ("low", "typical", "high"):
            consumption = sum(rate * demand["bucket_minutes"] for rate in demand[level])
            balance = inventory - consumption
            if not math.isfinite(balance) or not math.isfinite(balance + supplied):
                raise PlanningError("planning_invalid_request", "projected balance exceeds finite number range")
            planned = _project(inventory, demand, level, horizon, batches)
            threshold = thresholds.get(level)
            scenarios.append({
                "level": level,
                "baseline_stockout_at_utc": None if baseline[level] is None else utc_text(_after(origin, baseline[level])),
                "safety_stock_at_utc": None if threshold is None else utc_text(_after(origin, threshold)),
                "with_plan_stockout_at_utc": None if planned is None else utc_text(_after(origin, planned)),
                "baseline_end_balls": balance,
                "with_plan_end_balls": balance + supplied,
            })

    if now >= expiry or expired_fields:
        status = "EXPIRED"
    elif global_missing or not input_record["zones"] or (selected is None and missing) or (
        selected is not None and any(reason.startswith("missing:") for reason in selected["exclusion_reasons"])
    ):
        status = "MISSING_DATA"
    elif selected is None or not selected["eligible"] or selected["protects_high_demand_horizon"] is not True:
        status = "INFEASIBLE"
    else:
        status = "READY"
    rationale = [
        "Human-led demand assumptions; no learned forecast or probability claim.",
        "Opening inventory and demand retain their input origin; elapsed demand is not rebased.",
        "Low clean yield against high demand is the conservative feasibility comparison.",
        "Full replenishment includes travel, collect, return, unload, wash and supply plus the safety margin.",
    ]
    if status != "READY":
        rationale.append(f"Plan is {status}; confirmation is unavailable.")
    if selected is not None:
        if selected["cycle_minutes"] is not None:
            rationale.append(
                f"Selected zone {selected['zone_id']} with robot {selected['robot_id']}; "
                f"complete replenishment takes {selected['cycle_minutes']} minutes."
            )
        else:
            rationale.append(f"Selected zone {selected['zone_id']} with robot {selected['robot_id']}; full cycle duration is unknown.")
        rationale.extend(selected["exclusion_reasons"])
        if selected["protects_high_demand_horizon"] is False:
            rationale.append("The selected plan cannot protect the complete high-demand horizon.")
    excluded = sum(not candidate["eligible"] for candidate in candidates)
    rationale.append(f"{excluded} of {len(candidates)} candidates are excluded by evidence, restrictions or timing.")
    if system_candidate is not None:
        rationale.append(
            f"System suggestion: zone {system_candidate['zone_id']} with {system_candidate['robot_id']}; "
            "ranking prefers high-demand protection, earlier complete replenishment, then larger conservative yield and IDs."
        )
    before = previous_plan["selection"] if previous_plan is not None else system_selection
    return {
        "plan_id": plan_id, "version": version, "request": request,
        "input_revision": input_record["revision"], "input_digest": input_record["input_digest"],
        "rules_version": RULES_VERSION, "generated_at_utc": utc_text(now),
        "valid_until_utc": utc_text(expiry), "status": status,
        "missing": sorted(missing), "rationale": rationale,
        "system_selection": system_selection, "selection": selection,
        "adjustment": {"before": deepcopy(before), "after": deepcopy(selection),
            "operator": request["operator"], "reason": request["reason"],
            "scope": request["scope"], "valid_until_utc": utc_text(expiry)},
        "scenarios": scenarios, "candidates": candidates,
    }
