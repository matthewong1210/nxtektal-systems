"""CPU perspective camera rehearsal and independent image-only pixel baseline.

This is deliberately a synthetic-domain baseline, not a trained vision model,
photorealistic renderer, physical sensor, or field accuracy claim. The renderer
owns scene truth and reference masks. ``detect_frame`` receives PNG bytes only;
its uncalibrated colour/texture rules cannot read truth, pose, labels or seeds.
Both image and reference masks are disposable projections of supplied inputs.
The explicit camera projects a flat synthetic ground plane at z=0 metres.
"""
from __future__ import annotations

from collections import deque
from io import BytesIO
import math

import numpy as np
from PIL import Image, ImageFilter

RENDERER_VERSION = "synthetic-ground-camera/v1"
DETECTOR_VERSION = "synthetic-pixel-baseline/v1"
CONDITIONS = ("DIVOT", "BUNKER_SURFACE", "STANDING_WATER", "DEBRIS")
_SCENE_KEYS = {"x_m", "y_m", "surface_type", "patch_radius_m", "wetness", "sunlight", "cloud_cover", "defects"}
_CAMERA_KEYS = {"x_m", "y_m", "z_m", "target_x_m", "target_y_m", "fov_deg", "motion_blur_px", "lens_occlusion"}


def _number(value: object, name: str, minimum: float = -1e6, maximum: float = 1e6) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be finite and within [{minimum}, {maximum}]")
    return float(value)


def _camera(camera: dict, width: int, height: int) -> dict:
    if type(camera) is not dict or not set(camera) <= _CAMERA_KEYS:
        raise ValueError("camera has unsupported fields")
    for key in ("x_m", "y_m", "z_m", "target_x_m", "target_y_m"):
        if key not in camera:
            raise ValueError(f"camera requires {key}")
    pos = np.array([_number(camera[k], k) for k in ("x_m", "y_m", "z_m")])
    if not 0.2 <= pos[2] <= 50:
        raise ValueError("camera z_m must be 0.2..50 metres above the synthetic ground")
    target = np.array([_number(camera[k], k) for k in ("target_x_m", "target_y_m")] + [0.0])
    forward = target - pos
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
    if np.linalg.norm(right) < 1e-8:
        raise ValueError("camera must have a horizontal viewing direction")
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    fov = _number(camera.get("fov_deg", 70), "fov_deg", 20, 110)
    return {"schema": "synthetic-camera-calibration/v1", "ground_z_m": 0.0,
            "width": width, "height": height, "focal_px": width / (2 * math.tan(math.radians(fov) / 2)),
            "position_m": pos.tolist(), "forward": forward.tolist(), "right": right.tolist(), "up": up.tolist()}


def _rays(calibration: dict) -> np.ndarray:
    yy, xx = np.mgrid[:calibration["height"], :calibration["width"]]
    x = (xx + 0.5 - calibration["width"] / 2) / calibration["focal_px"]
    y = (calibration["height"] / 2 - yy - 0.5) / calibration["focal_px"]
    return (np.asarray(calibration["forward"])[None, None, :]
            + x[..., None] * np.asarray(calibration["right"])
            + y[..., None] * np.asarray(calibration["up"]))


def _bbox(mask: np.ndarray) -> list[int] | None:
    rows, cols = np.nonzero(mask)
    return None if not len(rows) else [int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1]


def render_frame(scene: dict, camera: dict, seed: int, width: int = 384, height: int = 240) -> dict:
    """Render a detached supplied scene into PNG, separate labels and calibration.

    Scene coordinates and defect centres are world XY metres; ``radius_m`` is
    the approximate radius of a procedural surface mark. All defects lie on the
    ground plane, including DEBRIS (a visible litter/obstacle footprint, not an
    inferred object height). Labels/masks are renderer evaluation outputs only.
    Shadows, material grain, wetness and perspective vary the visible pixels.
    Camera blur/occlusion modify the image rather than a detector quality flag.
    """
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("seed must be a uint32")
    if type(width) is not int or type(height) is not int or not 64 <= width <= 2048 or not 48 <= height <= 1536:
        raise ValueError("image dimensions exceed the bounded renderer limits")
    if type(scene) is not dict or not set(scene) <= _SCENE_KEYS:
        raise ValueError("scene has unsupported fields")
    if not {"x_m", "y_m", "surface_type"} <= set(scene):
        raise ValueError("scene requires x_m, y_m and surface_type")
    cx, cy = (_number(scene[k], k) for k in ("x_m", "y_m"))
    surface = scene["surface_type"]
    if surface not in {"FAIRWAY", "GRASS", "BUNKER", "GREEN"}:
        raise ValueError("unsupported surface_type")
    radius = _number(scene.get("patch_radius_m", 12), "patch_radius_m", 1, 100)
    wetness = _number(scene.get("wetness", 0), "wetness", 0, 1)
    sunlight = _number(scene.get("sunlight", 0.8), "sunlight", 0, 1)
    cloud = _number(scene.get("cloud_cover", 0), "cloud_cover", 0, 1)
    defects = scene.get("defects", [])
    if type(defects) is not list or len(defects) > 64:
        raise ValueError("defects must be a bounded list")
    calibration = _camera(camera, width, height)
    blur = _number(camera.get("motion_blur_px", 0), "motion_blur_px", 0, 30)
    occlusion = _number(camera.get("lens_occlusion", 0), "lens_occlusion", 0, 1)
    rng = np.random.default_rng(seed)
    rays = _rays(calibration)
    ground = rays[..., 2] < -1e-6
    distance = np.divide(-camera["z_m"], rays[..., 2], out=np.zeros((height, width)), where=ground)
    ground &= distance < 160
    x = camera["x_m"] + rays[..., 0] * distance
    y = camera["y_m"] + rays[..., 1] * distance
    yy, xx = np.mgrid[:height, :width]

    # Grass grain and mowing bands follow world coordinates, not image labels.
    grain = rng.normal(0, 4.0, (height, width))
    mottling = 4 * np.sin(x * 4.7 + np.sin(y * 2.1)) + 3 * np.sin(y * 5.1 + x)
    bands = np.sin((x + y * 0.13) * 0.7) * 6
    rgb = np.empty((height, width, 3), dtype=float)
    rgb[:] = (68, 116, 47)
    rgb += (grain + mottling + bands)[..., None]
    patch = ((x - cx) / radius) ** 2 + ((y - cy) / (radius * 0.72)) ** 2 < 1
    if surface == "BUNKER":
        sand = np.array([205.0, 187.0, 141.0])
        ripples = np.sin(x * 15 + 0.2 * np.sin(y * 2)) * 2.5
        rgb[patch] = (sand[None, None, :] + (grain * 0.7 + mottling * 0.4 + ripples)[..., None])[patch]
        rim = (((x - cx) / (radius + 0.35)) ** 2 + ((y - cy) / (radius * 0.72 + 0.35)) ** 2 < 1) & ~patch
        rgb[rim] *= 0.7
    elif surface == "GREEN":
        rgb[patch] = (np.array([76, 139, 57])[None, None, :] + (grain * 0.8 + bands * 0.5)[..., None])[patch]
    # Broad, irregular tree shadows, with no class-specific metadata in RGB.
    shadow = (np.sin(x * 0.7 + y * 0.4 + seed % 13) + np.sin(y * 1.1)) > 1.3
    lighting = (0.86 + sunlight * 0.14 - wetness * 0.12) * np.where(shadow, 1 - 0.17 * sunlight * (1 - cloud), 1)
    rgb *= lighting[..., None]

    masks = {condition: np.zeros((height, width), dtype=bool) for condition in CONDITIONS}
    for defect in defects:
        if type(defect) is not dict or not {"condition", "x_m", "y_m"} <= set(defect) or not set(defect) <= {"condition", "x_m", "y_m", "radius_m"}:
            raise ValueError("a defect needs condition, world x_m/y_m and optional radius_m")
        condition = defect["condition"]
        if condition not in CONDITIONS:
            raise ValueError("unsupported defect condition")
        dx, dy = (_number(defect[k], k) for k in ("x_m", "y_m"))
        dr = _number(defect.get("radius_m", 1), "radius_m", 0.01, 20)
        u, v = (x - dx) / dr, (y - dy) / dr
        # Irregular boundaries instead of class-colour rectangles or text.
        radial = u * u + v * v
        edge = 1 + 0.12 * np.sin(u * 8) * np.cos(v * 7)
        region = ground & (radial < edge)
        if condition == "DIVOT":
            region &= (u * 0.55) ** 2 + (v * 1.6) ** 2 < 1
            colour = np.array([106, 61, 32]) + (grain + np.sin(u * 20) * 6)[..., None]
            colour += np.clip(v, -1, 1)[..., None] * 15
        elif condition == "BUNKER_SURFACE":
            footprints = (np.sin(u * 10 + v * 2) > 0.05) & (np.cos(v * 7) > -0.6)
            region &= footprints
            colour = np.array([125, 116, 88]) + (grain * 0.8 + np.sin(v * 18) * 8)[..., None]
        elif condition == "STANDING_WATER":
            reflection = 9 * np.sin(x * 14 + y * 5) + 5 * np.cos(y * 18)
            colour = np.array([40, 103, 137]) + (reflection + grain * 0.5)[..., None]
        else:
            region &= (np.abs(u) < 0.8) & (np.abs(v + u * 0.2) < 0.65)
            # Synthetic terracotta litter with folded-light facets.
            colour = np.array([181, 58, 40]) + (grain + 12 * np.sign(u - v))[..., None]
        rgb[region] = (colour * lighting[..., None])[region]
        for mask in masks.values():
            mask[region] = False
        masks[condition][region] = True

    # Sky cannot masquerade as the darker reflected water palette.
    sky = np.array([153, 186, 218]) * (1 - cloud * 0.17) + cloud * np.array([30, 21, 9])
    rgb[~ground] = np.broadcast_to(sky, rgb.shape)[~ground]
    # Fine grain survives on usable images; the image-only gate measures its loss.
    vignette = np.clip(1 - 0.08 * (((xx - width / 2) / width) ** 2 + ((yy - height / 2) / height) ** 2), 0.8, 1)
    image = Image.fromarray(np.clip(rgb * vignette[..., None], 0, 255).astype(np.uint8))
    if blur:
        # Horizontal exposure integration, a simple motion-blur assumption.
        offsets = range(-math.ceil(blur), math.ceil(blur) + 1)
        pixels = np.asarray(image).astype(float)
        padded = np.pad(pixels, ((0, 0), (math.ceil(blur), math.ceil(blur)), (0, 0)), mode="edge")
        blurred = np.zeros_like(pixels)
        for offset in offsets:
            start = math.ceil(blur) + offset
            blurred += padded[:, start:start + width]
        image = Image.fromarray(np.uint8(blurred / len(offsets))).filter(ImageFilter.GaussianBlur(blur / 4))
    if occlusion:
        pixels = np.array(image)
        # An opaque lens obstruction; its presence must be inferred from pixels.
        blocked = xx < width * occlusion + 0.015 * width * np.sin(yy / height * 8)
        pixels[blocked] = (5, 6, 5)
        image = Image.fromarray(pixels)
        for mask in masks.values():
            mask[blocked] = False
    stream = BytesIO()
    image.save(stream, format="PNG", optimize=False)
    labels = [{"condition": condition, "bbox": _bbox(mask), "visible_pixels": int(mask.sum())}
              for condition, mask in masks.items() if mask.any()]
    return {"png_bytes": stream.getvalue(), "labels": labels, "evaluation_masks": masks,
            "calibration": calibration, "renderer_version": RENDERER_VERSION}


def _components(mask: np.ndarray, minimum: int) -> list[tuple[list[int], int]]:
    """Connected components over a pixel mask; no scene-informed proposals."""
    pending = mask.copy()
    components = []
    height, width = pending.shape
    for y, x in zip(*np.nonzero(mask)):
        if not pending[y, x]:
            continue
        pending[y, x] = False
        queue = deque([(int(y), int(x))])
        left = right = int(x)
        top = bottom = int(y)
        count = 0
        while queue:
            row, col = queue.popleft()
            count += 1
            left, right = min(left, col), max(right, col)
            top, bottom = min(top, row), max(bottom, row)
            for ny, nx in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
                if 0 <= ny < height and 0 <= nx < width and pending[ny, nx]:
                    pending[ny, nx] = False
                    queue.append((ny, nx))
        if count >= minimum:
            components.append(([left, top, right + 1, bottom + 1], count))
    return sorted(components, key=lambda item: (-item[1], item[0]))


def detect_frame(png_bytes: bytes) -> dict:
    """Image-only deterministic baseline; scores are NOT calibrated likelihoods.

    Rule thresholds intentionally target these synthetic materials. A different
    renderer or real image distribution requires independent validation. Bad
    quality yields no detections, not a claim the inspected area is clear.
    """
    if type(png_bytes) is not bytes or len(png_bytes) > 20_000_000:
        raise ValueError("detector requires bounded PNG bytes only")
    with Image.open(BytesIO(png_bytes)) as image:
        if image.format != "PNG" or not 64 <= image.width <= 2048 or not 48 <= image.height <= 1536:
            raise ValueError("unsupported image format or dimensions")
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    r, g, b = (rgb[..., channel] for channel in range(3))
    gray = rgb.mean(axis=2)
    dark_fraction = float(np.mean(rgb.max(axis=2) < 14))
    sharpness = float(np.mean(np.abs(np.diff(gray, n=2, axis=1))))
    quality = "OCCLUDED" if dark_fraction > 0.28 else "BLURRED" if sharpness < 1.2 else "USABLE"
    result = {"quality": quality, "detections": [], "detector_version": DETECTOR_VERSION,
              "score_calibration": "NOT_CALIBRATED", "quality_metrics": {"dark_fraction": round(dark_fraction, 6), "sharpness": round(sharpness, 6)}}
    if quality != "USABLE":
        return result
    masks = {
        "DIVOT": (r > g * 1.36) & (g > b * 1.35) & (r < 145) & (g > 25),
        "BUNKER_SURFACE": (r > g * 1.025) & (r < g * 1.19) & (g > b * 1.2) & (r < 142) & (g > 62),
        "STANDING_WATER": (b > r * 1.6) & (g > r * 1.4) & (b > g * 1.13) & (r < 95) & (g > 40),
        "DEBRIS": (r > g * 2.25) & (r > b * 2.5) & (r > 108),
    }
    minimum = max(8, int(rgb.shape[0] * rgb.shape[1] / 18000))
    for condition, mask in masks.items():
        for bbox, area in _components(mask, minimum)[:12]:
            score = min(0.95, 0.55 + area / (rgb.shape[0] * rgb.shape[1]) * 30)
            result["detections"].append({"condition": condition, "bbox": bbox, "score": round(score, 6)})
    return result


def project_detection(bbox: list[float], calibration: dict) -> dict | None:
    """Intersect the detected box-centre pixel ray with the calibrated ground.

    This uses no scene, known defect centre, evaluation mask or synthetic label.
    It estimates a surface footprint centre, not an arbitrary object's 3D pose.
    The caller owns uncertainty, map association and admission to observations.
    """
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("bbox requires four pixel coordinates")
    left, top, right, bottom = [_number(v, "bbox", 0, 2048) for v in bbox]
    required = {"schema", "ground_z_m", "width", "height", "focal_px", "position_m", "forward", "right", "up"}
    if type(calibration) is not dict or set(calibration) != required or calibration["schema"] != "synthetic-camera-calibration/v1":
        raise ValueError("unsupported camera calibration")
    width, height = calibration["width"], calibration["height"]
    if type(width) is not int or type(height) is not int or not 64 <= width <= 2048 or not 48 <= height <= 1536:
        raise ValueError("invalid calibrated image dimensions")
    if not 0 <= left < right <= width or not 0 <= top < bottom <= height:
        raise ValueError("bbox is outside the calibrated frame")
    focal = _number(calibration["focal_px"], "focal_px", 1, 10000)
    vectors = {}
    for key in ("position_m", "forward", "right", "up"):
        value = calibration[key]
        if type(value) is not list or len(value) != 3:
            raise ValueError("calibration vectors require three finite coordinates")
        vectors[key] = np.array([_number(v, key) for v in value])
    basis = np.array([vectors["forward"], vectors["right"], vectors["up"]])
    if not np.allclose(basis @ basis.T, np.eye(3), atol=1e-6, rtol=0):
        raise ValueError("camera axes must be orthonormal")
    ground_z = _number(calibration["ground_z_m"], "ground_z_m")
    if ground_z != 0 or vectors["position_m"][2] <= ground_z:
        raise ValueError("calibration requires camera above synthetic z=0 ground")
    ray = (vectors["forward"] + ((left + right) / 2 - width / 2) / focal * vectors["right"]
           + (height / 2 - (top + bottom) / 2) / focal * vectors["up"])
    if ray[2] >= -1e-6:
        return None
    distance = (ground_z - vectors["position_m"][2]) / ray[2]
    if not 0 < distance < 160:
        return None
    point = vectors["position_m"] + distance * ray
    return {"x_m": float(point[0]), "y_m": float(point[1])}
