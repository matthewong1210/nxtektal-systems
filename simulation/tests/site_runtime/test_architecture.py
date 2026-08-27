"""Architecture boundaries for the orchestration-only runtime layer."""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
import typing
from pathlib import Path


SIMULATION_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = SIMULATION_ROOT / "nxt_site_runtime"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_runtime_imports_only_existing_input_and_state_contracts():
    hot_path_files = [
        path
        for path in RUNTIME_ROOT.glob("*.py")
        if path.name != "composition.py"
    ]
    first_party = {
        module
        for path in hot_path_files
        for module in _imports(path)
        if module.startswith("nxt_")
    }
    assert first_party <= {
        "nxt_facility.state",
        "nxt_telemetry.assemble",
        "nxt_telemetry.observations",
    }
    forbidden = {
        "nxt_sim",
        "nxt_range_ops",
        "nxt_commissioning",
        "nxt_pilot_ops",
        "nxt_memory",
        "nxt_range_twin",
        "nxt_range_viewer",
    }
    assert not {module.split(".")[0] for module in first_party} & forbidden


def test_commissioning_is_setup_only_and_uses_existing_projection():
    imports = _imports(RUNTIME_ROOT / "composition.py")
    commissioning = {
        module for module in imports if module.startswith("nxt_commissioning")
    }
    assert commissioning == {
        "nxt_commissioning.contracts",
        "nxt_commissioning.projections",
    }
    source = (RUNTIME_ROOT / "composition.py").read_text(encoding="utf-8")
    assert "project_legacy_site_config(site, context)" in source


def test_runtime_defines_no_duplicate_domain_contracts():
    forbidden_classes = {
        "FacilityState",
        "Observation",
        "ObservationFrame",
        "AssemblyReport",
        "Recommendation",
        "RangeSimulation",
    }
    defined = set()
    for path in RUNTIME_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defined |= {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }
    assert not defined & forbidden_classes


def test_upstream_and_consumers_do_not_depend_on_runtime():
    package_names = (
        "nxt_sim",
        "nxt_range_ops",
        "nxt_facility",
        "nxt_telemetry",
        "nxt_commissioning",
        "nxt_pilot_ops",
        "nxt_memory",
        "nxt_range_twin",
        "nxt_range_viewer",
        # The edge adapter kit is pure conversion: composing its output into
        # a SequencedObservationFrame belongs to a composition root, not to
        # the package.  Only nxt_agent_runtime may import the runtime.
        "nxt_edge_observation",
        # Workflow enablement gates readiness before any runtime exists;
        # assembling the runtime it authorizes is composition-root work.
        "nxt_workflow_enablement",
        # The Course World Model is static spatial truth evaluated before
        # any runtime exists; composing it with runtime layers is
        # composition-root work.
        "nxt_course_world_model",
    )
    for package_name in ("nxt_commissioning", "nxt_pilot_ops", "nxt_edge_observation"):
        assert (SIMULATION_ROOT / package_name).is_dir(), (
            f"required existing package is missing: {package_name}"
        )
    offenders = []
    for package_name in package_names:
        package = SIMULATION_ROOT / package_name
        if not package.exists():
            continue
        for path in package.rglob("*.py"):
            if "nxt_site_runtime" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(SIMULATION_ROOT)))
    assert offenders == []


def test_deterministic_contracts_use_no_clock_uuid_or_randomness():
    banned = {"time", "datetime", "uuid", "random", "secrets"}
    for name in ("envelope.py", "ports.py", "checkpoints.py"):
        roots = {module.split(".")[0] for module in _imports(RUNTIME_ROOT / name)}
        assert not roots & banned, f"{name} imports {roots & banned}"


def test_contract_surface_imports_without_simulation_or_usd_stack():
    probe = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class Blocker(importlib.abc.MetaPathFinder):
            BLOCKED = {"simpy", "gymnasium", "numpy", "pandas", "pyarrow", "pxr",
                       "nxt_sim", "nxt_range_ops", "nxt_range_twin"}

            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in self.BLOCKED:
                    raise ImportError("blocked: " + fullname)
                return None

        sys.meta_path.insert(0, Blocker())
        import nxt_site_runtime
        from nxt_site_runtime import (
            FacilitySnapshotEnvelope,
            RuntimeCheckpoint,
            SiteRuntimePipeline,
        )
        print(FacilitySnapshotEnvelope.__name__, RuntimeCheckpoint.__name__,
              SiteRuntimePipeline.__name__)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=SIMULATION_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "FacilitySnapshotEnvelope RuntimeCheckpoint SiteRuntimePipeline" in result.stdout


def test_public_contract_annotations_are_introspectable():
    from nxt_site_runtime import FacilitySnapshotEnvelope, SequencedObservationFrame

    envelope_hints = typing.get_type_hints(FacilitySnapshotEnvelope)
    batch_hints = typing.get_type_hints(SequencedObservationFrame)

    assert "facility_state" in envelope_hints
    assert "assembly_report" in envelope_hints
    assert "frame" in batch_hints
    assert "upstream" in batch_hints


def test_injected_assembler_failure_is_typed_before_default_import():
    probe = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "nxt_telemetry.assemble":
                    raise ImportError("default assembler was imported")
                return None

        sys.meta_path.insert(0, Blocker())

        from nxt_site_runtime import (
            FailureCode,
            SiteRuntimeError,
            SiteRuntimePipeline,
            SourceReference,
        )
        from nxt_site_runtime.ports import SequencedObservationFrame
        from nxt_telemetry.observations import (
            Observation,
            ObservationFrame,
            ObservationStatus,
            SiteConfig,
            SourceType,
            UpstreamInputs,
        )

        observation = Observation(
            channel="sensor.test.value",
            value=1,
            sample_timestamp_s=10.0,
            available_timestamp_s=10.0,
            status=ObservationStatus.OK,
            source_type=SourceType.SENSOR,
            source_id="sensor.test",
            calibration_id="cal-1",
            confidence=1.0,
        )
        frame = ObservationFrame(t_s=10.0, observations=(observation,))
        upstream = UpstreamInputs((0.0,), 0, 0, 0.0, 1.0)
        reference = SourceReference(
            source_type="external_system",
            source_id="forecast.test",
            channel="upstream.site",
            reference_id="forecast.test:1",
            sample_timestamp_s=10.0,
            available_timestamp_s=10.0,
            confidence=1.0,
            status="ok",
            calibration_id="not-applicable",
        )
        batch = SequencedObservationFrame(0, frame, upstream, (reference,))
        config = SiteConfig(
            "probe", 1, 1, 1.0, 1, 1, 1, 1.0, 0, 1440, 30,
            (), (), (), (), (),
        )

        class Publisher:
            def publish(self, envelope):
                raise AssertionError("publisher must not be reached")

        def injected(frame, site, upstream):
            raise RuntimeError("injected assembler reached")

        runtime = SiteRuntimePipeline(
            site_id="site-probe",
            deployment_id="deploy-probe",
            site_config=config,
            publisher=Publisher(),
            assembler=injected,
        )
        try:
            runtime.process(batch)
        except SiteRuntimeError as exc:
            assert exc.failure.code is FailureCode.ASSEMBLY_FAILED
            assert "injected assembler reached" in exc.failure.detail
        else:
            raise AssertionError("expected typed assembly failure")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=SIMULATION_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_runtime_is_registered_as_a_distribution_package():
    pyproject = (SIMULATION_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"nxt_site_runtime"' in pyproject
    assert '"nxt_commissioning"' in pyproject
    assert '"nxt_pilot_ops"' in pyproject
