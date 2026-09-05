"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ScannedRangeScene } from "./ScannedRangeScene";
import { ycDemoMission } from "./yc-dispatch-report.config";
import type { YcDemoQuery, YcDemoState } from "./yc-dispatch-report.query";
import styles from "./YcDispatchReport.module.css";

const TRANSITION_MS = 560;
const FULLSCREEN_REQUEST_COOLDOWN_MS = 5_000;

type TransitionState = Readonly<{
  active: YcDemoState;
  outgoing?: YcDemoState;
  revision: number;
}>;

type PanelProps = Readonly<{
  state: YcDemoState;
  phase: "settled" | "incoming" | "outgoing";
  onAdvance: () => void;
}>;

function DispatchPanel({ phase, onAdvance }: Omit<PanelProps, "state">) {
  const mission = ycDemoMission;

  return (
    <section
      aria-hidden={phase === "outgoing"}
      aria-labelledby="dispatch-status"
      className={`${styles.statePanel} ${styles.dispatchPanel} ${styles[phase]}`}
      data-demo-state="dispatch"
    >
      <ScannedRangeScene mission={mission} onAdvance={onAdvance} />
    </section>
  );
}

function ReportPanel({ phase }: Omit<PanelProps, "state" | "onAdvance">) {
  const mission = ycDemoMission;

  return (
    <section
      aria-hidden={phase === "outgoing"}
      aria-labelledby="report-status"
      className={`${styles.statePanel} ${styles.reportPanel} ${styles[phase]}`}
      data-demo-state="report"
    >
      <div className={styles.heroColumn}>
        <p className={styles.eyebrow}>02 / Mission report</p>
        <h1 className={styles.headline} id="report-status">
          <span className={`${styles.statusTrigger} ${styles.reportStatus}`}>
            <span>Mission</span>
            <strong>Complete</strong>
          </span>
        </h1>

        <p className={styles.missionIdentity}>
          <span>{mission.robotName}</span>
          <span aria-hidden="true">/</span>
          <span>{mission.zoneName}</span>
          <span aria-hidden="true">/</span>
          <span>{mission.missionId}</span>
        </p>

        <div className={styles.outcomeLine}>
          <i aria-hidden="true" />
          <span>Mission execution recorded</span>
        </div>
      </div>

      <div className={styles.informationBlock}>
        <div className={styles.blockHeading}>
          <span>Mission report</span>
          <span>{mission.missionId}</span>
        </div>
        <dl className={styles.metricGrid}>
          <div>
            <dt>Runtime</dt>
            <dd>{mission.runtime}</dd>
          </div>
          <div>
            <dt>Balls collected</dt>
            <dd>{mission.ballsCollected}</dd>
          </div>
          <div>
            <dt>Collection passes</dt>
            <dd>{mission.collectionPasses}</dd>
          </div>
          <div>
            <dt>Completion</dt>
            <dd>{mission.completionPercentage}%</dd>
          </div>
        </dl>

        <div className={styles.confirmation}>
          <div>
            <span>Demo confirmation</span>
            <strong>Mission report generated</strong>
          </div>
          <dl>
            <dt>Execution mode</dt>
            <dd>{mission.executionMode}</dd>
          </dl>
        </div>
      </div>
    </section>
  );
}

function StatePanel({ state, phase, onAdvance }: PanelProps) {
  return state === "dispatch" ? (
    <DispatchPanel onAdvance={onAdvance} phase={phase} />
  ) : (
    <ReportPanel phase={phase} />
  );
}

export function YcDispatchReport({
  initialState,
  autoplay,
  autoplayDelayMs,
}: YcDemoQuery) {
  const [transition, setTransition] = useState<TransitionState>({
    active: initialState,
    revision: 0,
  });
  const lastFullscreenRequestAt = useRef<number | null>(null);

  const transitionTo = useCallback((next: YcDemoState) => {
    if (document.activeElement instanceof HTMLButtonElement) {
      document.activeElement.blur();
    }

    setTransition((current) =>
      current.active === next
        ? current
        : {
            active: next,
            outgoing: current.active,
            revision: current.revision + 1,
          },
    );
  }, []);

  const showReport = useCallback(
    () => transitionTo("report"),
    [transitionTo],
  );

  useEffect(() => {
    if (!transition.outgoing) {
      return;
    }

    const revision = transition.revision;
    const timer = window.setTimeout(() => {
      setTransition((current) =>
        current.revision === revision
          ? { active: current.active, revision: current.revision }
          : current,
      );
    }, TRANSITION_MS);

    return () => window.clearTimeout(timer);
  }, [transition.outgoing, transition.revision]);

  useEffect(() => {
    if (!autoplay || transition.active !== "dispatch") {
      return;
    }

    const timer = window.setTimeout(showReport, autoplayDelayMs);
    return () => window.clearTimeout(timer);
  }, [autoplay, autoplayDelayMs, showReport, transition.active]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }

      if (event.repeat) {
        return;
      }

      if (
        event.code === "Space" ||
        event.key === " " ||
        event.key === "ArrowRight"
      ) {
        event.preventDefault();
        showReport();
        return;
      }

      if (event.key.toLowerCase() === "r") {
        transitionTo("dispatch");
        return;
      }

      if (event.key.toLowerCase() === "f") {
        const requestedAt = window.performance.now();
        if (
          lastFullscreenRequestAt.current !== null &&
          requestedAt - lastFullscreenRequestAt.current <
            FULLSCREEN_REQUEST_COOLDOWN_MS
        ) {
          return;
        }
        lastFullscreenRequestAt.current = requestedAt;

        const fullscreenRequest = document.documentElement.requestFullscreen?.();
        if (fullscreenRequest) {
          void fullscreenRequest.catch(() => undefined);
        }
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [showReport, transitionTo]);

  return (
    <main className={styles.shell} data-active-state={transition.active}>
      <header className={styles.header}>
        <div className={styles.brandLockup}>
          <span className={styles.brandName}>NXTektal Systems</span>
          <span className={styles.agentName}>RangeOps Agent</span>
        </div>
        <div className={styles.headerMode}>
          <i aria-hidden="true" />
          {ycDemoMission.facilityName ?? ycDemoMission.executionMode}
        </div>
      </header>

      <div className={styles.stateStack}>
        {transition.outgoing ? (
          <StatePanel
            key={`outgoing-${transition.revision}`}
            onAdvance={showReport}
            phase="outgoing"
            state={transition.outgoing}
          />
        ) : null}
        <StatePanel
          key={`${transition.active}-${transition.revision}`}
          onAdvance={showReport}
          phase={transition.outgoing ? "incoming" : "settled"}
          state={transition.active}
        />
      </div>

      <p aria-live="polite" className="visually-hidden">
        {transition.active === "dispatch"
          ? "Mission dispatched"
          : "Mission complete"}
      </p>

      <footer className={styles.footer}>
        <span>{transition.active === "dispatch" ? "01 / 02" : "02 / 02"}</span>
        <p>Prototype orchestration demo · supervised hardware execution</p>
        <span>Presentation only</span>
      </footer>

      <p className="visually-hidden" id="operator-shortcuts">
        Space or Right Arrow shows the report. R returns to dispatch. F requests
        browser fullscreen.
      </p>
    </main>
  );
}
