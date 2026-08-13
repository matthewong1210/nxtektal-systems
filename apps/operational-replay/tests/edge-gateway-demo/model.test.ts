import { describe, expect, test } from "vitest";

import replayExcerpt from "../../lib/edge-gateway-model/fixtures/normal-weekday-inventory-threshold-seed-101.json";

import {
  CONCEPTUAL_NOT_IMPLEMENTED,
  ILLUSTRATIVE_OPERATIONAL_SCENARIO,
  activateEmergencyStop,
  addFleetDevice,
  completeConceptualUpdate,
  createInitialGatewayDemoState,
  failConceptualUpdateHealthCheck,
  gatewayDemoReducer,
  recordManagerResponse,
  requestCameraWorkload,
  resetDemoState,
  selectPart,
  setCameraPreset,
  setCameraProjection,
  setCutaway,
  setDimensionsVisible,
  setDoorOpen,
  setExplodeAmount,
  setLabelsVisible,
  setLayerVisibility,
  setScene,
  setTransparentEnclosure,
  startConceptualUpdate,
} from "../../lib/edge-gateway-model/demo-state";
import {
  GATEWAY_ID,
  GATEWAY_PARTS,
  GATEWAY_PART_BY_ID,
  GATEWAY_WORLD_SCALE_METERS_PER_UNIT,
  INSTALLATION_INTERFACES,
  dimensionsMeters,
  gatewayPartPosition,
  millimetersToMeters,
} from "../../lib/edge-gateway-model/manifest";
import {
  MODEL_REGISTRY,
  registeredModelLoadError,
  resolveModel,
  resolvePartModel,
  validateModelRegistryEntry,
} from "../../lib/edge-gateway-model/model-registry";
import {
  PRESENTATION_DURATION_SECONDS,
  PRESENTATION_SEGMENTS,
  advancePresentation,
  createPresentationState,
  pausePresentation,
  presentationSegmentAt,
  restartPresentation,
  resumePresentation,
  stepPresentation,
} from "../../lib/edge-gateway-model/presentation";
import { SCENE_IDS, type GatewayDemoAction } from "../../lib/edge-gateway-model/types";

describe("conceptual Gateway model manifest", () => {
  test("keeps stable component IDs and approximate dimensions in one manifest", () => {
    expect(GATEWAY_PARTS.map((part) => part.id)).toEqual([
      "gateway-enclosure",
      "enclosure-door",
      "internal-backplate",
      "din-rails",
      "fanless-edge-computer",
      "industrial-lte-router",
      "remote-io-module",
      "ups-power-system",
      "surge-protection",
      "dc-power-supply",
      "ethernet-switch",
      "terminal-blocks",
      "structured-wiring",
      "cable-glands",
      "load-cell-interface",
      "vision-node",
      "local-safety-controller",
    ]);
    expect(new Set(GATEWAY_PARTS.map((part) => part.id)).size).toBe(
      GATEWAY_PARTS.length,
    );
    for (const part of GATEWAY_PARTS) {
      expect(part.notForFabrication).toBe(true);
      expect(part.approximateDimensionsMm).toHaveLength(3);
      expect(part.approximateDimensionsMm.every((value) => value > 0)).toBe(true);
      expect(part.installedPosition).toHaveLength(3);
      expect(part.explodedPosition).toHaveLength(3);
      expect(part.description).toMatch(/concept/i);
      expect(part.connections.every((connection) =>
        connection.implementationStatus === "conceptual-connection",
      )).toBe(true);
    }
  });

  test("uses meters at the Three.js boundary and interpolates explosion deterministically", () => {
    expect(GATEWAY_WORLD_SCALE_METERS_PER_UNIT).toBe(1);
    expect(millimetersToMeters(600)).toBe(0.6);
    expect(dimensionsMeters([600, 800, 220])).toEqual([0.6, 0.8, 0.22]);

    const computer = GATEWAY_PART_BY_ID.get("fanless-edge-computer");
    expect(computer).toBeDefined();
    expect(computer?.installedPosition).toEqual([-0.12, 0.08, -0.035]);
    expect(gatewayPartPosition(computer!, 0)).toEqual(computer?.installedPosition);
    expect(gatewayPartPosition(computer!, 1)).toEqual(computer?.explodedPosition);
    const halfway = gatewayPartPosition(computer!, 0.5);
    expect(halfway[0]).toBeCloseTo(-0.405);
    expect(halfway[1]).toBeCloseTo(0.095);
    expect(halfway[2]).toBeCloseTo(0.0225);
    expect(gatewayPartPosition(computer!, Number.NaN)).toEqual(
      computer?.installedPosition,
    );
  });

  test("keeps conceptual connections resolvable without inventing external facts", () => {
    const internalIds = new Set<string>(GATEWAY_PARTS.map((part) => part.id));
    for (const part of GATEWAY_PARTS) {
      for (const connection of part.connections) {
        expect(
          internalIds.has(connection.targetId) ||
            connection.targetId.startsWith("external:"),
          `${part.id} -> ${connection.targetId}`,
        ).toBe(true);
      }
    }
    const remoteIo = GATEWAY_PART_BY_ID.get("remote-io-module");
    expect(remoteIo?.description).toMatch(/not part of the emergency-stop/i);
    expect(remoteIo?.layers).not.toContain("safety");
    expect(GATEWAY_PART_BY_ID.get("vision-node")?.optional).toBe(true);
    expect(GATEWAY_PART_BY_ID.get("load-cell-interface")?.optional).toBe(true);
  });

  test("keeps installed service-area interfaces centralized and explicitly illustrative", () => {
    expect(INSTALLATION_INTERFACES.map((item) => item.id)).toEqual([
      "existing-washer",
      "dispenser",
      "universal-handoff",
      "range-outfield",
      "facility-network",
      "protected-power",
    ]);
    for (const item of INSTALLATION_INTERFACES) {
      expect(item.notForFabrication).toBe(true);
      expect(item.approximateDimensionsMm).toHaveLength(3);
      expect(item.installedPosition).toHaveLength(3);
      expect(item.description).toMatch(/concept|illustrative|simplified|visual/i);
    }
    const loadCell = GATEWAY_PART_BY_ID.get("load-cell-interface")!;
    expect(loadCell.description).toMatch(/hopper load cells/i);
    expect(loadCell.description).toMatch(/summing junction/i);
    expect(loadCell.description).toMatch(/Modbus weighing transmitter/i);
    expect(loadCell.description).toMatch(/non-canonical estimates/i);
  });

  test("routes the structured-wiring envelope directly from the cable-gland entry", () => {
    const wiring = GATEWAY_PART_BY_ID.get("structured-wiring")!;
    const glands = GATEWAY_PART_BY_ID.get("cable-glands")!;
    const wiringBottom =
      wiring.installedPosition[1] - wiring.approximateDimensionsMm[1] / 2_000;
    const glandTop =
      glands.installedPosition[1] + glands.approximateDimensionsMm[1] / 2_000;

    expect(wiringBottom).toBeCloseTo(glandTop, 6);
  });
});

describe("deterministic demo state", () => {
  test("starts with explicit product-truth and inactive execution gaps", () => {
    const state = createInitialGatewayDemoState();
    expect(state.gatewayIdentity).toBe(GATEWAY_ID);
    expect(state.productTruth).toEqual({
      conceptualVisualization: true,
      notForFabrication: true,
      illustrativeSimulation: true,
      liveCustomerData: false,
      canonicalFacilityStateSchema: false,
      canonicalRecommendation: false,
      robotMotionEvidence: false,
      robotControlAvailable: false,
    });
    expect(ILLUSTRATIVE_OPERATIONAL_SCENARIO.source).toBe(
      "user-specified-illustrative-storyboard",
    );
    expect(ILLUSTRATIVE_OPERATIONAL_SCENARIO.liveCustomerData).toBe(false);
    expect(ILLUSTRATIVE_OPERATIONAL_SCENARIO.canonicalRecommendation).toBe(false);
    expect(
      state.conceptualExecutionBoundaries.every(
        (boundary) =>
          boundary.implementationStatus === CONCEPTUAL_NOT_IMPLEMENTED &&
          boundary.active === false &&
          boundary.presentationOnly,
      ),
    ).toBe(true);
  });

  test("transitions through all six scenes without hidden side effects", () => {
    let state = createInitialGatewayDemoState();
    for (const scene of SCENE_IDS) {
      state = gatewayDemoReducer(state, setScene(scene));
      expect(state.scene).toBe(scene);
    }
    const beforeInvalid = state;
    state = gatewayDemoReducer(
      state,
      { type: "scene/set", scene: "unknown" } as unknown as GatewayDemoAction,
    );
    expect(state).toBe(beforeInvalid);
  });

  test("handles selection, layers, cameras, explode, door, and cutaway controls", () => {
    let state = createInitialGatewayDemoState();
    state = gatewayDemoReducer(state, selectPart("fanless-edge-computer"));
    state = gatewayDemoReducer(state, setLayerVisibility("telemetry", false));
    state = gatewayDemoReducer(state, setCameraPreset("top"));
    state = gatewayDemoReducer(state, setCameraProjection("orthographic"));
    state = gatewayDemoReducer(state, setExplodeAmount(1.7));
    state = gatewayDemoReducer(state, setDoorOpen(true));
    state = gatewayDemoReducer(state, setTransparentEnclosure(true));
    state = gatewayDemoReducer(state, setCutaway(true));
    state = gatewayDemoReducer(state, setDimensionsVisible(false));
    state = gatewayDemoReducer(state, setLabelsVisible(false));

    expect(state).toMatchObject({
      selectedPartId: "fanless-edge-computer",
      cameraPreset: "top",
      cameraProjection: "orthographic",
      explodeAmount: 1,
      doorOpen: true,
      transparentEnclosure: true,
      cutaway: true,
      showDimensions: false,
      showLabels: false,
    });
    expect(state.layerVisibility.telemetry).toBe(false);

    const invalidSelection = gatewayDemoReducer(state, selectPart("not-a-part"));
    expect(invalidSelection).toBe(state);
    expect(
      gatewayDemoReducer(state, setExplodeAmount(Number.NaN)).explodeAmount,
    ).toBe(1);
    expect(gatewayDemoReducer(state, setExplodeAmount(-2)).explodeAmount).toBe(0);
  });

  test("records manager acceptance as immutable workflow evidence without commands", () => {
    const initial = createInitialGatewayDemoState();
    const accepted = gatewayDemoReducer(initial, recordManagerResponse("accept"));

    expect(accepted.managerWorkflow).toMatchObject({
      status: "accepted-recorded",
      response: "accept",
      recorded: true,
      evidenceSequence: 1,
      commandsIssued: false,
      executionAuthorized: false,
    });
    expect(accepted.managerWorkflow.note).toMatch(/evidence only/i);
    expect(accepted.managerWorkflow.note).toMatch(/no command/i);
    expect(accepted.conceptualExecutionBoundaries).toEqual(
      initial.conceptualExecutionBoundaries,
    );
    expect(
      accepted.conceptualExecutionBoundaries.every((boundary) => !boundary.active),
    ).toBe(true);

    const attemptedOverwrite = gatewayDemoReducer(
      accepted,
      recordManagerResponse("decline"),
    );
    expect(attemptedOverwrite).toBe(accepted);
  });

  test("adds illustrative fleet devices without replacing the Gateway", () => {
    let state = createInitialGatewayDemoState();
    const gatewayIdentity = state.gatewayIdentity;
    state = gatewayDemoReducer(state, addFleetDevice("picker"));
    state = gatewayDemoReducer(state, addFleetDevice("picker"));
    state = gatewayDemoReducer(state, addFleetDevice("carrier"));

    expect(state.gatewayIdentity).toBe(gatewayIdentity);
    expect(state.fleetDevices.map((device) => device.id)).toEqual([
      "concept-picker-01",
      "concept-picker-02",
      "concept-carrier-01",
    ]);
    for (const device of state.fleetDevices) {
      expect(device.gatewayIdentity).toBe(gatewayIdentity);
      expect(device.connectedToLiveFacility).toBe(false);
      expect(device.capabilitySource).toBe(
        "user-specified-illustrative-storyboard",
      );
      expect(device.onboarding).toHaveLength(5);
      expect(
        device.onboarding.every(
          (step) =>
            step.implementationStatus === "conceptual-target-not-implemented",
        ),
      ).toBe(true);
    }
  });

  test("recommends one separate Vision Node for illustrative camera workload", () => {
    let state = createInitialGatewayDemoState();
    const gatewayIdentity = state.gatewayIdentity;
    state = gatewayDemoReducer(state, requestCameraWorkload());
    state = gatewayDemoReducer(state, requestCameraWorkload());

    expect(state.cameraWorkloadRequested).toBe(true);
    expect(state.dedicatedVisionNodeRecommended).toBe(true);
    expect(state.gatewayIdentity).toBe(gatewayIdentity);
    expect(
      state.fleetDevices.filter((device) => device.kind === "vision-node"),
    ).toHaveLength(1);
  });

  test("illustrates successful software activation without changing hardware", () => {
    let state = createInitialGatewayDemoState();
    state = gatewayDemoReducer(state, startConceptualUpdate("0.3.2"));
    expect(state.update.phase).toBe("staged");
    expect(state.update.signatureVerificationIllustrated).toBe(true);
    expect(state.update.digestVerificationIllustrated).toBe(true);
    expect(state.update.previousVersionRetained).toBe(true);

    state = gatewayDemoReducer(state, completeConceptualUpdate());
    expect(state.update).toMatchObject({
      implementationStatus: "conceptual-target-not-implemented",
      phase: "activated",
      activeEdgeRuntimeVersion: "0.3.2",
      previousEdgeRuntimeVersion: "0.3.1",
      healthCheck: "passed",
      softwareChangedInIllustration: true,
      gatewayHardwareChanged: false,
    });
  });

  test("illustrates a failed health check restoring the retained version", () => {
    let state = createInitialGatewayDemoState();
    state = gatewayDemoReducer(state, startConceptualUpdate("0.3.2"));
    state = gatewayDemoReducer(state, failConceptualUpdateHealthCheck());

    expect(state.update).toMatchObject({
      phase: "rolled-back",
      activeEdgeRuntimeVersion: "0.3.1",
      previousEdgeRuntimeVersion: "0.3.1",
      targetEdgeRuntimeVersion: "0.3.2",
      healthCheck: "failed",
      rollbackReportRecorded: true,
      softwareChangedInIllustration: false,
      gatewayHardwareChanged: false,
    });
    expect(gatewayDemoReducer(state, completeConceptualUpdate())).toBe(state);
  });

  test("rolls a later candidate back to the successfully retained active version", () => {
    let state = createInitialGatewayDemoState();
    state = gatewayDemoReducer(state, startConceptualUpdate("0.3.2"));
    state = gatewayDemoReducer(state, completeConceptualUpdate());
    state = gatewayDemoReducer(state, startConceptualUpdate("0.3.3-candidate"));
    state = gatewayDemoReducer(state, failConceptualUpdateHealthCheck());

    expect(state.update).toMatchObject({
      phase: "rolled-back",
      activeEdgeRuntimeVersion: "0.3.2",
      previousEdgeRuntimeVersion: "0.3.2",
      targetEdgeRuntimeVersion: "0.3.3-candidate",
      healthCheck: "failed",
      rollbackReportRecorded: true,
    });
  });

  test("keeps the emergency-stop illustration independent and non-actuating", () => {
    const state = gatewayDemoReducer(
      createInitialGatewayDemoState(),
      activateEmergencyStop(),
    );
    expect(state.safetyPath.demonstrationActive).toBe(true);
    expect(state.safetyPath.path).toEqual([
      "Emergency Stop",
      "Safety Relay / Robot Safety Controller",
      "Motor and mechanism power",
    ]);
    expect(state.safetyPath.bypassedSystems).toContain("Agent");
    expect(state.safetyPath.bypassedSystems).toContain("normal remote I/O");
    expect(state.safetyPath.independentFromAgent).toBe(true);
    expect(state.safetyPath.agentCanBypass).toBe(false);
    expect(state.safetyPath.physicalSignalIssued).toBe(false);
  });

  test("restart returns a fresh deterministic initial state", () => {
    let state = gatewayDemoReducer(
      createInitialGatewayDemoState(),
      addFleetDevice("sensor"),
    );
    state = gatewayDemoReducer(state, resetDemoState());
    expect(state).toEqual(createInitialGatewayDemoState());
    expect(state.fleetDevices).toEqual([]);
  });
});

describe("75-second deterministic presentation", () => {
  test("covers all six scenes in contiguous segments ending at 75 seconds", () => {
    expect(PRESENTATION_DURATION_SECONDS).toBe(75);
    expect(PRESENTATION_SEGMENTS[0].startSecond).toBe(0);
    expect(PRESENTATION_SEGMENTS.at(-1)?.endSecond).toBe(75);
    for (let index = 1; index < PRESENTATION_SEGMENTS.length; index += 1) {
      expect(PRESENTATION_SEGMENTS[index].startSecond).toBe(
        PRESENTATION_SEGMENTS[index - 1].endSecond,
      );
    }
    expect(new Set(PRESENTATION_SEGMENTS.map((segment) => segment.scene))).toEqual(
      new Set(SCENE_IDS),
    );
    expect(
      PRESENTATION_SEGMENTS.find(
        (segment) => segment.cue === "record-manager-workflow-evidence",
      )?.title,
    ).toMatch(/no command/i);
  });

  test("selects boundary segments deterministically", () => {
    expect(presentationSegmentAt(-1).id).toBe("installed-overview");
    expect(presentationSegmentAt(7.999).id).toBe("installed-overview");
    expect(presentationSegmentAt(8).id).toBe("open-enclosure");
    expect(presentationSegmentAt(31).id).toBe("manager-evidence");
    expect(presentationSegmentAt(75).id).toBe("final-overview");
    expect(presentationSegmentAt(Number.NaN).id).toBe("installed-overview");
  });

  test("supports pause, resume, restart, advance, and manual stepping", () => {
    let presentation = createPresentationState();
    presentation = advancePresentation(presentation, 15);
    expect(presentation).toMatchObject({
      elapsedSeconds: 15,
      segmentIndex: 2,
      playing: true,
      complete: false,
    });

    presentation = pausePresentation(presentation);
    const paused = presentation;
    expect(advancePresentation(presentation, 10)).toBe(paused);
    presentation = resumePresentation(presentation);
    presentation = stepPresentation(presentation, "next");
    expect(presentation.elapsedSeconds).toBe(20);
    expect(presentation.playing).toBe(false);
    presentation = stepPresentation(presentation, "previous");
    expect(presentation.elapsedSeconds).toBe(14);

    presentation = resumePresentation(presentation);
    presentation = advancePresentation(presentation, 1_000);
    expect(presentation).toMatchObject({
      elapsedSeconds: 75,
      playing: false,
      complete: true,
    });
    expect(restartPresentation(false)).toEqual({
      elapsedSeconds: 0,
      segmentIndex: 0,
      playing: false,
      complete: false,
    });
  });
});

describe("optional same-origin GLB registry", () => {
  const partId = "fanless-edge-computer";
  const dimensions = GATEWAY_PART_BY_ID.get(partId)!.approximateDimensionsMm;
  const validEntry = {
    partId,
    componentId: partId,
    sourcePath: "/models/edge-gateway/fanless-edge-computer.glb",
    format: "glb",
    metersPerUnit: 1,
    approximateDimensionsMm: dimensions,
    provenance: "Repository-cleared approved CAD export",
  } as const;

  test("uses procedural geometry only when no asset is registered", () => {
    expect(MODEL_REGISTRY).toEqual({});
    expect(resolvePartModel(partId)).toEqual({
      kind: "procedural",
      partId,
      reason: "asset-not-registered",
    });
  });

  test("resolves a valid same-origin GLB while preserving scale and ID", () => {
    expect(validateModelRegistryEntry(partId, validEntry)).toEqual({
      valid: true,
      entry: validEntry,
    });
    expect(resolveModel(partId, validEntry)).toEqual({
      kind: "glb",
      partId,
      componentId: partId,
      sourcePath: validEntry.sourcePath,
      format: "glb",
      metersPerUnit: 1,
      provenance: validEntry.provenance,
    });
  });

  test("turns malformed registered metadata into a visible no-fallback error", () => {
    for (const candidate of [
      { ...validEntry, componentId: "different-component" },
      { ...validEntry, sourcePath: "https://example.com/model.glb" },
      { ...validEntry, sourcePath: "/models/edge-gateway/model.obj" },
      { ...validEntry, sourcePath: "/models/edge-gateway/%2e%2e/model.glb" },
      { ...validEntry, metersPerUnit: 0.001 },
      { ...validEntry, approximateDimensionsMm: [1, 2, 3] },
    ]) {
      const resolved = resolveModel(partId, candidate);
      expect(resolved.kind).toBe("error");
      expect(resolved).toMatchObject({
        partId,
        visible: true,
        fallbackAllowed: false,
      });
    }
    expect(resolvePartModel(partId, { [partId]: undefined })).toMatchObject({
      kind: "error",
      visible: true,
      fallbackAllowed: false,
    });
  });

  test("accepts an explicitly registered same-origin glTF asset", () => {
    const gltfEntry = {
      ...validEntry,
      sourcePath: "/models/edge-gateway/fanless-edge-computer.gltf",
      format: "gltf",
    } as const;
    expect(resolveModel(partId, gltfEntry)).toMatchObject({
      kind: "glb",
      sourcePath: gltfEntry.sourcePath,
      format: "gltf",
      metersPerUnit: 1,
    });
  });

  test("keeps actual registered GLB load failures visible", () => {
    expect(registeredModelLoadError(partId, "missing scene root")).toEqual({
      kind: "error",
      partId,
      visible: true,
      fallbackAllowed: false,
      message:
        "Registered model for fanless-edge-computer could not be used: missing scene root",
    });
    expect(resolveModel("unknown-part")).toMatchObject({
      kind: "error",
      visible: true,
      fallbackAllowed: false,
    });
  });
});

describe("checked-in deterministic RangeOps replay excerpt", () => {
  test("preserves its source identity and separate SafetyShield evidence", () => {
    expect(replayExcerpt).toMatchObject({
      schema: "nxt-edge-gateway-demo/replay-excerpt/v1",
      source: {
        schema: "nxt-range-viewer/episode/v1",
        scenario: "normal_weekday",
        policy: "inventory_threshold",
        policyVersion: "0.5.0",
        seed: 101,
        gitCommit: "f5ae9e1",
        episodeSteps: 960,
        episodeSha256:
          "ef426d0b0fbbe03f45461c68362431a0be17474285c3da46530eb8d3e5cd9108",
      },
    });
    expect(replayExcerpt.frames.map((frame) => frame.step)).toEqual([1, 282, 960]);
    expect(replayExcerpt.frames.every((frame) => frame.safetyShieldAllowed)).toBe(true);
    expect(replayExcerpt.frames[1]).toMatchObject({
      directive: "send_to_handoff(R1)",
      robotId: "R1",
      robotActivity: "unloading",
      robotLocation: "station:H1",
    });
  });
});
