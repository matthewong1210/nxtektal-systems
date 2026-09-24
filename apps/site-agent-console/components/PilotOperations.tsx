"use client";

import { DispatchView, usePilotTaskOps } from "./DispatchPanel";
import { CollectionExecutionPanel, simulationClockFor, useCollectionExecutions } from "./execution/CollectionExecutionPanel";
import { PlanningPanel } from "./PlanningPanel";

/** Mounts the single task-ops poller and hands its validated scheduler
 * reading to both the planning panel (as its write gate) and the task
 * panel (as its status), so neither can disagree with the other. The
 * read-only collection execution panel sits between them. */
export function PilotOperations() {
  const { view, actions, health, tracker } = usePilotTaskOps();
  // The execution read is the only source of the simulation/business clock;
  // the schedule form compares dates against it while the read is fresh.
  const execution = useCollectionExecutions();
  const simulationClock = simulationClockFor(execution.view);
  return (
    <>
      <div className="dispatch-shell">
        <PlanningPanel health={health} healthSource={tracker.current} />
      </div>
      {/* Read-only V3 execution evidence. It has its own GET-only read loop,
          consumes no scheduler health and receives no session key from the
          planning panel, so a round change cannot touch pending planning
          requests or their recovery state. */}
      <CollectionExecutionPanel view={execution.view} onRetry={execution.retry} />
      <div className="dispatch-shell">
        <DispatchView view={view} actions={actions} health={health} simulationClock={simulationClock} />
      </div>
    </>
  );
}
