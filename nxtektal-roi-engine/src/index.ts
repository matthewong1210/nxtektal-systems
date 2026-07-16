export { calculateAssessment, calculateScenario, computeCoreValue } from "./engine/calculate.js";
export { buildQuickEstimateSnapshot } from "./adapters/quickEstimate.js";
export type { QuickEstimateInputs, LaborDisposition } from "./adapters/quickEstimate.js";
export { assessmentSnapshotSchema } from "./schema/snapshot.js";
export { EngineValidationError, SUPPORTED_MODEL_VERSION } from "./engine/validate.js";
export { RevenueDedupError } from "./engine/revenue.js";
export { InputRegistryError } from "./engine/scenario.js";
export type * from "./types/inputs.js";
export type * from "./types/outputs.js";
export type {
  EvidenceValue,
  NumericInput,
  Scenario,
  InputStatus,
  SourceType,
  ScenarioDirection,
} from "./types/evidence.js";
