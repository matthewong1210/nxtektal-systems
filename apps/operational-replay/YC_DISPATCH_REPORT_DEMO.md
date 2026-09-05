# YC Dispatch / Report demo runbook

The `/yc-dispatch-report` route is a presentation-only filming surface with two
screen states: **Dispatch** and **Report**. It does not connect to the robot or
to an operational system.

Dispatch presents a fixed **scan-style range scene** built from a sanitized
derivative of the supplied driving-range photo. Its field labels, route, and
moving marker are an operator-authored **site presentation schematic** with a
**presentation-only route animation**. They are not surveyed geometry, SLAM
output, live tracking, a live digital twin, or autonomous navigation output.

## Start the app

From the repository root:

```bash
cd apps/operational-replay
npm ci
npm run dev
```

Open the exact local URL:

```text
http://localhost:3000/yc-dispatch-report
```

The route opens directly in Dispatch and does not advance automatically unless
autoplay is enabled in the URL.

## Filming controls

Keep the browser window focused, then use:

| Control | Result |
|---|---|
| `Space` | Change from Dispatch to Report |
| `ArrowRight` | Change from Dispatch to Report |
| `R` | Reset to Dispatch |
| `F` | Request browser fullscreen |
| Click the large Dispatch status | Change from Dispatch to Report |

The browser may refuse a fullscreen request under its own permissions or
policy. If that happens, use the browser's fullscreen command. `Esc` normally
exits fullscreen.

## Direct state and autoplay URLs

Use these URL examples when preparing a recording:

```text
http://localhost:3000/yc-dispatch-report?state=dispatch
http://localhost:3000/yc-dispatch-report?state=report
http://localhost:3000/yc-dispatch-report?autoplay=1
http://localhost:3000/yc-dispatch-report?autoplay=1&delay=12000
```

`delay` is the autoplay delay in milliseconds. If it is missing or invalid,
the route uses `autoplayDelayMs` from the filming configuration. Autoplay is
off by default; the recommended supervised filming flow uses `Space` after the
physical run.

## Update the filming values

Edit the single configuration object in:

```text
app/yc-dispatch-report/yc-dispatch-report.config.ts
```

Enter only measured field-test values before recording. If `runtime`,
`ballsCollected`, or `collectionPasses` has not been measured and confirmed,
leave its value as `"—"`. Do not estimate or fabricate a value. The optional
facility name should remain unset until the filming facility is confirmed.

Restart the development server after editing if the change is not picked up
automatically, then check both states before recording.

## Recommended filming sequence

1. Open the Dispatch screen at the default URL and request fullscreen.
2. Begin recording with **MISSION DISPATCHED** visible.
3. Let the presentation-only marker travel along its predefined on-screen
   route; this animation does not represent a live robot position.
4. The operator controls the physical robot from outside the camera frame.
5. Film the supervised physical robot collecting balls.
6. Return the camera to the monitor.
7. Press `Space` to display the Report screen.
8. Show **MISSION COMPLETE**, the configured report values, and the supervised
   prototype execution mode.
9. End the recording.

## Prototype disclosure and limits

The on-screen disclosure must remain visible:

> Prototype orchestration demo · supervised hardware execution

Both screen states and every metric are operator-authored presentation
content. Pressing a key or clicking the status changes only the browser view:
no live dispatch occurs; it does not issue a hardware command, admit a mission,
or claim autonomous execution. The route receives no live telemetry and does
not automatically measure or infer a physical outcome.

The fixed scene coordinates in
`app/yc-dispatch-report/scanned-range-scene.config.ts` are normalized visual
placement values for this filming composition only. Do not describe them as
measured site coordinates, a surveyed map, or robot navigation input.

The Report state generates presentation content only. It performs no database
write, upload, or other persistence. Do not narrate the demo as proof of
autonomous motion, live telemetry, or a saved operational record.
