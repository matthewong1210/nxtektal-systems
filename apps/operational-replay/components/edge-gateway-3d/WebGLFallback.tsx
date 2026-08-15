import type { GatewayPart } from "../../lib/edge-gateway-model/types";

import styles from "./EdgeGatewayDemo.module.css";

export function WebGLFallback({ parts }: { parts: readonly GatewayPart[] }) {
  return (
    <section className={styles.fallback} data-testid="webgl-fallback">
      <div className={styles.fallbackDiagram} aria-hidden="true">
        <div className={styles.fallbackGateway}>
          <span>EDGE GATEWAY</span>
          <i>runtime lifecycle</i>
        </div>
        <div className={styles.fallbackRail} />
        <div className={styles.fallbackSource}>SYNTHETIC / FIXTURE INPUTS</div>
        <div className={styles.fallbackAdvice}>SHADOW OPS EVALUATION</div>
        <div className={styles.fallbackStop}>STOP · NO COMMAND ISSUED</div>
      </div>

      <div className={styles.fallbackCopy}>
        <p className={styles.eyebrow}>WebGL unavailable · accessible system view</p>
        <h2>Edge Gateway system architecture</h2>
        <p>
          Agent Runtime V1 is implemented for deterministic synthetic or
          fixture inputs. This browser only illustrates the quality-gated state,
          evaluation, recovery, status, and manager-workflow contracts; it does
          not run or connect to the Python runtime. Physical telemetry adapters,
          device enrollment, production OTA, physical command admission, robot
          execution, and safety installation remain unimplemented.
        </p>
        <ol className={styles.fallbackFlow}>
          <li>Synthetic / fixture observations — no physical adapter</li>
          <li>Site Runtime validation invokes telemetry-owned assembly</li>
          <li>Exact FacilityState + separate AssemblyReport</li>
          <li>Site Runtime publication quality gate — exact admitted envelope</li>
          <li>Agent Runtime invokes Shadow Ops evaluation</li>
          <li>Agent Runtime lifecycle evidence — checkpoint / recovery + read-only diagnostics</li>
          <li>Shadow Ops manager workflow response — evidence only</li>
          <li>Stop — no command issued</li>
        </ol>
        <p>Manager acceptance does not cause the separate RangeOps replay.</p>
      </div>

      <details className={styles.partsDetails} open>
        <summary>Conceptual component list</summary>
        <ul>
          {parts.map((part) => (
            <li key={part.id}>
              <strong>{part.label}</strong>
              <span>
                Approx. {part.approximateDimensionsMm.join(" × ")} mm · {part.category}
              </span>
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
