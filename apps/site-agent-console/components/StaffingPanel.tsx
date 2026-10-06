"use client";

import { useEffect, useState } from "react";
import { createStaffingActions, type StaffingActions } from "../lib/staffing-actions";
import {
  createStaffingController,
  type StaffingView as StaffingViewState,
} from "../lib/staffing-state";
import {
  createStaffingClient,
  type StaffingClient,
  type StaffingDateSnapshot,
} from "../lib/staffing";
import { staffingManagerLabelIssue } from "../lib/staffing-guards";
import { CandidateCards } from "./staffing/CandidateCards";
import { ExceptionForm } from "./staffing/ExceptionForm";
import { GenerationStatus } from "./staffing/GenerationStatus";
import { RosterImport } from "./staffing/RosterImport";
import { Badge, Section } from "./ui";

interface StaffingViewProps {
  view: StaffingViewState;
  actions: StaffingActions;
  managerLabel: string;
  onManagerLabelChange(value: string): void;
}

interface StaffingPanelProps {
  client?: StaffingClient;
}

interface StaffingBundle {
  controller: ReturnType<typeof createStaffingController>;
  actions: StaffingActions;
}

interface ManagerLabelCell {
  read(): string;
  write(value: string): void;
}

function createManagerLabelCell(): ManagerLabelCell {
  let value = "";
  return {
    read: () => value,
    write: (next) => {
      value = next;
    },
  };
}

function managerLabelError(value: string): string | null {
  const issue = staffingManagerLabelIssue(value);
  if (issue === "blank") return "请填写经理归属标签。";
  if (issue === "too_long") return "经理归属标签最多 128 个字符。";
  if (issue === "unsafe") return "经理归属标签包含不安全的字符。";
  return null;
}

function writeBlocksChanges(view: StaffingViewState): boolean {
  return view.write?.status === "in_flight" ||
    view.write?.status === "unknown" ||
    view.write?.status === "busy";
}

function generationIsOngoing(view: StaffingViewState): boolean {
  return view.activeGeneration?.state === "RESERVED" ||
    view.activeGeneration?.state === "IN_PROGRESS";
}

function WriteNotice({ view, actions }: { view: StaffingViewState; actions: StaffingActions }) {
  const write = view.write;
  if (write === null) return null;
  if (write.status === "in_flight") {
    return (
      <div className="staffing-notice" role="status" aria-live="polite">
        <Badge tone="info">提交中</Badge> 正在保存本次人员变更。
      </div>
    );
  }
  if (write.status === "unknown") {
    return (
      <div className="staffing-notice" role="alert">
        <Badge tone="bad">结果未知</Badge> 请求结果尚未确认：{write.detail}{" "}
        <button
          type="button"
          className="btn btn-quiet"
          disabled={write.recovering}
          onClick={() => void actions.recover().catch(() => undefined)}
        >
          {write.recovering ? "正在查询结果…" : "查询并恢复结果"}
        </button>
      </div>
    );
  }
  if (write.status === "busy") {
    return (
      <div className="staffing-notice" role="alert">
        <Badge tone="warn">繁忙</Badge> 服务暂未接收保留的请求：{write.detail}{" "}
        <button
          type="button"
          className="btn btn-quiet"
          onClick={() => void actions.retryBusy().catch(() => undefined)}
        >
          使用同一请求再次提交
        </button>{" "}
        <button type="button" className="btn btn-quiet" onClick={actions.acknowledgeWrite}>
          关闭
        </button>
      </div>
    );
  }
  if (write.status === "rejected") {
    return (
      <div className="staffing-notice" role="alert">
        <Badge tone="bad">未保存</Badge> {write.detail}（{write.code}）{" "}
        <button type="button" className="btn btn-quiet" onClick={actions.acknowledgeWrite}>
          知道了
        </button>
      </div>
    );
  }
  return (
    <div className="staffing-notice" role="status" aria-live="polite">
      <Badge tone={write.savedButStale ? "warn" : "ok"}>已保存</Badge>{" "}
      本次变更已有持久回执。
      {write.savedButStale ? " 刷新失败，当前记录可能不是最新状态。" : ""}{" "}
      <button type="button" className="btn btn-quiet" onClick={actions.acknowledgeWrite}>
        关闭
      </button>
    </div>
  );
}

function ReadNotice({ view, actions }: { view: StaffingViewState; actions: StaffingActions }) {
  if (view.read.status === "loading" && view.snapshot === null) {
    return (
      <p className="staffing-notice" role="status" aria-live="polite">
        正在读取人员与建议记录…
      </p>
    );
  }
  if (view.read.status === "loading" && view.snapshot !== null) {
    return (
      <p className="staffing-notice" role="status" aria-live="polite">
        正在刷新已保存的人员记录，完成前暂停变更。
      </p>
    );
  }
  if (view.read.status === "error") {
    return (
      <div className="staffing-notice" role="alert">
        <Badge tone={view.snapshot === null ? "bad" : "warn"}>
          {view.snapshot === null ? "不可用" : "旧数据"}
        </Badge>{" "}
        {view.read.detail ?? "人员记录刷新失败。"}{" "}
        <button
          type="button"
          className="btn btn-quiet"
          onClick={() => void actions.refresh().catch(() => undefined)}
        >
          刷新人员记录
        </button>
      </div>
    );
  }
  return null;
}

function ActiveExceptions({ view }: { view: StaffingViewState }) {
  const rows = view.snapshot?.active_exceptions ?? [];
  return (
    <section className="staffing-exceptions" aria-label="当前人员异常">
      <h3>当前人员异常</h3>
      {rows.length === 0 ? (
        <p className="empty-note">暂无已记录的人员异常。</p>
      ) : (
        <ul className="staffing-exception-list">
          {rows.map((entry) => (
            <li key={entry.exception_id}>
              <strong>{entry.display_name}</strong>
              <span className="mono">{entry.kind}</span>
              <span>{entry.unavailable_start_at} — {entry.unavailable_end_at}</span>
              {entry.note ? <span>{entry.note}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function EffectivePlanStatus({ snapshot }: { snapshot: StaffingDateSnapshot }) {
  const plan = snapshot.effective_plan;
  if (plan === null) {
    return (
      <section className="staffing-plan-status" aria-label="有效排班状态">
        <Badge tone="muted">尚无有效排班</Badge>
        <p>尚未保存经理确认后的排班版本。</p>
      </section>
    );
  }
  const needsReview = plan.status === "REVIEW_REQUIRED";
  return (
    <section
      className="staffing-plan-status"
      aria-label="有效排班状态"
      role={needsReview ? "alert" : "status"}
    >
      <Badge tone={needsReview ? "warn" : "ok"}>{plan.status}</Badge>
      <p>
        排班版本 {plan.revision} · {plan.assignments.length} 个班次。
        {needsReview ? " 人员表或异常已变化，需要经理重新审阅。" : " 当前为经理确认版本。"}
      </p>
    </section>
  );
}

export function StaffingView({
  view,
  actions,
  managerLabel,
  onManagerLabelChange,
}: StaffingViewProps) {
  const snapshot = view.snapshot;
  const managerError = managerLabelError(managerLabel);
  const fresh = snapshot !== null && view.read.status === "ready" && !view.read.stale;
  const commonDisabled = !fresh || managerError !== null || writeBlocksChanges(view);
  const rosterReady = snapshot?.roster !== null && snapshot?.roster !== undefined;
  const generationDisabled = commonDisabled ||
    !rosterReady ||
    generationIsOngoing(view) ||
    snapshot?.generation_capability.status === "UNAVAILABLE";
  const localDisabled = commonDisabled || !rosterReady;

  return (
    <Section
      title="人员异常与排班建议"
      aside={
        <>
          <Badge tone="sim">模拟环境</Badge>
          <Badge tone="info">仅提供建议</Badge>
        </>
      }
    >
      <p className="staffing-boundary">
        记录人员变化，查看可追溯的排班候选；所有建议都需要经理明确确认。
      </p>
      <div className="staffing-manager form-row">
        <label htmlFor="staffing-manager-label">经理归属标签</label>
        <input
          id="staffing-manager-label"
          name="manager_label"
          value={managerLabel}
          onChange={(event) => onManagerLabelChange(event.currentTarget.value)}
          autoComplete="off"
        />
        <span className="fineprint">仅作归属记录，非身份认证</span>
        {managerError ? <span className="form-error">{managerError}</span> : null}
      </div>

      <ReadNotice view={view} actions={actions} />
      <WriteNotice view={view} actions={actions} />

      {snapshot ? (
        <>
          <section className="staffing-rail" role="region" aria-label="服务日与人员表状态">
            <dl className="staffing-rail-facts">
              <div>
                <dt>服务日</dt>
                <dd>{snapshot.service_date}</dd>
              </div>
              <div>
                <dt>时区</dt>
                <dd>{snapshot.context.site_timezone}</dd>
              </div>
              {snapshot.roster === null ? (
                <div className="staffing-rail-open">
                  <dt>人员表</dt>
                  <dd>尚未导入人员表</dd>
                </div>
              ) : (
                <>
                  <div>
                    <dt>员工</dt>
                    <dd>{snapshot.roster.worker_count}</dd>
                  </div>
                  <div>
                    <dt>常规班次</dt>
                    <dd>{snapshot.roster.assignment_count}</dd>
                  </div>
                  <div>
                    <dt>版本</dt>
                    <dd>{snapshot.roster.revision}</dd>
                  </div>
                </>
              )}
            </dl>
            <button
              type="button"
              className="btn btn-quiet"
              disabled={view.read.status === "loading"}
              onClick={() => void actions.refresh().catch(() => undefined)}
            >
              刷新
            </button>
          </section>

          <div className="staffing-workbench">
            <div className="staffing-entry-column">
              <ExceptionForm
                assignments={snapshot.assignments}
                disabled={localDisabled}
                onRecord={actions.recordException}
              />
              <RosterImport disabled={commonDisabled} onImport={actions.importRoster} />
            </div>
            <div className="staffing-status-column">
              <EffectivePlanStatus snapshot={snapshot} />
              <ActiveExceptions view={view} />
              <GenerationStatus
                capability={snapshot.generation_capability}
                generation={view.activeGeneration}
                disabled={generationDisabled}
                onGenerate={actions.generateSuggestion}
              />
              {view.activeGeneration?.state === "RESULT_UNKNOWN" ? (
                <button
                  type="button"
                  className="btn"
                  disabled={generationDisabled}
                  onClick={() => void actions.retryUnknownGeneration().catch(() => undefined)}
                >
                  用新请求重试建议
                </button>
              ) : null}
            </div>
          </div>

          {view.activeGeneration ? (
            <CandidateCards
              generation={view.activeGeneration}
              revisions={snapshot.revisions}
              disabled={commonDisabled || !rosterReady || generationIsOngoing(view)}
              onAccept={actions.acceptSuggestion}
              onModify={actions.modifySuggestion}
              onReject={actions.rejectSuggestion}
            />
          ) : null}
        </>
      ) : (
        <div className="staffing-read-actions">
          <button
            type="button"
            className="btn btn-quiet"
            disabled={view.read.status === "loading"}
            onClick={() => void actions.refresh().catch(() => undefined)}
          >
            刷新人员记录
          </button>
        </div>
      )}
    </Section>
  );
}

export function StaffingPanel({ client }: StaffingPanelProps) {
  const [managerLabelCell] = useState(createManagerLabelCell);
  const [managerLabel, setManagerLabel] = useState("");
  const [bundle] = useState<StaffingBundle>(() => {
    const selectedClient = client ?? createStaffingClient((input, init) => fetch(input, init));
    const readManagerLabel = managerLabelCell.read;
    const controller = createStaffingController(selectedClient, {
      getManagerLabel: readManagerLabel,
    });
    return {
      controller,
      actions: createStaffingActions(controller, readManagerLabel),
    };
  });
  const [view, setView] = useState<StaffingViewState>(() => bundle.controller.view());

  useEffect(() => {
    const unsubscribe = bundle.controller.subscribe(setView);
    bundle.controller.start();
    return () => {
      bundle.controller.stop();
      unsubscribe();
    };
  }, [bundle]);

  const updateManagerLabel = (value: string) => {
    managerLabelCell.write(value);
    setManagerLabel(value);
  };

  return (
    <StaffingView
      view={view}
      actions={bundle.actions}
      managerLabel={managerLabel}
      onManagerLabelChange={updateManagerLabel}
    />
  );
}
