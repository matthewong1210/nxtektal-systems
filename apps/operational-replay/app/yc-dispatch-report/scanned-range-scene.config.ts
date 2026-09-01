export type NormalizedPoint = Readonly<{
  x: number;
  y: number;
}>;

export type SceneLabel = Readonly<{
  text: string;
  position: NormalizedPoint;
  align: "start" | "end";
}>;

export type SiteMarker = Readonly<{
  label: string;
  position: NormalizedPoint;
}>;

export type ScannedRangeSceneConfig = Readonly<{
  backgroundImage: string;
  backgroundAlt: string;
  objectPosition: NormalizedPoint;
  facilityLabel: SceneLabel;
  sceneLabel: SceneLabel;
  mapLabel: SceneLabel;
  teeLine: SiteMarker;
  zoneA: SiteMarker;
  returnStation: SiteMarker;
  robotStart: SiteMarker;
  routePoints: readonly NormalizedPoint[];
  animationDurationMs: number;
}>;

export const RANGE_SCENE_VIEWBOX = {
  width: 1_000,
  height: 450,
} as const;

/**
 * Operator-authored presentation geometry only. These normalized positions are
 * not surveyed coordinates, SLAM output, live tracking, or navigation input.
 */
export const scannedRangeScene = {
  backgroundImage: "/yc-site-schematic/range-scanned-demo.webp",
  backgroundAlt:
    "Dark scan-style presentation treatment of the supplied driving-range photo",
  objectPosition: { x: 0.5, y: 0.58 },
  facilityLabel: {
    text: "Demo range site",
    position: { x: 0.8, y: 0.06 },
    align: "end",
  },
  sceneLabel: {
    text: "Scan-style range scene",
    position: { x: 0.8, y: 0.11 },
    align: "end",
  },
  mapLabel: {
    text: "Site presentation schematic",
    position: { x: 0.8, y: 0.16 },
    align: "end",
  },
  teeLine: {
    label: "Tee line",
    position: { x: 0.5, y: 0.78 },
  },
  zoneA: {
    label: "Zone A",
    position: { x: 0.64, y: 0.59 },
  },
  returnStation: {
    label: "Return station",
    position: { x: 0.14, y: 0.71 },
  },
  robotStart: {
    label: "Picker-01 start",
    position: { x: 0.23, y: 0.73 },
  },
  routePoints: [
    { x: 0.23, y: 0.73 },
    { x: 0.34, y: 0.68 },
    { x: 0.47, y: 0.61 },
    { x: 0.61, y: 0.58 },
    { x: 0.73, y: 0.63 },
  ],
  animationDurationMs: 11_000,
} as const satisfies ScannedRangeSceneConfig;

function scenePoint(point: NormalizedPoint): NormalizedPoint {
  return {
    x: point.x * RANGE_SCENE_VIEWBOX.width,
    y: point.y * RANGE_SCENE_VIEWBOX.height,
  };
}

function formatCoordinate(value: number): string {
  return Number(value.toFixed(2)).toString();
}

/** Builds the single smooth presentation route rendered by the scene. */
export function buildPresentationRoutePath(
  points: readonly NormalizedPoint[] = scannedRangeScene.routePoints,
): string {
  if (points.length < 2) {
    throw new Error("The presentation route requires at least two points.");
  }

  const scaled = points.map(scenePoint);
  let path = `M ${formatCoordinate(scaled[0].x)} ${formatCoordinate(scaled[0].y)}`;

  for (let index = 0; index < scaled.length - 1; index += 1) {
    const previous = scaled[Math.max(0, index - 1)];
    const current = scaled[index];
    const next = scaled[index + 1];
    const following = scaled[Math.min(scaled.length - 1, index + 2)];
    const controlOne = {
      x: current.x + (next.x - previous.x) / 6,
      y: current.y + (next.y - previous.y) / 6,
    };
    const controlTwo = {
      x: next.x - (following.x - current.x) / 6,
      y: next.y - (following.y - current.y) / 6,
    };

    path += ` C ${formatCoordinate(controlOne.x)} ${formatCoordinate(controlOne.y)}, ${formatCoordinate(controlTwo.x)} ${formatCoordinate(controlTwo.y)}, ${formatCoordinate(next.x)} ${formatCoordinate(next.y)}`;
  }

  return path;
}
