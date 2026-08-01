# Current assumptions & validity limits

## Placeholder policy

**No real specifications exist in this repo.** Every physical quantity in
`configs/` is tagged `source: placeholder` with a note; values were chosen only
for internal consistency (the pipeline must execute), not as design targets.
`collect_placeholders()` inventories them and every report embeds the count +
list. When supplier/measured data arrives, values are replaced and re-tagged
(`supplier` / `measured`) — the inventory shrinking to zero is the definition
of "configs are real".

## Modeling assumptions (mock adapter)

| Area | Assumption | Status |
|---|---|---|
| Drive | differential drive, constant placeholder speed/decel | placeholder; confirm vs AgileX SDK |
| Marker alignment | each approach removes a configured fraction of current error + Gaussian noise | abstraction of AprilTag servoing; replaced by real perception in Isaac/hardware |
| Docking guide | capture envelope + linear error-correction factor | first-order stand-in for real funnel geometry |
| Longitudinal error | squeezed out by drive-to-contact | assumes a contact-switch stop; verify on hardware |
| Unloading | configured success probability by dump angle | **NOT physics** — see below |
| Stability | static margin heuristic `(track/2 - tan(slope)*com_h)/(track/2)` | coarse indicator only; no dynamics, no payload shift |
| Contact force | unavailable (`contact_force_available: false`) | Isaac Sim PhysX will provide it |
| E-stop | trigger at configured distance; stop in v²/2a | idealized sensing/braking |

## Explicit physical-test risks (simulation will NOT validate these)

* Granular golf-ball flow out of the basket: bridging, jamming, wet-ball
  clumping, partial discharge.
* Ball-to-ball and ball-to-basket friction under real payloads.
* Real marker detection in outdoor lighting (glare, dusk, rain).
* Surface conditions: wet grass/rubber mats, debris, thermal drift of sensors.
* Long-term mechanical wear of guides and contact switches.

Any claim that the system "works" requires physical trials for the above; the
lab's role is to narrow the design space (tolerances, geometry, sequencing,
recovery logic) before hardware time is spent.

## Scope guards

* Handoff zone only (~10 m x 10 m); no course-scale navigation, no fleet.
* No robotic arm: concepts under test are passive guides, funnels, adjustable
  lift platforms, controlled basket tilt, marker-assisted alignment, contact
  switches / short-range sensors.
* Visual fidelity is a non-goal; testability and adapter-swappability rule.
