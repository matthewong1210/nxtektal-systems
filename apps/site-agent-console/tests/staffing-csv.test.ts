import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, expectTypeOf, it } from "vitest";

import { ManagerApiError } from "../lib/api";
import * as csvRuntime from "../lib/staffing-csv";
import { parseStaffingRosterCsv, type RosterImportDraft } from "../lib/staffing-csv";

const HEADER = "record_type,timezone,effective_from_local_date,effective_until_local_date,staff_id,display_name,skill_codes,eligibility,max_daily_minutes,weekday,role_code,area_code,start_local,end_local,required_skill_codes,minimum_staff";

const rows = [
  "META,Asia/Shanghai,2026-10-06,,,,,,,,,,,,,",
  "WORKER,,,,staff-001,\"Operator, \"\"One\"\"\",BALL_PICKING,\"RANGE_ATTENDANT@RANGE_A\",480,,,,,,,",
  "AVAILABILITY,,,,staff-001,,,,,1,,,08:00,17:00,,",
  "ASSIGNMENT_RULE,,,,,,,,,,RANGE_ATTENDANT,RANGE_A,,,BALL_PICKING,",
  "REGULAR_ASSIGNMENT,,,,staff-001,,,,,1,RANGE_ATTENDANT,RANGE_A,09:00,17:00,,",
  "COVERAGE,,,,,,,,,1,RANGE_ATTENDANT,RANGE_A,09:00,17:00,,1",
];

const csv = (customRows = rows, eol = "\n", final = true) =>
  [HEADER, ...customRows].join(eol) + (final ? eol : "");

const bytes = (text: string): Uint8Array => new TextEncoder().encode(text);

const parse = (text: string, filename = "manager-roster.csv") =>
  parseStaffingRosterCsv(bytes(text), filename);

describe("staffing roster CSV", () => {
  it("freezes the exact runtime/type surface", () => {
    expect(Object.keys(csvRuntime)).toEqual(["parseStaffingRosterCsv"]);
    expectTypeOf<RosterImportDraft>().toMatchTypeOf<{
      schema: "nxt-staffing-roster-import/v1";
      site_timezone: string;
      source_ref: string;
    }>();
  });

  it("normalizes every row type without inventing identity, revision, request ID or operator", () => {
    const result = parse(csv());
    expect(result).toEqual({
      schema: "nxt-staffing-roster-import/v1",
      site_timezone: "Asia/Shanghai",
      effective_from_local_date: "2026-10-06",
      effective_until_local_date: null,
      source_ref: "manager-roster.csv",
      workers: [{
        staff_id: "staff-001",
        display_name: "Operator, \"One\"",
        skill_codes: ["BALL_PICKING"],
        eligibility: [{ role_code: "RANGE_ATTENDANT", area_code: "RANGE_A" }],
        max_daily_minutes: 480,
      }],
      availability: [{ staff_id: "staff-001", weekday: 1, start_local: "08:00", end_local: "17:00" }],
      assignment_rules: [{
        role_code: "RANGE_ATTENDANT",
        area_code: "RANGE_A",
        required_skill_codes: ["BALL_PICKING"],
      }],
      regular_assignments: [{
        staff_id: "staff-001",
        weekday: 1,
        role_code: "RANGE_ATTENDANT",
        area_code: "RANGE_A",
        start_local: "09:00",
        end_local: "17:00",
      }],
      coverage: [{
        weekday: 1,
        role_code: "RANGE_ATTENDANT",
        area_code: "RANGE_A",
        start_local: "09:00",
        end_local: "17:00",
        minimum_staff: 1,
      }],
    });
    expect(result).not.toHaveProperty("request_id");
    expect(result).not.toHaveProperty("operator");
    expect(result).not.toHaveProperty("expected_roster_revision");
    expect(result).not.toHaveProperty("site_id");
    expect(result).not.toHaveProperty("deployment_id");
  });

  it("supports CRLF/LF, final newline/no final newline, ArrayBuffer, and one leading BOM", () => {
    const expected = parse(csv(rows, "\n", true));
    expect(parse(csv(rows, "\r\n", true))).toEqual(expected);
    expect(parse(csv(rows, "\n", false))).toEqual(expected);
    const encoded = bytes(`\uFEFF${csv()}`);
    expect(parseStaffingRosterCsv(new Uint8Array(encoded).buffer, "manager-roster.csv")).toEqual(expected);
  });

  it("preserves CSV and list order deterministically", () => {
    const extra = [...rows];
    extra.splice(1, 0, "WORKER,,,,staff-002,Operator Two,MAINTENANCE|BALL_PICKING,RANGE_ATTENDANT@RANGE_A,420,,,,,,,");
    const result = parse(csv(extra));
    expect(result.workers.map((worker) => worker.staff_id)).toEqual(["staff-002", "staff-001"]);
    expect(result.workers[0].skill_codes).toEqual(["MAINTENANCE", "BALL_PICKING"]);
    expect(result.workers[0].eligibility).toEqual([
      { role_code: "RANGE_ATTENDANT", area_code: "RANGE_A" },
    ]);
  });

  it("rejects malformed RFC 4180 quoting and quoted newlines that violate wire text", () => {
    const malformed = [
      csv(rows.map((row) => row.replace("Operator, \"\"One\"\"", "Operator\" One"))),
      `${HEADER}\n${rows[0]}\nWORKER,,,,staff-001,\"Operator\"x,BALL_PICKING,RANGE_ATTENDANT@RANGE_A,480,,,,,,,\n${rows.slice(2).join("\n")}\n`,
      `${HEADER}\n${rows[0]}\nWORKER,,,,staff-001,\"Operator\nOne\",BALL_PICKING,RANGE_ATTENDANT@RANGE_A,480,,,,,,,\n${rows.slice(2).join("\n")}\n`,
    ];
    for (const value of malformed) expect(() => parse(value)).toThrow(ManagerApiError);
  });

  it("rejects invalid UTF-8, misplaced/repeated BOM and input over 512 KiB", () => {
    expect(() => parseStaffingRosterCsv(Uint8Array.of(0xc3, 0x28), "safe.csv")).toThrow(ManagerApiError);
    expect(() => parseStaffingRosterCsv(new DataView(new ArrayBuffer(8)) as never, "safe.csv")).toThrow(ManagerApiError);
    expect(() => parseStaffingRosterCsv(bytes(csv()), 42 as never)).toThrow(ManagerApiError);
    expect(() => parseStaffingRosterCsv(bytes(csv()), Symbol("SECRET") as never)).toThrow(ManagerApiError);
    expect(() => parse(`${HEADER}\n\uFEFF${rows.join("\n")}`)).toThrow(ManagerApiError);
    expect(() => parse(`\uFEFF\uFEFF${csv()}`)).toThrow(ManagerApiError);
    expect(() => parseStaffingRosterCsv(new Uint8Array(512 * 1024 + 1), "safe.csv")).toThrowError(
      expect.objectContaining({ status: 413, code: "body_too_large" }),
    );
  });

  it("rejects duplicate, missing, reordered or unknown headers", () => {
    const headers = [
      HEADER.replace("display_name", "staff_id"),
      HEADER.replace(",display_name", ""),
      `${HEADER},extra`,
      HEADER.split(",").reverse().join(","),
    ];
    for (const header of headers) {
      expect(() => parse(csv().replace(HEADER, header))).toThrow(ManagerApiError);
    }
  });

  it("rejects unknown rows, duplicate workers, missing required classes and extra populated cells", () => {
    const cases = [
      [...rows, "OTHER,,,,,,,,,,,,,,,"],
      [...rows, rows[1]],
      rows.filter((row) => !row.startsWith("WORKER,")),
      rows.filter((row) => !row.startsWith("ASSIGNMENT_RULE,")),
      rows.filter((row) => !row.startsWith("COVERAGE,")),
      rows.map((row) => row.startsWith("META,") ? `${row.slice(0, -1)}unexpected` : row),
    ];
    for (const value of cases) expect(() => parse(csv(value))).toThrow(ManagerApiError);
  });

  it("rejects duplicate/empty list items and broken local references", () => {
    const cases = [
      rows.map((row) => row.replace("BALL_PICKING,\"RANGE", "BALL_PICKING|BALL_PICKING,\"RANGE")),
      rows.map((row) => row.replace("BALL_PICKING,\"RANGE", "BALL_PICKING|,\"RANGE")),
      rows.map((row) => row.replace("RANGE_ATTENDANT@RANGE_A\",480", "RANGE_ATTENDANT@RANGE_A|RANGE_ATTENDANT@RANGE_A\",480")),
      rows.map((row) => row.startsWith("AVAILABILITY,") ? row.replace("staff-001", "missing") : row),
      rows.map((row) => row.startsWith("REGULAR_ASSIGNMENT,") ? row.replace("RANGE_A", "UNKNOWN") : row),
      rows.map((row) => row.startsWith("COVERAGE,") ? row.replace("RANGE_A", "UNKNOWN") : row),
    ];
    for (const value of cases) expect(() => parse(csv(value))).toThrow(ManagerApiError);
  });

  it("accepts 4096 list items and rejects 4097 before producing a draft", () => {
    const codes = Array.from({ length: 4096 }, (_, index) => `S${String(index).padStart(4, "0")}`);
    const accepted = rows.map((row) => row.startsWith("WORKER,")
      ? row.replace("BALL_PICKING,\"RANGE", `${codes.join("|")},\"RANGE`)
      : row);
    expect(parse(csv(accepted)).workers[0].skill_codes).toHaveLength(4096);
    const rejected = rows.map((row) => row.startsWith("WORKER,")
      ? row.replace("BALL_PICKING,\"RANGE", `${[...codes, "S4096"].join("|")},\"RANGE`)
      : row);
    expect(() => parse(csv(rejected))).toThrow(ManagerApiError);
  });

  it("rejects spreadsheet formulas in every cell and in the selected filename without echoing them", () => {
    for (const prefix of ["=", "+", "-", "@", " \t="]) {
      const secret = `${prefix}VERY_PRIVATE_FORMULA`;
      const changed = rows.map((row) => row.startsWith("WORKER,") ? row.replace("staff-001", secret) : row);
      const error = (() => { try { parse(csv(changed)); } catch (cause) { return cause; } })();
      expect(error).toBeInstanceOf(ManagerApiError);
      expect(String(error)).not.toContain("VERY_PRIVATE_FORMULA");
    }
    const error = (() => { try { parse(csv(), "  =PRIVATE.csv"); } catch (cause) { return cause; } })();
    expect(error).toBeInstanceOf(ManagerApiError);
    expect(String(error)).not.toContain("PRIVATE");
  });

  it("ships a downloadable fictional template containing every row type", () => {
    const template = readFileSync(join(import.meta.dirname, "..", "public", "staffing-roster-template.csv"), "utf-8");
    expect(template.split(/\r?\n/, 1)[0]).toBe(HEADER);
    for (const rowType of ["META", "WORKER", "AVAILABILITY", "ASSIGNMENT_RULE", "REGULAR_ASSIGNMENT", "COVERAGE"]) {
      expect(template).toContain(`\n${rowType},`);
    }
    expect(parse(template, "staffing-roster-template.csv").schema).toBe("nxt-staffing-roster-import/v1");
  });
});
