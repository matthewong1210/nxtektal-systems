"""Image evidence, hidden-truth isolation and perspective geometry checks."""
from copy import deepcopy
from io import BytesIO
import inspect
import math

import numpy as np
from PIL import Image
import pytest

from scripts.course_camera import detect_frame, project_detection, render_frame


def scene(condition=None, *, surface="FAIRWAY", x=0.0, y=0.0):
    return {"x_m": x, "y_m": y, "surface_type": surface, "defects": [] if condition is None else [
        {"condition": condition, "x_m": x, "y_m": y, "radius_m": 1.4}]}


def camera(**changes):
    return {"x_m": 0.0, "y_m": -10.0, "z_m": 2.0, "target_x_m": 0.0, "target_y_m": 0.0, **changes}


def test_png_and_reference_masks_are_separate_and_reproducible():
    inputs, pose = scene("BUNKER_SURFACE", surface="BUNKER"), camera()
    untouched = deepcopy((inputs, pose))
    first, second = (render_frame(inputs, pose, 73) for _ in range(2))
    assert first["png_bytes"] == second["png_bytes"]
    assert first["labels"] == second["labels"] and first["calibration"] == second["calibration"]
    assert (inputs, pose) == untouched
    assert first["png_bytes"].startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(first["png_bytes"])) as image:
        assert image.size == (384, 240) and image.mode == "RGB"
        assert not image.info  # no EXIF/text carrying labels or hidden positions
    for label in first["labels"]:
        mask = first["evaluation_masks"][label["condition"]]
        assert mask.dtype == bool and mask.shape == (240, 384)
        assert label["visible_pixels"] == int(mask.sum())
    assert render_frame(inputs, pose, 74)["png_bytes"] != first["png_bytes"]


@pytest.mark.parametrize("condition,surface", [
    ("DIVOT", "FAIRWAY"), ("BUNKER_SURFACE", "BUNKER"),
    ("STANDING_WATER", "GREEN"), ("DEBRIS", "FAIRWAY"),
])
def test_image_only_baseline_finds_visible_synthetic_marks(condition, surface):
    frame = render_frame(scene(condition, surface=surface), camera(), 3)
    answer = detect_frame(frame["png_bytes"])
    assert answer["quality"] == "USABLE"
    assert condition in {row["condition"] for row in answer["detections"]}
    mask = frame["evaluation_masks"][condition]
    for row in answer["detections"]:
        assert 0 < row["score"] < 1
        if row["condition"] == condition:
            left, top, right, bottom = row["bbox"]
            assert mask[top:bottom, left:right].any()
    assert answer["score_calibration"] == "NOT_CALIBRATED"


@pytest.mark.parametrize("surface", ["FAIRWAY", "BUNKER", "GREEN"])
def test_normal_surfaces_do_not_raise_defects(surface):
    frame = render_frame(scene(surface=surface), camera(), 17)
    assert detect_frame(frame["png_bytes"])["detections"] == []


def test_detector_api_has_no_truth_argument_and_labels_cannot_change_its_answer():
    assert list(inspect.signature(detect_frame).parameters) == ["png_bytes"]
    frame = render_frame(scene("DIVOT"), camera(), 13)
    before = detect_frame(frame["png_bytes"])
    frame["labels"] = [{"condition": "STANDING_WATER", "bbox": [0, 0, 384, 240]}]
    frame["calibration"]["position_m"] = [10000, 10000, 100]
    for mask in frame["evaluation_masks"].values():
        mask[:] = False
    assert detect_frame(frame["png_bytes"]) == before
    normal = render_frame(scene(), camera(), 13)
    assert detect_frame(normal["png_bytes"])["detections"] != before["detections"]


def test_hidden_defects_outside_camera_view_do_not_change_pixels_or_detections():
    normal = render_frame(scene(), camera(), 13)
    hidden = scene("STANDING_WATER", x=0, y=-40)
    hidden["surface_type"] = "FAIRWAY"
    hidden_frame = render_frame(hidden, camera(), 13)
    assert hidden_frame["png_bytes"] == normal["png_bytes"]
    assert hidden_frame["labels"] == []
    assert detect_frame(hidden_frame["png_bytes"])["detections"] == []


@pytest.mark.parametrize("change,quality", [({"motion_blur_px": 12}, "BLURRED"), ({"lens_occlusion": 0.6}, "OCCLUDED")])
def test_quality_is_measured_from_corrupted_pixels_and_returns_unknown(change, quality):
    frame = render_frame(scene("DIVOT"), camera(**change), 3)
    result = detect_frame(frame["png_bytes"])
    assert result["quality"] == quality
    assert result["detections"] == []
    clear_image = render_frame(scene("DIVOT"), camera(), 3)
    assert detect_frame(clear_image["png_bytes"])["quality"] == "USABLE"


def test_projection_uses_box_ray_not_a_known_target_coordinate():
    frame = render_frame(scene("DIVOT"), camera(), 3)
    detection = detect_frame(frame["png_bytes"])["detections"][0]
    target = project_detection(detection["bbox"], frame["calibration"])
    assert target is not None and math.hypot(target["x_m"], target["y_m"]) < 0.3
    shifted_box = [pixel + 20 if index % 2 == 0 else pixel for index, pixel in enumerate(detection["bbox"])]
    shifted = project_detection(shifted_box, frame["calibration"])
    assert shifted is not None and abs(shifted["x_m"] - target["x_m"]) > 0.6
    assert project_detection([160, 0, 200, 5], frame["calibration"]) is None


def test_pose_and_fov_change_apparent_size_and_position_without_changing_world_truth():
    near = render_frame(scene("STANDING_WATER", surface="GREEN"), camera(y_m=-8), 3)
    far = render_frame(scene("STANDING_WATER", surface="GREEN"), camera(y_m=-20), 3)
    assert near["labels"][0]["visible_pixels"] > far["labels"][0]["visible_pixels"] * 3
    offset = render_frame(scene("DIVOT", x=52, y=74), camera(x_m=52, y_m=64, target_x_m=52, target_y_m=74), 3)
    detection = detect_frame(offset["png_bytes"])["detections"][0]
    result = project_detection(detection["bbox"], offset["calibration"])
    assert result is not None and math.hypot(result["x_m"] - 52, result["y_m"] - 74) < 0.4
    narrow = render_frame(scene("DIVOT"), camera(fov_deg=40), 3)
    wide = render_frame(scene("DIVOT"), camera(fov_deg=90), 3)
    assert narrow["labels"][0]["visible_pixels"] > wide["labels"][0]["visible_pixels"]


def test_weather_and_material_changes_are_pixels_not_detector_context():
    dry = render_frame(scene("STANDING_WATER", surface="BUNKER"), camera(), 5)
    wet = render_frame({**scene("STANDING_WATER", surface="BUNKER"), "wetness": 0.8, "sunlight": 0.2, "cloud_cover": 0.9}, camera(), 5)
    assert dry["png_bytes"] != wet["png_bytes"]
    for frame in (dry, wet):
        result = detect_frame(frame["png_bytes"])
        assert result["quality"] == "USABLE"
        assert any(row["condition"] == "STANDING_WATER" for row in result["detections"])


def test_renderer_does_not_change_global_random_state():
    np.random.seed(231)
    expected = np.random.random(3)
    np.random.seed(231)
    render_frame(scene("DIVOT"), camera(), 99)
    assert np.array_equal(np.random.random(3), expected)


@pytest.mark.parametrize("scene_change,camera_change,seed", [
    ({"surface_type": "UNKNOWN"}, {}, 0), ({"wetness": -0.1}, {}, 0),
    ({"sunlight": float("nan")}, {}, 0), ({"truth_hint": "DIVOT"}, {}, 0),
    ({}, {"z_m": 0}, 0), ({}, {"x_m": float("inf")}, 0),
    ({}, {"fov_deg": 150}, 0), ({}, {"motion_blur_px": -1}, 0),
    ({}, {"lens_occlusion": 1.2}, 0), ({}, {}, -1), ({}, {}, True),
])
def test_invalid_scene_or_camera_fails_loudly(scene_change, camera_change, seed):
    with pytest.raises(ValueError):
        render_frame({**scene(), **scene_change}, camera(**camera_change), seed)


def test_detector_does_not_accept_paths_or_non_png_payloads():
    with pytest.raises(ValueError):
        detect_frame("/tmp/label-DIVOT.png")
    stream = BytesIO()
    Image.new("RGB", (100, 100)).save(stream, format="JPEG")
    with pytest.raises(ValueError):
        detect_frame(stream.getvalue())


@pytest.mark.parametrize("patch", [{"width": "384"}, {"forward": [0, 0, 0]},
                                  {"position_m": [0, 0, -1]}, {"ground_z_m": 1},
                                  {"focal_px": float("nan")}])
def test_projection_rejects_malformed_calibration(patch):
    calibration = render_frame(scene(), camera(), 0)["calibration"]
    with pytest.raises(ValueError):
        project_detection([180, 115, 200, 125], {**calibration, **patch})
