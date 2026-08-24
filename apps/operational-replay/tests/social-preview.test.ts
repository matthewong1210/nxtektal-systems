import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

import {
  EDGE_GATEWAY_METADATA,
  ROOT_METADATA,
  SOCIAL_PREVIEW,
} from "../lib/social-preview";

const ROOT = process.cwd();
const ACTIVE_SHA256 =
  "9d715678dd5910471591e7c45bd2ca7c9d88178355d0829e655b641e10d844eb";

type SocialMetadata = {
  openGraph?: { images?: Array<string | URL | { url: string | URL }> };
  twitter?: { images?: Array<string | URL> };
};

function metadataImageUrls(metadata: SocialMetadata): {
  openGraph: string[];
  twitter: string[];
} {
  const openGraph = (metadata.openGraph?.images ?? []).map((image) =>
    typeof image === "string" || image instanceof URL ? String(image) : String(image.url),
  );
  const twitter = (metadata.twitter?.images ?? []).map((image) => String(image));
  return { openGraph, twitter };
}

describe("cleared social preview", () => {
  test("uses the one cleared same-origin asset in every social metadata surface", () => {
    expect(metadataImageUrls(ROOT_METADATA)).toEqual({
      openGraph: [SOCIAL_PREVIEW.url],
      twitter: [SOCIAL_PREVIEW.url],
    });
    expect(metadataImageUrls(EDGE_GATEWAY_METADATA)).toEqual({
      openGraph: [SOCIAL_PREVIEW.url],
      twitter: [SOCIAL_PREVIEW.url],
    });
    expect(SOCIAL_PREVIEW).toMatchObject({
      height: 630,
      mimeType: "image/png",
      url: "/og.png",
      width: 1200,
    });
  });

  test("pins the repository-authored PNG bytes, MIME signature, and dimensions", () => {
    const bytes = readFileSync(join(ROOT, "public", "og.png"));

    expect(bytes.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    expect(bytes.subarray(12, 16).toString("ascii")).toBe("IHDR");
    expect(bytes.readUInt32BE(16)).toBe(SOCIAL_PREVIEW.width);
    expect(bytes.readUInt32BE(20)).toBe(SOCIAL_PREVIEW.height);
    expect(createHash("sha256").update(bytes).digest("hex")).toBe(
      ACTIVE_SHA256,
    );
  });

  test("keeps the reviewable SVG source self-contained and system-font only", () => {
    const source = readFileSync(
      join(ROOT, "assets", "social-preview", "operational-replay.svg"),
      "utf8",
    );

    expect(source).toMatch(/width="1200"/);
    expect(source).toMatch(/height="630"/);
    expect(source).toMatch(/font-family="[^"]*system-ui/i);
    expect(source).not.toMatch(/<(?:image|foreignObject|script)\b/i);
    expect(source).not.toMatch(/\b(?:href|xlink:href)\s*=/i);
    expect(source).not.toMatch(/@(?:font-face|import)\b/i);
    expect(source).not.toMatch(/url\(\s*["']?(?!#)/i);
  });
});
