"use client";

import dynamic from "next/dynamic";

import styles from "../../components/edge-gateway-3d/EdgeGatewayDemo.module.css";

const EdgeGatewayDemo = dynamic(
  () =>
    import("../../components/edge-gateway-3d/EdgeGatewayDemo").then(
      (module) => module.EdgeGatewayDemo,
    ),
  {
    ssr: false,
    loading: () => (
      <main className={styles.loadingShell} aria-busy="true">
        <p className={styles.eyebrow}>NXTektal · Engineering viewer</p>
        <h1>Preparing conceptual Edge Gateway model…</h1>
        <p>Browser-local procedural geometry · no external model request</p>
        <strong>CONCEPTUAL SYSTEM VISUALIZATION — NOT FOR FABRICATION</strong>
      </main>
    ),
  },
);

export function EdgeGatewayDemoLoader({
  presentation,
}: {
  presentation: boolean;
}) {
  return <EdgeGatewayDemo initialPresentation={presentation} />;
}
