import { ManagerApiError } from "../../lib/api";

/**
 * Closed, human-readable copy for staffing business rejections the console
 * trusts by code. Server `detail` text is never shown; only the code selects
 * the message, so a rejection reads the same however the service words it.
 */
const REJECTION_COPY: Readonly<Record<string, string>> = Object.freeze({
  staffing_exception_overlap:
    "该员工当天已有重叠的异常记录（例如已登记的请假）。原记录未被改动；请先取消或更正原记录，或改为不重叠的时间后再记录。",
  staffing_suggestion_expired:
    "该建议涉及的班次已经结束，不能再采用为当前排班。未保存任何变更；如仍需调整，请为剩余时段重新生成建议。",
  staffing_contract_mismatch:
    "控制台与服务来自不同的代码版本，服务已拒绝本次请求，未保存任何变更。请停止服务，从同一代码版本重新构建控制台并重启后，刷新页面再试。",
});

const CONTRACT_MISMATCH = "staffing_contract_mismatch";

/** A 2xx answer the console refused as another checkout's receipt: the service
 * may have saved the change, so the copy must not claim otherwise. */
const MISMATCHED_RECEIPT_COPY =
  "控制台与服务来自不同的代码版本：服务返回了本控制台无法解读的回执，本次变更是否已保存未知。请停止服务，从同一代码版本重新构建控制台并重启后刷新页面，再核对记录；不要重复提交。";

/** The read path's notice when the first snapshot names another revision. */
const MISMATCHED_SNAPSHOT_COPY =
  "控制台与服务来自不同的代码版本，人员记录无法读取，所有人员变更已停用。请停止服务，从同一代码版本重新构建控制台并重启后刷新页面。";

export function staffingRejectionCopy(code: string): string | null {
  return Object.prototype.hasOwnProperty.call(REJECTION_COPY, code) ? REJECTION_COPY[code] : null;
}

/** Guidance for a read failure whose detail names the contract revision
 * mismatch (`code: detail` as the client reports it); null otherwise. */
export function staffingReadFailureCopy(detail: string | null): string | null {
  return detail !== null && detail.startsWith(`${CONTRACT_MISMATCH}:`) ? MISMATCHED_SNAPSHOT_COPY : null;
}

export function describeStaffingFailure(cause: unknown): string {
  if (cause instanceof ManagerApiError) {
    if (cause.code === CONTRACT_MISMATCH && cause.status >= 200 && cause.status < 300) return MISMATCHED_RECEIPT_COPY;
    const copy = staffingRejectionCopy(cause.code);
    if (copy !== null) return copy;
  }
  return cause instanceof Error ? cause.message : String(cause);
}
