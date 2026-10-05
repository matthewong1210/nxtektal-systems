"use client";

import { canMutateConsole, type ConsoleView } from "../lib/actions";
import { DISCLAIMER } from "../lib/api";
import type { ConsoleActions, ConsoleData } from "../lib/console";
import { BriefingPanel } from "./BriefingPanel";
import { CourseOperationsPanel } from "./CourseOperationsPanel";
import { ExceptionsPanel } from "./ExceptionsPanel";
import { FixtureControls } from "./FixtureControls";
import { PilotOperations } from "./PilotOperations";
import { RecommendationsPanel } from "./RecommendationsPanel";
import { StatePanel } from "./StatePanel";
import { StatusBar } from "./StatusBar";
import { Badge, Section } from "./ui";

/** The whole console page as a function of one request-state view. The
 * page mounts the controller; this component only renders its output. */
export function ConsoleScreen({
  view,
  actions,
}: {
  view: ConsoleView<ConsoleData>;
  actions: ConsoleActions;
}) {
  const { data, loading, busy, error, notice } = view;
  // Every change control shares one lock: busy during a change, and
  // stale after a failed refresh until a read succeeds.
  const locked = !canMutateConsole(view);
  const refreshDisabled = loading || busy;
  const refresh = () => void actions.refresh();

  return (
    <>
      <div className="sim-banner">{DISCLAIMER}</div>
      <header className="console-header">
        <h1>
          <span className="brand-accent">NXT</span>ektal Site Agent
        </h1>
        <span className="header-sub">
          Manager Console · simulated pilot operations
        </span>
        <span className="header-spacer" />
        <button
          type="button"
          className="btn"
          onClick={refresh}
          disabled={refreshDisabled}
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </header>
      <CourseOperationsPanel />
      <PilotOperations />
      <div className="legacy-heading"><h2>Agent observations and advice</h2><span>Fixture-backed Shadow Mode</span></div>
      {data === null && error !== null ? (
        <div className="status-screen">
          <Section
            title="Service Unreachable"
            aside={<Badge tone="bad">NOT CONNECTED</Badge>}
          >
            <p>The Manager Console could not reach the local Site Agent.</p>
            <p className="detail-text">{error}</p>
            <p>
              Start the fixture-backed service and reload this page. The
              console holds no state of its own; everything shown comes from
              the local Manager API and its persisted evidence.
            </p>
            <div className="form-actions">
              <button
                type="button"
                className="btn btn-primary"
                onClick={refresh}
                disabled={refreshDisabled}
              >
                Retry
              </button>
            </div>
          </Section>
        </div>
      ) : data === null ? (
        <div className="status-screen">
          <Section title="Loading">
            <p>Loading the Site Agent projections…</p>
          </Section>
        </div>
      ) : (
        <main className="console-main">
          {error !== null ? (
            <div className="load-warning" role="alert">
              <Badge tone="warn">STALE</Badge> A refresh failed ({error}).
              Showing the last successful view; changes are disabled until a
              refresh succeeds.{" "}
              <button
                type="button"
                className="btn btn-quiet"
                onClick={refresh}
                disabled={refreshDisabled}
              >
                Retry refresh
              </button>
            </div>
          ) : null}
          {notice !== null ? (
            <div className="load-warning" role="status">
              <Badge tone="warn">UNCERTAIN</Badge> {notice}
            </div>
          ) : null}
          <div className="console-column">
            <StatusBar health={data.health} />
            <StatePanel state={data.state} />
            <ExceptionsPanel exceptions={data.briefing.exceptions} />
          </div>
          <div className="console-column">
            <RecommendationsPanel
              recommendations={data.recommendations}
              onRespond={actions.respond}
              busy={locked}
            />
            <BriefingPanel briefing={data.briefing} />
            <FixtureControls
              fixture={data.fixture}
              onAdvance={actions.advance}
              onRestart={actions.restart}
              onReset={actions.reset}
              busy={locked}
            />
          </div>
        </main>
      )}
    </>
  );
}
