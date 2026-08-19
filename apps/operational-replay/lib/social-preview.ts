import type { Metadata } from "next";

export const SOCIAL_PREVIEW = {
  alt: "NXTektal Operational Replay — read-only operating-layer evidence",
  height: 630,
  mimeType: "image/png",
  url: "/og.png",
  width: 1200,
} as const;

const openGraphImage = {
  alt: SOCIAL_PREVIEW.alt,
  height: SOCIAL_PREVIEW.height,
  type: SOCIAL_PREVIEW.mimeType,
  url: SOCIAL_PREVIEW.url,
  width: SOCIAL_PREVIEW.width,
};

export const ROOT_METADATA = {
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_REPLAY_SITE_URL ?? "http://localhost:3000",
  ),
  title: "NXTektal Replay Story",
  description:
    "A read-only investor storytelling layer for simulation-derived operational replay artifacts.",
  openGraph: {
    title: "NXTektal Operational Replay",
    description: "State → Risk → Recommendation → Simulated task → Outcome",
    type: "website",
    images: [openGraphImage],
  },
  twitter: {
    card: "summary_large_image",
    title: "NXTektal Operational Replay",
    description:
      "A read-only story layer over simulation-derived replay evidence.",
    images: [SOCIAL_PREVIEW.url],
  },
} satisfies Metadata;

export const EDGE_GATEWAY_METADATA = {
  title: "NXTektal Edge Gateway · Conceptual Digital Twin",
  description:
    "A browser-local, read-only conceptual engineering visualization of the NXTektal Edge Gateway.",
  openGraph: {
    title: "NXTektal Edge Gateway · Conceptual Digital Twin",
    description:
      "CAD-style conceptual system visualization — not for fabrication.",
    type: "website",
    images: [openGraphImage],
  },
  twitter: {
    card: "summary_large_image",
    title: "NXTektal Edge Gateway · Conceptual Digital Twin",
    description:
      "CAD-style conceptual system visualization — not for fabrication.",
    images: [SOCIAL_PREVIEW.url],
  },
} satisfies Metadata;
