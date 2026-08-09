"""Coordinate, geometry, and spatial-reference validation."""

from __future__ import annotations

import copy

import pytest

from nxt_commissioning import CommissionedSite, CommissioningValidationError

from .conftest import clone_payload


@pytest.mark.parametrize(
    ("mutation", "token"),
    [
        (
            lambda spatial: spatial["coordinate_system"].update(
                {"identifier": "EPSG:0"}
            ),
            "EPSG",
        ),
        (
            lambda spatial: spatial["coordinate_system"].update(
                {"axes": ["east", "north"]}
            ),
            "axes",
        ),
        (
            lambda spatial: spatial["coordinate_system"].update(
                {"axes": ["east", "east", "up"]}
            ),
            "axes",
        ),
    ],
)
def test_invalid_coordinate_reference_is_rejected(
    commissioned_site_payload, mutation, token
):
    payload = clone_payload(commissioned_site_payload)
    mutation(payload["spatial_reference"])
    with pytest.raises((CommissioningValidationError, ValueError), match=token):
        CommissionedSite.from_dict(payload)


@pytest.mark.parametrize("bad_value", [True, float("nan"), float("inf")])
def test_origin_coordinates_must_be_finite_numbers(
    commissioned_site_payload, bad_value
):
    payload = clone_payload(commissioned_site_payload)
    payload["spatial_reference"]["facility_origin"]["x"] = bad_value
    with pytest.raises((TypeError, ValueError), match="x"):
        CommissionedSite.from_dict(payload)


def test_polygon_requires_three_distinct_points(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    polygon = payload["spatial_reference"]["geometry_references"][0]
    polygon["points"] = polygon["points"][:2]
    with pytest.raises(ValueError, match="polygon"):
        CommissionedSite.from_dict(payload)


def test_polygon_requires_nonzero_area(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    polygon = payload["spatial_reference"]["geometry_references"][0]
    first = polygon["points"][0]
    polygon["points"] = [
        copy.deepcopy(first),
        {**copy.deepcopy(first), "x": first["x"] + 1},
        {**copy.deepcopy(first), "x": first["x"] + 2},
    ]
    with pytest.raises(CommissioningValidationError, match="non-zero area"):
        CommissionedSite.from_dict(payload)


def test_large_projected_collinear_decimal_polygon_is_rejected(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    polygon = payload["spatial_reference"]["geometry_references"][0]
    first = polygon["points"][0]
    polygon["points"] = [
        copy.deepcopy(first),
        {**copy.deepcopy(first), "x": first["x"] + 0.1, "y": first["y"] + 0.1},
        {**copy.deepcopy(first), "x": first["x"] + 0.2, "y": first["y"] + 0.2},
    ]
    with pytest.raises(CommissioningValidationError, match="non-zero area"):
        CommissionedSite.from_dict(payload)


def test_centimetre_polygon_at_large_projected_origin_is_valid(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    polygon = payload["spatial_reference"]["geometry_references"][0]
    first = polygon["points"][0]
    polygon["points"] = [
        copy.deepcopy(first),
        {**copy.deepcopy(first), "x": first["x"] + 0.01},
        {
            **copy.deepcopy(first),
            "x": first["x"] + 0.01,
            "y": first["y"] + 0.01,
        },
        {**copy.deepcopy(first), "y": first["y"] + 0.01},
    ]
    site = CommissionedSite.from_dict(payload)
    geometry = next(
        item
        for item in site.spatial_reference.geometry_references
        if item.geometry_id == "geom-zone-east"
    )
    assert len(geometry.points) == 4


def test_polygon_must_not_self_intersect(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    polygon = payload["spatial_reference"]["geometry_references"][0]
    first = polygon["points"][0]
    polygon["points"] = [
        {**copy.deepcopy(first), "x": first["x"], "y": first["y"]},
        {**copy.deepcopy(first), "x": first["x"] + 3, "y": first["y"] + 3},
        {**copy.deepcopy(first), "x": first["x"], "y": first["y"] + 3},
        {**copy.deepcopy(first), "x": first["x"] + 2, "y": first["y"]},
    ]
    with pytest.raises(CommissioningValidationError, match="self-intersect"):
        CommissionedSite.from_dict(payload)


def test_geometry_source_uri_rejects_whitespace(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    payload["spatial_reference"]["geometry_references"][0][
        "source_uri"
    ] = "urn:nxt:geometry:bad value"
    with pytest.raises(ValueError, match="whitespace"):
        CommissionedSite.from_dict(payload)


def test_duplicate_geometry_id_is_rejected(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    geometries = payload["spatial_reference"]["geometry_references"]
    geometries.append(copy.deepcopy(geometries[0]))
    with pytest.raises(CommissioningValidationError, match="duplicate geometry ID"):
        CommissionedSite.from_dict(payload)


def test_duplicate_zone_id_is_rejected(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    zones = payload["spatial_reference"]["zone_definitions"]
    zones.append(copy.deepcopy(zones[0]))
    with pytest.raises(CommissioningValidationError, match="duplicate zone ID"):
        CommissionedSite.from_dict(payload)


def test_zone_geometry_reference_must_resolve(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    payload["spatial_reference"]["zone_definitions"][0][
        "geometry_reference"
    ] = "geom-missing"
    with pytest.raises(CommissioningValidationError, match="geometry_reference"):
        CommissionedSite.from_dict(payload)


def test_epsg_units_and_axes_must_match_authority(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    coordinate_system = payload["spatial_reference"]["coordinate_system"]
    coordinate_system.update(
        {
            "identifier": "EPSG:4326",
            "horizontal_unit": "m",
            "axes": ["east", "north", "up"],
        }
    )
    with pytest.raises(CommissioningValidationError, match="EPSG:4326"):
        CommissionedSite.from_dict(payload)
