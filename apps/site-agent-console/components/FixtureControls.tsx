"use client";

import { useState } from "react";
import type { FixtureInfo } from "../lib/api";
import { Badge, Section } from "./ui";

export function FixtureControls({
  fixture,
  onAdvance,
  onRestart,
  onReset,
  busy,
}: {
  fixture: FixtureInfo;
  onAdvance: () => Promise<void>;
  onRestart: () => Promise<void>;
  onReset: () => Promise<void>;
  busy: boolean;
}) {
  const [error, setError] = useState<string | null>(null);
  const run = async (action: () => Promise<void>) => {
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };
  if (!fixture.fixture_mode) return null;
  return (
    <Section
      title="Fixture Controls"
      variant="fixture"
      aside={<Badge tone="sim">SIMULATED — NOT MANAGER ACTIONS</Badge>}
    >
      <p className="fixture-note">
        These controls drive the synthetic fixture storyline only. They are
        not operational controls and exist only in fixture mode.
      </p>
      <p className="fixture-next">
        Next cycle:{" "}
        {fixture.next_cycle ? (
          <>
            <span className="mono">#{fixture.next_cycle.cycle_index}</span>{" "}
            {fixture.next_cycle.label} ({fixture.next_cycle.scenario_time})
          </>
        ) : (
          "storyline complete — the fixture source is exhausted"
        )}
      </p>
      <div className="form-actions">
        <button
          type="button"
          className="btn btn-fixture"
          disabled={busy || !fixture.controls.advance}
          onClick={() => run(onAdvance)}
        >
          Advance one cycle
        </button>
        <button
          type="button"
          className="btn btn-fixture"
          disabled={busy || !fixture.controls.restart}
          onClick={() => run(onRestart)}
        >
          Restart / recover
        </button>
        <button
          type="button"
          className="btn btn-fixture"
          disabled={busy || !fixture.controls.reset}
          onClick={() => run(onReset)}
        >
          Reset to a new evidence directory
        </button>
      </div>
      {error ? <p className="form-error">{error}</p> : null}
      <details className="trace-details">
        <summary>Declared storyline ({fixture.cycle_catalog.length} cycles)</summary>
        <ol className="catalog-list">
          {fixture.cycle_catalog.map((cycle) => (
            <li key={cycle.cycle_index}>
              <span className="mono">{cycle.scenario_time}</span> {cycle.label}
            </li>
          ))}
        </ol>
      </details>
    </Section>
  );
}
