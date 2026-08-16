import type {
  DimensionsMm,
  GatewayPart,
  InstallationInterface,
  Vec3Meters,
} from "./types";

export const GATEWAY_ID = "conceptual-edge-gateway-primary-v1";
export const GATEWAY_WORLD_SCALE_METERS_PER_UNIT = 1 as const;

export function millimetersToMeters(millimeters: number): number {
  return millimeters / 1_000;
}

function positionMm(x: number, y: number, z: number): Vec3Meters {
  return [
    millimetersToMeters(x),
    millimetersToMeters(y),
    millimetersToMeters(z),
  ];
}

export function dimensionsMeters(dimensions: DimensionsMm): Vec3Meters {
  return dimensions.map(millimetersToMeters) as unknown as Vec3Meters;
}

/**
 * Simplified, user-requested service-area context. These are presentation
 * anchors, not surveyed site facts or commissioning records.
 */
export const INSTALLATION_INTERFACES = [
  {
    id: "existing-washer",
    label: "Existing Washer",
    approximateDimensionsMm: [780, 920, 650],
    installedPosition: positionMm(680, -150, -340),
    description:
      "Simplified existing washer context; identity, placement, and dimensions are illustrative rather than surveyed.",
    status: "conceptual-existing-context",
    notForFabrication: true,
  },
  {
    id: "dispenser",
    label: "Dispenser",
    approximateDimensionsMm: [540, 1080, 480],
    installedPosition: positionMm(1_600, -70, -380),
    description:
      "Simplified dispenser context for showing normal telemetry relationships; no live dispenser is connected.",
    status: "conceptual-existing-context",
    notForFabrication: true,
  },
  {
    id: "universal-handoff",
    label: "Universal Handoff",
    approximateDimensionsMm: [640, 440, 460],
    installedPosition: positionMm(800, -370, 450),
    description:
      "Simplified Universal Handoff context only; no physical lift, tilt, dock, or mechanism control is available here.",
    status: "conceptual-existing-context",
    notForFabrication: true,
  },
  {
    id: "range-outfield",
    label: "Range Outfield",
    approximateDimensionsMm: [1_150, 40, 1_300],
    installedPosition: positionMm(100, -610, 1_380),
    description:
      "A compact visual reference for the range outfield, not a surveyed layout or digital-twin truth source.",
    status: "conceptual-existing-context",
    notForFabrication: true,
  },
  {
    id: "facility-network",
    label: "Facility Network",
    approximateDimensionsMm: [180, 120, 60],
    installedPosition: positionMm(-1_450, 240, -500),
    description:
      "Conceptual wired-network demarcation with no live transport, credentials, or facility connection.",
    status: "conceptual-connection-point",
    notForFabrication: true,
  },
  {
    id: "protected-power",
    label: "Protected Power",
    approximateDimensionsMm: [180, 120, 60],
    installedPosition: positionMm(-1_700, 50, -630),
    description:
      "Conceptual protected-power connection point; this is not a final electrical design.",
    status: "conceptual-connection-point",
    notForFabrication: true,
  },
] as const satisfies readonly InstallationInterface[];

export const INSTALLATION_INTERFACE_BY_ID: ReadonlyMap<
  string,
  InstallationInterface
> = new Map(
  INSTALLATION_INTERFACES.map((item) => [item.id, item] as const),
);

export const LOAD_CELL_ASSEMBLY_GEOMETRY = {
  envelopeDimensionsMm: [360, 320, 180] as DimensionsMm,
  hopper: {
    radiusBottomMeters: 0.055,
    radiusTopMeters: 0.085,
    heightMeters: 0.15,
    positionMeters: [0, 0.125, 0] as Vec3Meters,
  },
  plate: {
    dimensionsMeters: [0.36, 0.025, 0.18] as Vec3Meters,
    positionMeters: [0, 0.015, 0] as Vec3Meters,
  },
  loadCellDimensionsMeters: [0.038, 0.022, 0.038] as Vec3Meters,
  loadCellPositionsMeters: [
    [-0.14, -0.012, -0.065],
    [-0.14, -0.012, 0.065],
    [0.14, -0.012, -0.065],
    [0.14, -0.012, 0.065],
  ] as readonly Vec3Meters[],
  summingJunction: {
    dimensionsMeters: [0.11, 0.1, 0.055] as Vec3Meters,
    positionMeters: [-0.1, -0.07, 0.012] as Vec3Meters,
  },
  transmitter: {
    dimensionsMeters: [0.15, 0.1, 0.06] as Vec3Meters,
    positionMeters: [0.09, -0.07, 0.012] as Vec3Meters,
  },
} as const;

export const GATEWAY_PARTS = [
  {
    id: "gateway-enclosure",
    label: "Edge Gateway Enclosure",
    category: "enclosure",
    approximateDimensionsMm: [600, 800, 220],
    installedPosition: positionMm(0, 0, 0),
    explodedPosition: positionMm(0, 0, -160),
    description:
      "Conceptual wall-mounted graphite enclosure that organizes the planned Pilot equipment.",
    status: "conceptual-structure",
    notForFabrication: true,
    optional: false,
    layers: ["power", "network", "telemetry"],
    connections: [
      {
        targetId: "external:service-wall",
        kind: "mechanical",
        label: "Conceptual wall mounting points",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "external:protective-earth",
        kind: "ground",
        label: "Conceptual protective-earth bond",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "enclosure-door",
    label: "Hinged Enclosure Door",
    category: "enclosure",
    approximateDimensionsMm: [570, 770, 28],
    installedPosition: positionMm(0, 0, 126),
    explodedPosition: positionMm(0, 0, 620),
    description:
      "Conceptual service door with optional transparency for presentation cutaway views.",
    status: "conceptual-structure",
    notForFabrication: true,
    optional: false,
    layers: [],
    connections: [
      {
        targetId: "gateway-enclosure",
        kind: "mechanical",
        label: "Conceptual left-side hinge",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "internal-backplate",
    label: "Internal Backplate",
    category: "enclosure",
    approximateDimensionsMm: [520, 700, 4],
    installedPosition: positionMm(0, 0, -92),
    explodedPosition: positionMm(0, 0, -330),
    description:
      "Conceptual metal mounting surface for DIN rails and supported components.",
    status: "conceptual-structure",
    notForFabrication: true,
    optional: false,
    layers: [],
    connections: [
      {
        targetId: "gateway-enclosure",
        kind: "mechanical",
        label: "Conceptual enclosure standoffs",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "din-rails",
    label: "DIN Rail Set",
    category: "enclosure",
    approximateDimensionsMm: [480, 335, 15],
    installedPosition: positionMm(0, 105, -72),
    explodedPosition: positionMm(0, 105, -210),
    description:
      "Conceptual supported rail set for power, network, I/O, and terminal equipment.",
    status: "conceptual-structure",
    notForFabrication: true,
    optional: false,
    layers: [],
    connections: [
      {
        targetId: "internal-backplate",
        kind: "mechanical",
        label: "Conceptual rail fasteners",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "fanless-edge-computer",
    label: "Fanless Edge Computer",
    category: "compute",
    approximateDimensionsMm: [210, 150, 65],
    installedPosition: positionMm(-120, 80, -35),
    explodedPosition: positionMm(-690, 110, 80),
    description:
      "Conceptual planned fanless x86 host. Repository software implements transport-neutral observation conversion plus Site Runtime and Agent Runtime integration for deterministic, fixture-backed, already-read samples. No live device transport, Edge Gateway production deployment, production OTA, or command path is connected.",
    status: "conceptual-pilot-component",
    notForFabrication: true,
    optional: false,
    layers: ["power", "network", "telemetry"],
    connections: [
      {
        targetId: "ethernet-switch",
        kind: "network",
        label: "Conceptual primary Ethernet link",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "ups-power-system",
        kind: "power",
        label: "Conceptual protected power feed",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "industrial-lte-router",
    label: "Industrial LTE Router",
    category: "network",
    approximateDimensionsMm: [135, 105, 45],
    installedPosition: positionMm(155, 235, -35),
    explodedPosition: positionMm(570, 455, 80),
    description:
      "Conceptual wired-uplink router with a cellular fallback path for monitoring and software-delivery illustrations.",
    status: "conceptual-pilot-component",
    notForFabrication: true,
    optional: false,
    layers: ["power", "network"],
    connections: [
      {
        targetId: "ethernet-switch",
        kind: "network",
        label: "Conceptual gateway LAN uplink",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "external:facility-network",
        kind: "network",
        label: "Conceptual primary facility uplink",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "ups-power-system",
        kind: "power",
        label: "Conceptual protected power feed",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "remote-io-module",
    label: "Remote I/O Module",
    category: "io",
    approximateDimensionsMm: [155, 115, 65],
    installedPosition: positionMm(155, 35, -35),
    explodedPosition: positionMm(690, 20, 80),
    description:
      "Conceptual normal-operating-signal interface. Repository software implements fixture-backed conversion of already-read digital-I/O samples; no live I/O transport or device connectivity exists. This module is explicitly not part of the emergency-stop safety chain.",
    status: "conceptual-pilot-component",
    notForFabrication: true,
    optional: false,
    layers: ["power", "network", "telemetry"],
    connections: [
      {
        targetId: "ethernet-switch",
        kind: "network",
        label: "Conceptual industrial Ethernet",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "terminal-blocks",
        kind: "telemetry",
        label: "Conceptual normal operating inputs",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "dc-power-supply",
        kind: "power",
        label: "Conceptual 24 VDC feed",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "ups-power-system",
    label: "UPS and Power System",
    category: "power",
    approximateDimensionsMm: [220, 170, 120],
    installedPosition: positionMm(-135, -230, -25),
    explodedPosition: positionMm(-260, -650, 80),
    description:
      "Conceptual short-duration backup and protected distribution for local state preservation and controlled shutdown.",
    status: "conceptual-pilot-component",
    notForFabrication: true,
    optional: false,
    layers: ["power"],
    connections: [
      {
        targetId: "surge-protection",
        kind: "power",
        label: "Conceptual protected AC input",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "fanless-edge-computer",
        kind: "power",
        label: "Conceptual backed-up compute feed",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "surge-protection",
    label: "Surge Protection and Breaker",
    category: "power",
    approximateDimensionsMm: [105, 95, 72],
    installedPosition: positionMm(-195, -335, -35),
    explodedPosition: positionMm(-500, -530, 80),
    description:
      "Conceptual protected AC entry. Ratings and final electrical design are intentionally unspecified.",
    status: "conceptual-pilot-component",
    notForFabrication: true,
    optional: false,
    layers: ["power", "safety"],
    connections: [
      {
        targetId: "external:facility-ac",
        kind: "power",
        label: "Conceptual facility supply",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "ups-power-system",
        kind: "power",
        label: "Conceptual protected downstream feed",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "dc-power-supply",
    label: "24 V Power Supply",
    category: "power",
    approximateDimensionsMm: [125, 125, 65],
    installedPosition: positionMm(70, -245, -35),
    explodedPosition: positionMm(180, -620, 80),
    description:
      "Conceptual conversion and protected low-voltage distribution for gateway devices.",
    status: "conceptual-pilot-component",
    notForFabrication: true,
    optional: false,
    layers: ["power"],
    connections: [
      {
        targetId: "ups-power-system",
        kind: "power",
        label: "Conceptual backed-up input",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "terminal-blocks",
        kind: "power",
        label: "Conceptual 24 VDC distribution",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "ethernet-switch",
    label: "Ethernet Switch",
    category: "network",
    approximateDimensionsMm: [180, 85, 45],
    installedPosition: positionMm(-60, 290, -35),
    explodedPosition: positionMm(-80, 680, 80),
    description:
      "Conceptual local network fan-out for compute, router, normal I/O, robots, and the optional vision node.",
    status: "conceptual-pilot-component",
    notForFabrication: true,
    optional: false,
    layers: ["power", "network"],
    connections: [
      {
        targetId: "dc-power-supply",
        kind: "power",
        label: "Conceptual low-voltage feed",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "external:robot-network",
        kind: "network",
        label: "Conceptual device network boundary",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "terminal-blocks",
    label: "Terminal Blocks",
    category: "io",
    approximateDimensionsMm: [250, 55, 55],
    installedPosition: positionMm(145, -340, -35),
    explodedPosition: positionMm(390, -390, 60),
    description:
      "Conceptual labeled terminations for orderly power and normal operating signals.",
    status: "conceptual-pilot-component",
    notForFabrication: true,
    optional: false,
    layers: ["power", "telemetry"],
    connections: [
      {
        targetId: "remote-io-module",
        kind: "telemetry",
        label: "Conceptual signal terminations",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "external:normal-operating-signals",
        kind: "telemetry",
        label: "Normal signals only; no emergency-stop chain",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "structured-wiring",
    label: "Structured Wiring",
    category: "io",
    approximateDimensionsMm: [470, 785, 18],
    installedPosition: positionMm(0, -10, -8),
    explodedPosition: positionMm(0, -10, 130),
    description:
      "Conceptual routed power, Ethernet, grounding, and normal-signal paths shown without fabrication detail.",
    status: "conceptual-structure",
    notForFabrication: true,
    optional: false,
    layers: ["power", "network", "telemetry"],
    connections: [
      {
        targetId: "cable-glands",
        kind: "mechanical",
        label: "Conceptual segregated cable entry",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "external:protective-earth",
        kind: "ground",
        label: "Conceptual protective-earth bond and grounding point",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "cable-glands",
    label: "Cable Glands",
    category: "enclosure",
    approximateDimensionsMm: [320, 45, 45],
    installedPosition: positionMm(0, -425, 0),
    explodedPosition: positionMm(0, -520, 180),
    description:
      "Conceptual separated exits for protected power, Ethernet, antennas, and normal operating signals.",
    status: "conceptual-structure",
    notForFabrication: true,
    optional: false,
    layers: ["power", "network", "telemetry"],
    connections: [
      {
        targetId: "gateway-enclosure",
        kind: "mechanical",
        label: "Conceptual gland plate",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "load-cell-interface",
    label: "Optional Load-Cell Interface",
    category: "optional",
    approximateDimensionsMm: LOAD_CELL_ASSEMBLY_GEOMETRY.envelopeDimensionsMm,
    installedPosition: positionMm(-570, -210, 0),
    explodedPosition: positionMm(-900, -390, 90),
    description:
      "Optional conceptual hopper load cells, summing junction, and Modbus weighing transmitter. Repository software converts deterministic, fixture-backed, already-read load-cell samples into canonical Observations; the depicted live Modbus reader and device connectivity remain unimplemented, while its non-canonical estimates and readouts remain illustrative and unmeasured.",
    status: "optional-future-hardware",
    notForFabrication: true,
    optional: true,
    layers: ["power", "network", "telemetry"],
    connections: [
      {
        targetId: "ethernet-switch",
        kind: "network",
        label: "Conceptual Modbus network path",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "external:load-cells",
        kind: "telemetry",
        label: "Conceptual summing-junction input",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "vision-node",
    label: "Optional Vision Node",
    category: "optional",
    approximateDimensionsMm: [220, 180, 70],
    installedPosition: positionMm(590, 170, 0),
    explodedPosition: positionMm(960, 340, 90),
    description:
      "Separate optional compute concept for local camera inference; it does not replace the primary Gateway.",
    status: "optional-future-hardware",
    notForFabrication: true,
    optional: true,
    layers: ["power", "network", "telemetry"],
    connections: [
      {
        targetId: "ethernet-switch",
        kind: "network",
        label: "Conceptual dedicated network attachment",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "external:cameras",
        kind: "telemetry",
        label: "Conceptual local camera input",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
  {
    id: "local-safety-controller",
    label: "Local Safety Controller",
    category: "safety",
    approximateDimensionsMm: [180, 145, 75],
    installedPosition: positionMm(830, -120, 0),
    explodedPosition: positionMm(1_080, -180, 90),
    description:
      "Conceptual independent local safety boundary shown outside Agent, Gateway policy, standard I/O, and dashboard control.",
    status: "independent-safety-concept",
    notForFabrication: true,
    optional: false,
    layers: ["safety"],
    connections: [
      {
        targetId: "external:emergency-stop",
        kind: "safety",
        label: "Independent conceptual emergency-stop input",
        implementationStatus: "conceptual-connection",
      },
      {
        targetId: "external:motor-mechanism-power",
        kind: "safety",
        label: "Independent conceptual safety output",
        implementationStatus: "conceptual-connection",
      },
    ],
  },
] as const satisfies readonly GatewayPart[];

export type GatewayPartManifestEntry = (typeof GATEWAY_PARTS)[number];

export const GATEWAY_PART_BY_ID: ReadonlyMap<string, GatewayPartManifestEntry> = new Map(
  GATEWAY_PARTS.map((part) => [part.id, part]),
);

export function gatewayPartPosition(
  part: GatewayPart,
  explodeAmount: number,
): Vec3Meters {
  const amount = Number.isFinite(explodeAmount)
    ? Math.min(1, Math.max(0, explodeAmount))
    : 0;
  return part.installedPosition.map(
    (coordinate, index) =>
      coordinate +
      (part.explodedPosition[index] - coordinate) * amount,
  ) as unknown as Vec3Meters;
}
