"use client";

import { useState } from "react";
import type { Recommendation, RespondInput } from "../lib/api";
import { formatIsoTime, shortId } from "../lib/format";
import { Badge, EmptyNote, KeyValue, Section, type Tone } from "./ui";

export type RespondHandler = (
  recommendationId: string,
  kind: "accept" | "reject" | "modify",
  input: RespondInput,
) => Promise<void>;

function caseTone(status: string): Tone {
  if (status === "pending") return "warn";
  if (status === "rejected") return "muted";
  return "ok";
}

function ResponseForm({
  recommendation,
  onRespond,
  busy,
}: {
  recommendation: Recommendation;
  onRespond: RespondHandler;
  busy: boolean;
}) {
  const [operatorId, setOperatorId] = useState("");
  const [reasonCode, setReasonCode] = useState("");
  const [note, setNote] = useState("");
  const [showModify, setShowModify] = useState(false);
  const [replacementDeadline, setReplacementDeadline] = useState(
    recommendation.execute_before,
  );
  const [error, setError] = useState<string | null>(null);

  const submit = async (kind: "accept" | "reject" | "modify") => {
    setError(null);
    const input: RespondInput = {
      operator_id: operatorId.trim(),
      reason_code: reasonCode.trim(),
      ...(note.trim() ? { note: note.trim() } : {}),
    };
    if (kind === "modify") {
      input.replacement_action = recommendation.action;
      input.replacement_execute_before = replacementDeadline.trim();
    }
    try {
      await onRespond(recommendation.recommendation_id, kind, input);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const ready = operatorId.trim() !== "" && reasonCode.trim() !== "";
  const controlId = recommendation.recommendation_id;
  return (
    <div className="response-form">
      <div className="form-row">
        <label htmlFor={`${controlId}-operator`}>Operator ID</label>
        <input
          id={`${controlId}-operator`}
          value={operatorId}
          onChange={(event) => setOperatorId(event.target.value)}
          placeholder="e.g. mgr-01"
          disabled={busy}
        />
      </div>
      <div className="form-row">
        <label htmlFor={`${controlId}-reason`}>Reason code</label>
        <input
          id={`${controlId}-reason`}
          value={reasonCode}
          onChange={(event) => setReasonCode(event.target.value)}
          placeholder="e.g. staffing_available"
          disabled={busy}
        />
      </div>
      <div className="form-row">
        <label htmlFor={`${controlId}-note`}>Note (optional)</label>
        <input
          id={`${controlId}-note`}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          disabled={busy}
        />
      </div>
      {showModify ? (
        <div className="form-row">
          <label htmlFor={`${controlId}-deadline`}>
            New execute-before (ISO, observation time)
          </label>
          <input
            id={`${controlId}-deadline`}
            value={replacementDeadline}
            onChange={(event) => setReplacementDeadline(event.target.value)}
            disabled={busy}
          />
        </div>
      ) : null}
      <div className="form-actions">
        <button
          type="button"
          className="btn btn-primary"
          disabled={!ready || busy}
          onClick={() => submit("accept")}
        >
          Accept
        </button>
        <button
          type="button"
          className="btn"
          disabled={!ready || busy}
          onClick={() => submit("reject")}
        >
          Reject
        </button>
        {showModify ? (
          <button
            type="button"
            className="btn"
            disabled={!ready || busy}
            onClick={() => submit("modify")}
          >
            Record modification
          </button>
        ) : (
          <button
            type="button"
            className="btn btn-quiet"
            disabled={busy}
            onClick={() => setShowModify(true)}
          >
            Modify…
          </button>
        )}
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      <p className="fineprint">
        A decision is recorded as human workflow evidence in the existing
        ledger. It does not command any robot or equipment.
      </p>
    </div>
  );
}

function TraceDetails({ recommendation }: { recommendation: Recommendation }) {
  const trace = recommendation.trace;
  if (!trace) return null;
  return (
    <details className="trace-details">
      <summary>Evidence and decision trace</summary>
      {trace.rationale.length > 0 ? (
        <>
          <h4>Rationale</h4>
          <ul>
            {trace.rationale.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        </>
      ) : null}
      {trace.missing_data_reasons.length > 0 ? (
        <>
          <h4>Missing facts (policy fails closed on these)</h4>
          <ul className="missing-list">
            {trace.missing_data_reasons.map((line, index) => (
              <li key={index}>
                <Badge tone="bad">MISSING</Badge> {line}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {trace.candidates.length > 0 ? (
        <>
          <h4>Collector candidates considered</h4>
          <ul>
            {trace.candidates.map((candidate) => (
              <li key={candidate.robot_id ?? "unknown"}>
                <span className="mono">{candidate.robot_id}</span>{" "}
                {candidate.eligible
                  ? "eligible"
                  : `excluded: ${candidate.exclusion_reasons.join(", ")}`}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <p className="fineprint">
        Trace <span className="mono">{trace.trace_id}</span> · policy{" "}
        {trace.policy_id} v{trace.policy_version} · data completeness{" "}
        {trace.data_completeness_score ?? "—"}
      </p>
    </details>
  );
}

export function RecommendationsPanel({
  recommendations,
  onRespond,
  busy,
}: {
  recommendations: Recommendation[];
  onRespond: RespondHandler;
  busy: boolean;
}) {
  const pending = recommendations.filter(
    (item) => item.case_status === "pending",
  );
  return (
    <Section
      title="Recommendations Awaiting Review"
      aside={
        pending.length === 0 ? (
          <Badge tone="ok">QUEUE EMPTY</Badge>
        ) : (
          <Badge tone="warn">{pending.length} PENDING</Badge>
        )
      }
    >
      {recommendations.length === 0 ? (
        <EmptyNote>
          No recommendations have been issued this shift. NO_ACTION
          evaluations are listed in the Shift Briefing with their full
          rationale.
        </EmptyNote>
      ) : (
        <div className="rec-list">
          {recommendations.map((recommendation) => (
            <article
              className="rec-card"
              key={recommendation.recommendation_id}
            >
              <header className="rec-head">
                <Badge tone={caseTone(recommendation.case_status)}>
                  {recommendation.case_status.toUpperCase()}
                </Badge>
                <span className="rec-action mono">
                  {recommendation.action}
                </span>
              </header>
              <p className="rec-summary">{recommendation.summary}</p>
              <dl className="kv-grid">
                <KeyValue label="Issued at">
                  {formatIsoTime(recommendation.issued_at)} observation time
                </KeyValue>
                <KeyValue label="Execute before">
                  {formatIsoTime(recommendation.execute_before)} observation
                  time
                </KeyValue>
                <KeyValue label="Source">
                  sequence {recommendation.source_sequence ?? "—"} · envelope{" "}
                  <span className="mono">
                    {shortId(recommendation.source_envelope_id)}
                  </span>
                </KeyValue>
                <KeyValue label="Recommendation ID">
                  <span className="mono">
                    {recommendation.recommendation_id}
                  </span>
                </KeyValue>
              </dl>
              <TraceDetails recommendation={recommendation} />
              {recommendation.manager_response ? (
                <p className="decision-line">
                  <Badge tone="info">MANAGER DECISION</Badge>{" "}
                  {recommendation.manager_response.kind} by{" "}
                  {recommendation.manager_response.operator_id} (
                  {recommendation.manager_response.reason_code})
                  {recommendation.manager_response.note
                    ? ` — ${recommendation.manager_response.note}`
                    : ""}
                </p>
              ) : null}
              {recommendation.case_status === "pending" ? (
                <ResponseForm
                  recommendation={recommendation}
                  onRespond={onRespond}
                  busy={busy}
                />
              ) : null}
            </article>
          ))}
        </div>
      )}
    </Section>
  );
}
