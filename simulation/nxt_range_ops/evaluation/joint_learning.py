"""Paired offline evaluation and conservative SIMULATION-only selection.

Policies receive a filtered view. The evaluator alone reads runtime truth for
scoring, conservation and demand pairing. No persistence, background scheduler,
LLM training or production promotion lives in this module.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
from statistics import mean

from nxt_range_ops.config.models import RangeOpsScenario, placeholder_census
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.evaluation.harness import episode_kpis
from nxt_range_ops.policies.joint_dispatch import (
    JointDispatchPolicy, candidate_catalog, policy_inputs, validate_candidate,
)

RESULT_SCHEMA = "nxt-joint-evaluation/v0"
SELECTION_SCHEMA = "nxt-joint-selection/v0"
# Declared hypothetical business preferences, never learned safety constraints.
SCORE_WEIGHTS = {"demand_fill_rate": 1000.0, "stockout_minutes": -1.0,
                 "inspection_completion_rate": 50.0, "energy_wh": -0.001,
                 "course_staff_wait_minutes": -0.01}
GATE_ASSUMPTIONS = {"min_paired_episodes": 3, "minimum_score_improvement": 1.0,
                    "max_fill_rate_decline": 0.005,
                    "max_inspection_completion_decline": 0.05,
                    "max_stockout_increase_minutes": 2.0,
                    "max_energy_increase_fraction": 0.15}


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def episode_input_digest(episode: dict) -> str:
    """Bind a compiled scenario, complete joint inputs and seed for a batch manifest."""
    return _digest({"scenario": episode["scenario"].model_dump(mode="json"),
                    "joint_inputs": episode["joint_inputs"], "seed": episode["seed"]})


def _expected_demand_history(episode: dict) -> list[dict]:
    opening = episode["scenario"].hours.open_minute
    return [{"minute": opening + i, "requested": value}
            for i, value in enumerate(episode["joint_inputs"]["demand_by_minute"])]


def episode_demand_digest(episode: dict) -> str:
    """Digest the expected full-day request history, including absolute minute."""
    return _digest(_expected_demand_history(episode))


def _sample(info: dict, obs: dict, sim, action_name: str | None) -> dict:
    """Evaluator-only projection. This object is never supplied to a policy."""
    return {"t_s": float(info["t_s"]), "action": action_name,
            "observed_inventory_frac": float(obs["dispenser_inventory_frac"][0]),
            "true_dispenser_count": int(info["true_dispenser_count"]),
            "demand_fill_rate": sim.metrics.demand_fill_rate,
            "stockout_minutes": sim.metrics.stockout_minutes,
            "joint_ops": deepcopy(info.get("joint_ops", {})),
            "joint_metrics": deepcopy(sim.joint_metrics)}


def evaluate_episode(episode: dict, candidate: dict,
                     max_timeline_points: int = 48) -> dict:
    """Run exactly one frozen episode through Env.step and a filtered policy.

    Setup errors reject the job. Runtime exceptions become explicit failed rows,
    never successful zero-valued results. Retrying and atomic saving belong to
    the composition root. An interrupted process replays the episode from start.
    """
    candidate = validate_candidate(candidate)
    if not isinstance(episode, dict) or episode.get("environment") != "SIMULATION":
        raise ValueError("episode must explicitly be SIMULATION")
    if episode.get("schema") != "nxt-joint-episode/v0":
        raise ValueError("unsupported episode schema")
    if type(episode.get("seed")) is not int or episode["seed"] < 0:
        raise ValueError("episode seed must be a non-negative integer")
    if episode.get("split") not in {"train", "validation", "test"}:
        raise ValueError("episode requires an explicit train/validation/test split")
    for key in ("episode_id", "regime"):
        if not isinstance(episode.get(key), str) or not episode[key].strip():
            raise ValueError(f"episode requires {key}")
    if type(max_timeline_points) is not int or not 2 <= max_timeline_points <= 96:
        raise ValueError("max_timeline_points must be 2..96")
    scenario = episode["scenario"]
    if not isinstance(scenario, RangeOpsScenario):
        raise ValueError("episode scenario must be a validated RangeOpsScenario")
    joint_inputs = deepcopy(episode["joint_inputs"])
    input_digest = episode_input_digest(episode)
    env = RangeOpsEnv(scenario, joint_inputs=joint_inputs)
    policy = JointDispatchPolicy(scenario, env.catalog, candidate, seed=episode["seed"])
    expected_steps = math.ceil((scenario.hours.close_seconds - scenario.hours.open_seconds)
                               / scenario.episode.control_interval_s)
    # Includes both the opening state and final state without an unbounded buffer.
    sample_interval = max(1, math.ceil(expected_steps / (max_timeline_points - 1)))
    timeline: list[dict] = []
    reward_total = 0.0
    reward_components: dict[str, float] = {}
    terminated = truncated = False
    conservation_ok = True
    error = None
    n_steps = 0
    info: dict = {}
    obs: dict = {}
    try:
        obs, info = env.reset(seed=episode["seed"])
        policy.reset()
        timeline.append(_sample(info, obs, env.sim, None))
        while not (terminated or truncated):
            visible_obs, visible_info = policy_inputs(obs, info)
            action = policy.act(visible_obs, visible_info)
            obs, reward, terminated, truncated, info = env.step(action)
            n_steps += 1
            env.sim.ledger.assert_conserved()
            reward_total += float(reward)
            for key, value in info["reward_components"].items():
                reward_components[key] = reward_components.get(key, 0.0) + float(value)
            if n_steps % sample_interval == 0 or terminated or truncated:
                sample = _sample(info, obs, env.sim, info.get("action_name"))
                if len(timeline) < max_timeline_points:
                    timeline.append(sample)
                elif terminated or truncated:
                    timeline[-1] = sample
    except Exception as exc:  # Failure evidence is returned; never accepted by gates.
        error = {"type": type(exc).__name__, "detail": str(exc)[:500]}
        if env.sim is not None:
            try:
                env.sim.ledger.assert_conserved()
            except AssertionError:
                conservation_ok = False
    sim = env.sim
    summary: dict = {"success": False, "error": error, "n_steps": n_steps,
                     "truncated": bool(truncated), "conservation_ok": conservation_ok,
                     "termination_reason": info.get("termination_reason"),
                     "reward_total": reward_total, "reward_components": reward_components}
    history: list[dict] = []
    if sim is not None:
        metrics = sim.metrics
        joint_metrics = deepcopy(sim.joint_metrics)
        history = sim.demand_history()
        complete = int(joint_metrics["staff_jobs_completed"])
        total_jobs = len(joint_inputs["staff_jobs"])
        summary.update(metrics.to_dict())
        summary.update(episode_kpis(scenario, metrics))
        summary.update(joint_metrics)
        summary.update({
            "demand_fill_rate": metrics.demand_fill_rate,
            "staff_jobs_total": total_jobs,
            "inspection_completion_rate": complete / total_jobs if total_jobs else 1.0,
            "inspection_exposure": total_jobs,
            "course_staff_wait_minutes": joint_metrics["course_staff_wait_s"] / 60,
            "ledger_total": sim.ledger.total,
            "ledger_counts": sim.ledger.counts(),
            "demand_history_minutes": len(history),
        })
        summary["conservation_ok"] = (conservation_ok and sim.ledger.total == scenario.total_balls
                                      and sum(sim.ledger.counts().values()) == scenario.total_balls)
        expected = _expected_demand_history(episode)
        summary["demand_matches_input"] = history == expected
        summary["success"] = (error is None and terminated and not truncated
                              and summary["conservation_ok"]
                              and summary["demand_matches_input"]
                              and info.get("termination_reason") == "day_complete")
    else:
        summary["conservation_ok"] = False
        summary["demand_matches_input"] = False
    result = {"schema": RESULT_SCHEMA, "environment": "SIMULATION",
            "episode_id": episode["episode_id"], "seed": episode["seed"],
            "split": episode["split"], "regime": episode["regime"],
            "candidate_id": candidate["candidate_id"], "candidate": candidate,
            "input_digest": input_digest, "demand_digest": _digest(history),
            "summary": summary, "timeline": timeline,
            "score_weights": deepcopy(SCORE_WEIGHTS),
            "placeholder_parameters": placeholder_census(scenario),
            "disclaimer": "SIMULATION with hypothetical parameters. Inspection labor is not repair; "
                          "offline candidate selection is not LLM training or production promotion."}
    result["score"] = score_result(result) if _eligible(result) else None
    return result


def _finite_number(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _eligible(result: dict) -> bool:
    s = result.get("summary", {})
    required = set(SCORE_WEIGHTS) | {"unsafe_rejections", "hard_failures", "estops",
                                    "staff_jobs_total", "staff_jobs_completed"}
    return (result.get("schema") == RESULT_SCHEMA
            and result.get("environment") == "SIMULATION"
            and s.get("success") is True and s.get("truncated") is False
            and s.get("conservation_ok") is True and s.get("demand_matches_input") is True
            and s.get("error") is None and s.get("termination_reason") == "day_complete"
            and all(_finite_number(s.get(key)) and s[key] >= 0 for key in required)
            and 0 <= s["demand_fill_rate"] <= 1
            and 0 <= s["inspection_completion_rate"] <= 1
            and s["staff_jobs_completed"] <= s["staff_jobs_total"])


def score_result(result: dict) -> float:
    if not _eligible(result):
        raise ValueError("cannot score failed, incomplete or invalid episode evidence")
    return sum(result["summary"][key] * weight for key, weight in SCORE_WEIGHTS.items())


def _index(results: list[dict], split: str) -> dict[str, dict[str, dict]]:
    by_candidate: dict[str, dict[str, dict]] = {}
    for result in results:
        if result.get("split") != split:
            raise ValueError(f"{split} selection cannot consume another data split")
        candidate = validate_candidate(result.get("candidate"))
        candidate_id = candidate["candidate_id"]
        if result.get("candidate_id") != candidate_id:
            raise ValueError("candidate identity does not match frozen parameters")
        episode_id = result.get("episode_id")
        if not isinstance(episode_id, str) or not episode_id:
            raise ValueError("missing episode identity")
        group = by_candidate.setdefault(candidate_id, {})
        if episode_id in group:
            raise ValueError("duplicate candidate/episode result")
        group[episode_id] = result
    return by_candidate


def _pairs(candidate: dict[str, dict], reference: dict[str, dict]) -> tuple[list, str | None]:
    if not candidate or set(candidate) != set(reference):
        return [], "missing_paired_episodes"
    pairs = []
    exposures = set()
    for episode_id in sorted(reference):
        current, base = candidate[episode_id], reference[episode_id]
        if not _eligible(current) or not _eligible(base):
            return [], "failed_or_incomplete_episode"
        for key in ("seed", "regime", "input_digest", "demand_digest"):
            if current.get(key) is None or current.get(key) != base.get(key):
                return [], f"paired_{key}_mismatch"
        for key in ("input_digest", "demand_digest"):
            value = current[key]
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                return [], f"invalid_{key}"
        exposure = (current["regime"], current["seed"])
        if type(current["seed"]) is not int or current["seed"] < 0 or not isinstance(current["regime"], str) or not current["regime"]:
            return [], "invalid_seed_regime_exposure"
        if exposure in exposures:
            return [], "duplicate_seed_regime_exposure"
        exposures.add(exposure)
        if current["summary"]["staff_jobs_total"] != base["summary"]["staff_jobs_total"]:
            return [], "paired_inspection_exposure_mismatch"
        pairs.append((current, base))
    return pairs, None


def _paired_evidence(pairs: list[tuple[dict, dict]]) -> dict:
    keys = tuple(SCORE_WEIGHTS) + ("unsafe_rejections", "hard_failures", "estops")
    deltas = {key: mean(c["summary"][key] - b["summary"][key] for c, b in pairs)
              for key in keys}
    scores = [score_result(c) - score_result(b) for c, b in pairs]
    return {"episodes": len(pairs), "mean_delta": deltas,
            "mean_score_delta": mean(scores), "worst_score_delta": min(scores),
            "demand_paired": True,
            "paired_episode_ids": [c["episode_id"] for c, _ in pairs]}


def _guard(pairs: list[tuple[dict, dict]]) -> str | None:
    # Hard incident and rejected-action regression is checked per episode.
    for current, base in pairs:
        for metric in ("unsafe_rejections", "hard_failures", "estops"):
            if current["summary"][metric] > base["summary"][metric]:
                return f"{metric}_regression"
    # Apply aggregate business limits independently to every weather regime.
    for regime in sorted({c["regime"] for c, _ in pairs}):
        group = [(c, b) for c, b in pairs if c["regime"] == regime]
        evidence = _paired_evidence(group)["mean_delta"]
        if evidence["demand_fill_rate"] < -GATE_ASSUMPTIONS["max_fill_rate_decline"]:
            return f"fill_rate_regression:{regime}"
        if evidence["inspection_completion_rate"] < -GATE_ASSUMPTIONS["max_inspection_completion_decline"]:
            return f"inspection_regression:{regime}"
        if evidence["stockout_minutes"] > GATE_ASSUMPTIONS["max_stockout_increase_minutes"]:
            return f"stockout_regression:{regime}"
        energy_base = mean(b["summary"]["energy_wh"] for _, b in group)
        if evidence["energy_wh"] > max(1.0, energy_base * GATE_ASSUMPTIONS["max_energy_increase_fraction"]):
            return f"energy_regression:{regime}"
    return None


def _decision(candidate_id: str, reason: str, **extra) -> dict:
    return {"schema": SELECTION_SCHEMA, "environment": "SIMULATION",
            "candidate_id": candidate_id, "reason": reason,
            "score_weights": deepcopy(SCORE_WEIGHTS),
            "gate_assumptions": deepcopy(GATE_ASSUMPTIONS),
            "production_promotion": False, **extra}


def choose_training_finalist(results: list[dict], incumbent_id: str = "baseline") -> dict:
    """Training selects a finalist only. Never pass validation or test rows here."""
    if incumbent_id not in {row["candidate_id"] for row in candidate_catalog()}:
        raise ValueError("incumbent must belong to the frozen catalog")
    grouped = _index(results, "train")
    if incumbent_id not in grouped or "baseline" not in grouped:
        return _decision(incumbent_id, "missing_incumbent_or_fixed_baseline", paired={})
    incumbent, baseline = grouped[incumbent_id], grouped["baseline"]
    _, error = _pairs(incumbent, baseline)
    if error:
        return _decision(incumbent_id, error, paired={})
    evidence, eligible = {}, []
    for candidate_id, rows in sorted(grouped.items()):
        pairs, error = _pairs(rows, incumbent)
        fixed_pairs, fixed_error = _pairs(rows, baseline)
        error = error or fixed_error
        if not error:
            error = _guard(pairs) or _guard(fixed_pairs)
        if error:
            evidence[candidate_id] = {"eligible": False, "reason": error}
            continue
        item = _paired_evidence(pairs)
        evidence[candidate_id] = {"eligible": True, **item}
        eligible.append((item["mean_score_delta"], candidate_id))
    if not eligible:
        return _decision(incumbent_id, "no_eligible_candidate", paired=evidence)
    improvement, winner = sorted(eligible, key=lambda row: (-row[0], row[1]))[0]
    if improvement < GATE_ASSUMPTIONS["minimum_score_improvement"]:
        return _decision(incumbent_id, "retain_incumbent_no_training_improvement", paired=evidence)
    return _decision(winner, "training_finalist_requires_fresh_validation", paired=evidence)


def validation_gate(results: list[dict], finalist_id: str,
                    incumbent_id: str = "baseline") -> dict:
    """Check one frozen finalist; held-out test data may only be reported elsewhere."""
    if not {incumbent_id, finalist_id} <= {row["candidate_id"] for row in candidate_catalog()}:
        raise ValueError("incumbent and finalist must belong to the frozen catalog")
    grouped = _index(results, "validation")
    allowed = {"baseline", incumbent_id, finalist_id}
    if set(grouped) - allowed:
        raise ValueError("validation may only evaluate baseline, incumbent and frozen finalist")
    if not allowed <= set(grouped):
        return _decision(incumbent_id, "missing_frozen_finalist_or_reference", accepted=False, paired={})
    if finalist_id == incumbent_id:
        return _decision(incumbent_id, "incumbent_unchanged", accepted=False, paired={})
    evidence = {}
    for reference_id in sorted({"baseline", incumbent_id}):
        pairs, error = _pairs(grouped[finalist_id], grouped[reference_id])
        if error:
            return _decision(incumbent_id, error, accepted=False, paired=evidence)
        if len(pairs) < GATE_ASSUMPTIONS["min_paired_episodes"]:
            return _decision(incumbent_id, "insufficient_paired_validation_episodes", accepted=False, paired=evidence)
        evidence[reference_id] = _paired_evidence(pairs)
        error = _guard(pairs)
        if error:
            return _decision(incumbent_id, error, accepted=False, paired=evidence)
    if evidence[incumbent_id]["mean_score_delta"] < GATE_ASSUMPTIONS["minimum_score_improvement"]:
        return _decision(incumbent_id, "retain_incumbent_no_validation_improvement", accepted=False, paired=evidence)
    return _decision(finalist_id, "validated_simulation_candidate_only", accepted=True, paired=evidence)
