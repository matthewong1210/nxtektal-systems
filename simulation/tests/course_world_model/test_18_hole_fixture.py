"""The full-course fixture reuses validated geometry, never invents readiness."""

from dataclasses import replace

import pytest

from nxt_commissioning import CommissionedSite, dumps_manifest
from nxt_course_world_model import (
    CourseWorldModel, MapQueryService, QueryStatus, ScanSourceType,
    dumps_model, validate_model_against_site,
)
from scripts.course_18_hole_fixture import build_fixture, checkpoint_catalog


@pytest.fixture(scope="module")
def fixture_pair():
    return build_fixture()


def test_fixture_has_independent_identities_and_complete_18_hole_layout(fixture_pair):
    site, model = fixture_pair
    assert site.site_id == model.site_id == "synthetic-course-18"
    assert site.deployment_id == model.deployment_id == "synthetic-course-18-monitoring-v0"
    assert [hole.hole_number for hole in model.holes] == list(range(1, 19))
    assert len(model.surfaces) == 54 and len(model.cart_paths) == 18
    assert len(site.spatial_reference.zone_definitions) == 18
    assert model.frame.crs_kind == "local_cartesian"
    assert all(source.source_type is ScanSourceType.SYNTHETIC_FIXTURE for source in model.scan_sources)
    assert not site.sensor_bindings and not site.robot_assets and not site.equipment_assets
    validate_model_against_site(model, site)


def test_manifest_and_model_round_trip_and_rebuild_identically(fixture_pair):
    site, model = fixture_pair
    other_site, other_model = build_fixture()
    assert dumps_manifest(site) == dumps_manifest(other_site)
    assert dumps_model(model) == dumps_model(other_model)
    assert CommissionedSite.from_dict(site.to_dict()).to_dict() == site.to_dict()
    assert CourseWorldModel.from_dict(model.to_dict()).content_digest == model.content_digest


def test_checkpoint_catalog_is_unique_and_points_to_correct_surface_and_hole(fixture_pair):
    _, model = fixture_pair
    catalog = checkpoint_catalog()
    assert len(catalog) == len({point["id"] for point in catalog}) == 54
    assert len({point["feature_id"] for point in catalog}) == 54
    service = MapQueryService(model)
    surfaces = {surface.feature_id: surface for surface in model.surfaces}
    paths = {path.hole_id: path for path in model.cart_paths}
    for point in catalog:
        assert point["hole"] == point["hole_number"]
        surface = surfaces[point["feature_id"]]
        assert surface.surface_type.value == point["surface_type"]
        assert surface.polygon.strictly_contains(point["x_m"], point["y_m"])
        assert surface.hole_id == f"hole-{point['hole_number']:02d}"
        assert paths[surface.hole_id].covers(point["cart_x_m"], point["cart_y_m"])
        assert (point["x_m"], point["y_m"]) != (point["cart_x_m"], point["cart_y_m"])
        result = service.get_surface(point["x_m"], point["y_m"])
        assert result.status is QueryStatus.OK and result.feature_id == point["feature_id"]
        assert service.get_hole_context(point["x_m"], point["y_m"]).hole_number == point["hole_number"]
        assert service.get_elevation(point["x_m"], point["y_m"]).status is QueryStatus.OK


def test_catalog_returns_fresh_data_each_time():
    catalog = checkpoint_catalog()
    catalog[0]["x_m"] = -999
    catalog.pop()
    assert len(checkpoint_catalog()) == 54
    assert checkpoint_catalog()[0]["x_m"] == 130.0


def test_cross_site_model_binding_is_rejected(fixture_pair):
    site, model = fixture_pair
    foreign = replace(site, site_id="other-synthetic-site")
    with pytest.raises(ValueError, match="site_id"):
        validate_model_against_site(model, foreign)


def test_point_checks_do_not_claim_course_wide_inspection_coverage(fixture_pair):
    _, model = fixture_pair
    assert "54 point checks" in model.display_name
    for point in checkpoint_catalog():
        assert not {"inspected", "coverage_percent", "confirmed", "repaired"} & point.keys()
