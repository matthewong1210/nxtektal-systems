# Robot assets (placeholder slot)

Empty by design — no real chassis data exists yet.

Will hold, once received from AgileX (see docs/missing_inputs.md):

* `agilex_<model>.urdf` / meshes — the chassis URDF + visual/collision meshes
* `agilex_<model>.usd` — USD conversion for Isaac Sim (via the URDF importer)

Conversion pipeline for supplier CAD (STEP/IGES): the installed
`omniverse-cad-to-simready` skill workflow (CAD -> USD -> SimReady conformance
-> validation) on an NVIDIA GPU machine. Do not commit multi-hundred-MB CAD
archives here; commit the derived USD/URDF plus a note pointing at the source.
