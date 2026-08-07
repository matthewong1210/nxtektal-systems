"""Pure, read-only queries over stored memory windows.

These are the six-month payoff: given accumulated episodes, they answer
which advice gets accepted, how well the stockout estimates calibrate
against reality, and what measured outcomes followed different decisions.

Everything here is observational. Aggregations that condition on human
verdicts carry :data:`NON_CAUSAL_CAVEAT` in their result — the schema and
this module cannot express "rule X caused outcome Y", by design.

Stdlib only; operates on plain window dicts (from
:func:`nxt_memory.records.iter_windows`), never on a live simulation.
"""

from __future__ import annotations

from typing import Iterable, Optional

NON_CAUSAL_CAVEAT = (
    "Observational aggregation over recorded episodes; confounded by "
    "operator behavior and facility state — NOT causal evidence."
)


def _verdicts(window: dict) -> list[dict]:
    return window["decision"].get("human", {}).get("verdicts", [])


def rule_acceptance_rates(windows: Iterable[dict]) -> dict:
    """Per rule_id x urgency: offered / accepted / rejected / deferred /
    unaddressed counts."""
    rules: dict[str, dict[str, dict[str, int]]] = {}
    n_windows = 0
    for window in windows:
        n_windows += 1
        recommendations = window["decision"].get("recommendations", [])
        addressed = {v["rec_index"]: v["verdict"] for v in _verdicts(window)}
        for index, rec in enumerate(recommendations):
            bucket = rules.setdefault(rec["rule_id"], {}).setdefault(
                rec["urgency"],
                {
                    "offered": 0,
                    "accepted": 0,
                    "rejected": 0,
                    "deferred": 0,
                    "unaddressed": 0,
                },
            )
            bucket["offered"] += 1
            verdict = addressed.get(index)
            if verdict is None:
                bucket["unaddressed"] += 1
            else:
                bucket[verdict] += 1
    return {"rules": rules, "n_windows": n_windows}


def stockout_eta_calibration(windows: Iterable[dict]) -> dict:
    """Predicted stockout ETA vs realized STOCKOUT events.

    For every window whose observation carried a finite ETA, the realized
    stockout time is the first ``stockout`` event at or after the
    prediction instant, searched across all windows' execution sections.
    Predictions with no realized stockout in the record are counted as
    censored, not as errors.
    """
    ordered = sorted(windows, key=lambda w: (w["episode_id"], w["seq"]))
    stockout_times = sorted(
        event["t_s"]
        for window in ordered
        for event in window["execution"].get("events", [])
        if event.get("kind") == "stockout"
    )

    def first_stockout_at_or_after(t_s: float) -> Optional[float]:
        for t in stockout_times:
            if t >= t_s:
                return t
        return None

    n_predictions = 0
    errors: list[float] = []
    n_censored = 0
    for window in ordered:
        eta = window["observation"].get("stockout_eta_minutes")
        if eta is None or eta <= 0:
            continue
        n_predictions += 1
        t_start = window["t_start_s"]
        realized = first_stockout_at_or_after(t_start)
        if realized is None:
            n_censored += 1
            continue
        errors.append((realized - t_start) / 60.0 - eta)

    return {
        "n_predictions": n_predictions,
        "n_realized": len(errors),
        "n_censored": n_censored,
        "mean_error_minutes": (sum(errors) / len(errors)) if errors else None,
        "mean_abs_error_minutes": (
            sum(abs(e) for e in errors) / len(errors) if errors else None
        ),
    }


def outcome_deltas_by_decision(windows: Iterable[dict]) -> dict:
    """Mean measured impact per verdict group.

    Groups: ``any_accepted`` (at least one accepted verdict),
    ``none_accepted`` (verdicts exist, none accepted), ``no_verdicts``.
    Observational only — see the caveat carried in the result.
    """
    groups: dict[str, list[dict]] = {
        "any_accepted": [],
        "none_accepted": [],
        "no_verdicts": [],
    }
    for window in windows:
        verdicts = _verdicts(window)
        if not verdicts:
            key = "no_verdicts"
        elif any(v["verdict"] == "accepted" for v in verdicts):
            key = "any_accepted"
        else:
            key = "none_accepted"
        groups[key].append(window["outcome"]["impact"])

    def mean_impact(impacts: list[dict]) -> dict:
        if not impacts:
            return {}
        keys = ("availability_change", "stockout_minutes", "energy_wh")
        out = {k: sum(i[k] for i in impacts) / len(impacts) for k in keys}
        for nested in ("labor", "safety"):
            sub_keys = impacts[0][nested].keys()
            out[nested] = {
                k: sum(i[nested][k] for i in impacts) / len(impacts)
                for k in sub_keys
            }
        return out

    return {
        "caveat": NON_CAUSAL_CAVEAT,
        "groups": {
            name: {"n_windows": len(impacts), "mean_impact": mean_impact(impacts)}
            for name, impacts in groups.items()
        },
    }
