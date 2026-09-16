/**
 * Site-timezone wall-clock conversion for planning forms and display.
 *
 * Wire times are always RFC3339 UTC with `Z`; the site timezone from the
 * planning context is display and entry metadata only. Conversions use
 * the runtime's IANA timezone data through Intl; nothing here reads the
 * wall clock.
 */

const LOCAL_INPUT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/;

interface WallClock {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
}

function wallClockIn(instantMs: number, timeZone: string): WallClock {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const parts: Record<string, number> = {};
  for (const part of formatter.formatToParts(new Date(instantMs))) {
    if (part.type !== "literal") parts[part.type] = Number(part.value);
  }
  return {
    year: parts.year,
    month: parts.month,
    day: parts.day,
    hour: parts.hour === 24 ? 0 : parts.hour,
    minute: parts.minute,
    second: parts.second,
  };
}

/** Zone offset (ms) at an instant: local wall clock minus UTC. */
function zoneOffsetMs(instantMs: number, timeZone: string): number {
  const wall = wallClockIn(instantMs, timeZone);
  const asUtc = Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute, wall.second);
  return asUtc - Math.floor(instantMs / 1000) * 1000;
}

const pad = (value: number) => String(value).padStart(2, "0");

/** RFC3339 UTC text without fractional seconds, as the contract examples use. */
export function utcText(instantMs: number): string {
  return new Date(instantMs).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** Convert a `datetime-local` value entered as site-local wall clock to UTC. */
export function siteTimeToUtc(value: string, timeZone: string): string {
  const match = LOCAL_INPUT.exec(value);
  if (!match) throw new Error("Choose a complete site-local date and time.");
  const [year, month, day, hour, minute] = match.slice(1).map(Number);
  const guess = Date.UTC(year, month - 1, day, hour, minute);
  if (!Number.isFinite(guess)) throw new Error("That site-local date and time is not valid.");
  let instant = guess - zoneOffsetMs(guess, timeZone);
  instant = guess - zoneOffsetMs(instant, timeZone);
  const wall = wallClockIn(instant, timeZone);
  if (
    wall.year !== year ||
    wall.month !== month ||
    wall.day !== day ||
    wall.hour !== hour ||
    wall.minute !== minute
  ) {
    throw new Error(
      "That site-local time does not exist. Check the date or a daylight-saving change.",
    );
  }
  return utcText(instant);
}

/** Render a UTC instant as a `datetime-local` value in the site timezone. */
export function utcToSiteInput(utc: string, timeZone: string): string {
  const instant = Date.parse(utc);
  if (!Number.isFinite(instant)) throw new Error(`Unreadable UTC time: ${utc}`);
  const wall = wallClockIn(instant, timeZone);
  return `${wall.year}-${pad(wall.month)}-${pad(wall.day)}T${pad(wall.hour)}:${pad(wall.minute)}`;
}

/** Display helper: `YYYY-MM-DD HH:MM` in the site timezone, or an em dash. */
export function formatSiteTime(utc: string | null | undefined, timeZone: string): string {
  if (!utc) return "—";
  const instant = Date.parse(utc);
  if (!Number.isFinite(instant)) return utc;
  const wall = wallClockIn(instant, timeZone);
  return `${wall.year}-${pad(wall.month)}-${pad(wall.day)} ${pad(wall.hour)}:${pad(wall.minute)}`;
}
