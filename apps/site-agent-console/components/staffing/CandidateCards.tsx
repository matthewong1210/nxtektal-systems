"use client";

import { useEffect, useRef, useState } from "react";

import type { StaffingActions } from "../../lib/staffing-actions";
import type {
  CandidateProjection,
  CoverageGap,
  GenerationProjection,
  ManagerResponseSummary,
  RevisionVector,
} from "../../lib/staffing";
import {
  actionabilityLabel,
  candidateIsActionable,
  candidateStatusLabel,
  generationIsHistorical,
  reasonCodeLabel,
  responseKindLabel,
} from "../../lib/staffing-labels";
import { formatSiteRange, formatSiteTime } from "../../lib/site-time";
import { Badge } from "../ui";
import { CandidateEditor } from "./CandidateEditor";

export interface CandidateCardsProps {
  generation: GenerationProjection;
  revisions: RevisionVector;
  /** IANA site timezone from the snapshot context; every time is shown in it. */
  timeZone: string;
  /** The controller's server-relative timer reached the earliest open action
   * window: controls close until the service answers again. The service alone
   * relabels a candidate EXPIRED. */
  deadlineReached: boolean;
  disabled: boolean;
  onAccept: StaffingActions["acceptSuggestion"];
  onModify: StaffingActions["modifySuggestion"];
  onReject: StaffingActions["rejectSuggestion"];
}

type ResponseMode = "idle" | "modify";
type RejectReason = "MANUAL_HANDLING" | "INSUFFICIENT_CONTEXT" | "OTHER";

function rejectReason(value: string): RejectReason | "" {
  return value === "MANUAL_HANDLING"
    || value === "INSUFFICIENT_CONTEXT"
    || value === "OTHER"
    ? value
    : "";
}

function basisMatches(generation: GenerationProjection, revisions: RevisionVector): boolean {
  return generation.basis.roster === revisions.roster
    && generation.basis.exception_set === revisions.exception_set
    && generation.basis.effective_plan === revisions.effective_plan;
}

function normalizedNote(value: string): string | null {
  return value.trim() === "" ? null : value;
}

function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

/** Site-local interval with the exact contract values kept on the element. */
function SiteRange({ start, end, timeZone }: { start: string; end: string; timeZone: string }) {
  return (
    <span className="staffing-time" title={`${start} – ${end}`}>
      <time dateTime={start}>{formatSiteRange(start, end, timeZone)}</time>
    </span>
  );
}

function CoverageGaps({ gaps, timeZone }: { gaps: CoverageGap[]; timeZone: string }) {
  if (gaps.length === 0) return null;
  return (
    <section className="staffing-gaps" role="region" aria-label="覆盖缺口">
      <h4>覆盖缺口</h4>
      <ul>
        {gaps.map((gap, index) => (
          <li key={`${gap.role_code}:${gap.area_code}:${gap.start_at}:${index}`}>
            <strong>{gap.role_code}</strong>
            <span>{gap.area_code}</span>
            <SiteRange start={gap.start_at} end={gap.end_at} timeZone={timeZone} />
            <span>需要 {gap.required_count}，已有 {gap.assigned_count}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The committed receipt. Its nested plan status is the value recorded at
 * commit time, never the live status: that is derived only from the
 * snapshot's own `effective_plan`, rendered by the plan status section. */
function ManagerResponse({ response }: { response: ManagerResponseSummary }) {
  const plan = response.effective_plan;
  return (
    <section className="staffing-manager-response" aria-label="经理处理结果" role="status" aria-live="polite">
      <h3>经理处理结果</h3>
      <dl>
        <div>
          <dt>处理</dt>
          <dd>{responseKindLabel(response.response_kind)} <span className="staffing-code">{response.response_kind}</span></dd>
        </div>
        <div>
          <dt>原因</dt>
          <dd>{reasonCodeLabel(response.reason_code)} <span className="staffing-code">{response.reason_code}</span></dd>
        </div>
        <div><dt>经理</dt><dd>{response.operator}</dd></div>
        <div><dt>备注</dt><dd>{response.note ?? "无"}</dd></div>
        <div>
          <dt>确认版本</dt>
          <dd>
            {plan === null
              ? "未形成排班版本"
              : (
                <>
                  <span title={`schedule_digest ${plan.schedule_digest}`}>确认时形成的版本 {plan.revision}</span>{" "}
                  <span className="staffing-code" title="提交时记录的合同值，不表示现在是否有效">{plan.status}</span>
                </>
              )}
          </dd>
        </div>
      </dl>
      {plan === null ? null : (
        <p className="fineprint staffing-receipt-note">此为经理确认时的记录；现行状态以“有效排班状态”栏为准。</p>
      )}
    </section>
  );
}

function CandidateCard({
  candidate,
  generation,
  timeZone,
  controlsAvailable,
  deadlineReached,
  onAccept,
  onModify,
}: {
  candidate: CandidateProjection;
  generation: GenerationProjection;
  timeZone: string;
  controlsAvailable: boolean;
  deadlineReached: boolean;
  onAccept: StaffingActions["acceptSuggestion"];
  onModify: StaffingActions["modifySuggestion"];
}) {
  const [mode, setMode] = useState<ResponseMode>("idle");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const lock = useRef(false);
  const modifyTriggerRef = useRef<HTMLButtonElement>(null);
  const restoreModifyFocus = useRef(false);
  const noteTooLong = Array.from(note).length > 500;
  const actionDisabled = submitting || noteTooLong;
  const expired = candidate.actionability === "EXPIRED";

  useEffect(() => {
    if (mode === "idle" && restoreModifyFocus.current) {
      restoreModifyFocus.current = false;
      modifyTriggerRef.current?.focus();
    }
  }, [mode]);

  async function accept(): Promise<void> {
    if (actionDisabled || lock.current) return;
    lock.current = true;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await onAccept(generation.suggestion_id, candidate.candidate_index, normalizedNote(note));
    } catch (cause) {
      setSubmitError(errorText(cause));
    } finally {
      lock.current = false;
      setSubmitting(false);
    }
  }

  function closeEditor(): void {
    restoreModifyFocus.current = true;
    setMode("idle");
  }

  return (
    <article
      className={`staffing-candidate${expired ? " staffing-candidate-expired" : ""}`}
      aria-label={`排班建议 ${candidate.candidate_index}`}
      data-status={candidate.status}
      data-actionability={candidate.actionability}
    >
      <header className="staffing-candidate-heading">
        <h3>建议 {candidate.candidate_index}</h3>
        <strong>
          {candidateStatusLabel(candidate.status)} <span className="staffing-code">{candidate.status}</span>
        </strong>
      </header>
      <p className="staffing-advisory-label">AI 建议，需经理确认</p>
      {expired ? (
        <p className="staffing-historical" role="note">
          <Badge tone="muted">历史记录</Badge> {actionabilityLabel(candidate.actionability)}：相关班次中最早的一班已于{" "}
          <time dateTime={candidate.action_window_end_at}>{formatSiteTime(candidate.action_window_end_at, timeZone)}</time>
          {" "}结束，仅供查阅，不能再采用为当前排班。
        </p>
      ) : null}
      <div className="staffing-provenance" aria-label="建议来源">
        {generation.provenance.length === 0 ? (
          <span>暂无可用来源记录</span>
        ) : generation.provenance.map((item) => (
          <span key={`${item.attempt_index}:${item.route_id}`}>
            <strong>{item.provider}</strong> / <span className="staffing-code">{item.model_id}</span>
          </span>
        ))}
      </div>

      <p className="staffing-rationale">{candidate.rationale_local}</p>
      {candidate.rationale_local === candidate.rationale ? null : (
        <details className="staffing-raw">
          <summary>查看原始建议文本（含服务端别名）</summary>
          <p className="staffing-code">{candidate.rationale}</p>
        </details>
      )}
      {candidate.operations.length === 0 ? (
        <p className="staffing-empty">没有建议调整项。</p>
      ) : (
        <ol className="staffing-adjustments">
          {candidate.operations.map((item, index) => (
            <li key={`${item.operation}:${item.staff_id}:${item.start_at}:${index}`}>
              <strong>{item.operation === "REMOVE" ? "移除" : "补入"}</strong>
              <span>{item.display_name}</span>
              <span className="staffing-code">{item.staff_id}</span>
              <span>{item.role_code} / {item.area_code}</span>
              <SiteRange start={item.start_at} end={item.end_at} timeZone={timeZone} />
              {item.operation === "REMOVE" ? (
                <span className="staffing-code">{item.assignment_id}</span>
              ) : null}
            </li>
          ))}
        </ol>
      )}

      {candidate.operational_warnings_local.length === 0 ? null : (
        <section className="staffing-warnings" role="region" aria-label="运营提示">
          <h4>运营提示</h4>
          <ul>
            {candidate.operational_warnings_local.map((warning, index) => (
              <li key={`${warning}:${index}`}>{warning}</li>
            ))}
          </ul>
        </section>
      )}
      <CoverageGaps gaps={candidate.coverage_gaps} timeZone={timeZone} />
      {candidate.status === "REJECTED" ? (
        <p className="staffing-rejection">
          未通过：{candidate.rejection_codes.join("、")}
        </p>
      ) : null}

      {controlsAvailable ? (
        <div className="staffing-response">
          {mode === "modify" ? (
            <CandidateEditor
              candidate={candidate}
              disabled={submitting}
              initialNote={note}
              onCancel={closeEditor}
              onSubmit={async (operations, responseNote) => {
                await onModify(
                  generation.suggestion_id,
                  candidate.candidate_index,
                  operations,
                  responseNote,
                );
              }}
            />
          ) : (
            <>
              <label htmlFor={`staffing-${candidate.candidate_index}-response-note`}>经理备注（可选）</label>
              <textarea
                id={`staffing-${candidate.candidate_index}-response-note`}
                name="manager_note"
                value={note}
                disabled={submitting}
                aria-invalid={noteTooLong}
                onChange={(event) => setNote(event.target.value)}
              />
              {noteTooLong ? <p className="staffing-error" role="alert">经理备注不能超过 500 个字符。</p> : null}
              <div className="staffing-actions">
                <button
                  type="button"
                  className="btn btn-primary"
                  aria-label={`接受排班建议 ${candidate.candidate_index}`}
                  disabled={actionDisabled}
                  onClick={() => void accept()}
                >
                  {submitting ? "正在提交…" : "接受此建议"}
                </button>
                {candidate.operations.length === 0 ? null : (
                  <button
                    ref={modifyTriggerRef}
                    type="button"
                    className="btn"
                    aria-label={`修改排班建议 ${candidate.candidate_index}`}
                    disabled={actionDisabled}
                    onClick={() => setMode("modify")}
                  >
                    修改此建议
                  </button>
                )}
              </div>
            </>
          )}
          {submitError === null ? null : <p className="staffing-error" role="alert">{submitError}</p>}
        </div>
      ) : deadlineReached ? null : candidate.status === "VALID" && !expired && generation.manager_response === null ? (
        <p className="staffing-readonly">当前数据版本已变化，此建议仅供查阅。</p>
      ) : null}
    </article>
  );
}

function SuggestionReject({
  suggestionId,
  onReject,
}: {
  suggestionId: string;
  onReject: StaffingActions["rejectSuggestion"];
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<RejectReason | "">("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const lock = useRef(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const reasonRef = useRef<HTMLSelectElement>(null);
  const restoreTriggerFocus = useRef(false);
  const noteTooLong = Array.from(note).length > 500;

  useEffect(() => {
    if (open) {
      reasonRef.current?.focus();
    } else if (restoreTriggerFocus.current) {
      restoreTriggerFocus.current = false;
      triggerRef.current?.focus();
    }
  }, [open]);

  function cancel(): void {
    setReason("");
    setNote("");
    setSubmitError(null);
    restoreTriggerFocus.current = true;
    setOpen(false);
  }

  async function reject(): Promise<void> {
    if (reason === "" || noteTooLong || submitting || lock.current) return;
    lock.current = true;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await onReject(suggestionId, reason, normalizedNote(note));
    } catch (cause) {
      setSubmitError(errorText(cause));
    } finally {
      lock.current = false;
      setSubmitting(false);
    }
  }

  return (
    <section className="staffing-reject-all">
      {open ? (
        <div id="staffing-reject-all-form" className="staffing-reject" aria-label="拒绝本次全部建议">
          <p>此操作会拒绝本次生成的全部候选建议，不针对单张建议。</p>
          <label htmlFor="staffing-reject-all-reason">拒绝原因</label>
          <select
            ref={reasonRef}
            id="staffing-reject-all-reason"
            name="reason_code"
            value={reason}
            disabled={submitting}
            onChange={(event) => setReason(rejectReason(event.target.value))}
          >
            <option value="">请选择</option>
            <option value="MANUAL_HANDLING">需要人工处理</option>
            <option value="INSUFFICIENT_CONTEXT">信息不足</option>
            <option value="OTHER">其他</option>
          </select>
          <label htmlFor="staffing-reject-all-note">本次拒绝备注（可选）</label>
          <textarea
            id="staffing-reject-all-note"
            name="manager_note"
            value={note}
            disabled={submitting}
            aria-invalid={noteTooLong}
            onChange={(event) => setNote(event.target.value)}
          />
          {noteTooLong ? <p className="staffing-error" role="alert">本次拒绝备注不能超过 500 个字符。</p> : null}
          {submitError === null ? null : <p className="staffing-error" role="alert">{submitError}</p>}
          <div className="staffing-actions">
            <button
              type="button"
              className="btn btn-primary"
              disabled={reason === "" || noteTooLong || submitting}
              onClick={() => void reject()}
            >
              {submitting ? "正在提交…" : "确认拒绝本次全部建议"}
            </button>
            <button type="button" className="btn" disabled={submitting} onClick={cancel}>
              取消
            </button>
          </div>
        </div>
      ) : (
        <button
          ref={triggerRef}
          type="button"
          className="btn staffing-reject-all-trigger"
          aria-expanded="false"
          aria-controls="staffing-reject-all-form"
          onClick={() => setOpen(true)}
        >
          拒绝本次全部建议
        </button>
      )}
    </section>
  );
}

export function CandidateCards({
  generation,
  revisions,
  timeZone,
  deadlineReached,
  disabled,
  onAccept,
  onModify,
  onReject,
}: CandidateCardsProps) {
  const matchingBasis = basisMatches(generation, revisions);
  const response = generation.manager_response;
  const historical = generationIsHistorical(generation);
  // Current actions need a fresh basis, no recorded response, an open action
  // window on the console's server-relative timer, and at least one candidate
  // that is valid and whose shifts have not ended. A historical generation is
  // shown for the record only.
  const responseAvailable = !disabled
    && matchingBasis
    && response === null
    && !historical
    && !deadlineReached
    && generation.candidates.some(candidateIsActionable);

  return (
    <section
      className="staffing-candidates"
      aria-label={historical ? "历史排班建议列表" : "排班建议列表"}
      data-review={historical ? "HISTORICAL" : "CURRENT"}
    >
      {generation.candidates.length === 0 ? null : (
        <header className="staffing-candidates-heading">
          {historical ? (
            <>
              <Badge tone="muted">历史记录</Badge>
              <h3>历史建议（班次已结束，仅供查阅）</h3>
            </>
          ) : (
            <h3>当前建议</h3>
          )}
        </header>
      )}
      {deadlineReached && response === null && !historical ? (
        <p className="staffing-notice staffing-deadline" role="status" aria-live="polite">
          已到服务端截止时间，正在等待服务端的最新结果，暂不可采用。
        </p>
      ) : null}
      {response === null ? null : <ManagerResponse response={response} />}
      <CoverageGaps gaps={generation.coverage_gaps} timeZone={timeZone} />
      {generation.candidates.length === 0 ? null : (
        <div className="staffing-candidate-list">
          {generation.candidates.map((candidate) => (
            <CandidateCard
              key={candidate.candidate_index}
              candidate={candidate}
              generation={generation}
              timeZone={timeZone}
              controlsAvailable={
                !disabled
                && matchingBasis
                && response === null
                && !deadlineReached
                && candidateIsActionable(candidate)
              }
              deadlineReached={deadlineReached}
              onAccept={onAccept}
              onModify={onModify}
            />
          ))}
        </div>
      )}
      {responseAvailable ? (
        <SuggestionReject suggestionId={generation.suggestion_id} onReject={onReject} />
      ) : null}
    </section>
  );
}
