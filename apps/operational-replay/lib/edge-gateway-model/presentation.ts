import type { PresentationSegment } from "./types";

export const PRESENTATION_DURATION_SECONDS = 75 as const;

export const PRESENTATION_SEGMENTS = [
  {
    id: "installed-overview",
    startSecond: 0,
    endSecond: 8,
    scene: "installed-system",
    title: "Installed Gateway overview",
    cue: "installed-overview",
  },
  {
    id: "open-enclosure",
    startSecond: 8,
    endSecond: 14,
    scene: "exploded-gateway",
    title: "Open the conceptual enclosure",
    cue: "open-enclosure",
  },
  {
    id: "explode-components",
    startSecond: 14,
    endSecond: 20,
    scene: "exploded-gateway",
    title: "Identify conceptual Gateway components",
    cue: "explode-components",
  },
  {
    id: "operational-flow",
    startSecond: 20,
    endSecond: 31,
    scene: "operational-flow",
    title: "Illustrative simulated operating flow",
    cue: "illustrative-operational-flow",
  },
  {
    id: "manager-evidence",
    startSecond: 31,
    endSecond: 35,
    scene: "operational-flow",
    title: "Record manager workflow evidence; issue no command",
    cue: "record-manager-workflow-evidence",
  },
  {
    id: "separate-rangeops-replay",
    startSecond: 35,
    endSecond: 40,
    scene: "operational-flow",
    title: "Show separate RangeOps replay; do not infer causality",
    cue: "separate-rangeops-replay",
  },
  {
    id: "fleet-onboarding",
    startSecond: 40,
    endSecond: 52,
    scene: "scale-the-fleet",
    title: "Conceptual fleet onboarding with unchanged Gateway identity",
    cue: "conceptual-fleet-onboarding",
  },
  {
    id: "update-success",
    startSecond: 52,
    endSecond: 59,
    scene: "software-update",
    title: "Conceptual signed update and health-check success",
    cue: "conceptual-update-success",
  },
  {
    id: "update-rollback",
    startSecond: 59,
    endSecond: 64,
    scene: "software-update",
    title: "Conceptual failed health check and automatic rollback",
    cue: "conceptual-update-rollback",
  },
  {
    id: "independent-safety",
    startSecond: 64,
    endSecond: 72,
    scene: "safety-architecture",
    title: "Independent local safety path",
    cue: "independent-safety-path",
  },
  {
    id: "final-overview",
    startSecond: 72,
    endSecond: PRESENTATION_DURATION_SECONDS,
    scene: "installed-system",
    title: "An updatable on-site operating layer for autonomous golf facilities",
    cue: "final-overview",
  },
] as const satisfies readonly PresentationSegment[];

export type PresentationState = Readonly<{
  elapsedSeconds: number;
  segmentIndex: number;
  playing: boolean;
  complete: boolean;
}>;

export type PresentationStepDirection = "next" | "previous";

function boundedElapsed(seconds: number): number {
  if (!Number.isFinite(seconds)) {
    return 0;
  }
  return Math.min(PRESENTATION_DURATION_SECONDS, Math.max(0, seconds));
}

function segmentIndexAt(seconds: number): number {
  const elapsed = boundedElapsed(seconds);
  const index = PRESENTATION_SEGMENTS.findIndex(
    (segment) => elapsed >= segment.startSecond && elapsed < segment.endSecond,
  );
  return index === -1 ? PRESENTATION_SEGMENTS.length - 1 : index;
}

export function presentationSegmentAt(seconds: number): PresentationSegment {
  return PRESENTATION_SEGMENTS[segmentIndexAt(seconds)];
}

export function createPresentationState(playing = true): PresentationState {
  return {
    elapsedSeconds: 0,
    segmentIndex: 0,
    playing,
    complete: false,
  };
}

export function advancePresentation(
  state: PresentationState,
  deltaSeconds: number,
): PresentationState {
  if (!state.playing || state.complete) {
    return state;
  }
  const safeDelta = Number.isFinite(deltaSeconds)
    ? Math.max(0, deltaSeconds)
    : 0;
  const elapsedSeconds = boundedElapsed(state.elapsedSeconds + safeDelta);
  const complete = elapsedSeconds >= PRESENTATION_DURATION_SECONDS;
  return {
    elapsedSeconds,
    segmentIndex: segmentIndexAt(elapsedSeconds),
    playing: complete ? false : state.playing,
    complete,
  };
}

export function pausePresentation(state: PresentationState): PresentationState {
  return state.playing ? { ...state, playing: false } : state;
}

export function resumePresentation(state: PresentationState): PresentationState {
  return !state.playing && !state.complete
    ? { ...state, playing: true }
    : state;
}

export function restartPresentation(playing = true): PresentationState {
  return createPresentationState(playing);
}

export function stepPresentation(
  state: PresentationState,
  direction: PresentationStepDirection,
): PresentationState {
  const offset = direction === "next" ? 1 : -1;
  const segmentIndex = Math.min(
    PRESENTATION_SEGMENTS.length - 1,
    Math.max(0, state.segmentIndex + offset),
  );
  return {
    elapsedSeconds: PRESENTATION_SEGMENTS[segmentIndex].startSecond,
    segmentIndex,
    playing: false,
    complete: false,
  };
}
