"""Visual anatomy inference for GPT Spine Animation Intelligence V4.

This module is intentionally dependency-light. It fuses PSD semantics with geometry,
bilateral symmetry, alpha silhouette features, and neighborhood relationships so weakly
named layers can become useful anatomy without guessing recklessly.
"""
from __future__ import annotations

import math
import os
from collections import defaultdict
from typing import Iterable

from PIL import Image


ANATOMY_ROLES = {
    "head", "neck", "torso", "pelvis", "upper_arm", "lower_arm", "hand",
    "upper_leg", "lower_leg", "foot", "fore_leg", "hind_leg", "paw", "hoof",
    "tail", "wing", "hair", "cloth", "eye", "pupil", "eyelid", "brow", "mouth",
    "jaw", "nose", "ear",
}


def _bounds(item: dict) -> tuple[float, float, float, float]:
    value = list(item.get("bounds") or [0, 0, 0, 0])
    value += [0] * max(0, 4 - len(value))
    return tuple(float(v) for v in value[:4])


def _alpha_features(path: str) -> dict:
    try:
        image = Image.open(path).convert("RGBA")
    except (OSError, ValueError):
        return {}
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return {}
    width, height = image.size
    x0, y0, x1, y1 = bbox
    opaque = 0
    total_alpha = 0
    sx = sy = 0.0
    pixels = alpha.load()
    for y in range(y0, y1):
        for x in range(x0, x1):
            a = pixels[x, y]
            if a <= 16:
                continue
            opaque += 1
            total_alpha += a
            sx += x * a
            sy += y * a
    if not total_alpha:
        return {}
    return {
        "alpha_fill": round(opaque / max(1, width * height), 4),
        "alpha_cx": round((sx / total_alpha) / max(1, width - 1), 4),
        "alpha_cy": round((sy / total_alpha) / max(1, height - 1), 4),
        "alpha_aspect": round((x1 - x0) / max(1, y1 - y0), 4),
    }


def _distance(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _known_center(items: list[dict], roles: set[str]) -> tuple[float, float] | None:
    points = []
    for item in items:
        if item.get("role") in roles:
            x, y, _, _ = _bounds(item)
            points.append((x, y))
    if not points:
        return None
    return (sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points))


def _scene_extent(items: list[dict]) -> tuple[float, float, float, float]:
    valid = [_bounds(item) for item in items if _bounds(item)[2] > 0 and _bounds(item)[3] > 0]
    if not valid:
        return (-1, -1, 2, 2)
    min_x = min(x - w / 2 for x, y, w, h in valid)
    max_x = max(x + w / 2 for x, y, w, h in valid)
    min_y = min(y - h / 2 for x, y, w, h in valid)
    max_y = max(y + h / 2 for x, y, w, h in valid)
    return min_x, min_y, max(1.0, max_x - min_x), max(1.0, max_y - min_y)


def _side_from_x(x: float, center_x: float, width: float) -> str:
    if x < center_x - width * .055:
        return "l"
    if x > center_x + width * .055:
        return "r"
    return ""


def _pair_candidates(items: list[dict], center_x: float, scene_width: float) -> dict[int, int]:
    """Pair similar unnamed layers mirrored around the body center."""
    unknown = [(index, item) for index, item in enumerate(items)
               if item.get("role") == "unknown" and _bounds(item)[2] > 0]
    pairs = {}
    for i, (index, item) in enumerate(unknown):
        if index in pairs:
            continue
        x, y, w, h = _bounds(item)
        mirror_x = 2 * center_x - x
        best = None
        for other_index, other in unknown[i + 1:]:
            if other_index in pairs:
                continue
            ox, oy, ow, oh = _bounds(other)
            size_error = abs(w - ow) / max(1, max(w, ow)) + abs(h - oh) / max(1, max(h, oh))
            mirror_error = abs(ox - mirror_x) / max(1, scene_width)
            y_error = abs(oy - y) / max(1, h, oh)
            score = size_error * .45 + mirror_error * 2.5 + y_error * .35
            if best is None or score < best[0]:
                best = (score, other_index)
        if best and best[0] < .42:
            pairs[index] = best[1]
            pairs[best[1]] = index
    return pairs


def _role_from_geometry(item: dict, center_x: float, center_y: float,
                        scene_width: float, scene_height: float,
                        paired: bool, known: dict[str, tuple[float, float] | None]) -> tuple[str, float, list[str]]:
    x, y, w, h = _bounds(item)
    if w <= 0 or h <= 0:
        return "unknown", 0.0, []
    nx = (x - center_x) / scene_width
    ny = (y - center_y) / scene_height
    aspect = w / max(1.0, h)
    area = (w * h) / max(1.0, scene_width * scene_height)
    evidence = []

    candidates: list[tuple[float, str, str]] = []
    lateral = abs(nx)

    # Central masses first.
    if lateral < .16 and -.05 <= ny <= .24 and area > .055:
        candidates.append((.82 + min(.08, area), "torso", "large central upper-body mass"))
    if lateral < .18 and -.27 <= ny <= .04 and .025 < area < .18:
        candidates.append((.76 + min(.08, area), "pelvis", "central mass below torso"))
    if lateral < .15 and ny > .25 and .018 < area < .14:
        candidates.append((.75 + min(.08, area), "head", "central mass above torso"))
    if lateral < .12 and .17 < ny < .34 and area < .035:
        candidates.append((.66, "neck", "small connector between head and torso"))

    # Bilateral limbs. Pair evidence is deliberately important so random props do not become arms.
    pair_bonus = .13 if paired else 0.0
    if lateral > .16 and .02 < ny < .30 and h >= w * .85:
        candidates.append((.64 + pair_bonus, "upper_arm", "paired lateral upper-body piece"))
    if lateral > .20 and -.12 < ny < .18 and h >= w * .9:
        candidates.append((.62 + pair_bonus, "lower_arm", "paired lateral mid-body piece"))
    if lateral > .20 and -.18 < ny < .12 and area < .035 and aspect > .65:
        candidates.append((.60 + pair_bonus, "hand", "small paired arm-end piece"))
    if lateral > .09 and -.38 < ny < -.03 and h >= w * 1.15:
        candidates.append((.66 + pair_bonus, "upper_leg", "paired elongated piece below pelvis"))
    if lateral > .08 and ny < -.25 and h >= w * 1.05:
        candidates.append((.65 + pair_bonus, "lower_leg", "paired elongated lower-limb piece"))
    if lateral > .08 and ny < -.38 and aspect > 1.15 and area < .045:
        candidates.append((.68 + pair_bonus, "foot", "paired wide piece at lower extremity"))

    # Use known anchors to increase confidence and distinguish adjacent limb segments.
    head = known.get("head")
    torso = known.get("torso")
    pelvis = known.get("pelvis")
    if torso and lateral > .15:
        dist = _distance((x, y), torso) / max(1, scene_height)
        if dist < .30:
            candidates.append((.71 + pair_bonus, "upper_arm", "close to known torso/shoulder region"))
    if pelvis and y < pelvis[1] and lateral > .06:
        dist = _distance((x, y), pelvis) / max(1, scene_height)
        if dist < .38:
            candidates.append((.72 + pair_bonus, "upper_leg", "below and close to known pelvis"))
    if head and abs(y - head[1]) < scene_height * .13 and area < .04:
        candidates.append((.56, "hair", "small piece around known head"))

    if not candidates:
        return "unknown", .0, []
    candidates.sort(reverse=True)
    score, role, reason = candidates[0]
    evidence.append(reason)
    if paired:
        evidence.append("bilateral symmetry match")
    return role, round(min(.94, score), 2), evidence


def enrich_semantics(items: Iterable[dict], images_dir: str = "") -> dict:
    """Return enriched semantic items plus a visual-anatomy report.

    Naming semantics remain authoritative. Geometry is only allowed to replace an unknown
    role, or add side evidence to a role that already exists.
    """
    output = [dict(item) for item in items]
    min_x, min_y, scene_width, scene_height = _scene_extent(output)
    known_center = _known_center(output, {"torso", "pelvis", "head"})
    center_x = known_center[0] if known_center else min_x + scene_width / 2
    center_y = (_known_center(output, {"torso"}) or (center_x, min_y + scene_height * .56))[1]
    pairs = _pair_candidates(output, center_x, scene_width)
    known = {role: _known_center(output, {role}) for role in ("head", "torso", "pelvis")}
    inferred = []

    for index, item in enumerate(output):
        x, y, w, h = _bounds(item)
        attachment = item.get("attachment") or item.get("path") or ""
        features = {}
        if images_dir and attachment:
            for candidate in (
                os.path.join(images_dir, attachment + ".png"),
                os.path.join(images_dir, (item.get("path") or attachment) + ".png"),
            ):
                if os.path.isfile(candidate):
                    features = _alpha_features(candidate)
                    break
        item["visual_features"] = features
        if not item.get("side"):
            side = _side_from_x(x, center_x, scene_width)
            if side and index in pairs:
                item["side"] = side
                item.setdefault("evidence", []).append("visual bilateral side inference")

        if item.get("role") != "unknown":
            continue
        role, score, evidence = _role_from_geometry(
            item, center_x, center_y, scene_width, scene_height, index in pairs, known,
        )
        if role == "unknown" or score < .72:
            item.setdefault("warnings", []).append("visual anatomy could not resolve this layer safely")
            continue
        item["role"] = role
        item["parent_role"] = {
            "head": "torso", "neck": "torso", "torso": "body", "pelvis": "torso",
            "upper_arm": "torso", "lower_arm": "upper_arm", "hand": "lower_arm",
            "upper_leg": "pelvis", "lower_leg": "upper_leg", "foot": "lower_leg",
            "hair": "head",
        }.get(role, item.get("parent_role", "root"))
        item["visual_inference"] = {"role": role, "confidence": score, "evidence": evidence}
        item["confidence"] = max(float(item.get("confidence", 0)), score)
        item.setdefault("evidence", []).extend(evidence)
        item["warnings"] = [warning for warning in item.get("warnings", [])
                            if warning not in {"unrecognized layer role", "low semantic confidence"}]
        inferred.append({"attachment": attachment, "role": role, "side": item.get("side", ""),
                         "confidence": score, "evidence": evidence})

    role_counts = defaultdict(int)
    for item in output:
        role_counts[item.get("role", "unknown")] += 1
    return {
        "version": 4,
        "layers": output,
        "inferred": inferred,
        "role_counts": dict(role_counts),
        "unresolved": [item.get("attachment") or item.get("path") for item in output
                       if item.get("role") == "unknown"],
        "body_center": [round(center_x, 3), round(center_y, 3)],
        "bilateral_pairs": len(pairs) // 2,
    }
