import type {
  CandidateProjection,
  EffectivePlanProjection,
  ExceptionProjection,
  GenerationProjection,
  ManagerResponseSummary,
} from "./staffing";

/**
 * Chinese display labels for closed staffing contract values.
 *
 * Every function returns the label for a known value and the raw value
 * itself for anything else, so an unexpected code is shown rather than
 * hidden. Callers keep the contract value visible beside the label; the
 * label never replaces it in a request or a receipt.
 */

const RESPONSE_KIND: Readonly<Record<ManagerResponseSummary["response_kind"], string>> = Object.freeze({
  ACCEPT: "已接受",
  MODIFY: "修改后接受",
  REJECT: "已拒绝",
});

const REASON_CODE: Readonly<Record<ManagerResponseSummary["reason_code"], string>> = Object.freeze({
  APPROVED: "经理批准",
  APPROVED_WITH_CHANGES: "经理修改后批准",
  MANUAL_HANDLING: "需要人工处理",
  INSUFFICIENT_CONTEXT: "信息不足",
  OTHER: "其他",
});

const PLAN_STATUS: Readonly<Record<EffectivePlanProjection["status"], string>> = Object.freeze({
  CURRENT: "当前有效",
  REVIEW_REQUIRED: "需重新审阅",
});

const CANDIDATE_STATUS: Readonly<Record<CandidateProjection["status"], string>> = Object.freeze({
  VALID: "通过校验",
  REJECTED: "未通过校验",
});

const ACTIONABILITY: Readonly<Record<CandidateProjection["actionability"], string>> = Object.freeze({
  CURRENT: "可采用",
  EXPIRED: "班次已结束（历史记录）",
});

const EXCEPTION_KIND: Readonly<Record<ExceptionProjection["kind"], string>> = Object.freeze({
  LEAVE: "请假",
  LATE: "迟到",
  EARLY_DEPARTURE: "早退",
  UNAVAILABLE: "临时无法到岗",
});

function labelOf<K extends string>(table: Readonly<Record<K, string>>, value: K): string {
  return Object.prototype.hasOwnProperty.call(table, value) ? table[value] : value;
}

export function responseKindLabel(value: ManagerResponseSummary["response_kind"]): string {
  return labelOf(RESPONSE_KIND, value);
}

export function reasonCodeLabel(value: ManagerResponseSummary["reason_code"]): string {
  return labelOf(REASON_CODE, value);
}

export function planStatusLabel(value: EffectivePlanProjection["status"]): string {
  return labelOf(PLAN_STATUS, value);
}

export function candidateStatusLabel(value: CandidateProjection["status"]): string {
  return labelOf(CANDIDATE_STATUS, value);
}

export function actionabilityLabel(value: CandidateProjection["actionability"]): string {
  return labelOf(ACTIONABILITY, value);
}

export function exceptionKindLabel(value: ExceptionProjection["kind"]): string {
  return labelOf(EXCEPTION_KIND, value);
}

/** A candidate the manager may still accept or modify: valid and not yet ended. */
export function candidateIsActionable(candidate: CandidateProjection): boolean {
  return candidate.status === "VALID" && candidate.actionability === "CURRENT";
}

/** A generation whose every candidate has ended is historical: it is shown for
 * the record, separated from current actions. A generation with no candidates
 * has nothing to act on and is not labelled historical. */
export function generationIsHistorical(generation: GenerationProjection): boolean {
  return generation.candidates.length > 0
    && generation.candidates.every((candidate) => candidate.actionability === "EXPIRED");
}
