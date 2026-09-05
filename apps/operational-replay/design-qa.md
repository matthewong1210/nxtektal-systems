# YC scanned-range visual design QA

## Evidence

- Source visual truth: `public/yc-site-schematic/range-grass-scan-demo.webp`
- Implementation screenshot: external QA artifact `dispatch-grass-only-1366x768.jpg`
- Comparison artifact: external QA artifact `design-qa-grass-comparison.png`
- Route and state: `/yc-dispatch-report`, Dispatch
- CSS viewport: 1366 × 768 at device scale factor 1
- Source pixels: 1448 × 1086
- Implementation capture pixels: 1366 × 768
- Scene CSS size: 1229 × 553
- Density normalization: the source was cropped to 1448 × 652 at the configured 58% vertical object position; the implementation scene was cropped to its measured 1229 × 553 bounds; both sides were scaled to 800 × 360 for one side-by-side comparison

## Full-view comparison evidence

The revised source and implementation both center the uninterrupted grass range surface with only a low, indistinct tree boundary. Skyline buildings, transport infrastructure, and foreground tee equipment are absent. The implementation adds the requested restrained graphite treatment, mission hierarchy, normalized site labels, a single readable route, and one green robot marker without obscuring the field. The composition stays sparse and presentation-led rather than becoming a GIS view or dense dashboard.

The required fidelity surfaces were checked:

- Fonts and typography: the existing Geist/Geist Mono system is preserved; the mission title remains the dominant filming read, and the secondary labels retain legible weight and tracking at all required viewports.
- Spacing and layout rhythm: the scene, header, and footer remain inside the viewport at 1920 × 1080, 1440 × 900, and 1366 × 768, with no document scrolling or clipping.
- Colors and visual tokens: matte graphite, soft white, and restrained NXTektal green match the approved route design and keep state emphasis clear without neon overload.
- Image quality and asset fidelity: the optimized WebP stays sharp at the largest scene size, preserves believable range depth while isolating the grass surface, and contains no ICC, EXIF, XMP, or GPS metadata.
- Copy and content: all mission identifiers, site labels, presentation-only disclosures, and the supervised-hardware footer are present. No unsupported scan, survey, live-tracking, digital-twin, or autonomy claim appears.

## Focused-region evidence

A separate focused crop was not needed because the normalized 1600 × 360 comparison keeps the horizon, grass texture, route, robot marker, four spatial labels, and mission rail readable together. The full Dispatch screen was inspected at all three required viewports; the smallest labels remain legible and inside their containers at 1366 × 768.

## Interaction and runtime evidence

- Clicking `MISSION DISPATCHED` transitions to Report.
- `R` returns to Dispatch.
- `ArrowRight` and `Space` transition to Report.
- The robot marker changed screen position during a 1.2 second production-browser sample, confirming visible route motion.
- The Report route renders directly with `MISSION COMPLETE`, `Mission report generated`, and `Supervised prototype`.
- Production-browser console warnings and errors: none observed.

## Findings

No actionable P0, P1, or P2 findings remain.

## Comparison history

1. Initial 1920 × 1080 review found a P2 collision between the right-aligned scene descriptor group and the `DISPATCHED` badge.
2. The descriptor group was moved from normalized x = 0.965 to x = 0.8 in the scene configuration.
3. The revised 1920 × 1080 comparison shows clear separation, while the 1440 × 900 and 1366 × 768 captures preserve the same hierarchy without clipping.
4. A follow-up visual direction removed the rear skyline and made the scanned grass surface the sole prominent site subject.
5. The asset was regenerated without buildings, bridge, road, utility elements, or foreground tee equipment, then renamed to invalidate the previous optimized-image cache.
6. The final grass-only comparison shows the simplified horizon and field matching cleanly behind the unchanged route and presentation controls, with no new P0, P1, or P2 issue.

## Follow-up polish

No P3 polish is required for the requested filming pass.

final result: passed
