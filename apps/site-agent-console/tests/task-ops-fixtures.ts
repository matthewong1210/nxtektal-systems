import type { TaskOpsSnapshot } from "../lib/task-ops";

export function taskOpsFixture(overrides: Partial<TaskOpsSnapshot> = {}): TaskOpsSnapshot {
  return {
    schema: "nxt-pilot-dispatch/v0", environment: "SIMULATION",
    server_time_utc: "2026-09-16T12:00:00Z",
    scheduler: { state: "RUNNING", detail: null },
    service_capabilities: {
      schema: "nxt-pilot-dispatch/service-capabilities/v1",
      mode: "LEGACY_PILOT_DISPATCH",
      operations: {
        planning_inputs_create: "SUPPORTED", planning_plans_create: "SUPPORTED",
        planning_confirmations_create: "SUPPORTED", planning_outcomes_create: "SUPPORTED",
        schedules_create: "SUPPORTED", schedules_cancel: "SUPPORTED",
        notifications_acknowledge: "SUPPORTED", notifications_resolve: "SUPPORTED",
      },
    },
    schedules: [{ schedule_id: "schedule-1", robot_id: "picker-01", zone_id: "Z1",
      due_at_utc: "2026-09-16T12:05:00Z", expires_at_utc: "2026-09-16T12:15:00Z",
      operator: "staff-01", progress_window_s: 30, status: "SCHEDULED", task_id: null,
      reason_code: null, detail: null }],
    notifications: [{ notification_id: "notification-1", source_record_id: "record-1", robot_id: "picker-01",
      task_id: "task-1", schedule_id: "schedule-1", reason_code: "device_offline", detail: "Picker status has expired.",
      created_at_utc: "2026-09-16T12:00:00Z", status: "OPEN", condition_active: true, can_resolve: false,
      acknowledged_by: null, resolved_by: null }],
    devices: { "picker-01": { robot_id: "picker-01", connectivity: "ONLINE", last_reported_availability: "IDLE",
      session_regression: false, as_read: { connectivity: "OFFLINE", status_age_s: 90, basis: "journal_receipt_clock" } } },
    tasks: { "task-1": { task_id: "task-1", target_robot_id: "picker-01", zone_id: "Z1", state: "BLOCKED_AWAITING_HUMAN",
      effective_result: null, result_verification: null, acceptance_observed: true, evidence_incomplete: false,
      reconciliation_required: true, reconciliation_reasons: ["progress_window_elapsed"],
      last_progress_at_utc: "2026-09-16T12:00:00Z", expires_at_utc: "2026-09-16T12:15:00Z" } },
    available_robots: ["picker-01"], available_zones: ["Z1"], transport: "in_memory", ...overrides,
  };
}
