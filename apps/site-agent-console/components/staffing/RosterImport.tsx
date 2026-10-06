"use client";

import { useRef, useState, type ChangeEvent } from "react";

import {
  parseStaffingRosterCsv,
  type RosterImportDraft,
} from "../../lib/staffing-csv";

export interface RosterImportProps {
  disabled: boolean;
  onImport(draft: RosterImportDraft): Promise<unknown>;
}

function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function csvErrorText(cause: unknown): string {
  const detail = errorText(cause);
  if (/body_too_large/i.test(detail)) {
    return "CSV 文件过大，请将文件控制在 512 KiB 以内。";
  }
  return "CSV 无效，请按模板检查表头、字段和引用关系。";
}

export function RosterImport({ disabled, onImport }: RosterImportProps) {
  const [pendingDraft, setPendingDraft] = useState<RosterImportDraft | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const selection = useRef(0);

  async function chooseFile(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const file = event.currentTarget.files?.[0] ?? null;
    const currentSelection = selection.current + 1;
    selection.current = currentSelection;
    setPendingDraft(null);
    setSelectedName(file?.name ?? null);
    setError(null);
    setMessage(null);
    if (file === null) {
      setReading(false);
      return;
    }

    setReading(true);
    try {
      const bytes = await file.arrayBuffer();
      const draft = parseStaffingRosterCsv(bytes, file.name);
      if (selection.current !== currentSelection) return;
      setPendingDraft(draft);
    } catch (cause) {
      if (selection.current !== currentSelection) return;
      setPendingDraft(null);
      setError(csvErrorText(cause));
    } finally {
      if (selection.current === currentSelection) setReading(false);
    }
  }

  async function confirmImport(): Promise<void> {
    if (disabled || submitting || pendingDraft === null) return;
    setSubmitting(true);
    setError(null);
    setMessage(null);
    try {
      await onImport(pendingDraft);
      setPendingDraft(null);
      setMessage("人员表已提交；最新版本将在刷新后显示。");
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setSubmitting(false);
    }
  }

  const locked = disabled || reading || submitting;

  return (
    <section
      className="staffing-import"
      role="region"
      aria-label="导入人员表"
      aria-busy={reading || submitting}
    >
      <div className="staffing-subhead">
        <div>
          <h3>人员表</h3>
          <p>使用统一 CSV，一次替换员工、班次与覆盖规则。</p>
        </div>
        <a className="staffing-template-link" href="/staffing-roster-template.csv" download>
          下载 CSV 模板
        </a>
      </div>

      <label className="staffing-file-field">
        <span>选择人员表 CSV</span>
        <input
          type="file"
          accept=".csv,text/csv"
          disabled={locked}
          onChange={(event) => void chooseFile(event)}
        />
      </label>

      {reading ? <p className="staffing-hint" role="status">正在检查 CSV…</p> : null}

      {pendingDraft !== null ? (
        <div className="staffing-import-preview" aria-label="待确认人员表">
          <div>
            <span>时区</span>
            <strong>{pendingDraft.site_timezone}</strong>
          </div>
          <div>
            <span>生效日期</span>
            <strong>{pendingDraft.effective_from_local_date}</strong>
          </div>
          <div>
            <span>员工</span>
            <strong>{pendingDraft.workers.length} 名</strong>
          </div>
          <div>
            <span>班次</span>
            <strong>{pendingDraft.regular_assignments.length} 个</strong>
          </div>
          <p className="staffing-source">待导入文件：{selectedName}</p>
          <button
            className="btn btn-primary"
            type="button"
            disabled={locked}
            onClick={() => void confirmImport()}
          >
            {submitting ? "正在导入…" : "确认导入人员表"}
          </button>
        </div>
      ) : null}

      {error !== null ? <p className="form-error" role="alert">{error}</p> : null}
      {message !== null ? <p className="staffing-hint" role="status">{message}</p> : null}
    </section>
  );
}
