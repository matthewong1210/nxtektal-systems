"use client";

import { DispatchView, usePilotTaskOps } from "./DispatchPanel";
import { PlanningPanel } from "./PlanningPanel";

/** Mounts the single task-ops poller and hands its validated scheduler
 * reading to both the planning panel (as its write gate) and the task
 * panel (as its status), so neither can disagree with the other. */
export function PilotOperations() {
  const { view, actions, health, tracker } = usePilotTaskOps();
  return (
    <>
      <div className="dispatch-shell">
        <PlanningPanel health={health} healthSource={tracker.current} />
      </div>
      <div className="dispatch-shell">
        <DispatchView view={view} actions={actions} health={health} />
      </div>
    </>
  );
}
