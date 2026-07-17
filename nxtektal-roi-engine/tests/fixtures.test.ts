/** The published example files must stay valid and reproduce the engine's output. */

import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { assessmentSnapshotSchema, calculateScenario } from "../src/index.js";
import type { AssessmentSnapshot } from "../src/types/inputs.js";

const examplesDir = join(dirname(fileURLToPath(import.meta.url)), "..", "examples");

describe("published example fixtures", () => {
  const rawInput = JSON.parse(readFileSync(join(examplesDir, "section11-input.json"), "utf8"));
  const rawOutput = JSON.parse(readFileSync(join(examplesDir, "section11-output.json"), "utf8"));

  it("section11-input.json passes the strict input schema", () => {
    expect(() => assessmentSnapshotSchema.parse(rawInput)).not.toThrow();
  });

  it("engine output matches section11-output.json outputs exactly", () => {
    const result = calculateScenario(rawInput as AssessmentSnapshot, "expected");
    expect(result.outputs).toEqual(rawOutput.outputs);
    expect(result.raw).toEqual(rawOutput.raw);
    expect(result.multi_year).toEqual(rawOutput.multi_year);
  });
});
