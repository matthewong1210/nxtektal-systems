# Handoff mechanism assets (placeholder slot)

Empty by design — the custom basket / lift / tilt mechanism is not designed yet.

Will hold:

* basket + lift mechanism CAD-derived USD (articulated: lift prismatic joint,
  tilt revolute joint)
* passive docking guide geometry (funnel / rails / V-guide variants) as
  separate USD props so guide geometry stays a sweepable parameter
* AprilTag marker plate models for the station bracket

Keep each concept variant a separate file; scenarios select variants via the
equipment profile's `docking_guide` section.
