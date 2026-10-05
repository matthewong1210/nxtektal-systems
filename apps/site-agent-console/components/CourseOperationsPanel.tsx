"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ManagerApiError } from "../lib/api";
import { createCourseOpsClient, type CourseFrame, type CourseOpsSnapshot } from "../lib/course-ops";
import { Badge, EmptyNote, KeyValue, Section } from "./ui";

const COURSE_POLL_MS = 5_000;
type ReadView = { data: CourseOpsSnapshot | null; loading: boolean; error: string | null; unavailable: boolean };
type Selection = { kind: "checkpoint" | "cart"; id: string; frameId?: string };
const count = (value: number) => value.toLocaleString("en-US");
const age = (now: number, minute: number) => `${count(Math.max(0, Math.round(now - minute)))} simulation minutes old`;
const qualityName = (quality: string) => ({ USABLE: "Usable image", BLURRED: "Blurred · revisit needed", OCCLUDED: "Occluded · revisit needed", STALE: "Old observation", UNOBSERVED: "Unobserved" })[quality] ?? quality;
const utc = (value: string) => `${value.replace("T", " ").replace(/Z$/, "")} UTC`;

/** One serial read loop. A failed read keeps the exact previous snapshot,
 * including its round-specific evidence URLs. Unmount cancels the request. */
export function CourseOperationsPanel() {
  const [view, setView] = useState<ReadView>({ data: null, loading: true, error: null, unavailable: false });
  const refresh = useRef<() => void>(() => {});
  useEffect(() => {
    const client = createCourseOpsClient((input, init) => fetch(input, init));
    let active = true;
    let pending = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | undefined;
    const read = async () => {
      if (!active || pending) return;
      clearTimeout(timer);
      pending = true;
      request = new AbortController();
      setView((previous) => ({ ...previous, loading: true }));
      try {
        const data = await client.read(request.signal);
        if (active) setView({ data, loading: false, error: null, unavailable: false });
      } catch (cause) {
        if (active) setView((previous) => ({ ...previous, loading: false, error: cause instanceof Error ? cause.message : "Read failed", unavailable: cause instanceof ManagerApiError && cause.status === 404 }));
      } finally {
        pending = false;
        if (active) timer = setTimeout(() => void read(), COURSE_POLL_MS);
      }
    };
    refresh.current = () => void read();
    void read();
    return () => {
      active = false;
      clearTimeout(timer);
      request?.abort();
      refresh.current = () => {};
    };
  }, []);

  return (
    <div className="course-operations">
      <Section title="Whole-course observations" aside={<Badge tone="sim">READ ONLY · SIMULATION</Badge>}>
        {view.error !== null ? (
          <div className="course-read-warning" role="alert">
            <strong>{view.unavailable ? "Whole-course data is not connected" : "Refresh failed"}.</strong>{" "}
            {view.data ? "Last successful view shown below; these observations have not been refreshed." : "No course observations are available from this service."}
            <p className="detail-text">{view.error}</p>
            <button type="button" className="btn" onClick={() => refresh.current()} disabled={view.loading}>Retry course read</button>
          </div>
        ) : null}
        {view.data ? <CourseSnapshot key={`${view.data.source.series_id}:${view.data.source.session_id}:${view.data.source.round_id}`} data={view.data} refreshing={view.loading} stale={view.error !== null} onRefresh={() => refresh.current()} /> : view.error === null ? <EmptyNote>Loading course observations…</EmptyNote> : null}
      </Section>
    </div>
  );
}

function CourseSnapshot({ data, refreshing, stale, onRefresh }: { data: CourseOpsSnapshot; refreshing: boolean; stale: boolean; onRefresh: () => void }) {
  const [selected, setSelected] = useState<Selection | null>(null);
  const { course, source } = data;
  const paused = source.parent_paused || source.child_paused;
  const statuses = course.checkpoints.reduce<Record<string, number>>((result, point) => {
    result[point.coverage_status] = (result[point.coverage_status] ?? 0) + 1;
    return result;
  }, {});
  const matchingFrames = selected === null ? [] : data.frames.filter((frame) => selected.kind === "cart" ? frame.cart_id === selected.id : frame.checkpoint_id === selected.id).sort((a, b) => b.minute - a.minute);
  const selectedFrame = matchingFrames.find((frame) => frame.frame_id === selected?.frameId) ?? matchingFrames[0] ?? null;
  const checkpointId = selected?.kind === "checkpoint" ? selected.id : selectedFrame?.checkpoint_id ?? null;
  const selectedCheckpoint = course.checkpoints.find((point) => point.checkpoint_id === checkpointId);
  const selectedCart = selected?.kind === "cart" ? data.carts.find((cart) => cart.cart_id === selected.id) : null;

  return (
    <>
      <div className="course-heading">
        <div><h3>Saved simulation report</h3><p>Inspect what the cameras recorded and where follow-up work stands.</p></div>
        <div className="course-heading-actions">
          <Badge tone={paused ? "warn" : "muted"}>{paused ? "Paused" : "Saved snapshot"}</Badge>
          {stale ? <Badge tone="warn">Read stale</Badge> : null}
          <button type="button" className="btn" onClick={onRefresh} disabled={refreshing}>{refreshing ? "Reading…" : "Refresh course"}</button>
        </div>
      </div>
      <dl className="course-times">
        <KeyValue label="Round"><span className="mono">{source.round_id}</span></KeyValue>
        <KeyValue label="Simulation time">Minute {count(data.clock.simulated_minute)}<small>{utc(data.clock.simulated_at_utc)}</small></KeyValue>
        <KeyValue label="API read time">{utc(data.generated_at_utc)}<small>Publication time unknown</small></KeyValue>
        <KeyValue label="Saved run status">{source.status}<small>Series {source.parent_paused ? "paused" : "not paused"}; round {source.child_paused ? "paused" : "not paused"}</small></KeyValue>
      </dl>
      <p className="course-provenance">Camera frames are synthetic. This panel reads saved course observations. The planning and task controls below run a separate pilot exercise; they do not schedule this 18-hole session.</p>
      <div className="course-coverage" aria-label="Observation coverage">
        <strong>{course.hole_count} holes · {course.checkpoints.length} observation points</strong>
        <span>{count(statuses.UNOBSERVED ?? 0)} unobserved</span>
        <span>{count(statuses.STALE ?? 0)} old</span>
        <span>{count((statuses.BLURRED ?? 0) + (statuses.OCCLUDED ?? 0))} need another image</span>
        <span>{count(statuses.USABLE ?? 0)} usable</span>
      </div>
      <div className="course-observation-layout">
        <div className="course-map-column">
          <CourseMap data={data} selected={selected} onSelect={setSelected} />
          <div className="course-map-legend"><span className="course-key course-key-usable">Usable</span><span className="course-key course-key-stale">Old / unusable</span><span className="course-key course-key-unknown">Unobserved</span><span className="course-key course-key-cart">Cart camera position</span></div>
          <p className="fineprint">Local coordinates in metres. Only observation points and camera-linked cart positions are supplied; course boundaries and routes are unavailable. Unobserved areas remain unknown.</p>
          <label className="course-frame-picker">Observation point<select aria-label="Choose observation point" value={selected?.kind === "checkpoint" ? selected.id : ""} onChange={(event) => setSelected(event.target.value ? { kind: "checkpoint", id: event.target.value } : null)}><option value="">Choose a point</option>{course.checkpoints.map((point) => <option key={point.checkpoint_id} value={point.checkpoint_id}>Hole {point.hole_number} · {point.surface_type} · {qualityName(point.coverage_status)}</option>)}</select></label>
          <div className="course-cart-list">
            <h4>{data.carts.length} / {course.cart_count} carts observed</h4>
            <p className="fineprint">These are the last recorded positions, not current locations. {course.cart_count - data.carts.length} carts have no recorded camera position.</p>
            {data.carts.map((cart) => <button type="button" className="course-cart" key={cart.cart_id} onClick={() => setSelected({ kind: "cart", id: cart.cart_id })} aria-label={`View ${cart.cart_id} evidence`} aria-pressed={selected?.kind === "cart" && selected.id === cart.cart_id}><strong>{cart.cart_id}</strong><span>±{cart.accuracy_m} m</span><small>{age(data.clock.simulated_minute, cart.minute)}</small></button>)}
          </div>
        </div>
        <aside className="course-inspector" aria-label="Selected observation evidence">
          {selected === null ? <div className="course-empty-evidence"><h4>Choose an observation point or cart</h4><p>Review its latest captured image, candidate detections, and recorded work.</p><p>Unknown coverage is not a clean inspection.</p></div> : <>
            <h4>{selected.id}</h4>
            {selectedCheckpoint ? <p>Hole {selectedCheckpoint.hole_number} · {selectedCheckpoint.surface_type} · {qualityName(selectedCheckpoint.coverage_status)}</p> : null}
            {selectedCart ? <p className="course-position">Camera position {selectedCart.x_m} m, {selectedCart.y_m} m · ±{selectedCart.accuracy_m} m<br />{age(data.clock.simulated_minute, selectedCart.minute)}</p> : null}
            {matchingFrames.length > 1 ? <label className="course-frame-picker">Captured image<select value={selectedFrame?.frame_id ?? ""} onChange={(event) => setSelected({ ...selected, frameId: event.target.value })}>{matchingFrames.map((frame) => <option key={frame.frame_id} value={frame.frame_id}>Minute {frame.minute} · {frame.cart_id} · {frame.frame_id}</option>)}</select></label> : null}
            {selectedFrame ? <FrameEvidence key={`${source.round_id}:${selectedFrame.frame_id}:${selectedFrame.image_sha256}`} frame={selectedFrame} minute={data.clock.simulated_minute} /> : <EmptyNote>No captured image for this observation point. Its condition is unknown.</EmptyNote>}
            {selectedFrame ? <div className="course-recorded-observations"><h5>Recorded observations</h5>{data.observations.filter((observation) => observation.frame_id === selectedFrame.frame_id).length === 0 ? <p>No observation record accompanies this image.</p> : data.observations.filter((observation) => observation.frame_id === selectedFrame.frame_id).map((observation) => <p key={observation.observation_id}>{observation.condition} · {observation.quality}<small>{utc(observation.captured_at_utc)}</small></p>)}</div> : null}
            {checkpointId ? <CheckpointWork data={data} checkpointId={checkpointId} /> : null}
          </>}
        </aside>
      </div>
      <div className="course-support-grid">
        <RangeObservations data={data} />
        <section className="course-work-summary" aria-label="Recorded inspection and maintenance work">
          <h3>Inspection and maintenance</h3>
          <p>{data.cases.length} recorded cases · {data.tasks.length} workflow tasks · {data.staff_jobs.length} human jobs</p>
          <p className="fineprint">Records belong to this saved round. Select a point to trace its work. Task states are reported as recorded; a completed task alone does not prove a successful reinspection.</p>
          {data.cases.length ? <details><summary>View recorded cases</summary><div className="course-table-scroll"><table><thead><tr><th>Case / point</th><th>Issue</th><th>Priority</th><th>Status</th></tr></thead><tbody>{data.cases.map((item) => <tr key={item.case_id}><td><button className="btn btn-quiet" type="button" onClick={() => setSelected({ kind: "checkpoint", id: item.checkpoint_id })}>{item.checkpoint_id}</button><small className="mono">{item.case_id}</small></td><td>{item.condition_kind}</td><td>{item.priority}</td><td>{item.status}</td></tr>)}</tbody></table></div></details> : <EmptyNote>No cases were recorded in this report.</EmptyNote>}
          <h4>Weather unknown</h4><p className="fineprint">This saved observation contract has no weather reading. Grass firmness, sand workability and soil moisture are not measured by these images.</p>
        </section>
      </div>
      <details className="course-source-details"><summary>Source and evidence identity</summary><dl className="kv-grid"><KeyValue label="Site">{course.site_id}</KeyValue><KeyValue label="Deployment">{course.deployment_id}</KeyValue><KeyValue label="Map revision">{course.map_revision ?? "Unknown"}</KeyValue><KeyValue label="Series"><span className="mono">{source.series_id}</span></KeyValue><KeyValue label="Session"><span className="mono">{source.session_id}</span></KeyValue><KeyValue label="Engine"><span className="mono">{source.engine_digest}</span></KeyValue><KeyValue label="Report SHA-256"><span className="mono">{source.report_sha256}</span></KeyValue></dl></details>
    </>
  );
}

function CourseMap({ data, selected, onSelect }: { data: CourseOpsSnapshot; selected: Selection | null; onSelect: (selection: Selection) => void }) {
  const points = [...data.course.checkpoints, ...data.carts];
  const minX = Math.min(0, ...points.map((point) => point.x_m));
  const maxX = Math.max(1, ...points.map((point) => point.x_m));
  const minY = Math.min(0, ...points.map((point) => point.y_m));
  const maxY = Math.max(1, ...points.map((point) => point.y_m));
  const width = 780, height = 500, padding = 34;
  const scale = Math.min((width - padding * 2) / (maxX - minX), (height - padding * 2) / (maxY - minY));
  const x = (metres: number) => padding + (metres - minX) * scale;
  const y = (metres: number) => height - padding - (metres - minY) * scale;
  const activate = (event: KeyboardEvent<SVGGElement>, selection: Selection) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(selection); }
  };
  return <svg className="course-map" viewBox={`0 0 ${width} ${height}`} role="group" aria-label="Course observation point map">
    <title>Saved observation points and cart camera positions</title>
    <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} className="course-map-axis" /><line x1={padding} y1={padding} x2={padding} y2={height - padding} className="course-map-axis" />
    <text x={padding + 5} y={height - 8} className="course-map-label">Local X · metres</text><text x={padding + 5} y={18} className="course-map-label">Local Y · metres</text>
    {Array.from({ length: data.course.hole_count }, (_, index) => index + 1).map((hole) => {
      const anchor = data.course.checkpoints.find((point) => point.hole_number === hole);
      return anchor ? <text key={hole} x={x(anchor.x_m) + 12} y={y(anchor.y_m) - 12} className="course-hole-label">H{hole}</text> : null;
    })}
    {data.course.checkpoints.map((point) => {
      const selection: Selection = { kind: "checkpoint", id: point.checkpoint_id };
      return <g key={point.checkpoint_id} data-checkpoint-id={point.checkpoint_id} role="button" tabIndex={0} aria-label={`Inspect ${point.checkpoint_id}`} aria-pressed={selected?.kind === "checkpoint" && selected.id === point.checkpoint_id} className={`course-map-point course-map-point-${point.coverage_status.toLowerCase()}`} onClick={() => onSelect(selection)} onKeyDown={(event) => activate(event, selection)} transform={`translate(${x(point.x_m)},${y(point.y_m)})`}><title>{point.checkpoint_id} · hole {point.hole_number} · {qualityName(point.coverage_status)}</title><circle r={14} className="course-map-hit" /><circle r={6} /></g>;
    })}
    {data.carts.map((cart) => {
      const selection: Selection = { kind: "cart", id: cart.cart_id };
      return <g key={cart.cart_id} role="button" tabIndex={0} aria-label={`Inspect ${cart.cart_id}`} aria-pressed={selected?.kind === "cart" && selected.id === cart.cart_id} className="course-map-cart" transform={`translate(${x(cart.x_m)},${y(cart.y_m)})`} onClick={() => onSelect(selection)} onKeyDown={(event) => activate(event, selection)}><title>{cart.cart_id} · ±{cart.accuracy_m} m · {age(data.clock.simulated_minute, cart.minute)}</title><circle r={14} className="course-map-hit" /><rect x={-5} y={-5} width={10} height={10} transform="rotate(45)" /><text x={10} y={8}>{cart.cart_id.replace("CART-", "")}</text></g>;
    })}
  </svg>;
}

function FrameEvidence({ frame, minute }: { frame: CourseFrame; minute: number }) {
  const [failed, setFailed] = useState(false);
  return <div className="course-frame-evidence">
    {failed ? <div className="course-image-unavailable"><p role="status">Image unavailable for this saved frame. No replacement image is shown.</p><button type="button" className="btn" onClick={() => setFailed(false)}>Retry image</button></div> : <div className="course-photo" style={{ aspectRatio: `${frame.width} / ${frame.height}` }}>
      {/* Local, hash-bound evidence is served by the Manager API, never by an image optimizer. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={frame.image_url} width={frame.width} height={frame.height} alt={`Synthetic camera image ${frame.frame_id} at ${frame.checkpoint_id}`} onError={() => setFailed(true)} />
      <svg viewBox={`0 0 ${frame.width} ${frame.height}`} className="course-detection-overlay" aria-label="Candidate detection regions">{frame.detections.map((detection, index) => <rect key={index} data-detection-box="true" x={detection.bbox[0]} y={detection.bbox[1]} width={detection.bbox[2] - detection.bbox[0]} height={detection.bbox[3] - detection.bbox[1]}><title>{detection.condition} · score {detection.score.toFixed(2)}</title></rect>)}</svg>
    </div>}
    <p><Badge tone={frame.quality === "USABLE" ? "muted" : "warn"}>{qualityName(frame.quality)}</Badge> <span className="fineprint">{age(minute, frame.minute)}</span></p>
    <div className="course-candidates"><h5>Candidate detections</h5>{frame.detections.length ? <ul>{frame.detections.map((detection, index) => <li key={index}>{detection.condition}<span>Score {detection.score.toFixed(2)}</span></li>)}</ul> : <p>{frame.quality === "USABLE" ? "No candidate detected in this image. This is not a full inspection clearance." : "Image quality prevents a reliable inspection; another image is needed."}</p>}<small>Not calibrated — these scores are not probabilities or confirmation of a defect.</small></div>
    <details className="course-frame-identity"><summary>Frame identity</summary><p className="mono">{frame.frame_id}</p><p className="mono">SHA-256 {frame.image_sha256}</p></details>
  </div>;
}

function CheckpointWork({ data, checkpointId }: { data: CourseOpsSnapshot; checkpointId: string }) {
  const cases = data.cases.filter((item) => item.checkpoint_id === checkpointId);
  const jobs = data.staff_jobs.filter((job) => job.checkpoint_id === checkpointId);
  return <div className="course-checkpoint-work"><h5>Recorded work at this point</h5>{cases.length === 0 && jobs.length === 0 ? <p>No case or human job is recorded here.</p> : null}{cases.map((item) => <article key={item.case_id}><strong>{item.condition_kind} · {item.status}</strong><small>{item.priority} · <span className="mono">{item.case_id}</span></small>{data.tasks.filter((task) => task.case_id === item.case_id).map((task) => <p key={task.task_id}>{task.task_kind} · {task.status}<small>{task.resource_id} ({task.resource_kind})</small></p>)}</article>)}{jobs.map((job) => <article key={job.job_id}><strong>{job.task_kind} · {job.status}</strong><small>Human job <span className="mono">{job.job_id}</span></small><small>{job.started_at_s === null ? "Start time unknown" : `Started at simulation second ${job.started_at_s}`}; {job.completed_at_s === null ? "completion not recorded" : `completed at simulation second ${job.completed_at_s}`}</small></article>)}</div>;
}

function RangeObservations({ data }: { data: CourseOpsSnapshot }) {
  const range = data.range;
  return <section className="course-range" aria-label="Saved driving range observations"><h3>Driving range and resources</h3>{range.status === "UNKNOWN" ? <EmptyNote>Range observations unavailable. Ball counts, robots and staffing remain unknown in this saved report.</EmptyNote> : <>
    <p>Observed at simulation minute {range.observed_minute ?? "unknown"}{range.observed_minute !== null ? ` · ${age(data.clock.simulated_minute, range.observed_minute)}` : ""}</p>
    <dl className="kv-grid"><KeyValue label="Inventory ratio">{range.inventory_fraction === null ? "Unknown" : `${Math.round(range.inventory_fraction * 100)}%`}</KeyValue><KeyValue label="Human resources">{range.staff === null ? "Unknown" : `${range.staff.busy} / ${range.staff.capacity} busy · ${range.staff.queued} queued`}</KeyValue></dl>
    {range.zones.length ? <div className="course-table-scroll"><table><thead><tr><th>Range zone</th><th>Observed balls</th><th>Access</th></tr></thead><tbody>{range.zones.map((zone) => <tr key={zone.zone_id}><td>{zone.zone_id}</td><td>{zone.balls === null ? "Unknown" : count(zone.balls)}</td><td>{zone.is_open === null ? "Unknown" : zone.is_open ? "Open" : "Closed"}</td></tr>)}</tbody></table></div> : <p>Zone ball counts unknown.</p>}
    {range.robots.length ? <div className="course-table-scroll"><table><thead><tr><th>Robot / location</th><th>Activity / health</th><th>Battery</th><th>Payload</th></tr></thead><tbody>{range.robots.map((robot) => <tr key={robot.robot_id}><td>{robot.robot_id}<small>{robot.location}</small></td><td>{robot.activity}<small>{robot.health}</small>{robot.awaiting_human ? <Badge tone="warn">Human help requested</Badge> : null}</td><td>{Math.round(robot.battery_fraction * 100)}%</td><td>{count(robot.payload_balls)} balls</td></tr>)}</tbody></table></div> : <p>Robot observations unknown.</p>}
  </>}</section>;
}
