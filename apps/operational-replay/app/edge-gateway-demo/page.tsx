import type { Metadata } from "next";

import { EDGE_GATEWAY_METADATA } from "../../lib/social-preview";
import { EdgeGatewayDemoLoader } from "./EdgeGatewayDemoLoader";

export const metadata: Metadata = EDGE_GATEWAY_METADATA;

export default async function EdgeGatewayDemoPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  return <EdgeGatewayDemoLoader presentation={query.presentation === "1"} />;
}
