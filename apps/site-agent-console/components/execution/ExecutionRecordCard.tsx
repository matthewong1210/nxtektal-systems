import type { Binding, CollectionExecutionsSnapshot, ExecutionRecord } from "../../lib/collection-executions";
import { Badge, type Tone } from "../ui";
import { isTerminal, PROTECTION_TEXT, quantityText, REASON_TEXT, simSeconds, STAGE_LABEL, stateBadge, utcLabel } from "./shared";

/** A bound task that has no durable execution request in this snapshot. */
export function BindingOnlyCard({ binding }: { binding: Binding }) {
  return (
    <article className="dispatch-record exec-binding-only">
      <div className="rec-head">
        <Badge tone="muted">BOUND</Badge>
        <strong>
          Task <span className="mono">{binding.task_id}</span>
        </strong>
      </div>
      <p className="rec-summary">Bound, no durable execution request yet. Nothing has been accepted, started or collected for this binding.</p>
      <p className="fineprint">
        Plan <span className="mono">{binding.plan_id}</span> v{binding.plan_version} · confirmation <span className="mono">{binding.confirmation_id}</span> · admitted (lower bound) at{" "}
        {utcLabel(binding.task_created_at_utc)} · bound at {simSeconds(binding.bound_at_sim_t_s)}
      </p>
    </article>
  );
}

function startText(record: ExecutionRecord): string {
  if (record.started_sim_t_s !== null) {
    return `Started at ${simSeconds(record.started_sim_t_s)} (assignment ${record.assignment_id ?? "unknown"}); the robot may still be travelling to the zone`;
  }
  if (record.state === "PENDING") return "Not started yet · waiting for a policy slot";
  if (isTerminal(record)) return "Did not start (contract evidence)";
  return "No start evidence";
}

function collectionText(record: ExecutionRecord): { label: string; tone?: Tone } {
  const raw = record.raw_quantity;
  if (raw.status !== "NOT_REACHED" || isTerminal(record)) return quantityText(raw);
  if (record.state === "PENDING") return { label: "No collection evidence yet" };
  switch (record.stage) {
    case "TRAVEL_TO_COLLECTION":
      return { label: "Not yet at the zone" };
    case "COLLECTING":
      return { label: "Collecting now · the quantity is reported when the assignment ends" };
    case "RAW_COLLECTED_TO_ROBOT":
      return { label: "Balls are on the robot · milestone, not success · the quantity is reported when the assignment ends" };
    default:
      return { label: "Collection ended · quantity not yet reported" };
  }
}

function unloadText(record: ExecutionRecord): { label: string; tone?: Tone } {
  const unload = record.unload_quantity;
  if (unload.status !== "NOT_REACHED" || isTerminal(record)) return quantityText(unload);
  if (record.state === "PENDING") return { label: "No unloading evidence yet" };
  switch (record.stage) {
    case "TRAVEL_TO_UNLOAD":
      return { label: "Returning to the station" };
    case "UNLOADING":
      return { label: "Unloading now · the quantity is reported when the assignment ends" };
    case "UNLOADED_TO_STATION":
      return { label: "Unloaded · quantity not yet reported" };
    default:
      return { label: "Not yet unloading" };
  }
}

/** One execution record with its six evidence levels. Every line is filled
 * only by its own contract field; absent evidence reads as unknown. */
export function ExecutionRecordCard({ record, snapshot }: { record: ExecutionRecord; snapshot: CollectionExecutionsSnapshot }) {
  const binding = snapshot.bindings.find((item) => item.binding_id === record.binding_id);
  const receipt = snapshot.receipts.find((item) => item.request_id === record.request_id);
  const state = stateBadge(record);
  const edge = record.edge_evidence;
  const protection = record.device_protection;
  const conflicts = Object.entries(record.conflicts).filter(([, flag]) => flag).map(([name]) => name);
  const collection = collectionText(record);
  const unloading = unloadText(record);
  const exit = record.runtime_evidence.collection_exit_reason;
  const terminal = isTerminal(record);
  const successShown = record.state === "SUCCEEDED" && record.success_display_allowed;
  const completeWithoutSuccess = !successShown && (record.raw_quantity.status === "COMPLETE" || record.unload_quantity.status === "COMPLETE");
  return (
    <article className="dispatch-record exec-record" data-testid="execution-record-card" data-execution-id={record.execution_id}>
      <div className="rec-head">
        <Badge tone={state.tone}>{state.label}</Badge>
        <strong>
          Task <span className="mono">{record.task_id}</span>
        </strong>
        <Badge tone="info">{STAGE_LABEL[record.stage]}</Badge>
      </div>
      {binding ? (
        <p className="fineprint">
          Plan <span className="mono">{binding.plan_id}</span> v{binding.plan_version} · confirmation <span className="mono">{binding.confirmation_id}</span> · schedule{" "}
          <span className="mono">{binding.schedule_id}</span> · Edge <span className="mono">{binding.robot_id}</span>/<span className="mono">{binding.zone_id}</span> → runtime{" "}
          <span className="mono">{record.runtime_robot_id}</span>/<span className="mono">{record.runtime_zone_id}</span> → station <span className="mono">{record.handoff_station_id}</span> (synthetic
          fixture mapping, not a physical alias) · session <span className="mono">{record.session_id}</span> · round <span className="mono">{record.round_id}</span>
        </p>
      ) : (
        <p className="fineprint">Binding not present in this snapshot.</p>
      )}
      <ol className="exec-ladder">
        <li>
          <strong>1 · Task admitted</strong>
          <span>
            {binding ? (
              <>
                Admitted (lower bound) at {utcLabel(binding.task_created_at_utc)} · bound at {simSeconds(binding.bound_at_sim_t_s)} · not the start time
              </>
            ) : (
              "Admission evidence not present"
            )}
          </span>
        </li>
        <li>
          <strong>2 · Execution request durable</strong>
          <span>
            {receipt ? (
              <>
                Durable request #{receipt.sequence} · attempt <span className="mono">{receipt.attempt_id}</span> · eligible from {simSeconds(record.eligible_sim_t_s)}
              </>
            ) : (
              "No durable receipt in this snapshot"
            )}
          </span>
        </li>
        <li>
          <strong>3 · Device acceptance</strong>
          <span>
            {edge.accepted ? "Accepted by the simulated device" : "Not accepted by the device"} · Edge {edge.effective_state}
          </span>
        </li>
        <li>
          <strong>4 · Simulated job start</strong>
          <span>{startText(record)}</span>
        </li>
        <li>
          <strong>5 · Collection</strong>
          <span>
            Collected into <span className="mono">{record.raw_quantity.destination_id}</span>: {collection.tone ? <Badge tone={collection.tone}>{collection.label}</Badge> : collection.label}
            {exit ? <> · Collection ended: {REASON_TEXT[exit]}</> : null}
          </span>
        </li>
        <li>
          <strong>6 · Unloading</strong>
          <span>
            Unloaded to <span className="mono">{record.unload_quantity.destination_id}</span>: {unloading.tone ? <Badge tone={unloading.tone}>{unloading.label}</Badge> : unloading.label}
          </span>
        </li>
      </ol>
      <p className="fineprint">
        Eligible from {simSeconds(record.eligible_sim_t_s)} · latest start before {simSeconds(record.latest_start_sim_t_s)} (not a stop time) ·{" "}
        {record.execution_deadline_sim_t_s === null ? "execution deadline set at the actual start" : `execution deadline ${simSeconds(record.execution_deadline_sim_t_s)}`} · maximum{" "}
        {record.max_execution_s} s
      </p>
      {terminal && record.reason !== null && record.terminal_sim_t_s !== null ? (
        <p className="exec-terminal">
          <Badge tone={state.tone}>{state.label}</Badge> Ended at {simSeconds(record.terminal_sim_t_s)} · {record.reason}: {REASON_TEXT[record.reason]}
        </p>
      ) : null}
      {successShown ? (
        <p className="fineprint">Success is displayed because the contract verified equal collection and unloading before the deadline (success_display_allowed).</p>
      ) : null}
      {completeWithoutSuccess ? (
        <p className="fineprint">Complete collection or unloading evidence here does not establish success; balls on the robot are a milestone, not completion.</p>
      ) : null}
      {protection.protected ? (
        <p className="exec-protection">
          <Badge tone="bad">DEVICE PROTECTED</Badge> {protection.reasons.map((reason) => `${reason} (${PROTECTION_TEXT[reason]})`).join(", ")}
          {protection.authorization_blocked ? " · new authorization blocked" : ""}
        </p>
      ) : null}
      {conflicts.length > 0 ? (
        <p className="exec-conflict">
          <Badge tone="bad">EVIDENCE CONFLICT</Badge> {conflicts.join(", ")}
        </p>
      ) : null}
      <p className="exec-edge">
        Edge {edge.effective_state} · verification {edge.result_verification} · reason <span className="mono">{edge.reason ?? "none"}</span> · terminal claims{" "}
        {edge.terminal_states.length ? edge.terminal_states.join(", ") : "none"}
      </p>
      <p className="fineprint">Washing, supply and manager-recorded outcomes are independent; nothing here is copied into them.</p>
      <details className="trace-details exec-trace">
        <summary>
          Arbitration trace ({record.actions.length} tick{record.actions.length === 1 ? "" : "s"})
        </summary>
        {record.actions.length === 0 ? (
          <p className="fineprint">No control tick was recorded for this request.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Sim time</th>
                <th>Original action</th>
                <th>Selected action</th>
                <th>Selection</th>
                <th>Safety check</th>
                <th>Eligible pending</th>
              </tr>
            </thead>
            <tbody>
              {record.actions.map((action) => (
                <tr key={`${action.sim_t_s}:${action.selection}`}>
                  <td>{simSeconds(action.sim_t_s)}</td>
                  <td>
                    {action.original_action.name}
                    {action.original_action.robot_id ? ` · ${action.original_action.robot_id}` : ""}
                  </td>
                  <td>
                    {action.selected_action.name}
                    {action.selected_action.robot_id ? ` · ${action.selected_action.robot_id}` : ""}
                    {action.selected_action.target_id ? ` → ${action.selected_action.target_id}` : ""}
                  </td>
                  <td>{action.selection}</td>
                  <td>
                    {action.safety_shield}
                    {action.safety_reason ? `: ${action.safety_reason}` : ""}
                  </td>
                  <td>{action.eligible_pending.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </details>
      <p className="fineprint mono">
        request {record.request_id} · execution {record.execution_id.slice(0, 16)}… · binding {record.binding_id.slice(0, 16)}… · policy {record.policy_id} · arbiter {record.arbiter_version} ·
        reconciliation {record.reconciliation}
      </p>
    </article>
  );
}
