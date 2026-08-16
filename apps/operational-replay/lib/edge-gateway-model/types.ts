export const SCENE_IDS = [
  "installed-system",
  "exploded-gateway",
  "operational-flow",
  "scale-the-fleet",
  "software-update",
  "safety-architecture",
] as const;

export type SceneId = (typeof SCENE_IDS)[number];

export const GATEWAY_LAYERS = [
  "power",
  "network",
  "telemetry",
  "safety",
] as const;

export type GatewayLayer = (typeof GATEWAY_LAYERS)[number];

export type GatewayPartCategory =
  | "enclosure"
  | "compute"
  | "network"
  | "io"
  | "power"
  | "safety"
  | "optional";

export type ConnectionKind =
  | "mechanical"
  | "power"
  | "network"
  | "telemetry"
  | "safety"
  | "ground";

export type DimensionsMm = readonly [
  widthMm: number,
  heightMm: number,
  depthMm: number,
];

/** Three.js world-space coordinates. One unit is one meter. */
export type Vec3Meters = readonly [xMeters: number, yMeters: number, zMeters: number];

export type GatewayConnection = Readonly<{
  targetId: string;
  kind: ConnectionKind;
  label: string;
  implementationStatus: "conceptual-connection";
}>;

export type GatewayPart = Readonly<{
  id: string;
  label: string;
  category: GatewayPartCategory;
  approximateDimensionsMm: DimensionsMm;
  installedPosition: Vec3Meters;
  explodedPosition: Vec3Meters;
  description: string;
  status:
    | "conceptual-pilot-component"
    | "conceptual-structure"
    | "optional-future-hardware"
    | "independent-safety-concept";
  notForFabrication: true;
  optional: boolean;
  layers: readonly GatewayLayer[];
  connections: readonly GatewayConnection[];
}>;

export type InstallationInterface = Readonly<{
  id:
    | "existing-washer"
    | "dispenser"
    | "universal-handoff"
    | "range-outfield"
    | "facility-network"
    | "protected-power";
  label: string;
  approximateDimensionsMm: DimensionsMm;
  installedPosition: Vec3Meters;
  description: string;
  status: "conceptual-existing-context" | "conceptual-connection-point";
  notForFabrication: true;
}>;

export const CAMERA_PRESETS = [
  "installed",
  "isometric",
  "front",
  "side",
  "top",
] as const;

export type CameraPreset = (typeof CAMERA_PRESETS)[number];
export type CameraProjection = "perspective" | "orthographic";

export type FleetDeviceKind =
  | "picker"
  | "carrier"
  | "handoff"
  | "sensor"
  | "vision-node";

export type ManagerResponse = "accept" | "decline";

export type ManagerWorkflowEvidence = Readonly<{
  status: "awaiting-response" | "accepted-recorded" | "declined-recorded";
  response: ManagerResponse | null;
  recorded: boolean;
  evidenceSequence: 0 | 1;
  commandsIssued: false;
  executionAuthorized: false;
  note: string;
}>;

export type ConceptualBoundaryStatus = Readonly<{
  id: "mission-admission" | "typed-mission" | "robot-adapter";
  label: string;
  implementationStatus: "not-implemented";
  active: false;
  presentationOnly: true;
}>;

export type ConceptualOnboardingStep = Readonly<{
  id:
    | "device-registration"
    | "certificate-enrollment"
    | "capability-assignment"
    | "adapter-loading"
    | "physical-device-onboarding";
  label: string;
  implementationStatus: "conceptual-target-not-implemented";
}>;

export type FleetDevice = Readonly<{
  id: string;
  kind: FleetDeviceKind;
  label: string;
  capabilities: readonly string[];
  capabilitySource: "user-specified-illustrative-storyboard";
  onboarding: readonly ConceptualOnboardingStep[];
  gatewayIdentity: string;
  connectedToLiveFacility: false;
}>;

export type UpdateState = Readonly<{
  implementationStatus: "conceptual-target-not-implemented";
  phase: "idle" | "staged" | "activated" | "rolled-back";
  activeEdgeRuntimeVersion: string;
  previousEdgeRuntimeVersion: string;
  targetEdgeRuntimeVersion: string | null;
  signedManifestIllustrated: boolean;
  signatureVerificationIllustrated: boolean;
  digestVerificationIllustrated: boolean;
  activeMissionCheckIllustrated: boolean;
  stablePowerCheckIllustrated: boolean;
  diskCheckIllustrated: boolean;
  previousVersionRetained: boolean;
  healthCheck: "not-run" | "passed" | "failed";
  rollbackReportRecorded: boolean;
  softwareChangedInIllustration: boolean;
  gatewayHardwareChanged: false;
}>;

export type SafetyPathState = Readonly<{
  implementationStatus: "conceptual-target-not-implemented";
  demonstrationActive: boolean;
  path: readonly [
    "Emergency Stop",
    "Safety Relay / Robot Safety Controller",
    "Motor and mechanism power",
  ];
  bypassedSystems: readonly [
    "Cloud",
    "Agent",
    "Edge Gateway policy",
    "LLM",
    "normal remote I/O",
    "manager dashboard",
  ];
  independentFromAgent: true;
  agentCanBypass: false;
  physicalSignalIssued: false;
}>;

export type GatewayDemoState = Readonly<{
  scene: SceneId;
  gatewayIdentity: string;
  selectedPartId: string | null;
  layerVisibility: Readonly<Record<GatewayLayer, boolean>>;
  cameraPreset: CameraPreset;
  cameraProjection: CameraProjection;
  explodeAmount: number;
  doorOpen: boolean;
  transparentEnclosure: boolean;
  cutaway: boolean;
  showDimensions: boolean;
  showLabels: boolean;
  managerWorkflow: ManagerWorkflowEvidence;
  conceptualExecutionBoundaries: readonly ConceptualBoundaryStatus[];
  fleetDevices: readonly FleetDevice[];
  cameraWorkloadRequested: boolean;
  dedicatedVisionNodeRecommended: boolean;
  update: UpdateState;
  safetyPath: SafetyPathState;
  productTruth: Readonly<{
    conceptualVisualization: true;
    notForFabrication: true;
    illustrativeSimulation: true;
    liveCustomerData: false;
    canonicalFacilityStateSchema: false;
    canonicalRecommendation: false;
    robotMotionEvidence: false;
    robotControlAvailable: false;
    transportNeutralObservationConversionImplemented: true;
    fixtureBackedSiteAgentRuntimeIntegrationImplemented: true;
    liveDeviceTransportImplemented: false;
    edgeGatewayProductionDeploymentImplemented: false;
  }>;
}>;

export type GatewayDemoAction =
  | Readonly<{ type: "scene/set"; scene: SceneId }>
  | Readonly<{ type: "part/select"; partId: string | null }>
  | Readonly<{ type: "layer/set"; layer: GatewayLayer; visible: boolean }>
  | Readonly<{ type: "camera/preset"; preset: CameraPreset }>
  | Readonly<{ type: "camera/projection"; projection: CameraProjection }>
  | Readonly<{ type: "gateway/explode"; amount: number }>
  | Readonly<{ type: "gateway/door"; open: boolean }>
  | Readonly<{ type: "gateway/transparency"; enabled: boolean }>
  | Readonly<{ type: "gateway/cutaway"; enabled: boolean }>
  | Readonly<{ type: "gateway/dimensions"; visible: boolean }>
  | Readonly<{ type: "gateway/labels"; visible: boolean }>
  | Readonly<{ type: "manager/record-response"; response: ManagerResponse }>
  | Readonly<{ type: "fleet/add"; kind: FleetDeviceKind }>
  | Readonly<{ type: "fleet/request-camera-workload" }>
  | Readonly<{ type: "update/start"; targetEdgeRuntimeVersion: string }>
  | Readonly<{ type: "update/complete" }>
  | Readonly<{ type: "update/fail-health-check" }>
  | Readonly<{ type: "safety/demonstrate-estop"; active: boolean }>
  | Readonly<{ type: "demo/reset" }>;

export type PresentationSegment = Readonly<{
  id: string;
  startSecond: number;
  endSecond: number;
  scene: SceneId;
  title: string;
  cue:
    | "installed-overview"
    | "open-enclosure"
    | "explode-components"
    | "illustrative-operational-flow"
    | "record-manager-workflow-evidence"
    | "separate-rangeops-replay"
    | "conceptual-fleet-onboarding"
    | "conceptual-update-success"
    | "conceptual-update-rollback"
    | "independent-safety-path"
    | "final-overview";
}>;

export type ModelRegistryEntry = Readonly<{
  partId: string;
  componentId: string;
  sourcePath: string;
  format: "glb" | "gltf";
  metersPerUnit: 1;
  approximateDimensionsMm: DimensionsMm;
  provenance: string;
}>;

export type ResolvedModel =
  | Readonly<{
      kind: "procedural";
      partId: string;
      reason: "asset-not-registered";
    }>
  | Readonly<{
      kind: "glb";
      partId: string;
      componentId: string;
      sourcePath: string;
      format: "glb" | "gltf";
      metersPerUnit: 1;
      provenance: string;
    }>
  | Readonly<{
      kind: "error";
      partId: string;
      visible: true;
      fallbackAllowed: false;
      message: string;
    }>;
