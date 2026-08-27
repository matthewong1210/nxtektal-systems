import type {
  Briefing,
  FixtureInfo,
  Health,
  Recommendation,
  StateProjection,
} from "../lib/api";

export function sampleHealth(overrides: Partial<Health> = {}): Health {
  return {
    service_state: "serving",
    mode_label: "fixture-backed Shadow Mode",
    fixture_mode: true,
    source_type: "fixture",
    degraded: false,
    site_id: "pilot-course-a",
    deployment_id: "pilot-a-site-agent-v0",
    workflow_id: "range.closed_loop_collection_handoff",
    workflow_readiness: "READY_FOR_FIXTURE_SHADOW_MODE",
    report_id: "wer_0123456789abcdef01234567",
    run_directory: "run-001",
    runtime: {
      runtime_state: "running",
      degraded: false,
      cycles_completed: 2,
      evaluations_completed: 2,
      source_exhausted: false,
      last_observed_sequence: 1,
      last_published_sequence: 1,
      last_evaluated_sequence: 1,
      last_verdict: "recommend",
      pending_decision_count: 1,
      last_observation_timestamp_s: 66600,
      last_effective_confidence: 1,
      last_failure_code: null,
      last_failure_detail: null,
    },
    source: {
      cursor: { consumed_cycles: 2, next_sequence_number: 2 },
      declared_cycles: 6,
      exhausted: false,
      max_cycles: 8,
    },
    pending_recommendation_count: 1,
    last_failure_code: null,
    last_failure_detail: null,
    event_append_failures: 0,
    ...overrides,
  };
}

export function sampleState(
  overrides: Partial<StateProjection> = {},
): StateProjection {
  return {
    available: true,
    reason: null,
    envelope: {
      envelope_id: "fse:abc123",
      sequence_number: 1,
      observation_timestamp_s: 66600,
      site_id: "pilot-course-a",
      deployment_id: "pilot-a-site-agent-v0",
    },
    dispenser: {
      clean_available_balls: 2400,
      clean_sensed_balls: 2400,
      count_source: {
        channel: "inventory.dispenser.count",
        status: "ok",
        confidence: 1,
        sample_timestamp_s: 66595,
        available_timestamp_s: 66600,
        calibration_id: "CAL-LC-PILOTA-2026",
      },
      sensed_source: null,
      reading_age_s: 5,
    },
    quality: {
      assembly_report: {
        missing_channels: [],
        stale_channels: [],
        consistency_issues: [],
        overall_confidence: 1,
        provenance_grade: "high",
      },
      runtime_quality: {
        assembly_confidence: 1,
        upstream_confidence: 1,
        effective_confidence: 1,
      },
    },
    ...overrides,
  };
}

export function sampleRecommendation(
  overrides: Partial<Recommendation> = {},
): Recommendation {
  return {
    recommendation_id: "rec_8523e923aa00112233445566",
    action: "operator_intervention",
    target_robot_id: null,
    summary:
      "Recommend operator intervention: projected stockout with no " +
      "eligible collector.",
    policy_id: "ball-availability-guardian",
    policy_version: "0.1.0",
    trace_id: "trace_0011223344556677889900aa",
    issued_at: "2026-08-08T18:30:00.000000Z",
    execute_before: "2026-08-08T19:40:00.000000Z",
    case_status: "pending",
    response_kind: null,
    source_envelope_id: "fse:abc123",
    source_sequence: 1,
    evaluation_id: "aev_00112233445566778899aabb",
    recommendation: null,
    trace: {
      trace_id: "trace_0011223344556677889900aa",
      policy_id: "ball-availability-guardian",
      policy_version: "0.1.0",
      rationale: ["Projected stockout precedes the protection horizon."],
      missing_data_reasons: [
        "collection permission unavailable",
        "washer availability unavailable",
      ],
      data_completeness_score: 0.55,
      selected_robot_id: null,
      candidates: [
        {
          robot_id: "R1",
          eligible: false,
          exclusion_reasons: ["missing_replenishment_eta"],
        },
      ],
      projected_stockout_without_action_minutes: 42,
    },
    manager_response: null,
    ...overrides,
  };
}

export function sampleBriefing(overrides: Partial<Briefing> = {}): Briefing {
  return {
    disclaimer: "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA",
    identity: {
      site_id: "pilot-course-a",
      deployment_id: "pilot-a-site-agent-v0",
      workflow_id: "range.closed_loop_collection_handoff",
      mode_label: "fixture-backed Shadow Mode",
      run_directory: "run-001",
    },
    current_state: sampleState(),
    cycles: { admitted: 2, rejected: 1 },
    timeline: [
      {
        tag: "OBSERVED",
        text: "Cycle admitted at sequence 0: 17:30 calm.",
        scenario_t_s: 63000,
        scenario_time: "17:30",
        references: {},
      },
      {
        tag: "MISSING",
        text: "Cycle rejected before publication (insufficient_data_quality).",
        scenario_t_s: 68400,
        scenario_time: "19:00",
        references: {},
      },
    ],
    no_action_records: [],
    pending_review: [],
    manager_decisions: [],
    exceptions: [
      {
        kind: "rejected_cycle",
        tag: "MISSING",
        failure_code: "insufficient_data_quality",
        detail: "missing_inputs=2",
        scenario_time: "19:00",
        cycle_label: "19:00 dispenser load cell silent",
      },
    ],
    unresolved: ["Rejected fixture cycle needs attention."],
    ...overrides,
  };
}

export function sampleFixture(
  overrides: Partial<FixtureInfo> = {},
): FixtureInfo {
  return {
    fixture_mode: true,
    disclaimer: "SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA",
    cycle_catalog: [
      {
        cycle_index: 0,
        label: "17:30 calibrated dispenser and equipment state",
        scenario_t_s: 63000,
        scenario_time: "17:30",
        variant: "nominal",
        source: "SIMULATED",
      },
    ],
    cursor: { consumed_cycles: 0, next_sequence_number: 0 },
    next_cycle: {
      cycle_index: 0,
      label: "17:30 calibrated dispenser and equipment state",
      scenario_t_s: 63000,
      scenario_time: "17:30",
      variant: "nominal",
      source: "SIMULATED",
    },
    controls: { advance: true, restart: true, reset: true },
    ...overrides,
  };
}
