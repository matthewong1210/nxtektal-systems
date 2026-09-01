import type { Metadata } from "next";

import { YcDispatchReport } from "./YcDispatchReport";
import { parseYcDemoQuery } from "./yc-dispatch-report.query";

const title = "YC Dispatch / Report | NXTektal Systems";
const description =
  "A presentation-only NXTektal RangeOps dispatch and report demonstration for supervised prototype hardware execution.";

export const metadata: Metadata = {
  title,
  description,
  openGraph: {
    title,
    description,
    images: [],
  },
  twitter: {
    card: "summary",
    title,
    description,
    images: [],
  },
};

export default async function YcDispatchReportPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = parseYcDemoQuery(await searchParams);

  return <YcDispatchReport {...query} />;
}
