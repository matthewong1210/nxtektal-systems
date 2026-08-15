import { readFileSync } from "node:fs";
import { afterAll, beforeAll, describe, expect, test } from "vitest";
import { Box3, BoxGeometry, CylinderGeometry, Group, Mesh, Vector3 } from "three";
import {
  GLTFLoader,
  type GLTF,
} from "three/addons/loaders/GLTFLoader.js";

import {
  registeredModelLoadError,
  validateLoadedModelDimensions,
} from "../../lib/edge-gateway-model/model-registry";
import { LOAD_CELL_ASSEMBLY_GEOMETRY } from "../../lib/edge-gateway-model/manifest";

const PART_ID = "fanless-edge-computer";
const originalProgressEvent = globalThis.ProgressEvent;

class NodeProgressEvent extends Event {
  readonly lengthComputable: boolean;
  readonly loaded: number;
  readonly total: number;

  constructor(type: string, init: ProgressEventInit = {}) {
    super(type);
    this.lengthComputable = init.lengthComputable ?? false;
    this.loaded = init.loaded ?? 0;
    this.total = init.total ?? 0;
  }
}

beforeAll(() => {
  if (typeof globalThis.ProgressEvent === "undefined") {
    Object.defineProperty(globalThis, "ProgressEvent", {
      configurable: true,
      writable: true,
      value: NodeProgressEvent,
    });
  }
});

afterAll(() => {
  if (originalProgressEvent === undefined) {
    delete (globalThis as unknown as { ProgressEvent?: typeof ProgressEvent })
      .ProgressEvent;
    return;
  }
  Object.defineProperty(globalThis, "ProgressEvent", {
    configurable: true,
    writable: true,
    value: originalProgressEvent,
  });
});

function validPointsGltf(): string {
  const positions = new Float32Array([
    -0.105, -0.075, -0.0325,
    0.105, 0.075, 0.0325,
  ]);
  const encodedPositions = Buffer.from(positions.buffer).toString("base64");
  return JSON.stringify({
    asset: { version: "2.0", generator: "deterministic-node-regression" },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ name: PART_ID, mesh: 0 }],
    meshes: [
      {
        primitives: [{ attributes: { POSITION: 0 }, mode: 0 }],
      },
    ],
    buffers: [
      {
        uri: `data:application/octet-stream;base64,${encodedPositions}`,
        byteLength: positions.byteLength,
      },
    ],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: positions.byteLength },
    ],
    accessors: [
      {
        bufferView: 0,
        componentType: 5126,
        count: 2,
        type: "VEC3",
        min: [-0.105, -0.075, -0.0325],
        max: [0.105, 0.075, 0.0325],
      },
    ],
  });
}

function parseGltf(source: string): Promise<GLTF> {
  return new Promise((resolve, reject) => {
    try {
      new GLTFLoader().parse(source, "", resolve, reject);
    } catch (error) {
      reject(error);
    }
  });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

describe("registered model loading boundary", () => {
  test("wraps registered model suspension inside the fail-loud error boundary", () => {
    const source = readFileSync(
      new URL(
        "../../components/edge-gateway-3d/GatewayCanvas.tsx",
        import.meta.url,
      ),
      "utf8",
    );

    expect(source).toMatch(
      /<ModelErrorBoundary[\s\S]*?>\s*<Suspense[\s\S]*?fallback=\{[\s\S]*?<RegisteredModelLoadingMarker[\s\S]*?\}[\s\S]*?>\s*<RegisteredGatewayPart[\s\S]*?\/>\s*<\/Suspense>\s*<\/ModelErrorBoundary>/,
    );
  });

  test("parses an in-memory points asset and accepts its meter-space bounds", async () => {
    const gltf = await parseGltf(validPointsGltf());
    const points = gltf.scene.getObjectByName(PART_ID);
    expect(points?.type).toBe("Points");

    const size = new Box3().setFromObject(gltf.scene).getSize(new Vector3());
    const actualDimensionsMeters = [size.x, size.y, size.z] as const;
    expect(actualDimensionsMeters[0]).toBeCloseTo(0.21);
    expect(actualDimensionsMeters[1]).toBeCloseTo(0.15);
    expect(actualDimensionsMeters[2]).toBeCloseTo(0.065);
    expect(
      validateLoadedModelDimensions(PART_ID, actualDimensionsMeters),
    ).toEqual({ valid: true });
  });

  test("maps rejected parsed bounds to a visible no-fallback error", () => {
    const validation = validateLoadedModelDimensions(PART_ID, [2.1, 1.5, 0.65]);
    expect(validation.valid).toBe(false);
    if (validation.valid) {
      throw new Error("expected wrong-scale model bounds to be rejected");
    }

    expect(registeredModelLoadError(PART_ID, validation.message)).toMatchObject({
      kind: "error",
      partId: PART_ID,
      visible: true,
      fallbackAllowed: false,
      message: expect.stringContaining("do not match"),
    });
  });

  test("maps malformed glTF parsing to a visible no-fallback error", async () => {
    const failure = await parseGltf('{"asset":')
      .then(() => null)
      .catch((error: unknown) =>
        registeredModelLoadError(PART_ID, errorMessage(error)),
      );

    expect(failure).toMatchObject({
      kind: "error",
      partId: PART_ID,
      visible: true,
      fallbackAllowed: false,
      message: expect.stringContaining(`Registered model for ${PART_ID}`),
    });
  });

  test("keeps the procedural load-cell assembly inside its declared envelope", () => {
    const specification = LOAD_CELL_ASSEMBLY_GEOMETRY;
    const assembly = new Group();
    const geometries = [];
    const addBox = (
      dimensions: readonly [number, number, number],
      position: readonly [number, number, number],
    ) => {
      const geometry = new BoxGeometry(...dimensions);
      geometries.push(geometry);
      const mesh = new Mesh(geometry);
      mesh.position.set(...position);
      assembly.add(mesh);
    };

    const hopperGeometry = new CylinderGeometry(
      specification.hopper.radiusTopMeters,
      specification.hopper.radiusBottomMeters,
      specification.hopper.heightMeters,
      4,
      1,
      true,
    );
    geometries.push(hopperGeometry);
    const hopper = new Mesh(hopperGeometry);
    hopper.position.set(...specification.hopper.positionMeters);
    assembly.add(hopper);
    addBox(
      specification.plate.dimensionsMeters,
      specification.plate.positionMeters,
    );
    for (const position of specification.loadCellPositionsMeters) {
      addBox(specification.loadCellDimensionsMeters, position);
    }
    addBox(
      specification.summingJunction.dimensionsMeters,
      specification.summingJunction.positionMeters,
    );
    addBox(
      specification.transmitter.dimensionsMeters,
      specification.transmitter.positionMeters,
    );

    const size = new Box3().setFromObject(assembly).getSize(new Vector3());
    const dimensions = size.toArray();
    expect(dimensions[0]).toBeCloseTo(0.36, 6);
    expect(dimensions[1]).toBeCloseTo(0.32, 6);
    expect(dimensions[2]).toBeCloseTo(0.18, 6);
    expect(
      validateLoadedModelDimensions("load-cell-interface", dimensions),
    ).toEqual({ valid: true });

    for (const geometry of geometries) geometry.dispose();
  });
});
