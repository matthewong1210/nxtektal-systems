import { ycDemoMission } from "./yc-dispatch-report.config";

export type YcDemoState = "dispatch" | "report";

export type YcDemoQuery = Readonly<{
  initialState: YcDemoState;
  autoplay: boolean;
  autoplayDelayMs: number;
}>;

type SearchParams = Record<string, string | string[] | undefined>;

const MAX_AUTOPLAY_DELAY_MS = 10 * 60 * 1_000;

function firstValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function parseDelay(value: string | undefined): number {
  if (!value || !/^\d+$/.test(value)) {
    return ycDemoMission.autoplayDelayMs;
  }

  const parsed = Number(value);
  return Number.isSafeInteger(parsed)
    ? Math.min(parsed, MAX_AUTOPLAY_DELAY_MS)
    : ycDemoMission.autoplayDelayMs;
}

export function parseYcDemoQuery(searchParams: SearchParams): YcDemoQuery {
  const requestedState = firstValue(searchParams.state);

  return {
    initialState: requestedState === "report" ? "report" : "dispatch",
    autoplay: firstValue(searchParams.autoplay) === "1",
    autoplayDelayMs: parseDelay(firstValue(searchParams.delay)),
  };
}
