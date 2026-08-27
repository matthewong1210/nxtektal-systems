export type YcDemoMissionConfig = Readonly<{
  missionId: string;
  robotName: string;
  taskName: string;
  zoneName: string;
  runtime: string;
  ballsCollected: string;
  collectionPasses: string;
  completionPercentage: number;
  executionMode: string;
  facilityName?: string;
  autoplayDelayMs: number;
}>;

/**
 * FILMING CONFIGURATION
 *
 * Update only the values below before a field run. Keep unconfirmed
 * measurements as "Update after field run" rather than estimating them.
 */
export const ycDemoMission = {
  missionId: "RGO-0828-01",
  robotName: "Picker-01",
  taskName: "Collect range balls",
  zoneName: "Zone A",
  runtime: "Update after field run",
  ballsCollected: "Update after field run",
  collectionPasses: "Update after field run",
  completionPercentage: 100,
  executionMode: "Supervised prototype",
  // Optional: replace undefined with the confirmed facility name.
  facilityName: undefined,
  autoplayDelayMs: 12_000,
} as const satisfies YcDemoMissionConfig;
