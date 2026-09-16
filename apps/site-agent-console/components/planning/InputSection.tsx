"use client";

import { useRef, useState, type FormEvent } from "react";
import type { InputRecord, PlanningContext, Scope, SourceKind } from "../../lib/planning";
import {
  CYCLE_STAGES,
  draftFromInput,
  emptyInputDraft,
  emptyZoneDraft,
  fillEvidenceTimes,
  type BooleanText,
  type EvidenceDraft,
  type InputDraft,
  type ZoneDraft,
} from "../../lib/planning-forms";
import { formatSiteTime } from "../../lib/site-time";
import { Badge, EmptyNote } from "../ui";
import { errorText, EvidenceRow, yesNo } from "./shared";

const SCOPE_OPTIONS: Scope[] = ["SHIFT", "DAY", "ONE_TASK"];

export function InputSummary({ input, timeZone }: { input: InputRecord; timeZone: string }) {
  return (
    <div className="dispatch-record">
      <div className="rec-head">
        <Badge tone="info">Revision {input.revision}</Badge>
        <strong>Recorded {formatSiteTime(input.recorded_at_utc, timeZone)} by {input.operator}</strong>
        <Badge tone="muted">{input.scope}</Badge>
      </div>
      <p className="rec-summary">{input.reason}</p>
      <p className="fineprint">
        Effective {formatSiteTime(input.effective_at_utc, timeZone)} · valid until {formatSiteTime(input.valid_until_utc, timeZone)} ·
        operating window {formatSiteTime(input.operating_window.start_at_utc, timeZone)} to{" "}
        {formatSiteTime(input.operating_window.end_at_utc, timeZone)} · site time {timeZone}
      </p>
      <dl className="kv-grid evidence-list">
        <EvidenceRow label="Clean inventory" evidence={input.inventory_clean_balls} timeZone={timeZone} render={(value: number) => `${value} balls`} />
        <EvidenceRow
          label="Demand"
          evidence={input.demand}
          timeZone={timeZone}
          render={(value: { bucket_minutes: number; low: number[]; typical: number[]; high: number[] }) =>
            `${value.bucket_minutes}-minute buckets · low ${value.low.join(", ")} · typical ${value.typical.join(", ")} · high ${value.high.join(", ")} balls/minute`
          }
        />
        <EvidenceRow label="Safety stock" evidence={input.safety_stock_balls} timeZone={timeZone} render={(value: number) => `${value} balls`} />
        <EvidenceRow label="Buffer" evidence={input.buffer_minutes} timeZone={timeZone} render={(value: number) => `${value} minutes`} />
        <EvidenceRow label="Operations allowed" evidence={input.operations_allowed} timeZone={timeZone} render={(value: boolean) => yesNo(value)} />
        <EvidenceRow label="Washer available" evidence={input.washer_available} timeZone={timeZone} render={(value: boolean) => yesNo(value)} />
      </dl>
      <h4 className="subhead">Zones · {input.zones.length}</h4>
      {input.zones.length === 0 ? (
        <EmptyNote>No zone entries. A plan needs at least one zone with permission, net yield and full cycle time.</EmptyNote>
      ) : (
        input.zones.map((zone) => (
          <dl className="kv-grid evidence-list" key={`${zone.zone_id}:${zone.robot_id}`}>
            <div className="kv">
              <dt>Zone · robot</dt>
              <dd>
                <span className="mono">{zone.zone_id}</span> · <span className="mono">{zone.robot_id}</span>
              </dd>
            </div>
            <EvidenceRow label="Collection allowed" evidence={zone.collection_allowed} timeZone={timeZone} render={(value: boolean) => yesNo(value)} />
            <EvidenceRow
              label="Net clean yield"
              evidence={zone.clean_yield_balls}
              timeZone={timeZone}
              render={(value: { low: number; high: number }) => `${value.low} to ${value.high} balls supply-ready`}
            />
            <EvidenceRow
              label="Full cycle"
              evidence={zone.cycle_minutes}
              timeZone={timeZone}
              render={(value: Record<string, number>) =>
                `${CYCLE_STAGES.map((stage) => `${stage} ${value[stage]}`).join(", ")} minutes`
              }
            />
          </dl>
        ))
      )}
      {input.changes.length > 0 ? (
        <details className="trace-details">
          <summary>Changes from the previous revision ({input.changes.length})</summary>
          <ul>
            {input.changes.map((change, index) => (
              <li key={index}>
                <span className="mono">{change.path}</span>: {JSON.stringify(change.before)} → {JSON.stringify(change.after)}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <p className="fineprint mono">input digest {input.input_digest.slice(0, 16)}… · request {input.request_id}</p>
    </div>
  );
}

function EvidenceFields<T extends EvidenceDraft>({
  id,
  label,
  block,
  onChange,
  disabled,
  children,
}: {
  id: string;
  label: string;
  block: T;
  onChange: (next: T) => void;
  disabled: boolean;
  children: React.ReactNode;
}) {
  const set = <K extends keyof T>(key: K, value: T[K]) => onChange({ ...block, [key]: value });
  return (
    <fieldset className="evidence-block" disabled={disabled}>
      <legend>
        <label>
          <input type="checkbox" checked={block.known} onChange={(event) => set("known", event.target.checked as T["known"])} /> {label}
        </label>
      </legend>
      {block.known ? (
        <div className="evidence-grid">
          {children}
          <div className="form-row">
            <label htmlFor={`${id}-source-kind`}>Source</label>
            <select id={`${id}-source-kind`} value={block.sourceKind} onChange={(event) => set("sourceKind", event.target.value as SourceKind as T["sourceKind"])}>
              <option value="MANUAL_ESTIMATE">Manual estimate</option>
              <option value="MEASURED">Measured</option>
            </select>
          </div>
          <div className="form-row">
            <label htmlFor={`${id}-source-ref`}>Source reference</label>
            <input id={`${id}-source-ref`} value={block.sourceRef} onChange={(event) => set("sourceRef", event.target.value as T["sourceRef"])} placeholder="e.g. opening count sheet 3" />
          </div>
          <div className="form-row">
            <label htmlFor={`${id}-observed`}>Observed at · site time</label>
            <input id={`${id}-observed`} type="datetime-local" value={block.observedAt} onChange={(event) => set("observedAt", event.target.value as T["observedAt"])} />
          </div>
          <div className="form-row">
            <label htmlFor={`${id}-valid`}>Valid until · site time</label>
            <input id={`${id}-valid`} type="datetime-local" value={block.validUntil} onChange={(event) => set("validUntil", event.target.value as T["validUntil"])} />
          </div>
        </div>
      ) : (
        <p className="fineprint">Unknown. It is saved as unknown, not as zero, and the plan will report it as missing.</p>
      )}
    </fieldset>
  );
}

function BooleanSelect({ id, value, onChange }: { id: string; value: BooleanText; onChange: (value: BooleanText) => void }) {
  return (
    <div className="form-row">
      <label htmlFor={id}>Value</label>
      <select id={id} value={value} onChange={(event) => onChange(event.target.value as BooleanText)}>
        <option value="true">Yes</option>
        <option value="false">No</option>
      </select>
    </div>
  );
}

function ZoneFields({
  index,
  zone,
  context,
  disabled,
  onChange,
  onRemove,
}: {
  index: number;
  zone: ZoneDraft;
  context: PlanningContext;
  disabled: boolean;
  onChange: (next: ZoneDraft) => void;
  onRemove: () => void;
}) {
  const id = `zone-${index}`;
  return (
    <div className="dispatch-record zone-entry">
      <div className="dispatch-form-grid">
        <div className="form-row">
          <label htmlFor={`${id}-zone`}>Zone</label>
          <select id={`${id}-zone`} value={zone.zoneId} onChange={(event) => onChange({ ...zone, zoneId: event.target.value })} disabled={disabled}>
            <option value="">Choose a zone</option>
            {context.zone_ids.map((zoneId) => (
              <option key={zoneId}>{zoneId}</option>
            ))}
          </select>
        </div>
        <div className="form-row">
          <label htmlFor={`${id}-robot`}>Robot</label>
          <select id={`${id}-robot`} value={zone.robotId} onChange={(event) => onChange({ ...zone, robotId: event.target.value })} disabled={disabled}>
            <option value="">Choose a robot</option>
            {context.robot_ids.map((robotId) => (
              <option key={robotId}>{robotId}</option>
            ))}
          </select>
        </div>
      </div>
      <EvidenceFields id={`${id}-allowed`} label="Collection allowed in this zone" block={zone.collectionAllowed} disabled={disabled} onChange={(next) => onChange({ ...zone, collectionAllowed: next })}>
        <BooleanSelect id={`${id}-allowed-value`} value={zone.collectionAllowed.value} onChange={(value) => onChange({ ...zone, collectionAllowed: { ...zone.collectionAllowed, value } })} />
      </EvidenceFields>
      <EvidenceFields id={`${id}-yield`} label="Net clean yield · balls supply-ready after the full cycle" block={zone.cleanYield} disabled={disabled} onChange={(next) => onChange({ ...zone, cleanYield: next })}>
        <div className="form-row">
          <label htmlFor={`${id}-yield-low`}>Low</label>
          <input id={`${id}-yield-low`} type="number" min="0" step="1" value={zone.cleanYield.low} onChange={(event) => onChange({ ...zone, cleanYield: { ...zone.cleanYield, low: event.target.value } })} />
        </div>
        <div className="form-row">
          <label htmlFor={`${id}-yield-high`}>High</label>
          <input id={`${id}-yield-high`} type="number" min="0" step="1" value={zone.cleanYield.high} onChange={(event) => onChange({ ...zone, cleanYield: { ...zone.cleanYield, high: event.target.value } })} />
        </div>
      </EvidenceFields>
      <EvidenceFields id={`${id}-cycle`} label="Full cycle minutes · travel, collect, return, unload, wash, supply" block={zone.cycleMinutes} disabled={disabled} onChange={(next) => onChange({ ...zone, cycleMinutes: next })}>
        {CYCLE_STAGES.map((stage) => (
          <div className="form-row" key={stage}>
            <label htmlFor={`${id}-cycle-${stage}`}>{stage}</label>
            <input id={`${id}-cycle-${stage}`} type="number" min="0" step="0.5" value={zone.cycleMinutes[stage]} onChange={(event) => onChange({ ...zone, cycleMinutes: { ...zone.cycleMinutes, [stage]: event.target.value } })} />
          </div>
        ))}
      </EvidenceFields>
      <div className="form-actions">
        <button type="button" className="btn btn-quiet" onClick={onRemove} disabled={disabled}>
          Remove zone entry
        </button>
      </div>
    </div>
  );
}

export function InputForm({
  input,
  context,
  disabled,
  onSave,
}: {
  input: InputRecord | null;
  context: PlanningContext;
  disabled: boolean;
  onSave: (draft: InputDraft, expectedRevision: number) => Promise<void>;
}) {
  const timeZone = context.site_timezone;
  const [draft, setDraft] = useState<InputDraft>(() => (input ? draftFromInput(input, timeZone) : emptyInputDraft()));
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const lock = useRef(false);
  const expectedRevision = input?.revision ?? 0;
  const patch = (next: Partial<InputDraft>) => setDraft((current) => ({ ...current, ...next }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (disabled || lock.current) return;
    lock.current = true;
    setSubmitting(true);
    setError(null);
    setMessage(null);
    try {
      await onSave(draft, expectedRevision);
      setMessage(`Input revision ${expectedRevision + 1} was submitted; the recorded revision is shown above.`);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      lock.current = false;
      setSubmitting(false);
    }
  }

  const formDisabled = disabled || submitting;
  return (
    <form className="dispatch-form planning-form" onSubmit={submit}>
      <h4 className="subhead">{input ? `Revise the operating input (creates revision ${expectedRevision + 1})` : "Enter the operating input (revision 1)"}</h4>
      <p className="fineprint">
        A revision is a complete replacement of the current input and keeps the history. Estimates stay labelled as estimates; unknown fields stay unknown.
        Opening inventory must be observed at the effective time; evidence validity cannot end before the input validity.
      </p>
      <fieldset disabled={formDisabled} className="planning-fieldset">
        <div className="dispatch-form-grid">
          <div className="form-row">
            <label htmlFor="input-operator">Operator · attribution only</label>
            <input id="input-operator" value={draft.operator} onChange={(event) => patch({ operator: event.target.value })} autoComplete="off" />
          </div>
          <div className="form-row">
            <label htmlFor="input-scope">Scope</label>
            <select id="input-scope" value={draft.scope} onChange={(event) => patch({ scope: event.target.value as Scope })}>
              {SCOPE_OPTIONS.map((scope) => (
                <option key={scope}>{scope}</option>
              ))}
            </select>
          </div>
          <div className="form-row dispatch-time">
            <label htmlFor="input-reason">Reason</label>
            <input id="input-reason" value={draft.reason} onChange={(event) => patch({ reason: event.target.value })} placeholder="Why this input or correction is being recorded" />
          </div>
          <div className="form-row">
            <label htmlFor="input-effective">Effective at · site time ({timeZone})</label>
            <input id="input-effective" type="datetime-local" value={draft.effectiveAt} onChange={(event) => patch({ effectiveAt: event.target.value })} />
          </div>
          <div className="form-row">
            <label htmlFor="input-valid">Input valid until · site time</label>
            <input id="input-valid" type="datetime-local" value={draft.validUntil} onChange={(event) => patch({ validUntil: event.target.value })} />
          </div>
          <div className="form-row">
            <label htmlFor="input-window-start">Operating window start</label>
            <input id="input-window-start" type="datetime-local" value={draft.windowStart} onChange={(event) => patch({ windowStart: event.target.value })} />
          </div>
          <div className="form-row">
            <label htmlFor="input-window-end">Operating window end</label>
            <input id="input-window-end" type="datetime-local" value={draft.windowEnd} onChange={(event) => patch({ windowEnd: event.target.value })} />
          </div>
        </div>
        <div className="form-actions">
          <button type="button" className="btn btn-quiet" onClick={() => setDraft((current) => fillEvidenceTimes(current))}>
            Copy input times into empty evidence times
          </button>
        </div>
        <EvidenceFields id="inventory" label="Clean inventory now · supply-ready balls at the effective time" block={draft.inventory} disabled={formDisabled} onChange={(inventory) => patch({ inventory })}>
          <div className="form-row">
            <label htmlFor="inventory-value">Balls</label>
            <input id="inventory-value" type="number" min="0" step="1" value={draft.inventory.value} onChange={(event) => patch({ inventory: { ...draft.inventory, value: event.target.value } })} />
          </div>
        </EvidenceFields>
        <EvidenceFields id="demand" label="Demand assumptions · balls per minute per bucket, low / typical / high" block={draft.demand} disabled={formDisabled} onChange={(demand) => patch({ demand })}>
          <div className="form-row">
            <label htmlFor="demand-bucket">Bucket minutes</label>
            <input id="demand-bucket" type="number" min="1" step="1" value={draft.demand.bucketMinutes} onChange={(event) => patch({ demand: { ...draft.demand, bucketMinutes: event.target.value } })} />
          </div>
          <div className="form-row">
            <label htmlFor="demand-low">Low</label>
            <input id="demand-low" value={draft.demand.low} onChange={(event) => patch({ demand: { ...draft.demand, low: event.target.value } })} placeholder="e.g. 5, 5, 5, 5, 5, 5" />
          </div>
          <div className="form-row">
            <label htmlFor="demand-typical">Typical</label>
            <input id="demand-typical" value={draft.demand.typical} onChange={(event) => patch({ demand: { ...draft.demand, typical: event.target.value } })} placeholder="e.g. 10, 10, 10, 10, 10, 10" />
          </div>
          <div className="form-row">
            <label htmlFor="demand-high">High</label>
            <input id="demand-high" value={draft.demand.high} onChange={(event) => patch({ demand: { ...draft.demand, high: event.target.value } })} placeholder="e.g. 15, 15, 15, 15, 15, 15" />
          </div>
        </EvidenceFields>
        <EvidenceFields id="safety" label="Safety stock target · balls" block={draft.safetyStock} disabled={formDisabled} onChange={(safetyStock) => patch({ safetyStock })}>
          <div className="form-row">
            <label htmlFor="safety-value">Balls</label>
            <input id="safety-value" type="number" min="0" step="1" value={draft.safetyStock.value} onChange={(event) => patch({ safetyStock: { ...draft.safetyStock, value: event.target.value } })} />
          </div>
        </EvidenceFields>
        <EvidenceFields id="buffer" label="Scheduling buffer · minutes" block={draft.bufferMinutes} disabled={formDisabled} onChange={(bufferMinutes) => patch({ bufferMinutes })}>
          <div className="form-row">
            <label htmlFor="buffer-value">Minutes</label>
            <input id="buffer-value" type="number" min="0" step="1" value={draft.bufferMinutes.value} onChange={(event) => patch({ bufferMinutes: { ...draft.bufferMinutes, value: event.target.value } })} />
          </div>
        </EvidenceFields>
        <EvidenceFields id="ops" label="Operations allowed · work and turf permission" block={draft.operationsAllowed} disabled={formDisabled} onChange={(operationsAllowed) => patch({ operationsAllowed })}>
          <BooleanSelect id="ops-value" value={draft.operationsAllowed.value} onChange={(value) => patch({ operationsAllowed: { ...draft.operationsAllowed, value } })} />
        </EvidenceFields>
        <EvidenceFields id="washer" label="Washer available" block={draft.washerAvailable} disabled={formDisabled} onChange={(washerAvailable) => patch({ washerAvailable })}>
          <BooleanSelect id="washer-value" value={draft.washerAvailable.value} onChange={(value) => patch({ washerAvailable: { ...draft.washerAvailable, value } })} />
        </EvidenceFields>
        <h4 className="subhead">Zone entries · {draft.zones.length}</h4>
        {draft.zones.map((zone, index) => (
          <ZoneFields
            key={index}
            index={index}
            zone={zone}
            context={context}
            disabled={formDisabled}
            onChange={(next) => patch({ zones: draft.zones.map((item, i) => (i === index ? next : item)) })}
            onRemove={() => patch({ zones: draft.zones.filter((_, i) => i !== index) })}
          />
        ))}
        <div className="form-actions">
          <button type="button" className="btn btn-quiet" onClick={() => patch({ zones: [...draft.zones, emptyZoneDraft(context.zone_ids[0] ?? "", context.robot_ids[0] ?? "")] })}>
            Add zone entry
          </button>
          <button type="submit" className="btn btn-primary" disabled={formDisabled}>
            {submitting ? "Saving…" : `Save revision ${expectedRevision + 1}`}
          </button>
        </div>
      </fieldset>
      {error ? <p role="alert" className="form-error">{error}</p> : null}
      {message ? <p role="status" className="dispatch-success">{message}</p> : null}
    </form>
  );
}
