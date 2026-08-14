"""Byte-identical evidence and simulator neutrality."""

from __future__ import annotations

import pytest

from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.scenarios.generators import make_scenario
from nxt_telemetry.bank import SyntheticSensorBank, site_config_from_scenario

from nxt_agent_runtime import (
    AgentRuntimeError,
    EvaluationJournal,
    JsonEvaluationCheckpointStore,
)
from nxt_site_runtime.checkpoints import JsonCheckpointStore

from .conftest import (
    DEPLOYMENT_ID,
    PILOT_SEED,
    SITE_ID,
    PilotObservationSource,
    calm_batch,
    evidence_paths,
    make_runtime,
    spike_batch,
)

EVIDENCE_FILES = ("snapshots.jsonl", "ledger.jsonl", "evaluations.jsonl")


def _run_pilot(root, base_inputs):
    source = PilotObservationSource(
        [calm_batch(base_inputs), spike_batch(base_inputs)]
    )
    runtime = make_runtime(root, base_inputs, source)
    runtime.run(3)
    return runtime


def test_same_inputs_produce_byte_identical_runtime_evidence(
    tmp_path, base_inputs
):
    _run_pilot(tmp_path / "first", base_inputs)
    _run_pilot(tmp_path / "second", base_inputs)
    for name in EVIDENCE_FILES:
        first = (tmp_path / "first" / name).read_bytes()
        second = (tmp_path / "second" / name).read_bytes()
        assert first == second, f"{name} differs between identical runs"


def test_restart_sequence_reaches_the_same_final_persistent_state(
    tmp_path, base_inputs
):
    clean_root = tmp_path / "clean"
    _run_pilot(clean_root, base_inputs)

    # Interrupted run: one cycle, then a fresh runtime instance finishes.
    restart_root = tmp_path / "restart"
    first = make_runtime(
        restart_root,
        base_inputs,
        PilotObservationSource([calm_batch(base_inputs)]),
    )
    first.run(1)
    second = make_runtime(
        restart_root,
        base_inputs,
        PilotObservationSource(
            [calm_batch(base_inputs), spike_batch(base_inputs)]
        ),
    )
    outcomes = second.run(3)
    assert [outcome.kind.value for outcome in outcomes] == [
        "replay_skipped",
        "evaluated",
        "source_exhausted",
    ]
    for name in EVIDENCE_FILES:
        clean = (clean_root / name).read_bytes()
        restarted = (restart_root / name).read_bytes()
        assert clean == restarted, f"{name} differs after restart"

    clean_site = JsonCheckpointStore(
        evidence_paths(clean_root)["site_checkpoints"]
    ).load(SITE_ID, DEPLOYMENT_ID)
    restart_site = JsonCheckpointStore(
        evidence_paths(restart_root)["site_checkpoints"]
    ).load(SITE_ID, DEPLOYMENT_ID)
    assert clean_site == restart_site
    clean_evaluation = JsonEvaluationCheckpointStore(
        evidence_paths(clean_root)["evaluation_checkpoints"]
    ).load(SITE_ID, DEPLOYMENT_ID)
    restart_evaluation = JsonEvaluationCheckpointStore(
        evidence_paths(restart_root)["evaluation_checkpoints"]
    ).load(SITE_ID, DEPLOYMENT_ID)
    assert clean_evaluation == restart_evaluation


def test_journal_read_is_deterministic_and_round_trips(tmp_path, base_inputs):
    root = tmp_path / "run"
    _run_pilot(root, base_inputs)
    journal = EvaluationJournal(evidence_paths(root)["journal"])
    assert journal.read() == journal.read()
    reread = EvaluationJournal(evidence_paths(root)["journal"]).read()
    assert reread == journal.read()


def test_journal_rejects_tampered_evidence(tmp_path, base_inputs):
    root = tmp_path / "run"
    _run_pilot(root, base_inputs)
    path = evidence_paths(root)["journal"]
    tampered = path.read_text(encoding="utf-8").replace(
        '"verdict":"no_action"', '"verdict":"recommend"'
    )
    path.write_text(tampered, encoding="utf-8")
    with pytest.raises(ValueError):
        EvaluationJournal(path).read()
    restarted = make_runtime(root, base_inputs, PilotObservationSource([]))
    with pytest.raises(AgentRuntimeError) as raised:
        restarted.recover()
    assert raised.value.incident_code == "evidence_verification_failed"


def test_runtime_consumes_no_simulator_rng(tmp_path):
    """The composition layer never receives a simulator reference, so the
    invariant under test is that generating fixtures and running the full
    runtime leaves the simulator's RNG streams and the bank's keyed
    sampling byte-identical — nothing in the loop reaches back upstream."""
    scenario = make_scenario("normal_weekday")
    sim = RangeSimulation(scenario, seed=PILOT_SEED)
    streams = (
        "_rng_demand",
        "_rng_skills",
        "_rng_failures",
        "_rng_sensors",
        "_rng_forecast",
    )
    bank = SyntheticSensorBank(sim)
    frame = bank.sample()
    states_before = {
        name: getattr(sim, name).bit_generator.state for name in streams
    }
    base_inputs = {
        "scenario": scenario,
        "frame": frame,
        "site_config": site_config_from_scenario(scenario, seed=PILOT_SEED),
    }
    _run_pilot(tmp_path / "rng", base_inputs)
    states_after = {
        name: getattr(sim, name).bit_generator.state for name in streams
    }
    assert states_before == states_after
    # Re-sampling after the full runtime run reproduces the identical frame.
    assert SyntheticSensorBank(sim).sample() == frame
