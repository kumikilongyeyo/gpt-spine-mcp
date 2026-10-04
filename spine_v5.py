"""V5 rendered self-critique and Photoshop blend translation.

Runs after V4 and before editable .spine creation. It preserves PSD blend intent,
fixes blend-neutral transparent texels, renders representative animation frames,
audits visual problems, and applies only conservative automatic corrections.

V6 hardening note: PSD traversal/naming now comes from ``spine_psd`` so rigging,
inspection and blend translation cannot silently disagree about selected groups or
attachment names. JSON writes use ``spine_guard.atomic_write_json``.
"""
from __future__ import annotations

import json
import os

from PIL import Image, ImageChops, ImageStat

import spine_blend
import spine_guard
import spine_preview
import spine_psd


def apply_psd_blends(source: str, runtime_json: str, images_dir: str,
                     source_group: str = "") -> dict:
    """Read PSD blend modes and translate them to Spine slot blending."""
    if not source.lower().endswith(".psd"):
        return {"applied": False, "reason": "source is not PSD", "layers": [], "warnings": []}

    scan = spine_psd.scan_psd(source, source_group)
    metadata = {}
    warnings = []
    for leaf in scan.leaves:
        name = leaf.attachment
        path = leaf.layer_path
        strategy = spine_blend.translate_psd_blend(
            leaf.blend_mode, role=leaf.role, layer_name=path,
        )
        meta = {
            "layer_path": path,
            "psd_blend": strategy["psd"],
            "spine_blend": strategy["spine"],
            "blend_exact": strategy["exact"],
            "strategy": strategy["strategy"],
            "bake_required": strategy["bake_required"],
        }
        if strategy["warning"]:
            warnings.append(f"{path}: {strategy['warning']}")
        image_path = os.path.join(images_dir, f"{name}.png")
        if os.path.isfile(image_path):
            try:
                with Image.open(image_path) as source_image:
                    image = source_image.convert("RGBA")
                risk = spine_blend.source_risk(image, strategy["spine"])
                meta["texture_risk"] = risk
                cleaned = spine_blend.sanitize_transparent_rgb(image, strategy["spine"])
                cleaned.save(image_path)
            except OSError as exc:
                warnings.append(f"{path}: could not inspect texture: {exc}")
        metadata[name] = meta

    with open(runtime_json, encoding="utf-8") as handle:
        data = json.load(handle)
    patched = []
    for slot in data.get("slots", []):
        name = slot.get("name", "")
        meta = metadata.get(name)
        if not meta:
            continue
        blend = meta["spine_blend"]
        if blend == "normal":
            slot.pop("blend", None)
        else:
            slot["blend"] = blend
        patched.append({
            "slot": name,
            "psd": meta["psd_blend"],
            "spine": blend,
            "exact": meta["blend_exact"],
            "bake_required": meta["bake_required"],
        })
    spine_guard.atomic_write_json(runtime_json, data)
    slot_audit = spine_blend.audit_slots(data.get("slots", []), metadata)
    return {
        "applied": True,
        "patched": patched,
        "patched_count": len(patched),
        "layers": metadata,
        "slot_audit": slot_audit,
        "warnings": warnings,
    }


def _sample_times(animation: dict, planned: dict | None = None) -> list[float]:
    if planned and planned.get("beats"):
        return sorted({round(float(beat.get("time", 0)), 3) for beat in planned["beats"]})[:7]
    times = {0.0}
    for channels in animation.get("bones", {}).values():
        for track in channels.values():
            if isinstance(track, list):
                times.update(round(float(key.get("time", 0)), 3) for key in track if isinstance(key, dict))
    for channels in animation.get("slots", {}).values():
        for track in channels.values():
            if isinstance(track, list):
                times.update(round(float(key.get("time", 0)), 3) for key in track if isinstance(key, dict))
    ordered = sorted(times)
    if len(ordered) <= 7:
        return ordered
    picks = [0, 1, len(ordered) // 3, len(ordered) // 2,
             2 * len(ordered) // 3, len(ordered) - 2, len(ordered) - 1]
    return [ordered[index] for index in sorted(set(picks))]


def _frame_metrics(image: Image.Image) -> dict:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return {"blank": True, "coverage": 0.0, "touches_edge": False, "dark_visible": 0.0}
    width, height = rgba.size
    left, top, right, bottom = bbox
    coverage = ((right - left) * (bottom - top)) / max(1, width * height)
    # Keep this metric cheap: operate only inside the visible bounding box.
    visible_crop = rgba.crop(bbox)
    pixels = visible_crop.getdata()
    visible = [(r, g, b, a) for r, g, b, a in pixels if a >= 48]
    dark = sum(1 for r, g, b, _ in visible if max(r, g, b) < 18) / max(1, len(visible))
    return {
        "blank": False,
        "coverage": round(coverage, 4),
        "touches_edge": bool(left <= 1 or top <= 1 or right >= width - 1 or bottom >= height - 1),
        "dark_visible": round(dark, 4),
        "bbox": [left, top, right, bottom],
    }


def _difference(a: Image.Image, b: Image.Image) -> float:
    before = a.convert("RGB")
    after = b.convert("RGB")
    if before.size != after.size:
        after = after.resize(before.size)
    diff = ImageChops.difference(before, after)
    stat = ImageStat.Stat(diff)
    return round(sum(stat.mean) / (3 * 255), 4)


def _conservative_autofix(data: dict, issues: list[dict]) -> list[str]:
    """Apply only fixes that cannot destroy authored posing."""
    fixes = []
    for issue in issues:
        if issue.get("kind") != "fx_setup_visible":
            continue
        clip = issue.get("clip")
        slot = issue.get("slot")
        animation = data.get("animations", {}).get(clip, {})
        tracks = animation.setdefault("slots", {}).setdefault(slot, {})
        rgba = tracks.setdefault("rgba", [])
        if not rgba or float(rgba[0].get("time", 0)) != 0:
            rgba.insert(0, {"color": "ffffff00"})
            fixes.append(f"hid {slot} at {clip} setup")
    return fixes


def visual_self_critique(runtime_json: str, images_dir: str, out_dir: str,
                         motion_plan: dict | None = None, autofix: bool = True) -> dict:
    """Render actual representative frames and audit what the viewer sees."""
    with open(runtime_json, encoding="utf-8") as handle:
        data = json.load(handle)
    os.makedirs(out_dir, exist_ok=True)
    planned_clips = (motion_plan or {}).get("clips", {})
    issues = []
    frames_report = {}

    for clip, animation in data.get("animations", {}).items():
        times = _sample_times(animation, planned_clips.get(clip))
        rendered = []
        for index, time in enumerate(times):
            frame = spine_preview.render_frame(
                data, images_dir, clip, time, maxpx=320, transparent=True,
            )
            path = os.path.join(out_dir, f"{clip}_{index:02d}_{time:.3f}.png")
            frame.save(path)
            metrics = _frame_metrics(frame)
            rendered.append({"time": time, "path": path, "metrics": metrics, "image": frame})
            if metrics["blank"]:
                issues.append({"kind": "blank_frame", "clip": clip, "time": time, "severity": "high"})
            if metrics["touches_edge"]:
                issues.append({"kind": "possible_crop", "clip": clip, "time": time, "severity": "medium"})
        changes = [
            _difference(before["image"], after["image"])
            for before, after in zip(rendered, rendered[1:])
        ]
        if len(changes) >= 2 and max(changes, default=0) < .008 and clip != "blink":
            issues.append({
                "kind": "visually_static",
                "clip": clip,
                "severity": "medium",
                "max_frame_change": max(changes, default=0),
            })
        if changes and max(changes) > .72:
            issues.append({
                "kind": "visual_pop_spike",
                "clip": clip,
                "severity": "medium",
                "max_frame_change": max(changes),
            })
        frames_report[clip] = {
            "times": times,
            "frame_changes": changes,
            "frames": [{key: value for key, value in item.items() if key != "image"} for item in rendered],
        }

    fixes = _conservative_autofix(data, issues) if autofix else []
    if fixes:
        spine_guard.atomic_write_json(runtime_json, data)

    high = sum(1 for item in issues if item.get("severity") == "high")
    medium = sum(1 for item in issues if item.get("severity") == "medium")
    score = max(0, 100 - high * 25 - medium * 7)
    return {
        "version": 5,
        "score": score,
        "ok": high == 0,
        "issues": issues,
        "autofixes": fixes,
        "clips": frames_report,
        "render_dir": out_dir,
    }


def apply(source: str, runtime_json: str, images_dir: str, out_dir: str,
          source_group: str = "", motion_plan: dict | None = None) -> dict:
    blend = apply_psd_blends(source, runtime_json, images_dir, source_group)
    visual = visual_self_critique(
        runtime_json, images_dir, os.path.join(out_dir, "visual_qa"), motion_plan, True,
    )
    return {"version": 5, "blend_intelligence": blend, "visual_self_critique": visual}
