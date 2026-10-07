import { ManagerApiError } from "../../lib/api";

/**
 * Closed, human-readable copy for staffing business rejections the console
 * trusts by code. Server `detail` text is never shown; only the code selects
 * the message, so a rejection reads the same however the service words it.
 */
const REJECTION_COPY: Readonly<Record<string, string>> = Object.freeze({
  staffing_exception_overlap:
    "该员工当天已有重叠的异常记录（例如已登记的请假）。原记录未被改动；请先取消或更正原记录，或改为不重叠的时间后再记录。",
});

export function staffingRejectionCopy(code: string): string | null {
  return Object.prototype.hasOwnProperty.call(REJECTION_COPY, code) ? REJECTION_COPY[code] : null;
}

export function describeStaffingFailure(cause: unknown): string {
  if (cause instanceof ManagerApiError) {
    const copy = staffingRejectionCopy(cause.code);
    if (copy !== null) return copy;
  }
  return cause instanceof Error ? cause.message : String(cause);
}
