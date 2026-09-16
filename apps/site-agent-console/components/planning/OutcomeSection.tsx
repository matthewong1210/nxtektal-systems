"use client";

import { useRef, useState, type FormEvent } from "react";
import { STAGES, type ConfirmationRecord, type OutcomeRecord, type SourceKind, type Stage } from "../../lib/planning";
import type { OutcomeDraft } from "../../lib/planning-forms";
import { latestOutcomes, outcomeHistory } from "../../lib/planning-view";
import { formatSiteTime } from "../../lib/site-time";
import { Badge, EmptyNote } from "../ui";
import { errorText, sourceLabel } from "./shared";

function OutcomeForm({
  confirmation,
  stage,
  supersedes,
  disabled,
  onRecord,
  onClose,
}: {
  confirmation: ConfirmationRecord;
  stage: Stage;
  supersedes: OutcomeRecord | null;
  disabled: boolean;
  onRecord: (confirmation: ConfirmationRecord, draft: OutcomeDraft, supersedesOutcomeId: string | null) => Promise<void>;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<OutcomeDraft>({
    operator: "",
    reason: "",
    stage,
    quantity: "",
    startedAt: "",
    completedAt: "",
    sourceKind: "MANUAL_ESTIMATE",
    sourceRef: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const lock = useRef(false);
  const id = `${confirmation.confirmation_id}-${stage}`;
  const patch = (next: Partial<OutcomeDraft>) => setDraft((current) => ({ ...current, ...next }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (disabled || lock.current) return;
    lock.current = true;
    setSubmitting(true);
    setError(null);
    try {
      await onRecord(confirmation, draft, supersedes?.outcome_id ?? null);
      onClose();
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      lock.current = false;
      setSubmitting(false);
    }
  }

  return (
    <form className="dispatch-form planning-form" onSubmit={submit}>
      <fieldset disabled={disabled || submitting} className="planning-fieldset">
        <div className="dispatch-form-grid">
          <div className="form-row">
            <label htmlFor={`${id}-quantity`}>Balls {stage.toLowerCase()} · whole number</label>
            <input id={`${id}-quantity`} type="number" min="0" step="1" value={draft.quantity} onChange={(event) => patch({ quantity: event.target.value })} />
          </div>
          <div className="form-row">
            <label htmlFor={`${id}-source-kind`}>How it was obtained</label>
            <select id={`${id}-source-kind`} value={draft.sourceKind} onChange={(event) => patch({ sourceKind: event.target.value as SourceKind })}>
              <option value="MEASURED">Measured (counted or weighed)</option>
              <option value="MANUAL_ESTIMATE">Manual estimate</option>
            </select>
          </div>
          <div className="form-row">
            <label htmlFor={`${id}-source-ref`}>Source reference</label>
            <input id={`${id}-source-ref`} value={draft.sourceRef} onChange={(event) => patch({ sourceRef: event.target.value })} placeholder="e.g. work log page 4" />
          </div>
          <div className="form-row">
            <label htmlFor={`${id}-operator`}>Recorded by · attribution only</label>
            <input id={`${id}-operator`} value={draft.operator} onChange={(event) => patch({ operator: event.target.value })} autoComplete="off" />
          </div>
          <div className="form-row">
            <label htmlFor={`${id}-started`}>Stage started · site time</label>
            <input id={`${id}-started`} type="datetime-local" value={draft.startedAt} onChange={(event) => patch({ startedAt: event.target.value })} />
          </div>
          <div className="form-row">
            <label htmlFor={`${id}-completed`}>Stage completed · site time</label>
            <input id={`${id}-completed`} type="datetime-local" value={draft.completedAt} onChange={(event) => patch({ completedAt: event.target.value })} />
          </div>
          <div className="form-row dispatch-time">
            <label htmlFor={`${id}-reason`}>Reason or note</label>
            <input id={`${id}-reason`} value={draft.reason} onChange={(event) => patch({ reason: event.target.value })} placeholder={supersedes ? "Why the earlier record is being corrected" : "Where and when this was observed"} />
          </div>
        </div>
        {supersedes ? (
          <p className="fineprint">
            Corrects <span className="mono">{supersedes.outcome_id}</span>; the earlier record stays in the history.
          </p>
        ) : null}
        <div className="form-actions">
          <button type="submit" className="btn btn-primary">
            {submitting ? "Saving…" : `Save ${stage} result`}
          </button>
          <button type="button" className="btn btn-quiet" onClick={onClose}>
            Cancel
          </button>
        </div>
      </fieldset>
      {error ? <p role="alert" className="form-error">{error}</p> : null}
    </form>
  );
}

function StageCard({
  confirmation,
  stage,
  latest,
  history,
  timeZone,
  disabled,
  onRecord,
}: {
  confirmation: ConfirmationRecord;
  stage: Stage;
  latest: OutcomeRecord | undefined;
  history: OutcomeRecord[];
  timeZone: string;
  disabled: boolean;
  onRecord: (confirmation: ConfirmationRecord, draft: OutcomeDraft, supersedesOutcomeId: string | null) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const canRecord = !!confirmation.task_id && !disabled;
  return (
    <article className="dispatch-record stage-card">
      <div className="rec-head">
        <Badge tone={latest ? "ok" : "warn"}>{stage}</Badge>
        {latest ? (
          <strong>
            {latest.request.quantity_balls} balls · {sourceLabel(latest.request.source_kind)}
          </strong>
        ) : (
          <strong>Not recorded</strong>
        )}
      </div>
      {latest ? (
        <>
          <p className="fineprint">
            {formatSiteTime(latest.request.started_at_utc, timeZone)} to {formatSiteTime(latest.request.completed_at_utc, timeZone)} · by {latest.request.operator} · source{" "}
            <span className="mono">{latest.request.source_ref}</span>
          </p>
          <p className="rec-summary">{latest.request.reason}</p>
          <p className="fineprint mono">
            {latest.outcome_id}
            {latest.request.supersedes_outcome_id ? ` · corrects ${latest.request.supersedes_outcome_id}` : ""}
          </p>
          {history.length > 1 ? (
            <details className="trace-details">
              <summary>History ({history.length} records)</summary>
              <ul>
                {history.map((item) => (
                  <li key={item.outcome_id}>
                    <span className="mono">{item.outcome_id}</span> · {item.request.quantity_balls} balls · {sourceLabel(item.request.source_kind)} ·{" "}
                    {formatSiteTime(item.recorded_at_utc, timeZone)}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      ) : (
        <p className="fineprint">This stage stays unknown until someone records its quantity, source and times.</p>
      )}
      {open ? (
        <OutcomeForm confirmation={confirmation} stage={stage} supersedes={latest ?? null} disabled={disabled} onRecord={onRecord} onClose={() => setOpen(false)} />
      ) : (
        <div className="form-actions">
          <button type="button" className="btn" disabled={!canRecord} onClick={() => setOpen(true)}>
            {latest ? "Correct this stage" : `Record ${stage}`}
          </button>
        </div>
      )}
    </article>
  );
}

export function OutcomeSection({
  confirmations,
  outcomes,
  timeZone,
  disabled,
  onRecord,
}: {
  confirmations: ConfirmationRecord[];
  outcomes: OutcomeRecord[];
  timeZone: string;
  disabled: boolean;
  onRecord: (confirmation: ConfirmationRecord, draft: OutcomeDraft, supersedesOutcomeId: string | null) => Promise<void>;
}) {
  return (
    <>
      <h3 className="subhead planning-step">5 · Actual results, stage by stage</h3>
      <p className="fineprint">
        A successful task does not tell you how many balls were collected; each stage is unknown until someone records it with its source and times. Recording
        SUPPLIED does not change the clean inventory shown in the operating input: enter a new opening count, with its provenance, when you next revise the input.
        A correction adds a record that supersedes the earlier one; nothing is deleted.
      </p>
      {confirmations.length === 0 ? (
        <EmptyNote>No confirmation, so no task to record results for.</EmptyNote>
      ) : (
        confirmations.map((confirmation) => {
          const latest = latestOutcomes(outcomes, confirmation.confirmation_id);
          return (
            <div className="outcome-group" key={confirmation.confirmation_id}>
              <h4 className="subhead">
                Results for <span className="mono">{confirmation.confirmation_id}</span> · task {confirmation.task_id ? <span className="mono">{confirmation.task_id}</span> : "not admitted yet"}
              </h4>
              {!confirmation.task_id ? <p className="fineprint">Results can be recorded once the schedule has been admitted as a task.</p> : null}
              <div className="stage-grid">
                {STAGES.map((stage) => (
                  <StageCard
                    key={stage}
                    confirmation={confirmation}
                    stage={stage}
                    latest={latest[stage]}
                    history={outcomeHistory(outcomes, confirmation.confirmation_id, stage)}
                    timeZone={timeZone}
                    disabled={disabled}
                    onRecord={onRecord}
                  />
                ))}
              </div>
            </div>
          );
        })
      )}
    </>
  );
}
