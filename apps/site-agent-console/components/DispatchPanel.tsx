"use client";

import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  INITIAL_TASK_OPS_VIEW, buildSchedule, canMutateTaskOps, createTaskOpsClient,
  createTaskOpsPoller, humanResponse, isAmbiguousMutation, localTimeToUtc,
  type HumanResponse, type ScheduleInput, type TaskNotification, type TaskOpsView,
} from "../lib/task-ops";
import {
  createSchedulerHealthTracker,
  SCHEDULER_HEALTH_EXPIRY_MS,
  schedulerAllowsWrites,
  schedulerHealthLabel,
  schedulerHealthReason,
  UNKNOWN_SCHEDULER_HEALTH,
  type SchedulerHealth,
  type SchedulerHealthTracker,
} from "../lib/scheduler-health";
import { Badge, EmptyNote, KeyValue, Section, type Tone } from "./ui";

const client = createTaskOpsClient((input, init) => fetch(input, init));
const statusTone = (status: string): Tone => {
  if (["FAILED", "REJECTED", "MISSED", "OFFLINE", "BLOCKED_AWAITING_HUMAN"].includes(status)) return "bad";
  if (["SCHEDULED", "OPEN", "ACKNOWLEDGED", "STALE", "UNKNOWN"].includes(status)) return "warn";
  if (["SUCCEEDED", "COMPLETED", "RESOLVED", "ONLINE", "RUNNING"].includes(status)) return "ok";
  return "info";
};
const errorText = (cause: unknown) => cause instanceof Error ? cause.message : String(cause);
const utcLabel = (value: string | null) => value ? value.replace("T", " ").replace(/\.\d+Z$/, " UTC").replace(/Z$/, " UTC") : "Not reported";

export type TaskOpsActions = {
  schedule: (input: ScheduleInput) => Promise<void>;
  cancel: (id: string, operator: string) => Promise<void>;
  respond: (id: string, kind: "acknowledge" | "resolve", input: HumanResponse) => Promise<void>;
  refresh: () => Promise<void>;
};

export function ScheduleForm({ robots, zones, disabled, onSchedule }: {
  robots: string[]; zones: string[]; disabled: boolean;
  onSchedule: (input: ScheduleInput) => Promise<void>;
}) {
  const [robot, setRobot] = useState(robots[0] ?? "");
  const [zone, setZone] = useState(zones[0] ?? "");
  const [localTime, setLocalTime] = useState("");
  const [validityMinutes, setValidityMinutes] = useState("10");
  const [operator, setOperator] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const pending = useRef<ScheduleInput | null>(null);
  const locked = useRef(false);
  let preview = "Choose a date and time to see the UTC conversion.";
  try {
    const utc = localTimeToUtc(localTime);
    preview = `${Intl.DateTimeFormat().resolvedOptions().timeZone} → ${utcLabel(utc)}`;
  } catch { /* Incomplete fields are validated on submit. */ }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (disabled || locked.current) return;
    locked.current = true;
    setSubmitting(true);
    setError(null);
    setMessage(null);
    try {
      const input = pending.current ?? buildSchedule({ robot, zone, localTime, validityMinutes, operator });
      pending.current = input;
      await onSchedule(input);
      pending.current = null;
      setUncertain(false);
      setLocalTime("");
      setMessage("Schedule recorded. Its status and any task result appear below.");
    } catch (cause) {
      if (pending.current && isAmbiguousMutation(cause)) {
        setUncertain(true);
        setError("The schedule may have been saved. Retry the same request to confirm it; the original date, robot and operator are retained to prevent another schedule.");
      } else {
        pending.current = null;
        setUncertain(false);
        setError(errorText(cause));
      }
    } finally { locked.current = false; setSubmitting(false); }
  }
  const formDisabled = disabled || submitting || uncertain;
  return (
    <form className="dispatch-form" onSubmit={submit}>
      <div className="dispatch-form-grid">
        <div className="form-row"><label htmlFor="dispatch-robot">Robot</label>
          <select id="dispatch-robot" value={robot} onChange={(event) => setRobot(event.target.value)} disabled={formDisabled} required>
            {robots.length ? robots.map((id) => <option key={id}>{id}</option>) : <option value="">No available robots</option>}
          </select></div>
        <div className="form-row"><label htmlFor="dispatch-zone">Collection zone</label>
          <select id="dispatch-zone" value={zone} onChange={(event) => setZone(event.target.value)} disabled={formDisabled} required>
            {zones.length ? zones.map((id) => <option key={id}>{id}</option>) : <option value="">No available zones</option>}
          </select></div>
        <div className="form-row dispatch-time"><label htmlFor="dispatch-time">Start date and time · browser local time</label>
          <input id="dispatch-time" type="datetime-local" value={localTime} onChange={(event) => setLocalTime(event.target.value)} disabled={formDisabled} required aria-describedby="dispatch-time-preview" /></div>
        <div className="form-row"><label htmlFor="dispatch-validity">Task validity after start · minutes</label>
          <input id="dispatch-validity" type="number" min="1" max="1440" step="1" value={validityMinutes} onChange={(event) => setValidityMinutes(event.target.value)} disabled={formDisabled} required /></div>
        <div className="form-row"><label htmlFor="dispatch-operator">Schedule operator</label>
          <input id="dispatch-operator" autoComplete="off" value={operator} onChange={(event) => setOperator(event.target.value)} disabled={formDisabled} required /></div>
      </div>
      <p className="fineprint" id="dispatch-time-preview">{preview}</p>
      <p className="fineprint">One dated collection task. The service checks availability when it is due. Task expiry is a validity limit; it is not a physical stop command.</p>
      <div className="form-actions"><button className="btn btn-primary" type="submit" disabled={disabled || submitting || (!uncertain && (!operator.trim() || !localTime || !robot || !zone))}>
        {submitting ? "Saving…" : uncertain ? "Retry same schedule" : "Schedule simulated collection"}
      </button></div>
      {error ? <p role="alert" className="form-error">{error}</p> : null}
      {message ? <p role="status" className="dispatch-success">{message}</p> : null}
    </form>
  );
}

function NotificationCard({ item, disabled, onRespond }: {
  item: TaskNotification; disabled: boolean; onRespond: TaskOpsActions["respond"];
}) {
  const [operator, setOperator] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const lock = useRef(false);
  async function respond(kind: "acknowledge" | "resolve") {
    if (disabled || lock.current) return;
    lock.current = true;
    setError(null);
    try { await onRespond(item.notification_id, kind, humanResponse(operator, note)); }
    catch (cause) { setError(errorText(cause)); }
    finally { lock.current = false; }
  }
  const ready = !!operator.trim() && !!note.trim() && !disabled;
  return (
    <article className="dispatch-record">
      <div className="rec-head"><Badge tone={statusTone(item.status)}>{item.status}</Badge><strong>{item.reason_code}</strong></div>
      <p className="rec-summary">{item.detail}</p>
      <p className="fineprint">{item.robot_id ?? "Schedule service"} · {utcLabel(item.created_at_utc)}</p>
      <p className="fineprint mono">Task: {item.task_id ?? "No task created"} · Schedule: {item.schedule_id ?? "Not linked"}</p>
      {item.acknowledged_by ? <p className="fineprint">Acknowledged by {item.acknowledged_by}</p> : null}
      {item.resolved_by ? <p className="fineprint">Resolved by {item.resolved_by}</p> : null}
      {item.status !== "RESOLVED" ? (
        <div className="response-form">
          <div className="dispatch-response-grid">
            <div className="form-row"><label htmlFor={`${item.notification_id}-operator`}>Response operator</label>
              <input id={`${item.notification_id}-operator`} value={operator} onChange={(event) => setOperator(event.target.value)} disabled={disabled} required /></div>
            <div className="form-row"><label htmlFor={`${item.notification_id}-note`}>Handling note · required</label>
              <input id={`${item.notification_id}-note`} value={note} onChange={(event) => setNote(event.target.value)} disabled={disabled} required /></div>
          </div>
          <div className="form-actions">
            {item.status === "OPEN" ? <button type="button" className="btn" disabled={!ready} onClick={() => void respond("acknowledge")}>Acknowledge</button> : null}
            <button type="button" className="btn" disabled={!ready || !item.can_resolve || item.condition_active} onClick={() => void respond("resolve")}>Resolve notification</button>
          </div>
          <p className="fineprint">{item.condition_active ? "The condition is still active. Resolution is unavailable." : !item.can_resolve ? "Waiting for the service to permit resolution." : "The condition has cleared; record the handling outcome."} Resolving this notification never unlocks or restarts a robot.</p>
          {error ? <p role="alert" className="form-error">{error}</p> : null}
        </div>
      ) : null}
    </article>
  );
}

function CancelSchedule({ id, disabled, cancel }: { id: string; disabled: boolean; cancel: TaskOpsActions["cancel"] }) {
  const [operator, setOperator] = useState("");
  const [error, setError] = useState<string | null>(null);
  const lock = useRef(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (disabled || lock.current || !operator.trim()) return;
    lock.current = true;
    setError(null);
    try { await cancel(id, operator.trim()); } catch (cause) { setError(errorText(cause)); }
    finally { lock.current = false; }
  }
  return <form className="dispatch-cancel" onSubmit={submit}>
    <div className="form-row"><label htmlFor={`${id}-cancel`}>Cancellation operator</label>
      <input id={`${id}-cancel`} value={operator} onChange={(event) => setOperator(event.target.value)} disabled={disabled} required /></div>
    <button className="btn" type="submit" disabled={disabled || !operator.trim()}>Cancel schedule</button>
    <p className="fineprint">Cancels this pending schedule only.</p>
    {error ? <p className="form-error" role="alert">{error}</p> : null}
  </form>;
}

export function DispatchView({ view, actions, health }: { view: TaskOpsView; actions: TaskOpsActions; health?: SchedulerHealth }) {
  const data = view.data;
  // With the shared reading, the task panel labels and gates on the same
  // freshness-aware health as the planning panel; without it (standalone
  // render) it keeps the raw snapshot semantics.
  const healthBlock = health !== undefined && !schedulerAllowsWrites(health) ? schedulerHealthReason(health) : null;
  const disabled = !canMutateTaskOps(view) || healthBlock !== null;
  const count = data?.notifications.filter((item) => item.status !== "RESOLVED").length ?? 0;
  return (
    <Section title="Pilot task operations" aside={<><Badge tone="sim">SIMULATION</Badge>{health !== undefined ? <Badge tone={schedulerAllowsWrites(health) ? "ok" : health.status === "unknown" ? "warn" : "bad"}>{schedulerHealthLabel(health)}</Badge> : data ? <Badge tone={statusTone(data.scheduler.state)}>{data.scheduler.state}</Badge> : null}</>}>
      <p className="sim-note">Schedule a collection, follow its progress, and record staff handling in one place.</p>
      <p className="fineprint dispatch-boundary">Local simulation with protocol doubles. No physical robot or CE82A is connected. Advice acceptance below remains a separate workflow record.</p>
      {view.unavailable ? <div className="dispatch-service-note" role="status"><Badge tone="muted">UNAVAILABLE</Badge>
        <p>This standalone demo does not expose pilot task operations. Start the pilot dispatch service to enable scheduling and the local notification inbox. The fixture console remains available below.</p>
      </div> : view.error ? <div className="load-warning" role="alert"><Badge tone="bad">{data ? "STALE" : "OFFLINE"}</Badge>
        {view.error} {data ? "Showing the last successful task view. Changes are disabled." : "Task controls are unavailable."}
      </div> : view.loading ? <p className="empty-note">Loading pilot schedules and task evidence…</p> : null}
      {data ? <>
        <div className="dispatch-health">
          <span className="detail-text">Updated {utcLabel(data.server_time_utc)} · refreshes every 2 seconds</span>
          <span className="detail-text">{data.transport === "in_memory" ? "Local protocol double" : "MQTT protocol double"} · protocol rehearsal, not V3 execution evidence</span>
          <button className="btn btn-quiet" type="button" onClick={() => void actions.refresh()} disabled={view.busy || view.loading}>Refresh tasks</button>
        </div>
        {data.scheduler.state === "FAILED" ? <p className="form-error" role="alert">Scheduler failed. Changes are disabled. {data.scheduler.detail ?? "Inspect the service before continuing."}</p> : healthBlock !== null ? <p className="form-error" role="alert">Changes are disabled. {healthBlock}</p> : null}
        <div className="dispatch-device-list">{Object.values(data.devices).map((device) => <div className="dispatch-device" key={device.robot_id}>
          <strong>{device.robot_id}</strong><Badge tone={statusTone(device.as_read.connectivity)}>{device.as_read.connectivity}</Badge>
          <span>{device.last_reported_availability ?? "Availability unknown"}</span>
          <span className="detail-text">Status age: {device.as_read.status_age_s === null ? "unknown" : `${Math.round(device.as_read.status_age_s)}s`}</span>
          {device.session_regression ? <Badge tone="bad">SESSION REGRESSION</Badge> : null}
        </div>)}</div>
        <div className="dispatch-grid">
          <div className="dispatch-column"><h3 className="subhead">Schedule collection</h3>
            <ScheduleForm robots={data.available_robots} zones={data.available_zones} disabled={disabled} onSchedule={actions.schedule} />
            <h3 className="subhead">Schedules · {data.schedules.length}</h3>
            <div className="dispatch-list">{data.schedules.length ? data.schedules.map((schedule) => <article key={schedule.schedule_id} className="dispatch-record">
              <div className="rec-head"><Badge tone={statusTone(schedule.status)}>{schedule.status}</Badge><strong>{schedule.robot_id} · {schedule.zone_id}</strong></div>
              {schedule.status === "DISPATCHED" ? <p className="fineprint">Task created at the Edge. Acceptance and progress require separate device evidence below.</p> : null}
              <dl className="kv-grid dispatch-kv"><KeyValue label="Starts">{utcLabel(schedule.due_at_utc)}</KeyValue><KeyValue label="Expires">{utcLabel(schedule.expires_at_utc)}</KeyValue><KeyValue label="Scheduled by">{schedule.operator}</KeyValue></dl>
              {schedule.reason_code ? <p className="inline-flag"><Badge tone="warn">{schedule.reason_code}</Badge></p> : null}
              {schedule.detail ? <p className="rec-summary">{schedule.detail}</p> : null}
              <p className="fineprint mono">{schedule.schedule_id}</p>
              <p className="fineprint mono">Task: {schedule.task_id ?? "Not created"}</p>
              {schedule.status === "SCHEDULED" ? <CancelSchedule id={schedule.schedule_id} disabled={disabled} cancel={actions.cancel} /> : null}
            </article>) : <EmptyNote>No schedules yet. Add one dated collection task above.</EmptyNote>}</div>
          </div>
          <div className="dispatch-column"><div className="dispatch-subhead"><h3 className="subhead">Local notification inbox</h3><Badge tone={count ? "warn" : "muted"}>{count} UNRESOLVED</Badge></div>
            <p className="fineprint dispatch-inbox-note">Notifications appear in this page while it is open. Email, text messages and remote alerts are not connected.</p>
            <div className="dispatch-list">{data.notifications.length ? data.notifications.map((item) => <NotificationCard key={item.notification_id} item={item} disabled={disabled} onRespond={actions.respond} />) : <EmptyNote>No notifications recorded.</EmptyNote>}</div>
            <h3 className="subhead">Task progress and results</h3>
            <p className="fineprint">Protocol-double task states from the rehearsal device. Simulated collection and unloading evidence, when a V3 session is connected, appears in the collection execution panel above.</p>
            <div className="dispatch-list">{Object.values(data.tasks).length ? Object.values(data.tasks).map((task) => <article className="dispatch-record" key={task.task_id}>
              <div className="rec-head"><Badge tone={statusTone(task.state)}>{task.state}</Badge><strong>{task.target_robot_id} · {task.zone_id}</strong></div>
              <dl className="kv-grid dispatch-kv"><KeyValue label="Accepted">{task.acceptance_observed ? "Observed" : "Not observed"}</KeyValue><KeyValue label="Result">{task.effective_result ?? "Not reported"}</KeyValue><KeyValue label="Verification">{task.result_verification ?? "Not reported"}</KeyValue><KeyValue label="Last progress">{utcLabel(task.last_progress_at_utc)}</KeyValue></dl>
              {task.evidence_incomplete ? <p className="inline-flag"><Badge tone="warn">INCOMPLETE EVIDENCE</Badge></p> : null}
              {task.reconciliation_required ? <p className="fineprint">Reconciliation needed: {task.reconciliation_reasons.join(", ")}</p> : null}
              <p className="fineprint mono">{task.task_id}</p>
            </article>) : <EmptyNote>No task evidence yet. A scheduled entry is not evidence that work has started.</EmptyNote>}</div>
          </div>
        </div>
      </> : !view.loading && !view.unavailable ? <div className="form-actions"><button className="btn" type="button" onClick={() => void actions.refresh()}>Retry task connection</button></div> : null}
    </Section>
  );
}

/** The one task-ops poller for the page. Its validated view feeds the task
 * panel and, through the health tracker, the planning panel's scheduler
 * gate, so both panels always describe the same reading. */
export function usePilotTaskOps(): { view: TaskOpsView; actions: TaskOpsActions; health: SchedulerHealth; tracker: SchedulerHealthTracker } {
  const [view, setView] = useState<TaskOpsView>(INITIAL_TASK_OPS_VIEW);
  const [tracker] = useState(() => createSchedulerHealthTracker());
  const [health, setHealth] = useState<SchedulerHealth>(UNKNOWN_SCHEDULER_HEALTH);
  const poller = useRef<ReturnType<typeof createTaskOpsPoller> | null>(null);
  useEffect(() => {
    const controller = createTaskOpsPoller(client.read, (next) => {
      setView(next);
      setHealth(tracker.observe(next));
    });
    poller.current = controller;
    controller.start();
    return () => { controller.stop(); poller.current = null; };
  }, [tracker]);
  useEffect(() => {
    // The shared reading expires by itself. One timer, owned here with the
    // poller, re-evaluates the same tracker at the expiry instant so the
    // planning panel and the task panel flip together, and a successful read
    // (a new health value) re-arms it. Cleared on unmount and on every change.
    if (health.status !== "fresh" || health.observedAtMs === null) return;
    const delay = Math.max(0, health.observedAtMs + SCHEDULER_HEALTH_EXPIRY_MS - Date.now() + 1);
    const timer = setTimeout(() => setHealth(tracker.current()), delay);
    return () => clearTimeout(timer);
  }, [health, tracker]);
  const actions = useMemo<TaskOpsActions>(() => {
    const mutate = async (operation: () => Promise<unknown>) => {
      if (!poller.current) throw new Error("The task view is not connected.");
      await poller.current.mutate(operation);
    };
    return {
      schedule: (input) => mutate(() => client.schedule(input)),
      cancel: (id, operator) => mutate(() => client.cancel(id, operator)),
      respond: (id, kind, input) => mutate(() => client.respond(id, kind, input)),
      refresh: async () => { await poller.current?.refresh(); },
    };
  }, []);
  return { view, actions, health, tracker };
}
