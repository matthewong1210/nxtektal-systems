import { ManagerApiError } from "./api";
import type { RosterImportRequest } from "./staffing";

export type RosterImportDraft = Omit<
  RosterImportRequest,
  "request_id" | "operator" | "expected_roster_revision" | "site_id" | "deployment_id"
>;

const HEADER = [
  "record_type",
  "timezone",
  "effective_from_local_date",
  "effective_until_local_date",
  "staff_id",
  "display_name",
  "skill_codes",
  "eligibility",
  "max_daily_minutes",
  "weekday",
  "role_code",
  "area_code",
  "start_local",
  "end_local",
  "required_skill_codes",
  "minimum_staff",
] as const;

const INDEX = Object.fromEntries(HEADER.map((name, index) => [name, index])) as Record<(typeof HEADER)[number], number>;

const ALLOWED_FIELDS: Record<string, readonly (typeof HEADER)[number][]> = {
  META: ["record_type", "timezone", "effective_from_local_date", "effective_until_local_date"],
  WORKER: ["record_type", "staff_id", "display_name", "skill_codes", "eligibility", "max_daily_minutes"],
  AVAILABILITY: ["record_type", "staff_id", "weekday", "start_local", "end_local"],
  ASSIGNMENT_RULE: ["record_type", "role_code", "area_code", "required_skill_codes"],
  REGULAR_ASSIGNMENT: ["record_type", "staff_id", "weekday", "role_code", "area_code", "start_local", "end_local"],
  COVERAGE: ["record_type", "weekday", "role_code", "area_code", "start_local", "end_local", "minimum_staff"],
};

enum TokenizerState {
  FIELD,
  QUOTED,
  AFTER_QUOTE,
}

function failure(): never {
  throw new ManagerApiError(400, {
    code: "staffing_invalid_request",
    detail: "staffing roster CSV is invalid",
  });
}

function tooLarge(): never {
  throw new ManagerApiError(413, {
    code: "body_too_large",
    detail: "staffing roster CSV is too large",
  });
}

function ensure(condition: unknown): asserts condition {
  if (!condition) failure();
}

function hasUnpairedSurrogate(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return true;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) return true;
  }
  return false;
}

function safeText(value: string, minimum: number, maximum: number, nonblank = false): string {
  ensure(!hasUnpairedSurrogate(value));
  const length = Array.from(value).length;
  ensure(length >= minimum && length <= maximum);
  for (const character of value) {
    const point = character.codePointAt(0) ?? 0;
    ensure(!(point <= 0x1f || (point >= 0x7f && point <= 0x9f)));
  }
  if (nonblank) ensure(/\S/u.test(value));
  return value;
}

function formulaSafe(value: string): void {
  if (value !== "") ensure(!/^[ \t]*[=+@-]/.test(value));
}

function identifier(value: string): string {
  ensure(/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(value));
  return value;
}

function code(value: string): string {
  ensure(/^[A-Z][A-Z0-9_]{0,31}$/.test(value));
  return value;
}

function localDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  ensure(match);
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const maximum = month === 2 ? (leap ? 29 : 28) : [4, 6, 9, 11].includes(month) ? 30 : 31;
  ensure(year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= maximum);
  return value;
}

function localMinute(value: string): string {
  ensure(/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value));
  return value;
}

function integer(value: string, minimum: number, maximum: number): number {
  ensure(/^(?:0|[1-9]\d*)$/.test(value));
  const number = Number(value);
  ensure(Number.isSafeInteger(number) && number >= minimum && number <= maximum);
  return number;
}

function weekday(value: string): RosterImportRequest["availability"][number]["weekday"] {
  return integer(value, 0, 6) as RosterImportRequest["availability"][number]["weekday"];
}

function tokenize(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let state = TokenizerState.FIELD;
  let rowDirty = false;

  const finishField = () => {
    row.push(field);
    field = "";
  };
  const finishRow = () => {
    finishField();
    rows.push(row);
    row = [];
    rowDirty = false;
  };

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (state === TokenizerState.FIELD) {
      if (character === '"') {
        ensure(field === "");
        state = TokenizerState.QUOTED;
        rowDirty = true;
      } else if (character === ",") {
        finishField();
        rowDirty = true;
      } else if (character === "\n") {
        finishRow();
      } else if (character === "\r") {
        ensure(text[index + 1] === "\n");
        index += 1;
        finishRow();
      } else {
        field += character;
        rowDirty = true;
      }
    } else if (state === TokenizerState.QUOTED) {
      if (character === '"') state = TokenizerState.AFTER_QUOTE;
      else field += character;
      rowDirty = true;
    } else if (character === '"') {
      field += '"';
      state = TokenizerState.QUOTED;
      rowDirty = true;
    } else if (character === ",") {
      finishField();
      state = TokenizerState.FIELD;
      rowDirty = true;
    } else if (character === "\n") {
      finishRow();
      state = TokenizerState.FIELD;
    } else if (character === "\r") {
      ensure(text[index + 1] === "\n");
      index += 1;
      finishRow();
      state = TokenizerState.FIELD;
    } else failure();
  }
  ensure(state !== TokenizerState.QUOTED);
  if (rowDirty || field !== "" || row.length > 0) finishRow();
  return rows;
}

function splitCodes(value: string): string[] {
  if (value === "") return [];
  const values = value.split("|");
  ensure(values.length <= 4096);
  ensure(values.every((item) => item !== ""));
  values.forEach(code);
  ensure(new Set(values).size === values.length);
  return values;
}

function splitEligibility(value: string): Array<{ role_code: string; area_code: string }> {
  if (value === "") return [];
  const tokens = value.split("|");
  ensure(tokens.length <= 4096);
  ensure(tokens.every((item) => item !== "") && new Set(tokens).size === tokens.length);
  return tokens.map((token) => {
    const parts = token.split("@");
    ensure(parts.length === 2);
    return { role_code: code(parts[0]), area_code: code(parts[1]) };
  });
}

function cell(row: string[], name: (typeof HEADER)[number]): string {
  return row[INDEX[name]];
}

export function parseStaffingRosterCsv(
  bytes: ArrayBuffer | Uint8Array,
  filename: string,
): RosterImportDraft {
  ensure(bytes instanceof ArrayBuffer || bytes instanceof Uint8Array);
  const input = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  if (input.byteLength > 512 * 1024) tooLarge();
  ensure(typeof filename === "string");
  formulaSafe(filename);
  safeText(filename, 1, 256, true);

  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(input);
  } catch {
    failure();
  }
  if (text.startsWith("\uFEFF")) text = text.slice(1);
  ensure(!text.includes("\uFEFF"));

  const table = tokenize(text);
  ensure(table.length >= 1 && table[0].length === HEADER.length);
  ensure(table[0].every((value, index) => value === HEADER[index]));
  for (const row of table) {
    ensure(row.length === HEADER.length);
    for (const value of row) formulaSafe(value);
  }

  const workers: RosterImportRequest["workers"] = [];
  const availability: RosterImportRequest["availability"] = [];
  const assignmentRules: RosterImportRequest["assignment_rules"] = [];
  const regularAssignments: RosterImportRequest["regular_assignments"] = [];
  const coverage: RosterImportRequest["coverage"] = [];
  let meta: { timezone: string; from: string; until: string | null } | null = null;

  for (const row of table.slice(1)) {
    const rowType = cell(row, "record_type");
    const allowed = ALLOWED_FIELDS[rowType];
    ensure(allowed !== undefined);
    const allowedSet = new Set(allowed);
    HEADER.forEach((name) => {
      if (!allowedSet.has(name)) ensure(cell(row, name) === "");
    });

    if (rowType === "META") {
      ensure(meta === null);
      const timezone = safeText(cell(row, "timezone"), 1, 128, true);
      const from = localDate(cell(row, "effective_from_local_date"));
      const untilText = cell(row, "effective_until_local_date");
      meta = { timezone, from, until: untilText === "" ? null : localDate(untilText) };
    } else if (rowType === "WORKER") {
      workers.push({
        staff_id: identifier(cell(row, "staff_id")),
        display_name: safeText(cell(row, "display_name"), 1, 100, true),
        skill_codes: splitCodes(cell(row, "skill_codes")),
        eligibility: splitEligibility(cell(row, "eligibility")),
        max_daily_minutes: integer(cell(row, "max_daily_minutes"), 1, 1440),
      });
    } else if (rowType === "AVAILABILITY") {
      availability.push({
        staff_id: identifier(cell(row, "staff_id")),
        weekday: weekday(cell(row, "weekday")),
        start_local: localMinute(cell(row, "start_local")),
        end_local: localMinute(cell(row, "end_local")),
      });
    } else if (rowType === "ASSIGNMENT_RULE") {
      assignmentRules.push({
        role_code: code(cell(row, "role_code")),
        area_code: code(cell(row, "area_code")),
        required_skill_codes: splitCodes(cell(row, "required_skill_codes")),
      });
    } else if (rowType === "REGULAR_ASSIGNMENT") {
      regularAssignments.push({
        staff_id: identifier(cell(row, "staff_id")),
        weekday: weekday(cell(row, "weekday")),
        role_code: code(cell(row, "role_code")),
        area_code: code(cell(row, "area_code")),
        start_local: localMinute(cell(row, "start_local")),
        end_local: localMinute(cell(row, "end_local")),
      });
    } else if (rowType === "COVERAGE") {
      coverage.push({
        weekday: weekday(cell(row, "weekday")),
        role_code: code(cell(row, "role_code")),
        area_code: code(cell(row, "area_code")),
        start_local: localMinute(cell(row, "start_local")),
        end_local: localMinute(cell(row, "end_local")),
        minimum_staff: integer(cell(row, "minimum_staff"), 1, 10000),
      });
    }
  }

  ensure(meta !== null && workers.length >= 1 && assignmentRules.length >= 1 && coverage.length >= 1);
  ensure(workers.length <= 4096 && availability.length <= 4096 && assignmentRules.length <= 4096);
  ensure(regularAssignments.length <= 4096 && coverage.length <= 4096);
  const staffIds = new Set(workers.map((worker) => worker.staff_id));
  ensure(staffIds.size === workers.length);
  const ruleKeys = assignmentRules.map((rule) => `${rule.role_code}\u0000${rule.area_code}`);
  const rules = new Set(ruleKeys);
  ensure(rules.size === ruleKeys.length);
  ensure(availability.every((entry) => staffIds.has(entry.staff_id)));
  ensure(regularAssignments.every((entry) => staffIds.has(entry.staff_id) && rules.has(`${entry.role_code}\u0000${entry.area_code}`)));
  ensure(coverage.every((entry) => rules.has(`${entry.role_code}\u0000${entry.area_code}`)));
  ensure(workers.every((entry) => entry.eligibility.every((eligible) => rules.has(`${eligible.role_code}\u0000${eligible.area_code}`))));

  return {
    schema: "nxt-staffing-roster-import/v1",
    site_timezone: meta.timezone,
    effective_from_local_date: meta.from,
    effective_until_local_date: meta.until,
    source_ref: filename,
    workers,
    availability,
    assignment_rules: assignmentRules,
    regular_assignments: regularAssignments,
    coverage,
  };
}
