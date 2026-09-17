"use client";

import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { createPlanningClient } from "../lib/planning";
import { createPlanningActions, type PlanningActions } from "../lib/planning-actions";
import {
  canWritePlanning,
  createPlanningController,
  initialPlanningView,
  PLANNING_POLL_MS,
  type PlanningView as PlanningViewState,
  type WriteState,
} from "../lib/planning-state";
import { confirmationFor, focusPlan } from "../lib/planning-view";
import { schedulerAllowsWrites, schedulerHealthLabel, schedulerHealthReason, type SchedulerHealth } from "../lib/scheduler-health";
import { formatSiteTime } from "../lib/site-time";
import { InputForm, InputSummary } from "./planning/InputSection";
import { OutcomeSection } from "./planning/OutcomeSection";
import { ConfirmationSection, PlanSection } from "./planning/PlanSection";
import { Badge, EmptyNote, Section } from "./ui";

export type { PlanningActions } from "../lib/planning-actions";

const client = createPlanningClient((input, init) => fetch(input, init));

const KIND_LABEL: Record<WriteState["kind"], string> = {
  inputs: "operating input",
  plans: "plan version",
  confirmations: "confirmation",
  outcomes: "result record",
};

const REFUSAL_HINT: Record<string, string> = {
  planning_conflict: "Your view was behind the service (a newer revision, version, or a reused request ID). Refresh, then try again from the current records.",
  planning_expired: "The plan or input validity elapsed, or the latest safe start passed. Request a new plan.",
  planning_not_ready: "The plan is not confirmable as it stands: complete the missing evidence or choose an eligible candidate.",
  planning_invalid_request: "The service rejected the request shape or values; check the entered fields.",
  planning_not_found: "The referenced plan, confirmation or task is not in the verified evidence.",
  planning_request_not_found: "No committed request with this ID is in the verified evidence.",
};

function WriteBanner({ write, view, actions }: { write: WriteState; view: PlanningViewState; actions: PlanningActions }) {
  const label = KIND_LABEL[write.kind];
  if (write.status === "in_flight") {
    return (
      <div className="load-warning" role="status">
        <Badge tone="info">SUBMITTING</Badge> Sending the {label} request <span className="mono">{write.requestId}</span>…
      </div>
    );
  }
  if (write.status === "unknown") {
    return (
      <div className="load-warning" role="alert">
        <Badge tone="bad">UNKNOWN OUTCOME</Badge> The {label} request <span className="mono">{write.requestId}</span> did not receive a reliable receipt ({write.detail}).
        It was not retried and its content is kept. Recover it by its request ID before making other planning changes; the service returns the committed record if it
        exists and replays the identical request otherwise.{" "}
        <button type="button" className="btn btn-quiet" disabled={write.recovering || view.busy} onClick={() => void actions.recover().catch(() => undefined)}>
          {write.recovering ? "Recovering…" : "Recover by request ID"}
        </button>
      </div>
    );
  }
  if (write.status === "rejected") {
    return (
      <div className="load-warning" role="alert">
        <Badge tone="bad">REFUSED</Badge> The {label} request <span className="mono">{write.requestId}</span> was refused: <span className="mono">{write.code}</span> —{" "}
        {write.detail} {REFUSAL_HINT[write.code] ?? ""}{" "}
        <button type="button" className="btn btn-quiet" onClick={actions.acknowledgeWrite}>
          Dismiss
        </button>
      </div>
    );
  }
  const receipt = write.receipt;
  const record = receipt.record as unknown as Record<string, unknown>;
  const recordId =
    (record.confirmation_id as string | undefined) ??
    (record.outcome_id as string | undefined) ??
    (record.plan_id !== undefined ? `${String(record.plan_id)} v${String(record.version)}` : undefined) ??
    (record.revision !== undefined ? `input revision ${String(record.revision)}` : undefined) ??
    receipt.request_id;
  return (
    <div className="load-warning" role="status">
      <Badge tone="ok">SAVED</Badge>{" "}
      {receipt.disposition === "duplicate" ? (
        <>
          This {label} was already recorded under request ID <span className="mono">{receipt.request_id}</span>
          {receipt.request_id !== write.requestId ? (
            <>
              {" "}
              (your request ID <span className="mono">{write.requestId}</span> was not stored as an alias; use the original ID for lookups)
            </>
          ) : null}
          : <span className="mono">{recordId}</span>. No second record or task was created.
        </>
      ) : (
        <>
          The {label} <span className="mono">{recordId}</span> was saved (request <span className="mono">{receipt.request_id}</span>).
        </>
      )}
      {view.error !== null ? " The view could not be refreshed afterwards, so the records below may be stale until a refresh succeeds." : ""}{" "}
      <button type="button" className="btn btn-quiet" onClick={actions.acknowledgeWrite}>
        Dismiss
      </button>
    </div>
  );
}

function SchedulerHealthBanner({ view, timeZone }: { view: PlanningViewState; timeZone: string }) {
  const health = view.health;
  const reason = schedulerHealthReason(health);
  if (schedulerAllowsWrites(health) && reason === null) {
    return (
      <p className="fineprint planning-health" role="status">
        <Badge tone="ok">{schedulerHealthLabel(health)}</Badge> Confirmed by the task service at {formatSiteTime(health.observedAtUtc, timeZone)} (site time); the task panel below
        reads the same scheduler state.
      </p>
    );
  }
  return (
    <div className="load-warning" role={health.status === "unknown" ? "status" : "alert"}>
      <Badge tone={health.status === "unknown" ? "warn" : "bad"}>{schedulerHealthLabel(health)}</Badge> {reason}
      {view.write?.status === "unknown"
        ? " The request with an unknown outcome below keeps its ID and content; recovering it by that ID stays available and does not restart the scheduler."
        : ""}
    </div>
  );
}

export function PlanningView({ view, actions }: { view: PlanningViewState; actions: PlanningActions }) {
  const data = view.data;
  const locked = !canWritePlanning(view);
  const refreshDisabled = view.loading || view.busy;
  const timeZone = data?.context.site_timezone ?? "UTC";
  const plan = data ? focusPlan(data.plans) : null;
  const confirmation = data && plan ? confirmationFor(data.confirmations, plan) : null;
  return (
    <Section
      title="Manager planning · human-led ball supply"
      aside={
        <>
          <Badge tone="sim">SIMULATION</Badge>
          <Badge tone="info">MANUAL-LED</Badge>
        </>
      }
    >
      <p className="sim-note">Enter today&apos;s operating assumptions, review the three manual demand scenarios, adjust and confirm one exact plan version, then record what actually happened.</p>
      <p className="fineprint dispatch-boundary">
        Manual estimates are labelled as estimates and are not measurements or probabilities. Confirming a version creates one simulated schedule through the existing task
        admission; nothing here commands a physical machine. Operator names are attribution only, not authentication or permission.
      </p>
      {view.unavailable ? (
        <div className="dispatch-service-note" role="status">
          <Badge tone="muted">UNAVAILABLE</Badge>
          <p>The planning API is not available on this service. Start the pilot dispatch runner from the integrated baseline to use manager planning.</p>
        </div>
      ) : view.error !== null ? (
        <div className="load-warning" role="alert">
          <Badge tone={data ? "warn" : "bad"}>{data ? "STALE" : "OFFLINE"}</Badge> A refresh failed ({view.error}).{" "}
          {data ? "Showing the last successful planning view. Planning changes are disabled until a refresh succeeds." : "Planning is unavailable until the service answers."}{" "}
          <button type="button" className="btn btn-quiet" onClick={() => void actions.refresh()} disabled={refreshDisabled}>
            Retry refresh
          </button>
        </div>
      ) : view.loading && data === null ? (
        <p className="empty-note">Loading the planning records…</p>
      ) : null}
      <SchedulerHealthBanner view={view} timeZone={timeZone} />
      {view.write ? <WriteBanner write={view.write} view={view} actions={actions} /> : null}
      {data ? (
        <>
          <div className="dispatch-health">
            <span className="detail-text">
              Site <span className="mono">{data.context.site_id}</span> · deployment <span className="mono">{data.context.deployment_id}</span> · site time {data.context.site_timezone}
            </span>
            <span className="detail-text">
              Zones {data.context.zone_ids.join(", ") || "none"} · robots {data.context.robot_ids.join(", ") || "none"}
            </span>
            <span className="detail-text">
              Service time {formatSiteTime(data.server_time_utc, timeZone)} · refreshes every {PLANNING_POLL_MS / 1000} seconds
            </span>
            <button type="button" className="btn btn-quiet" onClick={() => void actions.refresh()} disabled={refreshDisabled}>
              Refresh planning
            </button>
          </div>
          <h3 className="subhead planning-step">1 · Operating input</h3>
          {data.latest_input ? (
            <InputSummary input={data.latest_input} timeZone={timeZone} />
          ) : (
            <EmptyNote>No operating input has been recorded for this site yet. Enter the opening count, demand assumptions and zone estimates below; unknown fields stay unknown.</EmptyNote>
          )}
          <InputForm key={data.latest_input?.revision ?? 0} input={data.latest_input} context={data.context} disabled={locked} onSave={actions.saveInput} />
          <PlanSection plan={plan} plans={data.plans} latestInput={data.latest_input} context={data.context} disabled={locked} onRequestPlan={actions.requestPlan} />
          <ConfirmationSection
            key={plan ? `${plan.plan_id}:${plan.version}` : "none"}
            plan={plan}
            plans={data.plans}
            latestInput={data.latest_input}
            confirmation={confirmation}
            confirmations={data.confirmations}
            timeZone={timeZone}
            disabled={locked}
            onConfirm={actions.confirmPlan}
          />
          <OutcomeSection confirmations={data.confirmations} outcomes={data.outcomes} timeZone={timeZone} disabled={locked} onRecord={actions.recordOutcome} />
        </>
      ) : !view.loading && !view.unavailable && view.error === null ? (
        <div className="form-actions">
          <button type="button" className="btn" onClick={() => void actions.refresh()}>
            Retry planning connection
          </button>
        </div>
      ) : null}
    </Section>
  );
}

export function PlanningPanel({ health, healthSource }: { health: SchedulerHealth; healthSource: () => SchedulerHealth }) {
  const [view, setView] = useState<PlanningViewState>(() => initialPlanningView());
  const [controller] = useState(() => createPlanningController(client, setView, { health: healthSource }));
  useEffect(() => {
    controller.start();
    return () => controller.stop();
  }, [controller]);
  useEffect(() => {
    // A changed source prop is honoured without recreating the controller.
    controller.setHealthSource(healthSource);
  }, [controller, healthSource]);
  useLayoutEffect(() => {
    // The shared reading changed (task-ops poll): republish before paint so
    // this panel and the task panel commit the same reading in one frame.
    controller.notifyHealth();
  }, [controller, health]);
  const context = view.data?.context ?? null;
  const actions = useMemo(() => createPlanningActions(controller, context), [controller, context]);
  return <PlanningView view={view} actions={actions} />;
}
