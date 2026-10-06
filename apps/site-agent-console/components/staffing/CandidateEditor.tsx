"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import type {
  CandidateProjection,
  ManagerPatchOperation,
} from "../../lib/staffing";

type AddPatch = Extract<ManagerPatchOperation, { operation: "ADD" }>;

export interface CandidateEditorProps {
  candidate: CandidateProjection;
  disabled: boolean;
  initialNote?: string;
  onCancel(): void;
  onSubmit(operations: ManagerPatchOperation[], note: string | null): Promise<unknown>;
}

function closedOperations(candidate: CandidateProjection): ManagerPatchOperation[] {
  return candidate.operations.map((item) => item.operation === "REMOVE"
    ? { operation: "REMOVE", assignment_id: item.assignment_id }
    : {
        operation: "ADD",
        staff_id: item.staff_id,
        role_code: item.role_code,
        area_code: item.area_code,
        start_at: item.start_at,
        end_at: item.end_at,
      });
}

function validIdentifier(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value);
}

function validCode(value: string): boolean {
  return /^[A-Z][A-Z0-9_]{0,31}$/.test(value);
}

function daysInMonth(year: number, month: number): number {
  if (month === 2) {
    return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28;
  }
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

function validOffsetMinute(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(?:[01]\d|2[0-3]):[0-5]\d:00(?:Z|[+-](?!00:00)(?:[01]\d|2[0-3]):[0-5]\d)$/.exec(value);
  if (match === null) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  return year >= 1
    && month >= 1
    && month <= 12
    && day >= 1
    && day <= daysInMonth(year, month)
    && Number.isFinite(Date.parse(value));
}

function patchError(operations: ManagerPatchOperation[]): string | null {
  if (operations.length === 0) return "至少保留一条修改。";
  for (const item of operations) {
    if (item.operation === "REMOVE") {
      if (!validIdentifier(item.assignment_id)) return "班次编号无效。";
      continue;
    }
    if (!validIdentifier(item.staff_id)) return "员工编号无效。";
    if (!validCode(item.role_code)) return "岗位代码无效。";
    if (!validCode(item.area_code)) return "区域代码无效。";
    if (!validOffsetMinute(item.start_at) || !validOffsetMinute(item.end_at)) {
      return "开始与结束时间必须包含有效时区偏移，并精确到分钟。";
    }
    if (Date.parse(item.start_at) >= Date.parse(item.end_at)) {
      return "结束时间必须晚于开始时间。";
    }
  }
  return null;
}

function normalizedNote(value: string): string | null {
  return value.trim() === "" ? null : value;
}

function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

export function CandidateEditor({
  candidate,
  disabled,
  initialNote = "",
  onCancel,
  onSubmit,
}: CandidateEditorProps) {
  const [operations, setOperations] = useState<ManagerPatchOperation[]>(() => closedOperations(candidate));
  const [note, setNote] = useState(initialNote);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const lock = useRef(false);
  const firstFieldRef = useRef<HTMLInputElement>(null);
  const operationError = patchError(operations);
  const noteTooLong = Array.from(note).length > 500;
  const formDisabled = disabled || submitting;
  const submitDisabled = formDisabled || operationError !== null || noteTooLong;

  useEffect(() => {
    firstFieldRef.current?.focus();
  }, []);

  function updateRemove(index: number, assignmentId: string): void {
    setOperations((current) => current.map((item, itemIndex) => itemIndex === index
      ? { operation: "REMOVE", assignment_id: assignmentId }
      : item));
  }

  function updateAdd(index: number, field: keyof Omit<AddPatch, "operation">, value: string): void {
    setOperations((current) => current.map((item, itemIndex) => {
      if (itemIndex !== index || item.operation !== "ADD") return item;
      return { ...item, [field]: value };
    }));
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (submitDisabled || lock.current) return;
    const error = patchError(operations);
    if (error !== null || Array.from(note).length > 500) return;
    lock.current = true;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await onSubmit(structuredClone(operations), normalizedNote(note));
    } catch (cause) {
      setSubmitError(errorText(cause));
    } finally {
      lock.current = false;
      setSubmitting(false);
    }
  }

  return (
    <form
      className="staffing-candidate-editor"
      aria-label={`修改排班建议 ${candidate.candidate_index}`}
      onSubmit={(event) => void submit(event)}
    >
      <div className="staffing-editor-rows">
        {operations.map((item, index) => item.operation === "REMOVE" ? (
          <fieldset className="staffing-editor-row" key={`remove-${index}`} disabled={formDisabled}>
            <legend>移除班次</legend>
            <label htmlFor={`staffing-${candidate.candidate_index}-${index}-assignment`}>班次编号</label>
            <input
              ref={index === 0 ? firstFieldRef : undefined}
              id={`staffing-${candidate.candidate_index}-${index}-assignment`}
              name="assignment_id"
              value={item.assignment_id}
              onChange={(event) => updateRemove(index, event.target.value)}
              autoComplete="off"
            />
          </fieldset>
        ) : (
          <fieldset className="staffing-editor-row" key={`add-${index}`} disabled={formDisabled}>
            <legend>补入班次</legend>
            <label htmlFor={`staffing-${candidate.candidate_index}-${index}-staff`}>员工编号</label>
            <input
              ref={index === 0 ? firstFieldRef : undefined}
              id={`staffing-${candidate.candidate_index}-${index}-staff`}
              name="staff_id"
              value={item.staff_id}
              onChange={(event) => updateAdd(index, "staff_id", event.target.value)}
              autoComplete="off"
            />
            <label htmlFor={`staffing-${candidate.candidate_index}-${index}-role`}>岗位代码</label>
            <input
              id={`staffing-${candidate.candidate_index}-${index}-role`}
              name="role_code"
              value={item.role_code}
              onChange={(event) => updateAdd(index, "role_code", event.target.value)}
              autoComplete="off"
            />
            <label htmlFor={`staffing-${candidate.candidate_index}-${index}-area`}>区域代码</label>
            <input
              id={`staffing-${candidate.candidate_index}-${index}-area`}
              name="area_code"
              value={item.area_code}
              onChange={(event) => updateAdd(index, "area_code", event.target.value)}
              autoComplete="off"
            />
            <label htmlFor={`staffing-${candidate.candidate_index}-${index}-start`}>开始时间（含时区）</label>
            <input
              id={`staffing-${candidate.candidate_index}-${index}-start`}
              name="start_at"
              value={item.start_at}
              onChange={(event) => updateAdd(index, "start_at", event.target.value)}
              autoComplete="off"
            />
            <label htmlFor={`staffing-${candidate.candidate_index}-${index}-end`}>结束时间（含时区）</label>
            <input
              id={`staffing-${candidate.candidate_index}-${index}-end`}
              name="end_at"
              value={item.end_at}
              onChange={(event) => updateAdd(index, "end_at", event.target.value)}
              autoComplete="off"
            />
          </fieldset>
        ))}
      </div>

      <label htmlFor={`staffing-${candidate.candidate_index}-modify-note`}>经理备注（可选）</label>
      <textarea
        id={`staffing-${candidate.candidate_index}-modify-note`}
        name="manager_note"
        value={note}
        disabled={formDisabled}
        aria-invalid={noteTooLong}
        onChange={(event) => setNote(event.target.value)}
      />
      {operationError === null ? null : <p className="staffing-error" role="alert">{operationError}</p>}
      {noteTooLong ? <p className="staffing-error" role="alert">经理备注不能超过 500 个字符。</p> : null}
      {submitError === null ? null : <p className="staffing-error" role="alert">{submitError}</p>}

      <div className="staffing-actions">
        <button
          type="submit"
          className="btn btn-primary"
          aria-label={`提交排班建议 ${candidate.candidate_index} 的修改`}
          disabled={submitDisabled}
        >
          {submitting ? "正在提交…" : "提交修改"}
        </button>
        <button type="button" className="btn" disabled={formDisabled} onClick={onCancel}>
          取消
        </button>
      </div>
    </form>
  );
}
