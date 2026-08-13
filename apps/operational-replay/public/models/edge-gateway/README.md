# Edge Gateway model assets

No GLB, glTF, texture, logo, or source CAD asset is currently stored in this
directory. The first Edge Gateway demo uses repository-authored procedural
geometry and CSS materials. Its dimensions are approximate and the result is a
presentation projection, not manufacturing truth.

## Future model-registry rules

Any cleared GLB or glTF added later must be registered explicitly rather than
discovered by filename. Each registry entry must:

- retain the procedural manifest's stable component ID;
- use the repository convention `1 Three.js unit = 1 meter` and preserve
  verified real-world scale through export and import;
- record asset provenance, source dimensions, license/clearance, and the
  approving workflow;
- remain local to this application, with no CDN or runtime network dependency;
- affect presentation geometry only and never become facility, telemetry,
  recommendation, safety, or execution truth; and
- load only from the code-split Edge Gateway route.

An absent optional model may resolve to the repository-authored procedural part.
Once a registry entry declares an asset, a missing, cross-origin-dependent,
malformed, empty, or dimensionally inconsistent file must produce a visible
asset error. The renderer uses Three.js' cached loader so repeated uses of one
source path do not issue duplicate model downloads. The loader must not hide
the error by silently substituting unrelated geometry.

Stable IDs are semantic links between the manifest, selection state, labels,
and model registry. Replacing an asset must not rename an ID merely because an
exporter's node names differ. Validate the model's bounding dimensions and axis
orientation against the manifest before accepting it; do not repair an unknown
scale with an unexplained scene transform.

## Approved replacement pipeline

Model conversion remains an offline, reviewed process:

```text
STEP / SolidWorks / Fusion 360
  -> approved Blender or CAD export workflow
  -> optimized GLB (preferred) or self-contained glTF with validated units, axes, and bounds
  -> registry entry using the existing stable component ID
  -> production-build visual and malformed-asset checks
```

Do not add a browser-side STEP converter. Do not commit source STEP or other
manufacturer CAD files unless repository policy and the asset's license
explicitly permit redistribution. Until both are documented, keep source CAD
out of the repository and commit no placeholder binary.

Every rendered replacement remains subject to:

- `CONCEPTUAL SYSTEM VISUALIZATION — NOT FOR FABRICATION`
- `SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA` when operating state is
  shown

See the application [README](../../../README.md) for the presentation truth
boundary and the [recording guide](../../../EDGE_GATEWAY_3D_DEMO_RECORDING.md)
for capture requirements.
