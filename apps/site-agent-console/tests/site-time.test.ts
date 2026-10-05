import { describe, expect, it } from "vitest";

import { compareUtc, SiteTimeAmbiguityError, siteTimeToUtc, utcToSiteInput } from "../lib/site-time";

describe("site time precision", () => {
  it("renders seconds and fractions for the entry widget and keeps whole minutes short", () => {
    expect(utcToSiteInput("2026-09-16T08:00:00Z", "Asia/Shanghai")).toBe("2026-09-16T16:00");
    expect(utcToSiteInput("2026-09-16T08:00:45Z", "Asia/Shanghai")).toBe("2026-09-16T16:00:45");
    expect(utcToSiteInput("2026-09-16T08:00:45.5Z", "Asia/Shanghai")).toBe("2026-09-16T16:00:45.5");
    // the widget shows at most milliseconds; the original string is preserved elsewhere
    expect(utcToSiteInput("2026-09-16T08:00:45.123456Z", "Asia/Shanghai")).toBe("2026-09-16T16:00:45.123");
    expect(utcToSiteInput("2026-09-16T08:00:45.500000Z", "Asia/Shanghai")).toBe("2026-09-16T16:00:45.5");
  });

  it("accepts explicit seconds and fractions when a time is edited", () => {
    expect(siteTimeToUtc("2026-09-16T16:00:45", "Asia/Shanghai")).toBe("2026-09-16T08:00:45Z");
    expect(siteTimeToUtc("2026-09-16T16:00:45.5", "Asia/Shanghai")).toBe("2026-09-16T08:00:45.5Z");
    expect(siteTimeToUtc("2026-09-16T16:00:45.123456", "Asia/Shanghai")).toBe("2026-09-16T08:00:45.123456Z");
    expect(siteTimeToUtc("2026-09-16T16:00", "Asia/Shanghai")).toBe("2026-09-16T08:00:00Z");
  });

  it("refuses to guess the offset inside a daylight-saving fall-back hour", () => {
    // 01:30 on 2026-11-01 happens twice in New York: 05:30Z (EDT) and 06:30Z (EST).
    expect(() => siteTimeToUtc("2026-11-01T01:30", "America/New_York")).toThrow(SiteTimeAmbiguityError);
    try {
      siteTimeToUtc("2026-11-01T01:30", "America/New_York");
    } catch (cause) {
      const error = cause as SiteTimeAmbiguityError;
      expect(error.candidates).toEqual(["2026-11-01T05:30:00Z", "2026-11-01T06:30:00Z"]);
      expect(error.message).toMatch(/twice/);
      expect(error.message).toContain("05:30:00Z");
      expect(error.message).toContain("06:30:00Z");
    }
    // outside the repeated hour the same day converts normally
    expect(siteTimeToUtc("2026-11-01T00:30", "America/New_York")).toBe("2026-11-01T04:30:00Z");
    expect(siteTimeToUtc("2026-11-01T02:30", "America/New_York")).toBe("2026-11-01T07:30:00Z");
  });

  it("rejects a local time skipped by a spring-forward change", () => {
    expect(() => siteTimeToUtc("2026-03-08T02:30", "America/New_York")).toThrow(/does not exist/);
    expect(siteTimeToUtc("2026-03-08T03:30", "America/New_York")).toBe("2026-03-08T07:30:00Z");
  });

  it("renders both instants of a fall-back hour distinctly enough to display", () => {
    expect(utcToSiteInput("2026-11-01T05:30:00Z", "America/New_York")).toBe("2026-11-01T01:30");
    expect(utcToSiteInput("2026-11-01T06:30:00Z", "America/New_York")).toBe("2026-11-01T01:30");
  });
});

describe("compareUtc", () => {
  it("orders by the real instant with mixed whole-second and microsecond text", () => {
    expect(compareUtc("2026-09-17T08:00:00Z", "2026-09-17T08:00:00.500000Z")).toBeLessThan(0);
    expect(compareUtc("2026-09-17T08:00:00.500000Z", "2026-09-17T08:00:00Z")).toBeGreaterThan(0);
    expect(compareUtc("2026-09-17T08:00:00.000001Z", "2026-09-17T08:00:00Z")).toBeGreaterThan(0);
    expect(compareUtc("2026-09-17T08:00:00.5Z", "2026-09-17T08:00:00.500000Z")).toBe(0);
    expect(compareUtc("2026-09-17T08:00:01Z", "2026-09-17T08:00:00.999999Z")).toBeGreaterThan(0);
    expect(compareUtc("2026-09-17T07:59:59.999999Z", "2026-09-17T08:00:00Z")).toBeLessThan(0);
  });

  it("does not lose microseconds to millisecond rounding", () => {
    expect(compareUtc("2026-09-17T08:00:00.123456Z", "2026-09-17T08:00:00.123457Z")).toBeLessThan(0);
    expect(compareUtc("2026-09-17T08:00:00.123999Z", "2026-09-17T08:00:00.124000Z")).toBeLessThan(0);
  });
});
