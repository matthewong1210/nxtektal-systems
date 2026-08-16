import type { Metadata } from "next";

import { EdgeGatewayDemoLoader } from "./EdgeGatewayDemoLoader";

export const metadata: Metadata = {
  title: "NXTektal Edge Gateway · Conceptual Digital Twin",
  description:
    "A browser-local, read-only conceptual engineering visualization of the NXTektal Edge Gateway.",
  openGraph: {
    title: "NXTektal Edge Gateway · Conceptual Digital Twin",
    description:
      "CAD-style conceptual system visualization — not for fabrication.",
    type: "website",
    images: [],
  },
  twitter: {
    card: "summary",
    title: "NXTektal Edge Gateway · Conceptual Digital Twin",
    description:
      "CAD-style conceptual system visualization — not for fabrication.",
    images: [],
  },
};

export default async function EdgeGatewayDemoPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  return <EdgeGatewayDemoLoader presentation={query.presentation === "1"} />;
}
