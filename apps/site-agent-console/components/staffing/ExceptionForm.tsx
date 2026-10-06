"use client";

import { useMemo, useRef, useState, type FormEvent } from "react";

import type { AssignmentProjection } from "../../lib/staffing";
import type { ExceptionDraft } from "../../lib/staffing-state";

export interface ExceptionFormProps {
  assignments: AssignmentProjection[];
  disabled: boolean;
  onRecord(draft: ExceptionDraft): Promise<unknown>;
}

type ExceptionKind = ExceptionDraft["kind"];

const TIMED_KINDS = new Set<ExceptionKind>(["LATE", "EARLY_DEPARTURE"]);

const KIND_LABELS: Record<ExceptionKind, string> = {
  LEAVE: "请假",
  LATE: "迟到",
  EARLY_DEPARTURE: "早退",
  UNAVAILABLE: "临时无法到岗",
};

function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function validLocalMinute(value: string): boolean {
  return /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value);
}

export function ExceptionForm({ assignments, disabled, onRecord }: ExceptionFormProps) {
  const employees = useMemo(() => {
    const unique = new Map<string, { staffId: string; displayName: string }>();
    for (const assignment of assignments) {
      if (!unique.has(assignment.staff_id)) {
        unique.set(assignment.staff_id, {
          staffId: assignment.staff_id,
          displayName: assignment.display_name,
        });
      }
    }
    return [...unique.values()];
  }, [assignments]);

  const [staffId, setStaffId] = useState("");
  const [kind, setKind] = useState<ExceptionKind>("LEAVE");
  const [timeLocal, setTimeLocal] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const lock = useRef(false);

  const timed = TIMED_KINDS.has(kind);
  const noteLength = Array.from(note).length;
  const noteTooLong = noteLength > 500;
  const knownStaff = employees.some((employee) => employee.staffId === staffId);
  const timeValid = !timed || validLocalMinute(timeLocal);
  const formDisabled = disabled || submitting || noteTooLong;

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (disabled || lock.current) return;
    if (!knownStaff) {
      setError("请选择当天常规班次中的员工。");
      return;
    }
    if (!timeValid) {
      setError("请填写有效的 HH:MM 时间。");
      return;
    }
    if (Array.from(note).length > 500) {
      setError("备注过长，最多 500 个字符。");
      return;
    }

    const normalizedNote = note.trim() === "" ? null : note;
    const draft: ExceptionDraft = timed
      ? {
          staff_id: staffId,
          kind: kind as "LATE" | "EARLY_DEPARTURE",
          time_local: timeLocal,
          note: normalizedNote,
        }
      : {
          staff_id: staffId,
          kind: kind as "LEAVE" | "UNAVAILABLE",
          time_local: null,
          note: normalizedNote,
        };

    lock.current = true;
    setSubmitting(true);
    setError(null);
    setMessage(null);
    try {
      await onRecord(draft);
      setStaffId("");
      setTimeLocal("");
      setNote("");
      setMessage("异常已记录。排班建议需另行生成。");
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      lock.current = false;
      setSubmitting(false);
    }
  }

  const fieldsLocked = disabled || submitting;

  return (
    <form className="staffing-exception-form" onSubmit={(event) => void submit(event)}>
      <fieldset className="staffing-exception-fields" aria-label="记录人员异常" disabled={fieldsLocked}>
        <legend>快速记录异常</legend>

        <label className="staffing-field">
          <span>员工</span>
          <select
            name="staff_id"
            value={staffId}
            disabled={fieldsLocked}
            onChange={(event) => {
              setStaffId(event.target.value);
              setError(null);
              setMessage(null);
            }}
          >
            <option value="">{employees.length === 0 ? "暂无可选员工" : "选择员工"}</option>
            {employees.map((employee) => (
              <option key={employee.staffId} value={employee.staffId}>
                {employee.displayName} · {employee.staffId}
              </option>
            ))}
          </select>
        </label>

        <label className="staffing-field">
          <span>异常类型</span>
          <select
            name="kind"
            value={kind}
            disabled={fieldsLocked}
            onChange={(event) => {
              const next = event.target.value as ExceptionKind;
              setKind(next);
              if (!TIMED_KINDS.has(next)) setTimeLocal("");
              setError(null);
              setMessage(null);
            }}
          >
            {(Object.keys(KIND_LABELS) as ExceptionKind[]).map((value) => (
              <option key={value} value={value}>{KIND_LABELS[value]}</option>
            ))}
          </select>
        </label>

        {timed ? (
          <label className="staffing-field">
            <span>{kind === "LATE" ? "预计到岗时间" : "预计离岗时间"}</span>
            <input
              name="time_local"
              type="time"
              step="60"
              value={timeLocal}
              disabled={fieldsLocked}
              onChange={(event) => {
                setTimeLocal(event.target.value);
                setError(null);
                setMessage(null);
              }}
            />
          </label>
        ) : null}

        <label className="staffing-field">
          <span>备注（可选）</span>
          <textarea
            name="note"
            rows={2}
            value={note}
            disabled={fieldsLocked}
            aria-invalid={noteTooLong}
            onChange={(event) => {
              setNote(event.target.value);
              setError(null);
              setMessage(null);
            }}
          />
        </label>

        {noteTooLong ? (
          <p className="form-error" role="alert">备注过长，最多 500 个字符。</p>
        ) : (
          <p className="staffing-hint">{noteLength}/500</p>
        )}

        <button className="btn btn-primary" type="submit" disabled={formDisabled}>
          {submitting ? "正在记录…" : "记录异常"}
        </button>

        {error !== null ? <p className="form-error" role="alert">{error}</p> : null}
        {message !== null ? <p className="staffing-hint" role="status">{message}</p> : null}
      </fieldset>
    </form>
  );
}
