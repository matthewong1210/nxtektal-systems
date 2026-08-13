import { GATEWAY_PART_BY_ID } from "./manifest";
import type {
  DimensionsMm,
  ModelRegistryEntry,
  ResolvedModel,
} from "./types";

export type ModelRegistry = Readonly<Record<string, unknown>>;

export type ModelRegistryValidation =
  | Readonly<{ valid: true; entry: ModelRegistryEntry }>
  | Readonly<{ valid: false; message: string }>;

export type LoadedModelDimensionsValidation =
  | Readonly<{ valid: true }>
  | Readonly<{ valid: false; message: string }>;

/**
 * No binary model is currently registered. Missing entries deliberately use
 * repository-owned procedural geometry.
 */
export const MODEL_REGISTRY: ModelRegistry = Object.freeze({});

type VisibleModelError = Extract<ResolvedModel, { kind: "error" }>;

function visibleModelError(partId: string, message: string): VisibleModelError {
  return {
    kind: "error",
    partId,
    visible: true,
    fallbackAllowed: false,
    message,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validateSameOriginModelPath(
  sourcePath: unknown,
  format: "glb" | "gltf",
): string | null {
  if (typeof sourcePath !== "string" || !sourcePath) {
    return "sourcePath must be a non-empty string";
  }
  if (
    !sourcePath.startsWith("/models/edge-gateway/") ||
    sourcePath.startsWith("//") ||
    sourcePath.includes("\\") ||
    sourcePath.includes("?") ||
    sourcePath.includes("#") ||
    !sourcePath.endsWith(`.${format}`)
  ) {
    return `sourcePath must be a root-relative .${format} path under /models/edge-gateway/`;
  }
  let decoded: string;
  try {
    decoded = decodeURIComponent(sourcePath);
  } catch {
    return "sourcePath contains malformed percent encoding";
  }
  if (
    decoded.split("/").some((segment) => segment === "." || segment === "..") ||
    decoded.includes("\\")
  ) {
    return "sourcePath must not contain traversal segments";
  }
  return null;
}

function validateDimensions(
  dimensions: unknown,
  expected: DimensionsMm,
): dimensions is DimensionsMm {
  return (
    Array.isArray(dimensions) &&
    dimensions.length === 3 &&
    dimensions.every(
      (value, index) =>
        typeof value === "number" &&
        Number.isFinite(value) &&
        value > 0 &&
        value === expected[index],
    )
  );
}

export function validateModelRegistryEntry(
  expectedPartId: string,
  candidate: unknown,
): ModelRegistryValidation {
  const part = GATEWAY_PART_BY_ID.get(expectedPartId);
  if (!part) {
    return { valid: false, message: `Unknown Gateway part: ${expectedPartId}` };
  }
  if (!isRecord(candidate)) {
    return {
      valid: false,
      message: `Registered model for ${expectedPartId} must be an object`,
    };
  }
  if (candidate.partId !== expectedPartId || candidate.componentId !== expectedPartId) {
    return {
      valid: false,
      message: `Registered model must preserve stable component ID ${expectedPartId}`,
    };
  }
  if (candidate.format !== "glb" && candidate.format !== "gltf") {
    return { valid: false, message: `${expectedPartId}: format must be glb or gltf` };
  }
  const pathError = validateSameOriginModelPath(
    candidate.sourcePath,
    candidate.format,
  );
  if (pathError) {
    return { valid: false, message: `${expectedPartId}: ${pathError}` };
  }
  if (candidate.metersPerUnit !== 1) {
    return {
      valid: false,
      message: `${expectedPartId}: metersPerUnit must remain 1`,
    };
  }
  if (
    !validateDimensions(
      candidate.approximateDimensionsMm,
      part.approximateDimensionsMm,
    )
  ) {
    return {
      valid: false,
      message: `${expectedPartId}: approximate dimensions must match the conceptual manifest`,
    };
  }
  if (
    typeof candidate.provenance !== "string" ||
    candidate.provenance.trim().length === 0
  ) {
    return {
      valid: false,
      message: `${expectedPartId}: provenance must be a non-empty string`,
    };
  }

  return {
    valid: true,
    entry: candidate as unknown as ModelRegistryEntry,
  };
}

export function resolveModel(
  partId: string,
  registeredEntry?: unknown,
): ResolvedModel {
  if (!GATEWAY_PART_BY_ID.has(partId)) {
    return visibleModelError(partId, `Unknown Gateway part: ${partId}`);
  }
  if (registeredEntry === undefined) {
    return {
      kind: "procedural",
      partId,
      reason: "asset-not-registered",
    };
  }
  const validation = validateModelRegistryEntry(partId, registeredEntry);
  if (!validation.valid) {
    return visibleModelError(partId, validation.message);
  }
  return {
    kind: "glb",
    partId,
    componentId: validation.entry.componentId,
    sourcePath: validation.entry.sourcePath,
    format: validation.entry.format,
    metersPerUnit: validation.entry.metersPerUnit,
    provenance: validation.entry.provenance,
  };
}

export function resolvePartModel(
  partId: string,
  registry: ModelRegistry = MODEL_REGISTRY,
): ResolvedModel {
  if (!Object.hasOwn(registry, partId)) {
    return resolveModel(partId);
  }
  if (registry[partId] === undefined) {
    return visibleModelError(
      partId,
      `Registered model for ${partId} is malformed: entry is undefined`,
    );
  }
  return resolveModel(partId, registry[partId]);
}

/**
 * Validate parsed model bounds at the Three.js meter boundary. A small
 * tolerance permits normal exporter quantization without accepting a model at
 * an ambiguous scale.
 */
export function validateLoadedModelDimensions(
  partId: string,
  actualDimensionsMeters: readonly number[],
): LoadedModelDimensionsValidation {
  const part = GATEWAY_PART_BY_ID.get(partId);
  if (!part) {
    return { valid: false, message: `Unknown Gateway part: ${partId}` };
  }
  if (actualDimensionsMeters.length !== 3) {
    return {
      valid: false,
      message: `loaded bounds for ${partId} must contain three meter dimensions`,
    };
  }
  const valid = part.approximateDimensionsMm.every((millimeters, index) => {
    const expectedMeters = millimeters / 1_000;
    const actual = actualDimensionsMeters[index];
    return (
      Number.isFinite(actual) &&
      actual > 0 &&
      Math.abs(actual - expectedMeters) <= Math.max(0.002, expectedMeters * 0.05)
    );
  });
  if (!valid) {
    const loaded = actualDimensionsMeters
      .map((value) => (Number.isFinite(value) ? value.toFixed(4) : String(value)))
      .join(" × ");
    return {
      valid: false,
      message: `loaded bounds ${loaded} m do not match the registered approximate dimensions for ${partId}`,
    };
  }
  return { valid: true };
}

/** Convert an actual GLB parse/load failure into a visible, non-fallback state. */
export function registeredModelLoadError(
  partId: string,
  reason: string,
): VisibleModelError {
  const detail = reason.trim() || "unknown GLB load failure";
  return visibleModelError(
    partId,
    `Registered model for ${partId} could not be used: ${detail}`,
  );
}
