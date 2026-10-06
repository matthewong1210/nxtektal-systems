import type { OperationsSection, SourceFreshness, SupervisorSnapshot, UnavailableSection } from "../lib/api";
import { Badge, Section } from "./ui";
import { formatCount, formatMinutes, isUnavailable, sectionStatus, SourceLine, UnavailableNote, ValueRow } from "./context/shared";

/** Operations today: ball-unit sales and play sessions, each labelled by
 * its evidence kind, plus the physical stores and machines this slice has
 * no evidence for, stated as unknown. Sold ball units are what a till
 * recorded; they say nothing about physical ball stores. */
export function OperationsTodayPanel({
  operations,
  sources,
  physicalStores,
  machines,
}: {
  operations: OperationsSection | UnavailableSection;
  sources: Record<string, SourceFreshness>;
  physicalStores: SupervisorSnapshot["physical_stores"];
  machines: SupervisorSnapshot["machines"];
}) {
  const status = sectionStatus(operations.status, "OPERATIONS");
  const dispenser = physicalStores.clean_balls_in_dispenser;
  return (
    <Section title="Operations today" aside={<Badge tone={status.tone}>{status.label}</Badge>}>
      {isUnavailable(operations) ? (
        <UnavailableNote section={operations} what="Operations context" />
      ) : (
        <>
          <h3 className="subhead">Ball-unit sales</h3>
          <dl className="kv-grid">
            <ValueRow label="Ball units sold today" item={operations.sales.ball_units_sold_today} />
            <ValueRow label="Transactions today" item={operations.sales.transactions_today} />
            <ValueRow label="Reversals today" item={operations.sales.reversals_today} />
            <ValueRow label="Unmapped SKU sales" item={operations.sales.unmapped_sku_transactions_today} />
            <ValueRow label="Unmatched reversals" item={operations.sales.unmatched_reversals} />
            <ValueRow
              label="Recent window"
              item={operations.sales.recent_window}
              render={(v) => `${formatCount(v.ball_units)} ball units · ${formatCount(v.transactions)} transactions · last ${Math.round(v.window_s / 60)} min${v.window_fully_covered ? "" : " · coverage partial"}`}
            />
          </dl>
          <h3 className="subhead">Play sessions</h3>
          <dl className="kv-grid">
            <ValueRow label="Booked sessions today" item={operations.play.booked_sessions_today} />
            <ValueRow label="Booked players today" item={operations.play.booked_players_today} />
            <ValueRow label="Upcoming booked players" item={operations.play.upcoming_booked_players} />
            <ValueRow label="Started sessions today" item={operations.play.started_sessions_today} />
            <ValueRow label="Active sessions now" item={operations.play.active_sessions_now} />
            <ValueRow label="Active players (booked headcount)" item={operations.play.active_players_booked} />
            <ValueRow label="Active players (recorded count)" item={operations.play.active_players_confirmed} />
            <ValueRow
              label="Completed sessions today"
              item={operations.play.completed_sessions_today}
              render={(v) => (v.count === 0 ? "0 completed" : `${formatCount(v.count)} completed · mean ${formatMinutes(v.mean_minutes)} (${formatMinutes(v.min_minutes)} to ${formatMinutes(v.max_minutes)})`)}
            />
            <ValueRow label="Started, no recorded finish" item={operations.play.sessions_missing_finish} />
          </dl>
          {[...operations.sales.notes, ...operations.play.notes].map((note) => (
            <p className="fineprint" key={note}>
              {note}
            </p>
          ))}
          <SourceLine name="Sales source" source={sources.sales} />
          <SourceLine name="Play source" source={sources.play} />
        </>
      )}
      <h3 className="subhead">Physical stores and machines</h3>
      <dl className="kv-grid">
        <div className="kv context-row">
          <dt>Clean balls in dispenser</dt>
          <dd>
            {"value" in dispenser && dispenser.value !== null ? (
              <>
                <span className="mono context-value">{formatCount(dispenser.value)}</span> <Badge tone="sim">{dispenser.label.replace(/_/g, " ")}</Badge>
                <span className="detail-text">
                  {" "}
                  · channel {dispenser.source_type} · service source {dispenser.service_source} · {dispenser.physical ? "physical" : "not physical"}
                </span>
              </>
            ) : (
              <>
                <span className="mono context-value">—</span> <Badge tone="warn">UNKNOWN</Badge>
                <span className="detail-text"> · {dispenser.reason}</span>
              </>
            )}
          </dd>
        </div>
        {(["clean_ball_weight_kg", "awaiting_wash_balls"] as const).map((key) => (
          <div className="kv context-row" key={key}>
            <dt>{key === "clean_ball_weight_kg" ? "Clean ball weight" : "Awaiting wash"}</dt>
            <dd>
              <span className="mono context-value">—</span> <Badge tone="warn">UNKNOWN</Badge>
              <span className="detail-text"> · {physicalStores[key].reason}</span>
            </dd>
          </div>
        ))}
        {Object.entries(machines).map(([key, item]) => (
          <div className="kv context-row" key={key}>
            <dt>{key.replace(/_/g, " ")}</dt>
            <dd>
              <span className="mono context-value">—</span> <Badge tone="warn">UNKNOWN</Badge>
              <span className="detail-text"> · {item.reason}</span>
            </dd>
          </div>
        ))}
      </dl>
      <p className="fineprint">{physicalStores.note}</p>
    </Section>
  );
}
