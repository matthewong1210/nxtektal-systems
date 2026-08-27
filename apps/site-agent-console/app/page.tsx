"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createClient,
  DISCLAIMER,
  type Briefing,
  type Evaluation,
  type FixtureInfo,
  type Health,
  type Recommendation,
  type RespondInput,
  type StateProjection,
} from "../lib/api";
import { BriefingPanel } from "../components/BriefingPanel";
import { ExceptionsPanel } from "../components/ExceptionsPanel";
import { FixtureControls } from "../components/FixtureControls";
import { RecommendationsPanel } from "../components/RecommendationsPanel";
import { StatePanel } from "../components/StatePanel";
import { StatusBar } from "../components/StatusBar";
import { Badge, Section } from "../components/ui";

interface ConsoleData {
  health: Health;
  state: StateProjection;
  evaluations: Evaluation[];
  recommendations: Recommendation[];
  briefing: Briefing;
  fixture: FixtureInfo;
}

const client = createClient((input, init) => fetch(input, init));

async function fetchAll(): Promise<ConsoleData> {
  const [health, state, evaluations, recommendations, briefing, fixture] =
    await Promise.all([
      client.health(),
      client.state(),
      client.evaluations(),
      client.recommendations(),
      client.briefing(),
      client.fixture(),
    ]);
  return { health, state, evaluations, recommendations, briefing, fixture };
}

export default function ConsolePage() {
  const [data, setData] = useState<ConsoleData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await fetchAll());
      setLoadError(null);
    } catch (cause) {
      setLoadError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchAll()
      .then((snapshot) => {
        if (cancelled) return;
        setData(snapshot);
        setLoadError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setLoadError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const act = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true);
      try {
        await action();
      } finally {
        setBusy(false);
        await load();
      }
    },
    [load],
  );

  const respond = useCallback(
    (recommendationId: string, kind: string, input: RespondInput) =>
      act(() => client.respond(recommendationId, kind, input)),
    [act],
  );

  return (
    <>
      <div className="sim-banner">{DISCLAIMER}</div>
      <header className="console-header">
        <h1>
          <span className="brand-accent">NXT</span>ektal Site Agent
        </h1>
        <span className="header-sub">
          Manager Console · local fixture service · Shadow Mode
        </span>
        <span className="header-spacer" />
        <button
          type="button"
          className="btn"
          onClick={() => {
            setLoading(true);
            void load();
          }}
          disabled={loading}
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </header>
      {loadError !== null ? (
        <div className="status-screen">
          <Section
            title="Service Unreachable"
            aside={<Badge tone="bad">NOT CONNECTED</Badge>}
          >
            <p>The Manager Console could not reach the local Site Agent.</p>
            <p className="detail-text">{loadError}</p>
            <p>
              Start the fixture-backed service and reload this page. The
              console holds no state of its own; everything shown comes from
              the local Manager API and its persisted evidence.
            </p>
            <div className="form-actions">
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void load()}
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
          <div className="console-column">
            <StatusBar health={data.health} />
            <StatePanel state={data.state} />
            <ExceptionsPanel exceptions={data.briefing.exceptions} />
          </div>
          <div className="console-column">
            <RecommendationsPanel
              recommendations={data.recommendations}
              onRespond={respond}
              busy={busy}
            />
            <BriefingPanel briefing={data.briefing} />
            <FixtureControls
              fixture={data.fixture}
              onAdvance={() => act(() => client.advance())}
              onRestart={() => act(() => client.restart())}
              onReset={() => act(() => client.reset())}
              busy={busy}
            />
          </div>
        </main>
      )}
    </>
  );
}
