"use client";

import { useRef, useState, type FormEvent } from "react";
import type { ConfirmationRecord, InputRecord, PlanRecord, PlanningContext, Scenario, Scope } from "../../lib/planning";
import type { PlanDraft } from "../../lib/planning-forms";
import { comparePlansNewestFirst, confirmationBlocker, effectiveStatus, previousVersion } from "../../lib/planning-view";
import { formatSiteTime, utcToSiteInput } from "../../lib/site-time";
import { Badge, EmptyNote, KeyValue } from "../ui";
import { errorText, SelectionText, statusTone } from "./shared";

const SCOPE_OPTIONS: Scope[] = ["SHIFT", "DAY", "ONE_TASK"];
const NO_SHORTAGE = "No shortage within the supplied horizon";
const time = (value: string | null, timeZone: string) => (value === null ? NO_SHORTAGE : formatSiteTime(value, timeZone));

function ScenarioTable({ scenarios, timeZone, caption }: { scenarios: Scenario[]; timeZone: string; caption: string }) {
  if (scenarios.length === 0) return <EmptyNote>No scenario could be computed; see the missing evidence above.</EmptyNote>;
  return (
    <table className="plan-table">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th>Scenario</th>
          <th>First shortage without action</th>
          <th>Safety stock reached</th>
          <th>Shortage with this plan</th>
          <th>Balls at horizon, no action</th>
          <th>Balls at horizon, with plan</th>
        </tr>
      </thead>
      <tbody>
        {scenarios.map((scenario) => (
          <tr key={scenario.level}>
            <td>{scenario.level}</td>
            <td>{time(scenario.baseline_stockout_at_utc, timeZone)}</td>
            <td>{time(scenario.safety_stock_at_utc, timeZone)}</td>
            <td>{time(scenario.with_plan_stockout_at_utc, timeZone)}</td>
            <td>{scenario.baseline_end_balls}</td>
            <td>{scenario.with_plan_end_balls}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CandidateTable({ plan, timeZone }: { plan: PlanRecord; timeZone: string }) {
  if (plan.candidates.length === 0) return <EmptyNote>No candidate zone could be evaluated.</EmptyNote>;
  return (
    <table className="plan-table">
      <caption>Candidate zones, ranked by the service: avoid the first shortage, replenish earlier, then lower net yield first.</caption>
      <thead>
        <tr>
          <th>Zone</th>
          <th>Robot</th>
          <th>Eligibility</th>
          <th>Earliest start</th>
          <th>Latest safe start</th>
          <th>Supply ready</th>
          <th>Full cycle</th>
          <th>Net yield</th>
          <th>Covers high demand</th>
        </tr>
      </thead>
      <tbody>
        {plan.candidates.map((candidate) => (
          <tr key={`${candidate.zone_id}:${candidate.robot_id}`}>
            <td className="mono">{candidate.zone_id}</td>
            <td className="mono">{candidate.robot_id}</td>
            <td>{candidate.eligible ? "eligible" : `excluded: ${candidate.exclusion_reasons.join(", ") || "reason not given"}`}</td>
            <td>{formatSiteTime(candidate.start_at_utc, timeZone)}</td>
            <td>{formatSiteTime(candidate.latest_start_at_utc, timeZone)}</td>
            <td>{formatSiteTime(candidate.supply_at_utc, timeZone)}</td>
            <td>{candidate.cycle_minutes === null ? "—" : `${candidate.cycle_minutes} min`}</td>
            <td>{candidate.clean_yield_balls === null ? "—" : `${candidate.clean_yield_balls.low} to ${candidate.clean_yield_balls.high} balls`}</td>
            <td>{candidate.protects_high_demand_horizon === null ? "unknown" : candidate.protects_high_demand_horizon ? "yes" : "no"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PlanRequestForm({
  title,
  submitLabel,
  plan,
  context,
  latestInput,
  disabled,
  allowSelection,
  onSubmit,
}: {
  title: string;
  submitLabel: string;
  plan: PlanRecord | null;
  context: PlanningContext;
  latestInput: InputRecord;
  disabled: boolean;
  allowSelection: boolean;
  onSubmit: (draft: PlanDraft) => Promise<void>;
}) {
  const timeZone = context.site_timezone;
  const base = plan?.selection ?? plan?.system_selection ?? null;
  // The UTC strings the time fields were rendered from; re-emitted verbatim
  // (seconds, microseconds, offset) while the manager leaves them unchanged.
  const originals = { validUntil: plan?.valid_until_utc ?? latestInput.valid_until_utc, startAt: base?.start_at_utc };
  const [operator, setOperator] = useState(plan?.request.operator ?? "");
  const [reason, setReason] = useState("");
  const [scope, setScope] = useState<Scope>(plan?.request.scope ?? "ONE_TASK");
  const [validUntil, setValidUntil] = useState(utcToSiteInput(plan?.valid_until_utc ?? latestInput.valid_until_utc, timeZone));
  const [zoneId, setZoneId] = useState(base?.zone_id ?? context.zone_ids[0] ?? "");
  const [robotId, setRobotId] = useState(base?.robot_id ?? context.robot_ids[0] ?? "");
  const [startAt, setStartAt] = useState(base ? utcToSiteInput(base.start_at_utc, timeZone) : "");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const lock = useRef(false);
  const id = plan ? `plan-${plan.plan_id}-${plan.version}` : "plan-new";

  async function submit(selection: PlanDraft["selection"]) {
    if (disabled || lock.current) return;
    lock.current = true;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit({ operator, reason, scope, validUntil, selection, originals });
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      lock.current = false;
      setSubmitting(false);
    }
  }

  const formDisabled = disabled || submitting;
  return (
    <form
      className="dispatch-form planning-form"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        void submit(allowSelection ? { zoneId, robotId, startAt } : null);
      }}
    >
      <h4 className="subhead">{title}</h4>
      <fieldset disabled={formDisabled} className="planning-fieldset">
        <div className="dispatch-form-grid">
          <div className="form-row">
            <label htmlFor={`${id}-operator`}>Operator · attribution only</label>
            <input id={`${id}-operator`} value={operator} onChange={(event) => setOperator(event.target.value)} autoComplete="off" />
          </div>
          <div className="form-row">
            <label htmlFor={`${id}-scope`}>Scope</label>
            <select id={`${id}-scope`} value={scope} onChange={(event) => setScope(event.target.value as Scope)}>
              {SCOPE_OPTIONS.map((option) => (
                <option key={option}>{option}</option>
              ))}
            </select>
          </div>
          <div className="form-row dispatch-time">
            <label htmlFor={`${id}-reason`}>Reason</label>
            <input id={`${id}-reason`} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Why this plan version is being requested" />
          </div>
          <div className="form-row">
            <label htmlFor={`${id}-valid`}>Plan valid until · site time</label>
            <input id={`${id}-valid`} type="datetime-local" step="1" value={validUntil} onChange={(event) => setValidUntil(event.target.value)} />
          </div>
          {allowSelection ? (
            <>
              <div className="form-row">
                <label htmlFor={`${id}-zone`}>Zone</label>
                <select id={`${id}-zone`} value={zoneId} onChange={(event) => setZoneId(event.target.value)}>
                  {context.zone_ids.map((option) => (
                    <option key={option}>{option}</option>
                  ))}
                </select>
              </div>
              <div className="form-row">
                <label htmlFor={`${id}-robot`}>Robot</label>
                <select id={`${id}-robot`} value={robotId} onChange={(event) => setRobotId(event.target.value)}>
                  {context.robot_ids.map((option) => (
                    <option key={option}>{option}</option>
                  ))}
                </select>
              </div>
              <div className="form-row">
                <label htmlFor={`${id}-start`}>Start at · site time</label>
                <input id={`${id}-start`} type="datetime-local" step="1" value={startAt} onChange={(event) => setStartAt(event.target.value)} />
              </div>
            </>
          ) : null}
        </div>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={formDisabled}>
            {submitting ? "Submitting…" : submitLabel}
          </button>
          {allowSelection && plan ? (
            <button type="button" className="btn" disabled={formDisabled} onClick={() => void submit(null)}>
              Restore the system suggestion
            </button>
          ) : null}
        </div>
      </fieldset>
      {error ? <p role="alert" className="form-error">{error}</p> : null}
    </form>
  );
}

export function PlanSection({
  plan,
  plans,
  latestInput,
  context,
  disabled,
  onRequestPlan,
}: {
  plan: PlanRecord | null;
  plans: PlanRecord[];
  latestInput: InputRecord | null;
  context: PlanningContext;
  disabled: boolean;
  onRequestPlan: (draft: PlanDraft, target: { planId: string | null; expectedPlanVersion: number; inputRevision: number }) => Promise<void>;
}) {
  const timeZone = context.site_timezone;
  const status = plan ? effectiveStatus(plan) : null;
  const revisable = plan !== null && status !== "CONFIRMED";
  const previous = plan ? previousVersion(plans, plan) : null;
  return (
    <>
      <h3 className="subhead planning-step">2 · Demand scenarios and the suggested plan</h3>
      <p className="fineprint">
        Three manual demand scenarios (low, typical, high) are the manager&apos;s assumptions, not probabilities. Times are site time ({timeZone}); the
        service computed every value from the recorded input and its rules version.
      </p>
      {plan === null ? (
        <EmptyNote>No plan has been requested for the current input yet.</EmptyNote>
      ) : (
        <article className="dispatch-record plan-card">
          <div className="rec-head">
            <Badge tone={statusTone(plan.status)}>{plan.status}</Badge>
            {plan.current_status && plan.current_status !== plan.status ? <Badge tone={statusTone(plan.current_status)}>{plan.current_status}</Badge> : null}
            <strong>
              Plan <span className="mono">{plan.plan_id}</span> · version {plan.version}
            </strong>
          </div>
          <dl className="kv-grid dispatch-kv">
            <KeyValue label="Generated">{formatSiteTime(plan.generated_at_utc, timeZone)}</KeyValue>
            <KeyValue label="Valid until">{formatSiteTime(plan.valid_until_utc, timeZone)}</KeyValue>
            <KeyValue label="Input revision">
              {plan.input_revision}
              {latestInput && latestInput.revision !== plan.input_revision ? ` (current is ${latestInput.revision})` : ""}
            </KeyValue>
            <KeyValue label="Rules">
              <span className="mono">{plan.rules_version}</span>
            </KeyValue>
          </dl>
          {plan.missing.length > 0 ? (
            <ul className="missing-list">
              {plan.missing.map((item) => (
                <li key={item}>
                  <Badge tone="bad">MISSING</Badge> <span className="mono">{item}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {plan.rationale.length > 0 ? (
            <ul className="rationale-list">
              {plan.rationale.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
          ) : null}
          <ScenarioTable scenarios={plan.scenarios} timeZone={timeZone} caption="Manual demand assumptions per scenario. Balls at horizon are signed projected balances, not observed inventory." />
          <CandidateTable plan={plan} timeZone={timeZone} />
          <h4 className="subhead">Selection</h4>
          <dl className="kv-grid dispatch-kv">
            <KeyValue label="System suggestion">
              <SelectionText selection={plan.system_selection} timeZone={timeZone} />
            </KeyValue>
            <KeyValue label="This version">
              <SelectionText selection={plan.selection} timeZone={timeZone} />
            </KeyValue>
          </dl>
          {plan.adjustment.before !== null || plan.adjustment.after !== null ? (
            <div className="plan-adjustment">
              <Badge tone="info">HUMAN ADJUSTMENT</Badge> {plan.adjustment.operator} · {plan.adjustment.scope} · until{" "}
              {formatSiteTime(plan.adjustment.valid_until_utc, timeZone)}
              <p className="rec-summary">{plan.adjustment.reason}</p>
              <dl className="kv-grid dispatch-kv">
                <KeyValue label="Before">
                  <SelectionText selection={plan.adjustment.before} timeZone={timeZone} />
                </KeyValue>
                <KeyValue label="After">
                  <SelectionText selection={plan.adjustment.after} timeZone={timeZone} />
                </KeyValue>
              </dl>
            </div>
          ) : (
            <p className="fineprint">No manual adjustment in this version. Reason recorded: {plan.adjustment.reason}</p>
          )}
          {previous ? (
            <details className="trace-details">
              <summary>Compare with version {previous.version}</summary>
              <ScenarioTable scenarios={previous.scenarios} timeZone={timeZone} caption={`Version ${previous.version} scenarios, for comparison with version ${plan.version} above.`} />
              <p className="fineprint">
                Version {previous.version} selection: <SelectionText selection={previous.selection} timeZone={timeZone} />
              </p>
            </details>
          ) : null}
          <p className="fineprint mono">
            request {plan.request.request_id} · operator {plan.request.operator} · input digest {plan.input_digest.slice(0, 16)}…
          </p>
        </article>
      )}
      {plans.length > 1 ? (
        <details className="trace-details">
          <summary>Plan history ({plans.length} versions)</summary>
          <ul>
            {[...plans]
              .sort(comparePlansNewestFirst)
              .map((item) => (
                <li key={`${item.plan_id}:${item.version}`}>
                  <span className="mono">{item.plan_id}</span> v{item.version} · {effectiveStatus(item)} · {formatSiteTime(item.generated_at_utc, timeZone)} ·{" "}
                  <SelectionText selection={item.selection} timeZone={timeZone} />
                </li>
              ))}
          </ul>
        </details>
      ) : null}
      <h3 className="subhead planning-step">3 · Manual adjustment</h3>
      {latestInput === null ? (
        <>
          <p className="fineprint">Record the operating input first; the service then evaluates the scenarios and suggests a plan.</p>
          <div className="form-actions">
            <button type="button" className="btn btn-primary" disabled>
              Ask for the system suggestion
            </button>
          </div>
        </>
      ) : (
        <>
          <PlanRequestForm
            key={`new-${latestInput.revision}`}
            title="Request a new plan from the current input"
            submitLabel="Ask for the system suggestion"
            plan={null}
            context={context}
            latestInput={latestInput}
            disabled={disabled}
            allowSelection={false}
            onSubmit={(draft) => onRequestPlan(draft, { planId: null, expectedPlanVersion: 0, inputRevision: latestInput.revision })}
          />
          {plan !== null && revisable ? (
            <PlanRequestForm
              key={`adjust-${plan.plan_id}-${plan.version}-${latestInput.revision}`}
              title={`Adjust plan ${plan.plan_id} (creates version ${plan.version + 1})`}
              submitLabel="Apply adjustment"
              plan={plan}
              context={context}
              latestInput={latestInput}
              disabled={disabled}
              allowSelection
              onSubmit={(draft) => onRequestPlan(draft, { planId: plan.plan_id, expectedPlanVersion: plan.version, inputRevision: latestInput.revision })}
            />
          ) : plan !== null ? (
            <p className="fineprint">Version {plan.version} is confirmed and can no longer be revised. Request a new plan if the situation changes.</p>
          ) : null}
        </>
      )}
    </>
  );
}

export function ConfirmationSection({
  plan,
  plans,
  latestInput,
  confirmation,
  confirmations,
  timeZone,
  disabled,
  onConfirm,
}: {
  plan: PlanRecord | null;
  plans: PlanRecord[];
  latestInput: InputRecord | null;
  confirmation: ConfirmationRecord | null;
  confirmations: ConfirmationRecord[];
  timeZone: string;
  disabled: boolean;
  onConfirm: (plan: PlanRecord, operator: string) => Promise<void>;
}) {
  const [operator, setOperator] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const lock = useRef(false);
  const blocker = plan ? confirmationBlocker(plan, plans, latestInput) : null;

  async function confirm() {
    if (!plan || disabled || lock.current) return;
    lock.current = true;
    setSubmitting(true);
    setError(null);
    try {
      await onConfirm(plan, operator);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      lock.current = false;
      setSubmitting(false);
    }
  }

  return (
    <>
      <h3 className="subhead planning-step">4 · Confirm one exact version and follow its simulated task</h3>
      <p className="fineprint">
        Confirmation records explicit human intent for one plan version and creates one simulated schedule through the existing task admission. It is not a
        physical command. Re-confirming the same version returns the original confirmation; it never creates a second task. The old advice queue&apos;s Accept
        button remains workflow evidence only. Simulated execution evidence for an admitted task appears in the collection execution panel below this one.
      </p>
      {confirmation ? (
        <article className="dispatch-record">
          <div className="rec-head">
            <Badge tone="ok">CONFIRMED</Badge>
            <strong>
              Confirmation <span className="mono">{confirmation.confirmation_id}</span>
            </strong>
            <Badge tone={confirmation.schedule_status ? statusTone(confirmation.schedule_status) : "warn"}>{confirmation.schedule_status ?? "PENDING LINKAGE"}</Badge>
          </div>
          <dl className="kv-grid dispatch-kv">
            <KeyValue label="Plan version">
              <span className="mono">{confirmation.plan_id}</span> v{confirmation.plan_version} · input revision {confirmation.input_revision}
            </KeyValue>
            <KeyValue label="Confirmed at">{formatSiteTime(confirmation.confirmed_at_utc, timeZone)} by {confirmation.request.operator}</KeyValue>
            <KeyValue label="Schedule">
              <span className="mono">{confirmation.schedule_id}</span>
            </KeyValue>
            <KeyValue label="Task">{confirmation.task_id ? <span className="mono">{confirmation.task_id}</span> : "No task yet; the schedule is admitted at its due time"}</KeyValue>
            <KeyValue label="Admitted (lower bound)">
              {confirmation.task_created_at_utc
                ? `${formatSiteTime(confirmation.task_created_at_utc, timeZone)} · Edge TASK_CREATED time: a lower bound on any start, not the start itself`
                : "Not admitted yet · no TASK_CREATED evidence"}
            </KeyValue>
            <KeyValue label="Due">{formatSiteTime(confirmation.schedule.due_at_utc, timeZone)}</KeyValue>
            <KeyValue label="Expires">{formatSiteTime(confirmation.schedule.expires_at_utc, timeZone)}</KeyValue>
            <KeyValue label="Robot · zone">
              <span className="mono">{confirmation.schedule.robot_id}</span> · <span className="mono">{confirmation.schedule.zone_id}</span>
            </KeyValue>
            <KeyValue label="Admission reference">
              <span className="mono">{confirmation.schedule.admission_reference}</span>
            </KeyValue>
          </dl>
          <p className="fineprint mono">request {confirmation.request.request_id} · schedule operator token {confirmation.schedule.operator}</p>
        </article>
      ) : plan === null ? (
        <EmptyNote>No confirmation yet. Request and review a plan first.</EmptyNote>
      ) : blocker !== null ? (
        <p className="fineprint">
          <Badge tone="warn">NOT CONFIRMABLE</Badge> Version {plan.version} cannot be confirmed: {blocker}
        </p>
      ) : (
        <div className="response-form">
          <div className="form-row">
            <label htmlFor="confirm-operator">Confirming operator · attribution only</label>
            <input id="confirm-operator" value={operator} onChange={(event) => setOperator(event.target.value)} disabled={disabled || submitting} autoComplete="off" />
          </div>
          <div className="form-actions">
            <button type="button" className="btn btn-primary" disabled={disabled || submitting} onClick={() => void confirm()}>
              {submitting ? "Confirming…" : `Confirm plan version ${plan.version}`}
            </button>
          </div>
          <p className="fineprint">
            Confirms <span className="mono">{plan.plan_id}</span> version {plan.version} only. The service re-checks freshness, restrictions and the latest safe start.
          </p>
          {error ? <p role="alert" className="form-error">{error}</p> : null}
        </div>
      )}
      {confirmations.length > 1 || (confirmations.length === 1 && confirmation === null) ? (
        <details className="trace-details">
          <summary>All confirmations ({confirmations.length})</summary>
          <ul>
            {confirmations.map((item) => (
              <li key={item.confirmation_id}>
                <span className="mono">{item.confirmation_id}</span> · plan <span className="mono">{item.plan_id}</span> v{item.plan_version} ·{" "}
                {item.schedule_status ?? "PENDING LINKAGE"} · task {item.task_id ?? "none"}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </>
  );
}
