/**
 * Manager form drafts and their translation to planning v1 wire requests.
 *
 * Drafts hold exactly what the manager typed (strings, site-local times).
 * Builders validate completeness and the schema-level shape, convert site
 * time to UTC, and preserve missingness: an evidence block marked unknown
 * becomes `null`, never zero or a default. Business rules (stockout,
 * ranking, expiry, admission) stay with the service.
 */

import {
  CONFIRMATION_SCHEMA,
  INPUT_SCHEMA,
  OUTCOME_SCHEMA,
  PLAN_REQUEST_SCHEMA,
  SCOPES,
  STAGES,
  type BallsEvidence,
  type BooleanEvidence,
  type ConfirmationRequest,
  type CycleEvidence,
  type CycleStages,
  type DemandEvidence,
  type Evidence,
  type InputRecord,
  type InputRequest,
  type MinutesEvidence,
  type OutcomeRequest,
  type PlanRequest,
  type PlanningContext,
  type Scope,
  type SourceKind,
  type Stage,
  type YieldEvidence,
  type ZoneInput,
} from "./planning";
import { siteTimeToUtc, utcToSiteInput } from "./site-time";

export interface EvidenceDraft {
  /** false = unknown: the field is sent as null and the plan reports it missing. */
  known: boolean;
  sourceKind: SourceKind;
  sourceRef: string;
  /** site-local `datetime-local` values */
  observedAt: string;
  validUntil: string;
}

export type BooleanText = "true" | "false";

export interface ZoneDraft {
  zoneId: string;
  robotId: string;
  collectionAllowed: EvidenceDraft & { value: BooleanText };
  cleanYield: EvidenceDraft & { low: string; high: string };
  cycleMinutes: EvidenceDraft & Record<keyof CycleStages, string>;
}

export interface InputDraft {
  operator: string;
  reason: string;
  scope: Scope;
  effectiveAt: string;
  validUntil: string;
  windowStart: string;
  windowEnd: string;
  inventory: EvidenceDraft & { value: string };
  demand: EvidenceDraft & { bucketMinutes: string; low: string; typical: string; high: string };
  safetyStock: EvidenceDraft & { value: string };
  bufferMinutes: EvidenceDraft & { value: string };
  operationsAllowed: EvidenceDraft & { value: BooleanText };
  washerAvailable: EvidenceDraft & { value: BooleanText };
  zones: ZoneDraft[];
}

export interface PlanDraft {
  operator: string;
  reason: string;
  scope: Scope;
  validUntil: string;
  selection: { zoneId: string; robotId: string; startAt: string } | null;
}

export interface OutcomeDraft {
  operator: string;
  reason: string;
  stage: Stage;
  quantity: string;
  startedAt: string;
  completedAt: string;
  sourceKind: SourceKind;
  sourceRef: string;
}

export const CYCLE_STAGES: (keyof CycleStages)[] = ["travel", "collect", "return", "unload", "wash", "supply"];

const emptyEvidence = (): EvidenceDraft => ({
  known: false,
  sourceKind: "MANUAL_ESTIMATE",
  sourceRef: "",
  observedAt: "",
  validUntil: "",
});

export function emptyZoneDraft(zoneId = "", robotId = ""): ZoneDraft {
  return {
    zoneId,
    robotId,
    collectionAllowed: { ...emptyEvidence(), value: "true" },
    cleanYield: { ...emptyEvidence(), low: "", high: "" },
    cycleMinutes: { ...emptyEvidence(), travel: "", collect: "", return: "", unload: "", wash: "", supply: "" },
  };
}

/** Nothing is prefilled: no inventory, demand, duration, or restriction. */
export function emptyInputDraft(): InputDraft {
  return {
    operator: "",
    reason: "",
    scope: "SHIFT",
    effectiveAt: "",
    validUntil: "",
    windowStart: "",
    windowEnd: "",
    inventory: { ...emptyEvidence(), value: "" },
    demand: { ...emptyEvidence(), bucketMinutes: "", low: "", typical: "", high: "" },
    safetyStock: { ...emptyEvidence(), value: "" },
    bufferMinutes: { ...emptyEvidence(), value: "" },
    operationsAllowed: { ...emptyEvidence(), value: "true" },
    washerAvailable: { ...emptyEvidence(), value: "true" },
    zones: [],
  };
}

const trimmed = (value: string, label: string): string => {
  const out = value.trim();
  if (!out) throw new Error(`${label} is required.`);
  return out;
};

const numberOf = (value: string, label: string, options: { integer?: boolean; positive?: boolean } = {}): number => {
  const out = Number(value.trim());
  if (value.trim() === "" || !Number.isFinite(out)) throw new Error(`${label} must be a number.`);
  if (options.integer && !Number.isInteger(out)) throw new Error(`${label} must be a whole number of balls.`);
  if (options.positive ? out <= 0 : out < 0) {
    throw new Error(`${label} must be ${options.positive ? "greater than zero" : "zero or more"}.`);
  }
  return out;
};

const numberList = (value: string, label: string): number[] => {
  const items = value.split(/[\s,;]+/).filter((item) => item !== "");
  if (items.length === 0) throw new Error(`${label} must list at least one bucket.`);
  return items.map((item, index) => numberOf(item, `${label} bucket ${index + 1}`));
};

const booleanOf = (value: string, label: string): boolean => {
  if (value === "true") return true;
  if (value === "false") return false;
  throw new Error(`${label} must be yes or no.`);
};

const scopeOf = (value: string): Scope => {
  if ((SCOPES as string[]).includes(value)) return value as Scope;
  throw new Error("Scope must be SHIFT, DAY or ONE_TASK.");
};

const timeOf = (value: string, label: string, timeZone: string): string => {
  try {
    return siteTimeToUtc(value, timeZone);
  } catch (cause) {
    throw new Error(`${label}: ${cause instanceof Error ? cause.message : String(cause)}`);
  }
};

function evidenceOf<V, U extends string>(
  draft: EvidenceDraft,
  label: string,
  unit: U,
  value: () => V,
  timeZone: string,
): Evidence<V, U> | null {
  if (!draft.known) return null;
  const sourceRef = draft.sourceRef.trim();
  if (!sourceRef) throw new Error(`${label}: enter the source reference (who or what produced this value).`);
  if (draft.sourceKind !== "MEASURED" && draft.sourceKind !== "MANUAL_ESTIMATE") {
    throw new Error(`${label}: source must be MEASURED or MANUAL_ESTIMATE.`);
  }
  return {
    value: value(),
    source_kind: draft.sourceKind,
    source_ref: sourceRef,
    observed_at_utc: timeOf(draft.observedAt, `${label} observed at`, timeZone),
    valid_until_utc: timeOf(draft.validUntil, `${label} valid until`, timeZone),
    unit,
  };
}

export function buildInputRequest(
  draft: InputDraft,
  options: { requestId: string; expectedRevision: number; context: PlanningContext },
): InputRequest {
  const { context } = options;
  const timeZone = context.site_timezone;
  const operator = trimmed(draft.operator, "Operator");
  const reason = trimmed(draft.reason, "Reason");
  const scope = scopeOf(draft.scope);
  const demand = evidenceOf<DemandEvidence["value"], "balls/minute">(
    draft.demand,
    "Demand",
    "balls/minute",
    () => {
      const low = numberList(draft.demand.low, "Low demand");
      const typical = numberList(draft.demand.typical, "Typical demand");
      const high = numberList(draft.demand.high, "High demand");
      if (low.length !== typical.length || typical.length !== high.length) {
        throw new Error("Low, typical and high demand must list the same number of buckets.");
      }
      if (low.some((value, index) => !(value <= typical[index] && typical[index] <= high[index]))) {
        throw new Error("Demand must satisfy low <= typical <= high in every bucket.");
      }
      return { bucket_minutes: numberOf(draft.demand.bucketMinutes, "Bucket minutes", { positive: true }), low, typical, high };
    },
    timeZone,
  );
  const zones: ZoneInput[] = [];
  const pairs = new Set<string>();
  for (const zone of draft.zones) {
    const zoneId = trimmed(zone.zoneId, "Zone");
    const robotId = trimmed(zone.robotId, "Robot");
    if (!context.zone_ids.includes(zoneId)) throw new Error(`Zone ${zoneId} is not an allowed zone for this site.`);
    if (!context.robot_ids.includes(robotId)) throw new Error(`Robot ${robotId} cannot collect balls at this site.`);
    const key = `${zoneId}|${robotId}`;
    if (pairs.has(key)) throw new Error(`Zone ${zoneId} with robot ${robotId} is listed twice.`);
    pairs.add(key);
    const label = `Zone ${zoneId}`;
    zones.push({
      zone_id: zoneId,
      robot_id: robotId,
      collection_allowed: evidenceOf<boolean, "boolean">(
        zone.collectionAllowed,
        `${label} collection allowed`,
        "boolean",
        () => booleanOf(zone.collectionAllowed.value, `${label} collection allowed`),
        timeZone,
      ) as BooleanEvidence | null,
      clean_yield_balls: evidenceOf<YieldEvidence["value"], "balls">(
        zone.cleanYield,
        `${label} clean yield`,
        "balls",
        () => {
          const low = numberOf(zone.cleanYield.low, `${label} clean yield low`, { integer: true });
          const high = numberOf(zone.cleanYield.high, `${label} clean yield high`, { integer: true });
          if (low > high) throw new Error(`${label}: clean yield low must not exceed high.`);
          return { low, high };
        },
        timeZone,
      ) as YieldEvidence | null,
      cycle_minutes: evidenceOf<CycleStages, "minutes">(
        zone.cycleMinutes,
        `${label} cycle minutes`,
        "minutes",
        () =>
          Object.fromEntries(
            CYCLE_STAGES.map((stage) => [stage, numberOf(zone.cycleMinutes[stage], `${label} ${stage} minutes`)]),
          ) as unknown as CycleStages,
        timeZone,
      ) as CycleEvidence | null,
    });
  }
  return {
    schema: INPUT_SCHEMA,
    request_id: options.requestId,
    expected_revision: options.expectedRevision,
    site_id: context.site_id,
    deployment_id: context.deployment_id,
    site_timezone: timeZone,
    operator,
    reason,
    scope,
    effective_at_utc: timeOf(draft.effectiveAt, "Effective at", timeZone),
    valid_until_utc: timeOf(draft.validUntil, "Input valid until", timeZone),
    operating_window: {
      start_at_utc: timeOf(draft.windowStart, "Operating window start", timeZone),
      end_at_utc: timeOf(draft.windowEnd, "Operating window end", timeZone),
    },
    inventory_clean_balls: evidenceOf<number, "balls">(
      draft.inventory,
      "Clean inventory",
      "balls",
      () => numberOf(draft.inventory.value, "Clean inventory"),
      timeZone,
    ) as BallsEvidence | null,
    demand,
    safety_stock_balls: evidenceOf<number, "balls">(
      draft.safetyStock,
      "Safety stock",
      "balls",
      () => numberOf(draft.safetyStock.value, "Safety stock"),
      timeZone,
    ) as BallsEvidence | null,
    buffer_minutes: evidenceOf<number, "minutes">(
      draft.bufferMinutes,
      "Buffer minutes",
      "minutes",
      () => numberOf(draft.bufferMinutes.value, "Buffer minutes"),
      timeZone,
    ) as MinutesEvidence | null,
    operations_allowed: evidenceOf<boolean, "boolean">(
      draft.operationsAllowed,
      "Operations allowed",
      "boolean",
      () => booleanOf(draft.operationsAllowed.value, "Operations allowed"),
      timeZone,
    ) as BooleanEvidence | null,
    washer_available: evidenceOf<boolean, "boolean">(
      draft.washerAvailable,
      "Washer available",
      "boolean",
      () => booleanOf(draft.washerAvailable.value, "Washer available"),
      timeZone,
    ) as BooleanEvidence | null,
    zones,
  };
}

function evidenceDraftOf(evidence: Evidence<unknown, string> | null, timeZone: string): EvidenceDraft {
  if (evidence === null) return emptyEvidence();
  return {
    known: true,
    sourceKind: evidence.source_kind,
    sourceRef: evidence.source_ref,
    observedAt: utcToSiteInput(evidence.observed_at_utc, timeZone),
    validUntil: utcToSiteInput(evidence.valid_until_utc, timeZone),
  };
}

const numberText = (value: number | null | undefined) => (value === null || value === undefined ? "" : String(value));
const listText = (values: number[] | undefined) => (values ? values.map(String).join(", ") : "");

/** A revision starts from the persisted record so the manager corrects the
 * current input rather than retyping it; unknown fields stay unknown. */
export function draftFromInput(record: InputRecord, timeZone: string): InputDraft {
  return {
    operator: record.operator,
    reason: record.reason,
    scope: record.scope,
    effectiveAt: utcToSiteInput(record.effective_at_utc, timeZone),
    validUntil: utcToSiteInput(record.valid_until_utc, timeZone),
    windowStart: utcToSiteInput(record.operating_window.start_at_utc, timeZone),
    windowEnd: utcToSiteInput(record.operating_window.end_at_utc, timeZone),
    inventory: { ...evidenceDraftOf(record.inventory_clean_balls, timeZone), value: numberText(record.inventory_clean_balls?.value) },
    demand: {
      ...evidenceDraftOf(record.demand, timeZone),
      bucketMinutes: numberText(record.demand?.value.bucket_minutes),
      low: listText(record.demand?.value.low),
      typical: listText(record.demand?.value.typical),
      high: listText(record.demand?.value.high),
    },
    safetyStock: { ...evidenceDraftOf(record.safety_stock_balls, timeZone), value: numberText(record.safety_stock_balls?.value) },
    bufferMinutes: { ...evidenceDraftOf(record.buffer_minutes, timeZone), value: numberText(record.buffer_minutes?.value) },
    operationsAllowed: {
      ...evidenceDraftOf(record.operations_allowed, timeZone),
      value: record.operations_allowed?.value === false ? "false" : "true",
    },
    washerAvailable: {
      ...evidenceDraftOf(record.washer_available, timeZone),
      value: record.washer_available?.value === false ? "false" : "true",
    },
    zones: record.zones.map((zone) => ({
      zoneId: zone.zone_id,
      robotId: zone.robot_id,
      collectionAllowed: {
        ...evidenceDraftOf(zone.collection_allowed, timeZone),
        value: zone.collection_allowed?.value === false ? "false" : "true",
      },
      cleanYield: {
        ...evidenceDraftOf(zone.clean_yield_balls, timeZone),
        low: numberText(zone.clean_yield_balls?.value.low),
        high: numberText(zone.clean_yield_balls?.value.high),
      },
      cycleMinutes: {
        ...evidenceDraftOf(zone.cycle_minutes, timeZone),
        ...(Object.fromEntries(
          CYCLE_STAGES.map((stage) => [stage, numberText(zone.cycle_minutes?.value[stage])]),
        ) as Record<keyof CycleStages, string>),
      },
    })),
  };
}

export function buildPlanRequest(
  draft: PlanDraft,
  options: {
    requestId: string;
    planId: string | null;
    expectedPlanVersion: number;
    inputRevision: number;
    timeZone: string;
  },
): PlanRequest {
  return {
    schema: PLAN_REQUEST_SCHEMA,
    request_id: options.requestId,
    plan_id: options.planId,
    expected_plan_version: options.expectedPlanVersion,
    input_revision: options.inputRevision,
    operator: trimmed(draft.operator, "Operator"),
    reason: trimmed(draft.reason, "Reason"),
    scope: scopeOf(draft.scope),
    valid_until_utc: timeOf(draft.validUntil, "Plan valid until", options.timeZone),
    selection:
      draft.selection === null
        ? null
        : {
            zone_id: trimmed(draft.selection.zoneId, "Zone"),
            robot_id: trimmed(draft.selection.robotId, "Robot"),
            start_at_utc: timeOf(draft.selection.startAt, "Start at", options.timeZone),
          },
  };
}

export function buildConfirmationRequest(options: {
  requestId: string;
  planId: string;
  planVersion: number;
  operator: string;
}): ConfirmationRequest {
  return {
    schema: CONFIRMATION_SCHEMA,
    request_id: options.requestId,
    plan_id: options.planId,
    plan_version: options.planVersion,
    operator: trimmed(options.operator, "Operator"),
  };
}

export function buildOutcomeRequest(
  draft: OutcomeDraft,
  options: {
    requestId: string;
    confirmationId: string;
    taskId: string;
    supersedesOutcomeId: string | null;
    timeZone: string;
  },
): OutcomeRequest {
  if (!(STAGES as string[]).includes(draft.stage)) throw new Error("Stage must be COLLECTED, UNLOADED, WASHED or SUPPLIED.");
  if (draft.sourceKind !== "MEASURED" && draft.sourceKind !== "MANUAL_ESTIMATE") {
    throw new Error("Result source must be MEASURED or MANUAL_ESTIMATE.");
  }
  const started = timeOf(draft.startedAt, "Started at", options.timeZone);
  const completed = timeOf(draft.completedAt, "Completed at", options.timeZone);
  if (Date.parse(started) > Date.parse(completed)) {
    throw new Error("The start time must be at or before the completion time.");
  }
  return {
    schema: OUTCOME_SCHEMA,
    request_id: options.requestId,
    confirmation_id: options.confirmationId,
    task_id: options.taskId,
    operator: trimmed(draft.operator, "Operator"),
    reason: trimmed(draft.reason, "Reason"),
    stage: draft.stage,
    quantity_balls: numberOf(draft.quantity, "Quantity", { integer: true }),
    started_at_utc: started,
    completed_at_utc: completed,
    source_kind: draft.sourceKind,
    source_ref: trimmed(draft.sourceRef, "Result source reference"),
    supersedes_outcome_id: options.supersedesOutcomeId,
  };
}

/** Form convenience only: copy the manager's own input times into evidence
 * blocks whose times are still empty (observation = effective time, validity
 * = input validity). It never fills a value, source, or unknown field. */
export function fillEvidenceTimes(draft: InputDraft): InputDraft {
  const fill = <T extends EvidenceDraft>(block: T): T => ({
    ...block,
    observedAt: block.observedAt || draft.effectiveAt,
    validUntil: block.validUntil || draft.validUntil,
  });
  return {
    ...draft,
    inventory: fill(draft.inventory),
    demand: fill(draft.demand),
    safetyStock: fill(draft.safetyStock),
    bufferMinutes: fill(draft.bufferMinutes),
    operationsAllowed: fill(draft.operationsAllowed),
    washerAvailable: fill(draft.washerAvailable),
    zones: draft.zones.map((zone) => ({
      ...zone,
      collectionAllowed: fill(zone.collectionAllowed),
      cleanYield: fill(zone.cleanYield),
      cycleMinutes: fill(zone.cycleMinutes),
    })),
  };
}
