"""End-to-end: commissioned bindings -> adapters -> Site Runtime -> Agent Runtime.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from nxt_agent_runtime import (
    AgentRuntime,
    CycleKind,
    EvaluationJournal,
    FailurePolicy,
    JsonEvaluationCheckpointStore,
    JsonlSnapshotPublisher,
)
from nxt_pilot_ops.ledger import JsonlEventLedger
from nxt_site_runtime.checkpoints import JsonCheckpointStore
from nxt_site_runtime.ports import SequencedObservationFrame
from nxt_telemetry.assemble import assemble_from_observations
from nxt_telemetry.observations import ObservationStatus

from scripts.pilot_course_a_edge_fixture import (
    DEPLOYMENT_ID,
    FACILITY_SYSTEM_CHANNELS,
    PILOT_CYCLES,
    SITE_ID,
    TOTAL_BALLS,
    commissioned_site,
    pilot_observation_source,
    raw_batch,
    site_config,
    upstream_inputs,
)

SIMULATION_MIDNIGHT = datetime(2026, 8, 8, tzinfo=timezone.utc)


@pytest.fixture
def config(site):
    return site_config(site)


@pytest.fixture
def source(site):
    return pilot_observation_source(site)


def make_runtime(root, config, source, **overrides):
    values = {
        "site_id": SITE_ID,
        "deployment_id": DEPLOYMENT_ID,
        "site_config": config,
        "observation_source": source,
        "publisher": JsonlSnapshotPublisher(root / "snapshots.jsonl"),
        "ledger": JsonlEventLedger(root / "ledger.jsonl"),
        "journal": EvaluationJournal(root / "evaluations.jsonl"),
        "simulation_midnight": SIMULATION_MIDNIGHT,
        "clean_sensed_valid": True,
        "site_checkpoint_store": JsonCheckpointStore(root / "cp" / "site"),
        "evaluation_checkpoint_store": JsonEvaluationCheckpointStore(
            root / "cp" / "evaluation"
        ),
    }
    values.update(overrides)
    return AgentRuntime(**values)


# -- commissioning -> adapter configuration ------------------------------


def test_commissioned_bindings_drive_the_adapter_configuration(site, kit):
    commissioned = {binding.channel for binding in site.sensor_bindings}
    assert set(kit.bindings.channels) == commissioned
    assert commissioned, "the pilot manifest must declare bindings"


def test_the_adapter_produces_no_channel_that_is_not_commissioned(source):
    batch = source.feed.peek().batch
    result = source.kit.convert(batch)
    commissioned = set(source.kit.bindings.channels)
    assert {item.channel for item in result.observations} <= commissioned


# -- raw samples -> canonical frame --------------------------------------


def test_a_raw_batch_becomes_a_complete_canonical_frame(source, config):
    sequenced = source.observe()
    assert isinstance(sequenced, SequencedObservationFrame)
    frame = sequenced.frame
    channels = set(frame.by_channel())
    assert set(FACILITY_SYSTEM_CHANNELS) <= channels
    state, report = assemble_from_observations(
        frame, config, upstream_inputs(PILOT_CYCLES[0])
    )
    assert report.missing_channels == ()
    assert report.stale_channels == ()
    assert report.consistency_issues == ()
    assert report.overall_confidence == 1.0
    assert state.ball_flow.conserved
    assert state.ball_flow.total_balls == TOTAL_BALLS


def test_the_frame_carries_calibration_and_provenance_per_observation(source):
    frame = source.observe().frame
    for observation in frame.observations:
        assert observation.calibration_id
        assert observation.source_id
        assert observation.sample_timestamp_s <= observation.available_timestamp_s
        assert observation.available_timestamp_s <= frame.t_s


def test_observe_is_a_peek_and_redelivers_the_identical_frame(source):
    first = source.observe()
    second = source.observe()
    assert first.frame.to_dict() == second.frame.to_dict()
    assert first.sequence_number == second.sequence_number


# -- Site Runtime and Agent Runtime lifecycle ----------------------------


def test_the_full_pilot_storyline_runs_deterministically(tmp_path, config, source):
    runtime = make_runtime(tmp_path, config, source)
    outcomes = runtime.run(max_cycles=8, failure_policy=FailurePolicy.CONTINUE)

    kinds = [outcome.kind for outcome in outcomes]
    assert kinds == [
        CycleKind.EVALUATED,
        CycleKind.EVALUATED,
        CycleKind.REJECTED,
        CycleKind.REJECTED,
        CycleKind.EVALUATED,
        CycleKind.SOURCE_EXHAUSTED,
    ]
    assert [outcome.sequence_number for outcome in outcomes[:5]] == [0, 1, 2, 2, 2]

    stale, uncalibrated = outcomes[2], outcomes[3]
    assert stale.failure.code.value == "stale_observation"
    assert uncalibrated.failure.code.value == "insufficient_data_quality"

    status = runtime.status()
    assert status.evaluations_completed == 3
    assert status.degraded is False
    assert status.last_evaluated_sequence == 2


def test_rejected_adapter_data_never_reaches_policy_evaluation(
    tmp_path, config, source
):
    runtime = make_runtime(tmp_path, config, source)
    outcomes = runtime.run(max_cycles=8, failure_policy=FailurePolicy.CONTINUE)
    rejected = [item for item in outcomes if item.kind is CycleKind.REJECTED]
    assert rejected
    for outcome in rejected:
        assert outcome.record is None
        assert outcome.evaluation_id is None
        assert outcome.envelope_id is None
        assert outcome.acknowledged is False
    journal = runtime._journal.read()
    assert len(journal) == 3
    assert {record.sequence_number for record in journal} == {0, 1, 2}


def test_an_admitted_envelope_is_evaluated_exactly_once(tmp_path, config, source):
    runtime = make_runtime(tmp_path, config, source)
    runtime.run(max_cycles=8, failure_policy=FailurePolicy.CONTINUE)
    journal = runtime._journal.read()
    envelope_ids = [record.envelope_id for record in journal]
    assert len(envelope_ids) == len(set(envelope_ids))
    evaluation_ids = [record.evaluation_id for record in journal]
    assert len(evaluation_ids) == len(set(evaluation_ids))


def test_corrected_redelivery_succeeds_at_the_reused_sequence(
    tmp_path, config, source
):
    runtime = make_runtime(tmp_path, config, source)
    runtime.run(max_cycles=8, failure_policy=FailurePolicy.CONTINUE)
    assert source.feed.acknowledged == (0, 1, 2)
    assert [reason for _, reason in source.feed.rejected] == [
        "stale_observation",
        "insufficient_data_quality",
    ]
    assert [sequence for sequence, _ in source.feed.rejected] == [2, 2]


def test_the_guardian_still_fails_closed_instead_of_dispatching(
    tmp_path, config, source
):
    """No invented capability/ETA/yield/permission means no collector dispatch."""
    runtime = make_runtime(tmp_path, config, source)
    runtime.run(max_cycles=8, failure_policy=FailurePolicy.CONTINUE)
    payloads = [record.to_payload() for record in runtime._journal.read()]
    verdicts = [item["verdict"] for item in payloads]
    actions = {item["recommendation_action"] for item in payloads}
    assert verdicts[0] == "no_action"
    assert actions <= {None, "operator_intervention"}
    assert "dispatch_collector" not in actions


def _evidence_map(root):
    """Every evidence file keyed by its path relative to ``root``.

    Keyed by relative path, never by bare name: the runtime writes
    checkpoint files under both ``cp/site`` and ``cp/evaluation``, and a
    name-keyed map would let two same-named files overwrite each other and
    hide a divergence in one of them.
    """
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.startswith(".")
    }


def test_the_evidence_map_distinguishes_same_named_files(tmp_path):
    """Regression: same-named files in different subdirectories never collide."""
    (tmp_path / "cp" / "site").mkdir(parents=True)
    (tmp_path / "cp" / "evaluation").mkdir(parents=True)
    (tmp_path / "cp" / "site" / "checkpoint.json").write_bytes(b"site-bytes")
    (tmp_path / "cp" / "evaluation" / "checkpoint.json").write_bytes(
        b"evaluation-bytes"
    )
    mapping = _evidence_map(tmp_path)
    assert len(mapping) == 2
    assert mapping["cp/site/checkpoint.json"] == b"site-bytes"
    assert mapping["cp/evaluation/checkpoint.json"] == b"evaluation-bytes"
    # A divergence in exactly one of them is visible, not masked.
    (tmp_path / "cp" / "evaluation" / "checkpoint.json").write_bytes(b"changed")
    assert _evidence_map(tmp_path) != mapping


def test_two_independent_runs_produce_identical_evidence(tmp_path, config, site):
    """Determinism across processes -- not a restart (see the tests below)."""
    evidence = _evidence_map

    first_root, second_root = tmp_path / "first", tmp_path / "second"
    for root in (first_root, second_root):
        root.mkdir()
        runtime = make_runtime(root, config, pilot_observation_source(site))
        runtime.run(max_cycles=8, failure_policy=FailurePolicy.CONTINUE)
    assert evidence(first_root) == evidence(second_root)


def test_a_restarted_runtime_reconciles_without_reevaluating(
    tmp_path, config, site
):
    """Recovery only; the cycle loop is exercised by the restart tests below."""
    first = make_runtime(tmp_path, config, pilot_observation_source(site))
    first.run(max_cycles=8, failure_policy=FailurePolicy.CONTINUE)
    before = [record.evaluation_id for record in first._journal.read()]

    resumed = make_runtime(tmp_path, config, pilot_observation_source(site))
    report = resumed.recover()
    assert report.journal_record_count == len(before)
    assert report.evaluation_has_pending is False
    assert [record.evaluation_id for record in resumed._journal.read()] == before


# -- adapter diagnostics stay outside facility truth ---------------------


def test_the_adapter_report_is_not_part_of_the_published_state(
    tmp_path, config, source
):
    runtime = make_runtime(tmp_path, config, source)
    runtime.run(max_cycles=8, failure_policy=FailurePolicy.CONTINUE)
    published = (tmp_path / "snapshots.jsonl").read_text(encoding="utf-8")
    for token in (
        "adapter_report",
        "nxt-edge-observation/adapter-report/v0",
        "unmapped",
        "raw_field",
    ):
        assert token not in published, token


def test_every_rejected_field_is_reported_never_silently_dropped(source):
    spec = dataclasses.replace(
        PILOT_CYCLES[3], cycle_index=0
    )  # uncalibrated dispenser
    result = source.kit.convert(raw_batch(spec))
    assert result.report.rejected
    for item in result.report.rejected:
        assert item.detail
        assert item.code.value
    missing = [
        observation
        for observation in result.observations
        if observation.status is ObservationStatus.MISSING
    ]
    assert missing
    assert all(observation.value is None for observation in missing)


def test_the_unsupported_channels_are_declared_and_not_adapter_output(
    source, site
):
    adapter_channels = set(source.kit.bindings.channels)
    for channel in FACILITY_SYSTEM_CHANNELS:
        assert channel not in adapter_channels
    commissioned = {binding.channel for binding in site.sensor_bindings}
    for channel in FACILITY_SYSTEM_CHANNELS:
        assert channel not in commissioned


# -- real restart of the cycle loop --------------------------------------


def test_a_restart_that_replays_from_zero_cannot_make_progress(
    tmp_path, config, site
):
    """The failure mode the resume hook exists to prevent.

    ``invalid_sequence`` is deliberately not discardable, so a source that
    restarts at sequence 0 after publishing sequence 1 retains its frame
    and the runtime can never advance again.
    """
    first = make_runtime(tmp_path, config, pilot_observation_source(site))
    first.run(max_cycles=2, failure_policy=FailurePolicy.CONTINUE)
    assert first.status().last_published_sequence == 1

    naive = make_runtime(tmp_path, config, pilot_observation_source(site))
    outcomes = naive.run(max_cycles=5, failure_policy=FailurePolicy.CONTINUE)
    # The runtime stops after two consecutive identical rejections because a
    # retained frame can make no progress without external remediation.
    assert {item.kind for item in outcomes} == {CycleKind.REJECTED}
    assert {item.failure.code.value for item in outcomes} == {"invalid_sequence"}
    assert all(item.failure.retryable is False for item in outcomes)
    assert naive.status().last_published_sequence == 1


def test_a_resumed_source_restarts_the_cycle_loop_and_completes(
    tmp_path, config, site
):
    """A restart that resumes at the checkpoint finishes the storyline once."""
    first = make_runtime(tmp_path, config, pilot_observation_source(site))
    first_outcomes = first.run(max_cycles=2, failure_policy=FailurePolicy.CONTINUE)
    assert [item.kind for item in first_outcomes] == [
        CycleKind.EVALUATED,
        CycleKind.EVALUATED,
    ]
    checkpoint_sequence = first.status().last_published_sequence
    assert checkpoint_sequence == 1

    resumed_source = pilot_observation_source(
        site,
        consumed_cycles=2,
        first_sequence_number=checkpoint_sequence + 1,
    )
    resumed = make_runtime(tmp_path, config, resumed_source)
    resumed_outcomes = resumed.run(
        max_cycles=8, failure_policy=FailurePolicy.CONTINUE
    )
    assert [item.kind for item in resumed_outcomes] == [
        CycleKind.REJECTED,
        CycleKind.REJECTED,
        CycleKind.EVALUATED,
        CycleKind.SOURCE_EXHAUSTED,
    ]

    # Exactly three evaluations across both processes, each envelope once.
    journal = resumed._journal.read()
    assert [record.sequence_number for record in journal] == [0, 1, 2]
    assert len({record.evaluation_id for record in journal}) == 3


def test_a_resumed_run_matches_an_uninterrupted_run_byte_for_byte(
    tmp_path, config, site
):
    straight_root = tmp_path / "straight"
    split_root = tmp_path / "split"
    straight_root.mkdir()
    split_root.mkdir()

    make_runtime(straight_root, config, pilot_observation_source(site)).run(
        max_cycles=8, failure_policy=FailurePolicy.CONTINUE
    )

    make_runtime(split_root, config, pilot_observation_source(site)).run(
        max_cycles=2, failure_policy=FailurePolicy.CONTINUE
    )
    make_runtime(
        split_root,
        config,
        pilot_observation_source(site, consumed_cycles=2, first_sequence_number=2),
    ).run(max_cycles=8, failure_policy=FailurePolicy.CONTINUE)

    assert _evidence_map(straight_root) == _evidence_map(split_root)


def test_the_resume_hook_validates_its_inputs(site):
    for kwargs in (
        {"consumed_cycles": -1},
        {"first_sequence_number": -1},
        {"consumed_cycles": True},
        {"consumed_cycles": len(PILOT_CYCLES) + 1},
    ):
        with pytest.raises((ValueError, TypeError)):
            pilot_observation_source(site, **kwargs)
