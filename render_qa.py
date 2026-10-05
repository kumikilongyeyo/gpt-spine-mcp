"""Render-space QA for silhouette readability and FX hierarchy.

Renders representative beats on a fixed padded canvas. For FX hierarchy it produces
three views of the exact same beat: full composite, art-only, and FX-only. The FX-only
pass matters because RGB-difference tests can miss a white/additive glow over already
white art due to saturation.
"""
from __future__ import annotations

import copy
import os

from PIL import Image

import spine_preview

FX_TOKENS = ("fx", "glow", "flash", "spark", "shine", "flare", "ring", "burst", "particle", "aura")
ACTIVE_CLIPS = {"attack", "hit", "land", "jump", "win", "mega_win", "celebration", "pop"}


def _sample_times(animation: dict, planned: dict | None = None) -> list[float]:
    if planned and planned.get("beats"):
        return sorted({round(float(beat.get("time", 0.0)), 4) for beat in planned["beats"]})[:8]
    times = {0.0}
    for channels in animation.get("bones", {}).values():
        for track in channels.values():
            if isinstance(track, list):
                times.update(round(float(key.get("time", 0.0)), 4) for key in track if isinstance(key, dict))
    ordered = sorted(times)
    if len(ordered) <= 8:
        return ordered
    picks = [0, 1, len(ordered)//4, len(ordered)//2, 3*len(ordered)//4, len(ordered)-2, len(ordered)-1]
    return [ordered[i] for i in sorted(set(picks))]


def _fixed_bounds(data: dict, padding: float = 0.30) -> dict:
    out = copy.deepcopy(data)
    skeleton = out.setdefault("skeleton", {})
    width = max(1, int(float(skeleton.get("width", 1))))
    height = max(1, int(float(skeleton.get("height", 1))))
    skeleton["width"] = max(width + 8, int(round(width * (1.0 + padding))))
    skeleton["height"] = max(height + 8, int(round(height * (1.0 + padding))))
    return out


def fx_slots(data: dict) -> list[str]:
    names = []
    for slot in data.get("slots", []):
        name = slot.get("name", "")
        low = name.casefold()
        if any(token in low for token in FX_TOKENS) or slot.get("blend") in {"additive", "screen"}:
            names.append(name)
    return names


def _filter_slots(data: dict, keep_names: set[str] | None = None,
                  drop_names: set[str] | None = None) -> dict:
    out = copy.deepcopy(data)
    keep_names = keep_names or set()
    drop_names = drop_names or set()
    if keep_names:
        out["slots"] = [slot for slot in out.get("slots", []) if slot.get("name") in keep_names]
    elif drop_names:
        out["slots"] = [slot for slot in out.get("slots", []) if slot.get("name") not in drop_names]
    allowed = {slot.get("name") for slot in out.get("slots", [])}
    for animation in out.get("animations", {}).values():
        tracks = animation.get("slots") if isinstance(animation, dict) else None
        if isinstance(tracks, dict):
            for name in list(tracks):
                if name not in allowed:
                    tracks.pop(name, None)
    return out


def _without_fx(data: dict, names: list[str]) -> dict:
    return _filter_slots(data, drop_names=set(names))


def _only_fx(data: dict, names: list[str]) -> dict:
    return _filter_slots(data, keep_names=set(names))


def _alpha_mask(image: Image.Image, size: int = 64, threshold: int = 32) -> tuple[list[int], tuple[int, int]]:
    rgba = image.convert("RGBA")
    rgba.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - rgba.width) // 2
    y = (size - rgba.height) // 2
    canvas.alpha_composite(rgba, (x, y))
    alpha = canvas.getchannel("A")
    return [1 if value >= threshold else 0 for value in alpha.getdata()], canvas.size


def _iou(a: list[int], b: list[int]) -> float:
    union = sum(1 for x, y in zip(a, b) if x or y)
    if not union:
        return 1.0
    inter = sum(1 for x, y in zip(a, b) if x and y)
    return inter / union


def _holes(mask: list[int], size: tuple[int, int]) -> int:
    width, height = size
    background = {i for i, value in enumerate(mask) if not value}
    visited = set()

    def neighbors(index: int):
        x, y = index % width, index // width
        if x > 0: yield index - 1
        if x + 1 < width: yield index + 1
        if y > 0: yield index - width
        if y + 1 < height: yield index + width

    holes = 0
    for start in list(background):
        if start in visited:
            continue
        stack = [start]
        visited.add(start)
        touches_edge = False
        while stack:
            node = stack.pop()
            x, y = node % width, node // width
            touches_edge = touches_edge or x == 0 or y == 0 or x == width - 1 or y == height - 1
            for nxt in neighbors(node):
                if nxt in background and nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        if not touches_edge:
            holes += 1
    return holes


def _touches_edge(image: Image.Image, margin: int = 1) -> bool:
    alpha = image.convert("RGBA").getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return False
    left, top, right, bottom = bbox
    width, height = image.size
    return left <= margin or top <= margin or right >= width - margin or bottom >= height - margin


def _art_pixels(art: Image.Image, other: Image.Image):
    a = art.convert("RGBA")
    b = other.convert("RGBA")
    if b.size != a.size:
        b = b.resize(a.size, Image.Resampling.LANCZOS)
    ap = list(a.getdata())
    bp = list(b.getdata())
    return [(x, y) for x, y in zip(ap, bp) if x[3] >= 32]


def _ssim_art_region(art: Image.Image, full: Image.Image) -> float:
    pairs = _art_pixels(art, full)
    if not pairs:
        return 1.0
    xs = [0.299*a[0] + 0.587*a[1] + 0.114*a[2] for a, _ in pairs]
    ys = [0.299*b[0] + 0.587*b[1] + 0.114*b[2] for _, b in pairs]
    n = len(xs)
    mx, my = sum(xs)/n, sum(ys)/n
    vx = sum((x-mx)**2 for x in xs) / max(1, n-1)
    vy = sum((y-my)**2 for y in ys) / max(1, n-1)
    cov = sum((x-mx)*(y-my) for x, y in zip(xs, ys)) / max(1, n-1)
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    numerator = (2*mx*my + c1) * (2*cov + c2)
    denominator = (mx*mx + my*my + c1) * (vx + vy + c2)
    return 1.0 if denominator <= 1e-9 else max(-1.0, min(1.0, numerator / denominator))


def _rgb_change_coverage(art: Image.Image, full: Image.Image, rgb_delta: float = 24.0) -> float:
    pairs = _art_pixels(art, full)
    if not pairs:
        return 0.0
    changed = 0
    for a, b in pairs:
        delta = (abs(a[0]-b[0]) + abs(a[1]-b[1]) + abs(a[2]-b[2])) / 3
        if delta >= rgb_delta:
            changed += 1
    return changed / len(pairs)


def _fx_overlap_coverage(art: Image.Image, fx: Image.Image, alpha_threshold: int = 24) -> float:
    """Fraction of visible art directly covered by visible FX.

    Unlike RGB-difference coverage, this still catches white additive FX over white art,
    where the full composite can saturate to the same RGB values.
    """
    pairs = _art_pixels(art, fx)
    if not pairs:
        return 0.0
    covered = sum(1 for _, effect in pairs if effect[3] >= alpha_threshold)
    return covered / len(pairs)


def _is_flash_time(spec: dict, at: float, fps: int) -> bool:
    tolerance = 2.0 / max(1, fps)
    for cue in spec.get("fx_beats", []):
        if cue.get("cue") == "impact_flash" and abs(float(cue.get("time", 0.0)) - at) <= tolerance:
            return True
    return False


def _finding(kind: str, clip: str, at: float, metric: str, value, threshold,
             severity: str, patch: dict) -> dict:
    return {
        "kind": kind,
        "clip": clip,
        "time": round(float(at), 4),
        "metric": metric,
        "value": value,
        "threshold": threshold,
        "severity": severity,
        "suggested_patch": patch,
    }


def audit(data: dict, images_dir: str, out_dir: str, motion_plan: dict | None = None,
          fps: int = 30, silhouette_delta_min: float = 0.15,
          ssim_min: float = 0.80, fx_coverage_max: float = 0.35) -> dict:
    """Run render checks #1 silhouette clarity and #9 FX hierarchy."""
    os.makedirs(out_dir, exist_ok=True)
    padded = _fixed_bounds(data)
    names = fx_slots(padded)
    art_data = _without_fx(padded, names)
    fx_data = _only_fx(padded, names) if names else None
    planned = (motion_plan or {}).get("clips", {})
    findings = []
    clips = {}

    for clip, animation in padded.get("animations", {}).items():
        spec = planned.get(clip, {})
        times = _sample_times(animation, spec)
        rows = []
        previous_mask = None
        previous_holes = None
        previous_time = None
        for index, at in enumerate(times):
            full = spine_preview.render_frame(padded, images_dir, clip, at, maxpx=320, transparent=True)
            art = spine_preview.render_frame(art_data, images_dir, clip, at, maxpx=320, transparent=True)
            fx = (spine_preview.render_frame(fx_data, images_dir, clip, at, maxpx=320, transparent=True)
                  if fx_data is not None else Image.new("RGBA", art.size, (0,0,0,0)))
            full_path = os.path.join(out_dir, f"{clip}_{index:02d}_{at:.3f}_full.png")
            art_path = os.path.join(out_dir, f"{clip}_{index:02d}_{at:.3f}_art.png")
            fx_path = os.path.join(out_dir, f"{clip}_{index:02d}_{at:.3f}_fx.png")
            full.save(full_path)
            art.save(art_path)
            fx.save(fx_path)

            mask, mask_size = _alpha_mask(art)
            holes = _holes(mask, mask_size)
            ssim = _ssim_art_region(art, full)
            rgb_coverage = _rgb_change_coverage(art, full)
            overlap_coverage = _fx_overlap_coverage(art, fx)
            coverage = max(rgb_coverage, overlap_coverage)
            flash = _is_flash_time(spec, at, fps)
            edge = _touches_edge(art)

            pose_delta = None
            if previous_mask is not None:
                pose_delta = 1.0 - _iou(previous_mask, mask)
                if clip in ACTIVE_CLIPS and pose_delta < silhouette_delta_min:
                    findings.append(_finding(
                        "silhouette_too_similar", clip, at, "silhouette_delta",
                        round(pose_delta, 4), {"min": silhouette_delta_min}, "medium",
                        {"operation": "increase_pose_extreme", "preserve_timing": True,
                         "compare_from_time": previous_time},
                    ))
                if previous_holes and previous_holes > 0 and holes == 0:
                    findings.append(_finding(
                        "negative_space_loss", clip, at, "interior_holes",
                        holes, {"previous": previous_holes, "min": 1}, "medium",
                        {"operation": "separate_overlapping_limbs_or_prop", "preserve_primary_action": True},
                    ))

            if edge:
                findings.append(_finding(
                    "padded_bounds_clip", clip, at, "touches_padded_edge", True,
                    {"expected": False}, "high",
                    {"operation": "fix_render_bounds_or_reduce_offscreen_travel", "change_animation_last": True},
                ))

            allowed_ssim = 0.68 if flash else ssim_min
            allowed_coverage = 0.65 if flash else fx_coverage_max
            if names and ssim < allowed_ssim:
                findings.append(_finding(
                    "fx_swallowing_art", clip, at, "art_region_ssim",
                    round(ssim, 4), {"min": allowed_ssim, "flash_exempt": flash},
                    "high" if ssim < 0.62 else "medium",
                    {"operation": "reduce_fx_alpha_or_scale", "preserve_impact_time": True,
                     "target_ssim": max(ssim_min, allowed_ssim)},
                ))
            if names and coverage > allowed_coverage:
                findings.append(_finding(
                    "fx_coverage_too_high", clip, at, "fx_coverage_over_art",
                    round(coverage, 4), {"max": allowed_coverage, "flash_exempt": flash}, "medium",
                    {"operation": "shorten_or_mask_fx", "target_coverage_max": fx_coverage_max},
                ))

            rows.append({
                "time": at, "full": full_path, "art": art_path, "fx": fx_path,
                "silhouette_delta_from_previous": None if pose_delta is None else round(pose_delta, 4),
                "negative_space_holes": holes,
                "art_region_ssim": round(ssim, 4),
                "fx_rgb_change_coverage": round(rgb_coverage, 4),
                "fx_overlap_coverage": round(overlap_coverage, 4),
                "fx_coverage_over_art": round(coverage, 4),
                "flash_window": flash,
                "touches_padded_edge": edge,
            })
            previous_mask, previous_holes, previous_time = mask, holes, at
        clips[clip] = rows

    high = sum(item["severity"] == "high" for item in findings)
    medium = sum(item["severity"] == "medium" for item in findings)
    score = max(0, 100 - high * 18 - medium * 6)
    return {
        "version": 2,
        "checks": [1, 9],
        "fps": fps,
        "fixed_bounds_padding": 0.30,
        "fx_slots": names,
        "ok": high == 0,
        "score": score,
        "findings": findings,
        "clips": clips,
        "render_dir": out_dir,
    }
