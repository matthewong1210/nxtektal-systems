import type { GatewayPart } from "../../lib/edge-gateway-model/types";

import styles from "./EdgeGatewayDemo.module.css";

export function WebGLFallback({ parts }: { parts: readonly GatewayPart[] }) {
  return (
    <section className={styles.fallback} data-testid="webgl-fallback">
      <div className={styles.fallbackDiagram} aria-hidden="true">
        <div className={styles.fallbackGateway}>
          <span>EDGE GATEWAY</span>
          <i>state + evidence</i>
        </div>
        <div className={styles.fallbackRail} />
        <div className={styles.fallbackSource}>SIMULATED OBSERVATIONS</div>
        <div className={styles.fallbackAdvice}>ADVISORY + TRACE</div>
        <div className={styles.fallbackStop}>STOP · NO COMMAND ISSUED</div>
      </div>

      <div className={styles.fallbackCopy}>
        <p className={styles.eyebrow}>WebGL unavailable · accessible system view</p>
        <h2>Edge Gateway system architecture</h2>
        <p>
          The conceptual pilot host receives simulated observation evidence,
          supports canonical state assembly, and presents deterministic advisory
          evidence. Physical site-task admission and robot translation are not
          implemented.
        </p>
        <ol className={styles.fallbackFlow}>
          <li>Simulated observation evidence</li>
          <li>FacilityState + separate AssemblyReport</li>
          <li>Deterministic advisory evaluation + DecisionTrace</li>
          <li>Manager response recorded — no command issued</li>
        </ol>
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
