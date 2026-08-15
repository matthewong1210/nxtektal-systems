import { GATEWAY_ID, GATEWAY_PART_BY_ID } from "./manifest";
import {
  CAMERA_PRESETS,
  GATEWAY_LAYERS,
  SCENE_IDS,
  type CameraPreset,
  type CameraProjection,
  type FleetDevice,
  type FleetDeviceKind,
  type GatewayDemoAction,
  type GatewayDemoState,
  type GatewayLayer,
  type ManagerResponse,
  type SceneId,
} from "./types";

export const CONCEPTUAL_NOT_IMPLEMENTED = "not-implemented" as const;

export const ILLUSTRATIVE_OPERATIONAL_SCENARIO = {
  id: "pilot-course-a-evening-demand-spike",
  label: "Pilot Course A — Evening Demand Spike",
  source: "user-specified-illustrative-storyboard",
  simulationOnly: true,
  liveCustomerData: false,
  canonicalFacilityState: false,
  canonicalRecommendation: false,
  robotMotionEvidence: false,
  recommendation:
    "Dispatch Picker R1 to Zone Z3 and stage Carrier C1 at Handoff H1",
} as const;

const SCENE_ID_SET = new Set<string>(SCENE_IDS);
const CAMERA_PRESET_SET = new Set<string>(CAMERA_PRESETS);
const GATEWAY_LAYER_SET = new Set<string>(GATEWAY_LAYERS);

const CONCEPTUAL_EXECUTION_BOUNDARIES = [
  {
    id: "mission-admission",
    label: "Physical site-level mission admission",
    implementationStatus: CONCEPTUAL_NOT_IMPLEMENTED,
    active: false,
    presentationOnly: true,
  },
  {
    id: "typed-mission",
    label: "Physical typed mission handoff",
    implementationStatus: CONCEPTUAL_NOT_IMPLEMENTED,
    active: false,
    presentationOnly: true,
  },
  {
    id: "robot-adapter",
    label: "Physical site-level robot adapter bridge",
    implementationStatus: CONCEPTUAL_NOT_IMPLEMENTED,
    active: false,
    presentationOnly: true,
  },
] as const;

const ONBOARDING_STEPS = [
  {
    id: "device-registration",
    label: "Device registration",
    implementationStatus: "conceptual-target-not-implemented",
  },
  {
    id: "certificate-enrollment",
    label: "Certificate enrollment",
    implementationStatus: "conceptual-target-not-implemented",
  },
  {
    id: "capability-assignment",
    label: "Capability assignment",
    implementationStatus: "conceptual-target-not-implemented",
  },
  {
    id: "adapter-loading",
    label: "Adapter loading",
    implementationStatus: "conceptual-target-not-implemented",
  },
  {
    id: "physical-device-onboarding",
    label: "Physical device onboarding",
    implementationStatus: "conceptual-target-not-implemented",
  },
] as const;

const FLEET_DEVICE_DETAILS: Readonly<
  Record<FleetDeviceKind, Readonly<{ label: string; capabilities: readonly string[] }>>
> = {
  picker: {
    label: "Concept Picker",
    capabilities: [
      "collect",
      "navigate",
      "return_to_handoff",
      "report_payload",
      "report_battery",
    ],
  },
  carrier: {
    label: "Concept Carrier",
    capabilities: ["carry", "navigate", "dock", "report_payload", "report_battery"],
  },
  handoff: {
    label: "Concept Handoff",
    capabilities: ["dock", "lift", "tilt", "report_status"],
  },
  sensor: {
    label: "Concept Sensor",
    capabilities: ["observe", "report_status"],
  },
  "vision-node": {
    label: "Concept Vision Node",
    capabilities: ["local_camera_inference", "report_inspection_result"],
  },
};

const FLEET_DEVICE_KIND_SET = new Set<string>(
  Object.keys(FLEET_DEVICE_DETAILS),
);

function managerWorkflowInitial(): GatewayDemoState["managerWorkflow"] {
  return {
    status: "awaiting-response",
    response: null,
    recorded: false,
    evidenceSequence: 0,
    commandsIssued: false,
    executionAuthorized: false,
    note:
      "No manager response is recorded. The storyboard recommendation is illustrative, not canonical advice.",
  };
}

function updateInitial(): GatewayDemoState["update"] {
  return {
    implementationStatus: "conceptual-target-not-implemented",
    phase: "idle",
    activeEdgeRuntimeVersion: "0.3.1",
    previousEdgeRuntimeVersion: "0.3.1",
    targetEdgeRuntimeVersion: null,
    signedManifestIllustrated: false,
    signatureVerificationIllustrated: false,
    digestVerificationIllustrated: false,
    activeMissionCheckIllustrated: false,
    stablePowerCheckIllustrated: false,
    diskCheckIllustrated: false,
    previousVersionRetained: true,
    healthCheck: "not-run",
    rollbackReportRecorded: false,
    softwareChangedInIllustration: false,
    gatewayHardwareChanged: false,
  };
}

function safetyPathInitial(): GatewayDemoState["safetyPath"] {
  return {
    implementationStatus: "conceptual-target-not-implemented",
    demonstrationActive: false,
    path: [
      "Emergency Stop",
      "Safety Relay / Robot Safety Controller",
      "Motor and mechanism power",
    ],
    bypassedSystems: [
      "Cloud",
      "Agent",
      "Edge Gateway policy",
      "LLM",
      "normal remote I/O",
      "manager dashboard",
    ],
    independentFromAgent: true,
    agentCanBypass: false,
    physicalSignalIssued: false,
  };
}

export function createInitialGatewayDemoState(): GatewayDemoState {
  return {
    scene: "installed-system",
    gatewayIdentity: GATEWAY_ID,
    selectedPartId: null,
    layerVisibility: {
      power: true,
      network: true,
      telemetry: true,
      safety: true,
    },
    cameraPreset: "installed",
    cameraProjection: "perspective",
    explodeAmount: 0,
    doorOpen: false,
    transparentEnclosure: false,
    cutaway: false,
    showDimensions: true,
    showLabels: true,
    managerWorkflow: managerWorkflowInitial(),
    conceptualExecutionBoundaries: [...CONCEPTUAL_EXECUTION_BOUNDARIES],
    fleetDevices: [],
    cameraWorkloadRequested: false,
    dedicatedVisionNodeRecommended: false,
    update: updateInitial(),
    safetyPath: safetyPathInitial(),
    productTruth: {
      conceptualVisualization: true,
      notForFabrication: true,
      illustrativeSimulation: true,
      liveCustomerData: false,
      canonicalFacilityStateSchema: false,
      canonicalRecommendation: false,
      robotMotionEvidence: false,
      robotControlAvailable: false,
    },
  };
}

export const INITIAL_GATEWAY_DEMO_STATE = createInitialGatewayDemoState();

function boundedUnitInterval(value: number, fallback: number): number {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : fallback;
}

function nextFleetDevice(state: GatewayDemoState, kind: FleetDeviceKind): FleetDevice {
  const sequence =
    state.fleetDevices.filter((device) => device.kind === kind).length + 1;
  const details = FLEET_DEVICE_DETAILS[kind];
  const suffix = String(sequence).padStart(2, "0");
  return {
    id: `concept-${kind}-${suffix}`,
    kind,
    label: `${details.label} ${suffix}`,
    capabilities: [...details.capabilities],
    capabilitySource: "user-specified-illustrative-storyboard",
    onboarding: [...ONBOARDING_STEPS],
    gatewayIdentity: state.gatewayIdentity,
    connectedToLiveFacility: false,
  };
}

function stateWithAddedFleetDevice(
  state: GatewayDemoState,
  kind: FleetDeviceKind,
): GatewayDemoState {
  const device = nextFleetDevice(state, kind);
  return {
    ...state,
    fleetDevices: [...state.fleetDevices, device],
  };
}

export function gatewayDemoReducer(
  state: GatewayDemoState,
  action: GatewayDemoAction,
): GatewayDemoState {
  switch (action.type) {
    case "scene/set":
      return SCENE_ID_SET.has(action.scene)
        ? { ...state, scene: action.scene }
        : state;
    case "part/select":
      return action.partId === null || GATEWAY_PART_BY_ID.has(action.partId)
        ? { ...state, selectedPartId: action.partId }
        : state;
    case "layer/set":
      return GATEWAY_LAYER_SET.has(action.layer)
        ? {
            ...state,
            layerVisibility: {
              ...state.layerVisibility,
              [action.layer]: action.visible,
            },
          }
        : state;
    case "camera/preset":
      return CAMERA_PRESET_SET.has(action.preset)
        ? { ...state, cameraPreset: action.preset }
        : state;
    case "camera/projection":
      return action.projection === "perspective" ||
        action.projection === "orthographic"
        ? { ...state, cameraProjection: action.projection }
        : state;
    case "gateway/explode":
      return {
        ...state,
        explodeAmount: boundedUnitInterval(action.amount, state.explodeAmount),
      };
    case "gateway/door":
      return { ...state, doorOpen: action.open };
    case "gateway/transparency":
      return { ...state, transparentEnclosure: action.enabled };
    case "gateway/cutaway":
      return { ...state, cutaway: action.enabled };
    case "gateway/dimensions":
      return { ...state, showDimensions: action.visible };
    case "gateway/labels":
      return { ...state, showLabels: action.visible };
    case "manager/record-response": {
      if (state.managerWorkflow.recorded) {
        return state;
      }
      const accepted = action.response === "accept";
      return {
        ...state,
        managerWorkflow: {
          status: accepted ? "accepted-recorded" : "declined-recorded",
          response: action.response,
          recorded: true,
          evidenceSequence: 1,
          commandsIssued: false,
          executionAuthorized: false,
          note: accepted
            ? "Manager acceptance is recorded as illustrative workflow evidence only. No command, mission admission, or adapter handoff occurs."
            : "Manager decline is recorded as illustrative workflow evidence only. No command is issued.",
        },
      };
    }
    case "fleet/add":
      return FLEET_DEVICE_KIND_SET.has(action.kind)
        ? stateWithAddedFleetDevice(state, action.kind)
        : state;
    case "fleet/request-camera-workload": {
      const withVisionNode = state.fleetDevices.some(
        (device) => device.kind === "vision-node",
      )
        ? state
        : stateWithAddedFleetDevice(state, "vision-node");
      return {
        ...withVisionNode,
        cameraWorkloadRequested: true,
        dedicatedVisionNodeRecommended: true,
      };
    }
    case "update/start": {
      const target = action.targetEdgeRuntimeVersion.trim();
      if (!target) {
        return state;
      }
      return {
        ...state,
        update: {
          ...state.update,
          phase: "staged",
          previousEdgeRuntimeVersion: state.update.activeEdgeRuntimeVersion,
          targetEdgeRuntimeVersion: target,
          signedManifestIllustrated: true,
          signatureVerificationIllustrated: true,
          digestVerificationIllustrated: true,
          activeMissionCheckIllustrated: true,
          stablePowerCheckIllustrated: true,
          diskCheckIllustrated: true,
          previousVersionRetained: true,
          healthCheck: "not-run",
          rollbackReportRecorded: false,
          softwareChangedInIllustration: false,
        },
      };
    }
    case "update/complete":
      return state.update.phase === "staged" &&
        state.update.targetEdgeRuntimeVersion !== null
        ? {
            ...state,
            update: {
              ...state.update,
              phase: "activated",
              activeEdgeRuntimeVersion: state.update.targetEdgeRuntimeVersion,
              healthCheck: "passed",
              rollbackReportRecorded: false,
              softwareChangedInIllustration: true,
            },
          }
        : state;
    case "update/fail-health-check":
      return state.update.phase === "staged" &&
        state.update.targetEdgeRuntimeVersion !== null
        ? {
            ...state,
            update: {
              ...state.update,
              phase: "rolled-back",
              activeEdgeRuntimeVersion: state.update.previousEdgeRuntimeVersion,
              healthCheck: "failed",
              rollbackReportRecorded: true,
              softwareChangedInIllustration: false,
            },
          }
        : state;
    case "safety/demonstrate-estop":
      return {
        ...state,
        safetyPath: {
          ...state.safetyPath,
          demonstrationActive: action.active,
          physicalSignalIssued: false,
        },
      };
    case "demo/reset":
      return createInitialGatewayDemoState();
  }
}

export const setScene = (scene: SceneId): GatewayDemoAction => ({
  type: "scene/set",
  scene,
});

export const selectPart = (partId: string | null): GatewayDemoAction => ({
  type: "part/select",
  partId,
});

export const setLayerVisibility = (
  layer: GatewayLayer,
  visible: boolean,
): GatewayDemoAction => ({ type: "layer/set", layer, visible });

export const setCameraPreset = (preset: CameraPreset): GatewayDemoAction => ({
  type: "camera/preset",
  preset,
});

export const setCameraProjection = (
  projection: CameraProjection,
): GatewayDemoAction => ({ type: "camera/projection", projection });

export const setExplodeAmount = (amount: number): GatewayDemoAction => ({
  type: "gateway/explode",
  amount,
});

export const setDoorOpen = (open: boolean): GatewayDemoAction => ({
  type: "gateway/door",
  open,
});

export const setTransparentEnclosure = (enabled: boolean): GatewayDemoAction => ({
  type: "gateway/transparency",
  enabled,
});

export const setCutaway = (enabled: boolean): GatewayDemoAction => ({
  type: "gateway/cutaway",
  enabled,
});

export const setDimensionsVisible = (visible: boolean): GatewayDemoAction => ({
  type: "gateway/dimensions",
  visible,
});

export const setLabelsVisible = (visible: boolean): GatewayDemoAction => ({
  type: "gateway/labels",
  visible,
});

export const recordManagerResponse = (
  response: ManagerResponse,
): GatewayDemoAction => ({ type: "manager/record-response", response });

export const addFleetDevice = (kind: FleetDeviceKind): GatewayDemoAction => ({
  type: "fleet/add",
  kind,
});

export const requestCameraWorkload = (): GatewayDemoAction => ({
  type: "fleet/request-camera-workload",
});

export const startConceptualUpdate = (
  targetEdgeRuntimeVersion: string,
): GatewayDemoAction => ({
  type: "update/start",
  targetEdgeRuntimeVersion,
});

export const completeConceptualUpdate = (): GatewayDemoAction => ({
  type: "update/complete",
});

export const failConceptualUpdateHealthCheck = (): GatewayDemoAction => ({
  type: "update/fail-health-check",
});

export const activateEmergencyStop = (active = true): GatewayDemoAction => ({
  type: "safety/demonstrate-estop",
  active,
});

export const resetDemoState = (): GatewayDemoAction => ({ type: "demo/reset" });
