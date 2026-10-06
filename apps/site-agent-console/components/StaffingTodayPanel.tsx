import type { StaffingSection, SourceFreshness, SupervisorSnapshot, UnavailableSection } from "../lib/api";
import { Badge, Section } from "./ui";
import { formatCount, formatMinutes, formatSiteClock, isUnavailable, sectionStatus, SourceLine, UnavailableNote, ValueRow } from "./context/shared";

/** Staffing today: planned shifts, recorded presence and derived coverage,
 * each labelled by its evidence kind. Scheduled is not present, clocked-in
 * is not availability, requested is not approved; nothing here changes a
 * schedule or reaches any HR or payroll system. */
export function StaffingTodayPanel({
  staffing,
  source,
  generation,
}: {
  staffing: StaffingSection | UnavailableSection;
  source: SourceFreshness | undefined;
  generation: SupervisorSnapshot["generation"];
}) {
  const status = sectionStatus(staffing.status, "STAFFING");
  return (
    <Section title="Staffing today" aside={<Badge tone={status.tone}>{status.label}</Badge>}>
      {isUnavailable(staffing) ? (
        <UnavailableNote section={staffing} what="Staffing context" />
      ) : (
        <>
          <p className="fineprint">
            Operating day {staffing.operating_day.date} ({staffing.operating_day.timezone}) · as of {formatSiteClock(generation.generated_at, staffing.operating_day.timezone)} ·{" "}
            {generation.clock_basis === "FIXTURE_DECLARED" ? "declared fixture clock" : generation.clock_basis === "SYSTEM_UTC" ? "system clock" : "no clock"}
          </p>
          <dl className="kv-grid">
            <ValueRow label="Scheduled today" item={staffing.scheduled_today} />
            <ValueRow label="Scheduled now" item={staffing.scheduled_now} />
            <ValueRow label="Clocked in now" item={staffing.confirmed_present_now} />
            <ValueRow label="Presence unknown now" item={staffing.presence_unknown_now} />
            <ValueRow label="Recorded absent today" item={staffing.confirmed_absent_today} />
            <ValueRow label="Clocked in, not scheduled" item={staffing.present_unscheduled_now} />
            <ValueRow label="Next planned change" item={staffing.next_material_change} render={(v) => `${v.kind === "shift_start" ? "shift start" : "shift end"} ×${v.count} at ${formatSiteClock(v.at_utc, staffing.operating_day.timezone)}`} />
            <ValueRow label="Approved changes today" item={staffing.approved_shift_changes_today} />
            <ValueRow label="Pending change requests" item={staffing.pending_change_requests} />
            <ValueRow label="Worked intervals today" item={staffing.worked_intervals_today} render={(v) => `${formatCount(v.count)} closed · ${formatMinutes(v.total_minutes)}`} />
            <ValueRow label="Open clock-ins" item={staffing.open_clock_ins} />
          </dl>
          {staffing.by_role.length > 0 ? (
            <table className="context-table">
              <thead>
                <tr>
                  <th>Role</th>
                  <th>Scheduled now</th>
                  <th>Clocked in</th>
                  <th>Unknown</th>
                  <th>Absent</th>
                </tr>
              </thead>
              <tbody>
                {staffing.by_role.map((row) => (
                  <tr key={row.role_code}>
                    <td className="mono">{row.role_code}</td>
                    <td>{formatCount(row.scheduled_now)}</td>
                    <td>{formatCount(row.present_now)}</td>
                    <td>{formatCount(row.unknown_now)}</td>
                    <td>{formatCount(row.absent_now)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {staffing.notes.map((note) => (
            <p className="fineprint" key={note}>
              {note}
            </p>
          ))}
          <SourceLine name="Staffing source" source={source} />
        </>
      )}
    </Section>
  );
}
