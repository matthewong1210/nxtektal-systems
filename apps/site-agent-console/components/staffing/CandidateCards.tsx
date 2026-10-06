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
import { CandidateEditor } from "./CandidateEditor";

export interface CandidateCardsProps {
  generation: GenerationProjection;
  revisions: RevisionVector;
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

function CoverageGaps({ gaps }: { gaps: CoverageGap[] }) {
  if (gaps.length === 0) return null;
  return (
    <section className="staffing-gaps" role="region" aria-label="覆盖缺口">
      <h4>覆盖缺口</h4>
      <ul>
        {gaps.map((gap, index) => (
          <li key={`${gap.role_code}:${gap.area_code}:${gap.start_at}:${index}`}>
            <strong>{gap.role_code}</strong>
            <span>{gap.area_code}</span>
            <span className="staffing-time">{gap.start_at} 至 {gap.end_at}</span>
            <span>需要 {gap.required_count}，已有 {gap.assigned_count}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ManagerResponse({ response }: { response: ManagerResponseSummary }) {
  return (
    <section className="staffing-manager-response" aria-label="经理处理结果" role="status" aria-live="polite">
      <h3>经理处理结果</h3>
      <dl>
        <div><dt>处理</dt><dd>{response.response_kind}</dd></div>
        <div><dt>原因</dt><dd>{response.reason_code}</dd></div>
        <div><dt>经理</dt><dd>{response.operator}</dd></div>
        <div><dt>备注</dt><dd>{response.note ?? "无"}</dd></div>
        <div>
          <dt>有效排班</dt>
          <dd>{response.effective_plan === null ? "未形成" : `版本 ${response.effective_plan.revision}`}</dd>
        </div>
      </dl>
    </section>
  );
}

function CandidateCard({
  candidate,
  generation,
  controlsAvailable,
  onAccept,
  onModify,
}: {
  candidate: CandidateProjection;
  generation: GenerationProjection;
  controlsAvailable: boolean;
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
    <article className="staffing-candidate" aria-label={`排班建议 ${candidate.candidate_index}`}>
      <header className="staffing-candidate-heading">
        <h3>建议 {candidate.candidate_index}</h3>
        <strong>{candidate.status}</strong>
      </header>
      <p className="staffing-advisory-label">AI 建议，需经理确认</p>
      <div className="staffing-provenance" aria-label="建议来源">
        {generation.provenance.length === 0 ? (
          <span>暂无可用来源记录</span>
        ) : generation.provenance.map((item) => (
          <span key={`${item.attempt_index}:${item.route_id}`}>
            <strong>{item.provider}</strong> / <span className="staffing-code">{item.model_id}</span>
          </span>
        ))}
      </div>

      <p className="staffing-rationale">{candidate.rationale}</p>
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
              <span className="staffing-time">{item.start_at} 至 {item.end_at}</span>
              {item.operation === "REMOVE" ? (
                <span className="staffing-code">{item.assignment_id}</span>
              ) : null}
            </li>
          ))}
        </ol>
      )}

      {candidate.operational_warnings.length === 0 ? null : (
        <section className="staffing-warnings" role="region" aria-label="运营提示">
          <h4>运营提示</h4>
          <ul>
            {candidate.operational_warnings.map((warning, index) => (
              <li key={`${warning}:${index}`}>{warning}</li>
            ))}
          </ul>
        </section>
      )}
      <CoverageGaps gaps={candidate.coverage_gaps} />
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
      ) : candidate.status === "VALID" && generation.manager_response === null ? (
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
  disabled,
  onAccept,
  onModify,
  onReject,
}: CandidateCardsProps) {
  const matchingBasis = basisMatches(generation, revisions);
  const response = generation.manager_response;
  const responseAvailable = !disabled
    && matchingBasis
    && response === null
    && generation.candidates.some((candidate) => candidate.status === "VALID");

  return (
    <section className="staffing-candidates" aria-label="排班建议列表">
      {response === null ? null : <ManagerResponse response={response} />}
      <CoverageGaps gaps={generation.coverage_gaps} />
      {generation.candidates.length === 0 ? null : (
        <div className="staffing-candidate-list">
          {generation.candidates.map((candidate) => (
            <CandidateCard
              key={candidate.candidate_index}
              candidate={candidate}
              generation={generation}
              controlsAvailable={
                !disabled
                && matchingBasis
                && response === null
                && candidate.status === "VALID"
              }
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
