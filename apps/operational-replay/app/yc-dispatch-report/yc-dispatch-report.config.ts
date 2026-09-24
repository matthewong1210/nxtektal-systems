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
 * Enter only measured field-test values before recording. Leave the em dash
 * in place for any metric that has not been measured and confirmed.
 */
export const ycDemoMission = {
  missionId: "RGO-0828-01",
  robotName: "Picker-01",
  taskName: "Collect range balls",
  zoneName: "Zone A",
  runtime: "—",
  ballsCollected: "—",
  collectionPasses: "—",
  completionPercentage: 100,
  executionMode: "Supervised prototype",
  // Optional: replace undefined with the confirmed facility name.
  facilityName: undefined,
  autoplayDelayMs: 12_000,
} as const satisfies YcDemoMissionConfig;
