import type { GatewayPart } from "../../lib/edge-gateway-model/types";

import styles from "./EdgeGatewayDemo.module.css";

export function WebGLFallback({ parts }: { parts: readonly GatewayPart[] }) {
  return (
    <section className={styles.fallback} data-testid="webgl-fallback">
      <div className={styles.fallbackDiagram} aria-hidden="true">
        <div className={styles.fallbackGateway}>
          <span>EDGE GATEWAY</span>
          <i>adapter Observations + fixture composition</i>
        </div>
        <div className={styles.fallbackRail} />
        <div className={styles.fallbackSource}>ALREADY-READ FIXTURE SAMPLES</div>
        <div className={styles.fallbackAdvice}>SHADOW OPS EVALUATION</div>
        <div className={styles.fallbackStop}>STOP · NO COMMAND ISSUED</div>
      </div>

      <div className={styles.fallbackCopy}>
        <p className={styles.eyebrow}>WebGL unavailable · accessible system view</p>
        <h2>Edge Gateway system architecture</h2>
        <p>
          Observation adapters are implemented and fixture-backed. They convert
          deterministic, already-read load-cell and digital-I/O samples and
          already-received robot status using commissioned bindings and validated
          profiles. The adapter diagnostic report stays separate local evidence.
          Fixture composition adds required simulation-only facility channels and
          upstream/source references before the complete frame reaches the
          quality-gated Site and Agent Runtime path. This browser does not run or
          connect to the Python runtime. Live physical transports and device
          connectivity remain unimplemented, as do Edge Gateway production deployment,
          device/certificate enrollment, production OTA, physical command
          admission, robot or actuator execution, and installed or certified safety
          integration.
        </p>
        <ol className={styles.fallbackFlow}>
          <li>At-least-once fixture feed — already-read samples</li>
          <li>Observation adapters — implemented, fixture-backed</li>
          <li>Canonical adapter Observations</li>
          <li>EdgeAdapterReport diagnostics — separate local conversion evidence</li>
          <li>Fixture composition — five simulation-only facility channels + upstream / source-reference inputs</li>
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
