/** Read-only saved simulation evidence. No execution or runtime truth is owned here. */
import { API_SCHEMA, ManagerApiError, type FetchLike } from "./api";
export const COURSE_OPS_SCHEMA = "nxt-course-ops/v1";
export type CameraQuality = "USABLE" | "BLURRED" | "OCCLUDED";
export interface CourseCheckpoint {
  checkpoint_id: string; hole_number: number; feature_id: string; surface_type: string;
  x_m: number; y_m: number; coverage_status: CameraQuality | "UNOBSERVED" | "STALE";
  last_observed_minute: number | null;
}
export interface CourseFrame {
  frame_id: string; cart_id: string; checkpoint_id: string; minute: number;
  image_url: string; image_sha256: string; width: number; height: number;
  quality: CameraQuality; score_calibration: "NOT_CALIBRATED";
  detections: { condition: string; score: number; bbox: number[] }[];
}
export interface CourseOpsSnapshot {
  schema: typeof COURSE_OPS_SCHEMA; environment: "SIMULATION"; generated_at_utc: string;
  source: {kind: "SAVED_SIMULATION_REPORT"; series_id: string; session_id: string; engine_digest: string; round_id: string; index: number; seed: number;
    status: string; parent_paused: boolean; child_paused: boolean; published_at_utc: null;
    report_sha256: string; live: false};
  clock: {simulated_minute: number; simulated_at_utc: string};
  course: {site_id: string; deployment_id: string; map_revision: string | null; geometry: null;
    hole_count: 18; cart_count: 16; checkpoints: CourseCheckpoint[]};
  carts: {cart_id: string; frame_id: string; minute: number; x_m: number; y_m: number; accuracy_m: number}[];
  frames: CourseFrame[];
  observations: {observation_id: string; checkpoint_id: string; frame_id: string; condition: string;
    quality: string; captured_at_utc: string; detection_score: number | null}[];
  cases: {case_id: string; checkpoint_id: string; condition_kind: string; status: string; priority: string;
    opened_at_utc: string; updated_at_utc: string; latest_task_id: string | null}[];
  tasks: {task_id: string; case_id: string; task_kind: string; status: string; resource_id: string;
    resource_kind: string; assigned_at_utc: string; started_at_utc: string | null; completed_at_utc: string | null}[];
  staff_jobs: {job_id: string; checkpoint_id: string; task_kind: string; status: string;
    started_at_s: number | null; completed_at_s: number | null}[];
  range: {status: "UNKNOWN" | "OBSERVED"; observed_minute: number | null; inventory_fraction: number | null;
    zones: {zone_id: string; balls: number | null; is_open: boolean | null}[];
    robots: {robot_id: string; activity: string; health: string; location: string; battery_fraction: number;
      payload_balls: number; awaiting_human: boolean}[];
    staff: {capacity: number; busy: number; queued: number} | null};
  weather: {status: "UNKNOWN"};
}

type Validator = (value: unknown) => boolean;
const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const text: Validator = (v) => typeof v === "string" && v.length > 0 && v.length <= 1000;
const num: Validator = (v) => typeof v === "number" && Number.isFinite(v) && v >= 0;
const coordinate: Validator = (v) => typeof v === "number" && Number.isFinite(v) && Math.abs(v) <= 10000;
const integer: Validator = (v) => num(v) && Number.isInteger(v);
const boolean: Validator = (v) => typeof v === "boolean";
const nullable = (check: Validator): Validator => (v) => v === null || check(v);
const literal = (expected: unknown): Validator => (v) => v === expected;
const oneOf = (...values: string[]): Validator => (v) => typeof v === "string" && values.includes(v);
const bounded = (max: number): Validator => (v) => num(v) && (v as number) <= max;
const array = (check: Validator, max = 20000): Validator => (v) => Array.isArray(v) && v.length <= max && v.every(check);
const shape = (fields: Record<string, Validator>): Validator => (v) => object(v) &&
  Object.keys(v).length === Object.keys(fields).length && Object.entries(fields).every(([key, check]) => check(v[key]));
const timestamp: Validator = (v) => typeof v === "string" && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$/.test(v) && Number.isFinite(Date.parse(v));
const digest: Validator = (v) => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const frameId: Validator = (v) => typeof v === "string" && /^frame-\d{6}$/.test(v);
const roundId: Validator = (v) => typeof v === "string" && /^round-\d{10}$/.test(v);
const quality = oneOf("USABLE", "BLURRED", "OCCLUDED");
const checkpoint = shape({ checkpoint_id: text, hole_number: (v) => integer(v) && (v as number) >= 1 && (v as number) <= 18,
  feature_id: text, surface_type: oneOf("fairway", "bunker", "green"), x_m: coordinate, y_m: coordinate,
  coverage_status: oneOf("UNOBSERVED", "STALE", "USABLE", "BLURRED", "OCCLUDED"), last_observed_minute: nullable(num) });
const frame = shape({frame_id: frameId, cart_id: text, checkpoint_id: text, minute: num, image_url: text,
  image_sha256: digest, width: (v) => integer(v) && (v as number) > 0 && (v as number) <= 1024,
  height: (v) => integer(v) && (v as number) > 0 && (v as number) <= 768,
  quality, score_calibration: literal("NOT_CALIBRATED"),
  detections: array(shape({condition: text, score: bounded(1), bbox: (v) => array(num, 4)(v) && (v as unknown[]).length === 4}), 100)});
const range = shape({status: oneOf("UNKNOWN", "OBSERVED"), observed_minute: nullable(num), inventory_fraction: nullable(bounded(2)),
  zones: array(shape({zone_id: text, balls: nullable(num), is_open: nullable(boolean)}), 100),
  robots: array(shape({robot_id: text, activity: text, health: text, location: text, battery_fraction: bounded(1),
    payload_balls: num, awaiting_human: boolean}), 100),
  staff: nullable(shape({capacity: integer, busy: integer, queued: integer}))});
const snapshot = shape({schema: literal(COURSE_OPS_SCHEMA), environment: literal("SIMULATION"), generated_at_utc: timestamp,
  source: shape({kind: literal("SAVED_SIMULATION_REPORT"), series_id: digest, session_id: digest, engine_digest: digest, round_id: roundId, index: integer, seed: integer, status: text,
    parent_paused: boolean, child_paused: boolean, published_at_utc: literal(null), report_sha256: digest, live: literal(false)}),
  clock: shape({simulated_minute: bounded(10080), simulated_at_utc: timestamp}),
  course: shape({site_id: literal("synthetic-course-18"), deployment_id: literal("synthetic-course-18-monitoring-v0"),
    map_revision: nullable(literal("sha256:058b2d16ad60dae347b5fb1f39d39e3880f5017313ce348df2dab3084ec5837f")),
    geometry: literal(null), hole_count: literal(18), cart_count: literal(16), checkpoints: array(checkpoint, 54)}),
  carts: array(shape({cart_id: text, frame_id: frameId, minute: num, x_m: coordinate, y_m: coordinate, accuracy_m: bounded(1000)}), 16),
  frames: array(frame, 10000),
  observations: array(shape({observation_id: text, checkpoint_id: text, frame_id: frameId, condition: text, quality: text,
    captured_at_utc: timestamp, detection_score: nullable(bounded(1))})),
  cases: array(shape({case_id: text, checkpoint_id: text, condition_kind: text, status: text, priority: text,
    opened_at_utc: timestamp, updated_at_utc: timestamp, latest_task_id: nullable(text)})),
  tasks: array(shape({task_id: text, case_id: text, task_kind: text, status: text, resource_id: text, resource_kind: text,
    assigned_at_utc: timestamp, started_at_utc: nullable(timestamp), completed_at_utc: nullable(timestamp)})),
  staff_jobs: array(shape({job_id: text, checkpoint_id: text, task_kind: text, status: text,
    started_at_s: nullable(num), completed_at_s: nullable(num)})), range, weather: shape({status: literal("UNKNOWN")})});

export function parseCourseOps(value: unknown): CourseOpsSnapshot {
  const fail = (): never => { throw new ManagerApiError(200, {code: "invalid_course_ops", detail: "Saved course evidence is incomplete or inconsistent."}); };
  if (!snapshot(value)) return fail();
  const result = value as CourseOpsSnapshot;
  const {source, clock, course, frames, carts} = result;
  const cpIds = new Set(course.checkpoints.map((p) => p.checkpoint_id));
  const framesById = new Map(frames.map((p) => [p.frame_id, p]));
  const caseIds = new Set(result.cases.map((p) => p.case_id));
  if (source.round_id !== `round-${String(source.index).padStart(10, "0")}` || source.seed >= 2 ** 32 ||
      cpIds.size !== 54 || framesById.size !== frames.length || caseIds.size !== result.cases.length ||
      new Set(carts.map((c) => c.cart_id)).size !== carts.length) return fail();
  for (const p of course.checkpoints) {
    if (p.checkpoint_id !== `cp-h${String(p.hole_number).padStart(2, "0")}-${p.surface_type}` ||
        p.feature_id !== `hole-${String(p.hole_number).padStart(2, "0")}-${p.surface_type}` ||
        (p.last_observed_minute !== null && p.last_observed_minute > clock.simulated_minute) ||
        (p.coverage_status === "UNOBSERVED") !== (p.last_observed_minute === null)) return fail();
  }
  for (const p of frames) {
    if (!cpIds.has(p.checkpoint_id) || !/^CART-(?:0[1-9]|1[0-6])$/.test(p.cart_id) || p.minute > clock.simulated_minute ||
        p.image_url !== `/api/v1/course-ops/media/${source.round_id}/${p.frame_id}.png?sha256=${p.image_sha256}` ||
        p.detections.some(({bbox: [l, t, r, b]}) => !(l < r && r <= p.width && t < b && b <= p.height))) return fail();
  }
  for (const p of carts) {
    const f = framesById.get(p.frame_id);
    if (!f || f.cart_id !== p.cart_id || f.minute !== p.minute) return fail();
  }
  if (result.observations.some((p) => !cpIds.has(p.checkpoint_id) || !framesById.has(p.frame_id)) ||
      result.cases.some((p) => !cpIds.has(p.checkpoint_id)) || result.tasks.some((p) => !caseIds.has(p.case_id)) ||
      result.staff_jobs.some((p) => !cpIds.has(p.checkpoint_id))) return fail();
  if (result.range.status === "UNKNOWN" && (result.range.observed_minute !== null || result.range.inventory_fraction !== null ||
      result.range.zones.length || result.range.robots.length || result.range.staff !== null)) return fail();
  if (result.range.status === "OBSERVED" && result.range.observed_minute !== clock.simulated_minute) return fail();
  if (result.range.staff && result.range.staff.busy > result.range.staff.capacity) return fail();
  return result;
}

export function createCourseOpsClient(fetchImpl: FetchLike) {
  return {async read(signal?: AbortSignal): Promise<CourseOpsSnapshot> {
    const abort = new AbortController();
    const cancel = () => abort.abort();
    signal?.addEventListener("abort", cancel, {once: true});
    if (signal?.aborted) abort.abort();
    const timer = setTimeout(cancel, 8000);
    try {
      const response = await fetchImpl("/api/v1/course-ops", {method: "GET", cache: "no-store", signal: abort.signal});
      const payload: unknown = await response.json();
      if (!object(payload) || payload.schema !== API_SCHEMA || !text(payload.disclaimer)) {
        throw new ManagerApiError(response.status, {code: "invalid_course_ops", detail: "Unexpected saved course response."});
      }
      if (!response.ok) {
        const error = object(payload.error) ? payload.error : {};
        throw new ManagerApiError(response.status, {code: typeof error.code === "string" ? error.code : "course_ops_unavailable",
          detail: typeof error.detail === "string" ? error.detail : "Saved course evidence is unavailable."});
      }
      return parseCourseOps(payload.data);
    } finally { clearTimeout(timer); signal?.removeEventListener("abort", cancel); }
  }};
}
