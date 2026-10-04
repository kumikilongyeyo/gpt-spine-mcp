"""V5 rendered self-critique and Photoshop blend translation.

Runs after V4 and before editable .spine creation.  It preserves PSD blend intent,
fixes blend-neutral transparent texels, renders representative animation frames,
audits visual problems, and applies only conservative automatic corrections.
"""
from __future__ import annotations

import json
import os
import re
from typing import Iterable

from PIL import Image, ImageChops, ImageStat

import spine_blend
import spine_preview


def _walk_psd_layers(parent, prefix=""):
    for layer in parent:
        path = f"{prefix}/{layer.name.strip()}" if prefix else layer.name.strip()
        if not layer.is_visible():
            continue
        if layer.is_group():
            yield from _walk_psd_layers(layer, path)
        else:
            yield path, layer


def _find_group(psd, requested: str):
    wanted = requested.strip().casefold().strip("/")
    found = []
    def visit(parent, prefix=""):
        for layer in parent:
            path = f"{prefix}/{layer.name.strip()}" if prefix else layer.name.strip()
            if layer.is_group():
                if layer.name.strip().casefold() == wanted or path.casefold() == wanted:
                    found.append((path, layer))
                visit(layer, path)
    visit(psd)
    if len(found) != 1:
        return psd, ""
    return found[0][1], found[0][0]


def _safe_part_name(path: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.strip()).strip("_.") or "part"
    candidate, index = base, 2
    while candidate.casefold() in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate.casefold())
    return candidate


def apply_psd_blends(source: str, runtime_json: str, images_dir: str,
                     source_group: str = "") -> dict:
    """Read PSD blend modes and translate them to Spine slot blending."""
    if not source.lower().endswith(".psd"):
        return {"applied": False, "reason": "source is not PSD", "layers": [], "warnings": []}
    from psd_tools import PSDImage

    psd = PSDImage.open(source)
    parent, prefix = psd, ""
    if source_group:
        parent, prefix = _find_group(psd, source_group)
    used = set()
    metadata = {}
    warnings = []
    for path, layer in _walk_psd_layers(parent, prefix):
        if layer.bbox == (0, 0, 0, 0):
            continue
        name = _safe_part_name(path, used)
        role_hint = path
        strategy = spine_blend.translate_psd_blend(
            getattr(layer, "blend_mode", None), role=role_hint, layer_name=path,
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
                image = Image.open(image_path).convert("RGBA")
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
        patched.append({"slot": name, "psd": meta["psd_blend"], "spine": blend,
                        "exact": meta["blend_exact"], "bake_required": meta["bake_required"]})
    with open(runtime_json, "w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))
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
    picks = [0, 1, len(ordered)//3, len(ordered)//2, 2*len(ordered)//3, len(ordered)-2, len(ordered)-1]
    return [ordered[index] for index in sorted(set(picks))]


def _frame_metrics(image: Image.Image) -> dict:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return {"blank": True, "coverage": 0.0, "touches_edge": False, "dark_visible": 0.0}
    width, height = rgba.size
    l, t, r, b = bbox
    coverage = ((r-l) * (b-t)) / max(1, width * height)
    pixels = list(rgba.getdata())
    visible = [(rr, gg, bb, aa) for rr, gg, bb, aa in pixels if aa >= 48]
    dark = sum(1 for rr, gg, bb, aa in visible if max(rr, gg, bb) < 18) / max(1, len(visible))
    return {
        "blank": False,
        "coverage": round(coverage, 4),
        "touches_edge": bool(l <= 1 or t <= 1 or r >= width-1 or b >= height-1),
        "dark_visible": round(dark, 4),
        "bbox": [l, t, r, b],
    }


def _difference(a: Image.Image, b: Image.Image) -> float:
    aa = a.convert("RGB")
    bb = b.convert("RGB")
    if aa.size != bb.size:
        bb = bb.resize(aa.size)
    diff = ImageChops.difference(aa, bb)
    stat = ImageStat.Stat(diff)
    return round(sum(stat.mean) / (3 * 255), 4)


def _conservative_autofix(data: dict, issues: list[dict]) -> list[str]:
    """Apply only fixes that cannot destroy authored posing."""
    fixes = []
    # If an FX slot is visible at setup due to a generated track, force setup alpha to 0.
    fx_words = ("fx_", "glow", "flash", "spark", "shine", "particle", "burst")
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
            frame = spine_preview.render_frame(data, images_dir, clip, time, maxpx=320,
                                               transparent=True)
            path = os.path.join(out_dir, f"{clip}_{index:02d}_{time:.3f}.png")
            frame.save(path)
            metrics = _frame_metrics(frame)
            rendered.append({"time": time, "path": path, "metrics": metrics, "image": frame})
            if metrics["blank"]:
                issues.append({"kind": "blank_frame", "clip": clip, "time": time, "severity": "high"})
            if metrics["touches_edge"]:
                issues.append({"kind": "possible_crop", "clip": clip, "time": time, "severity": "medium"})
        changes = []
        for before, after in zip(rendered, rendered[1:]):
            changes.append(_difference(before["image"], after["image"]))
        if len(changes) >= 2 and max(changes, default=0) < .008 and clip not in {"blink"}:
            issues.append({"kind": "visually_static", "clip": clip, "severity": "medium",
                           "max_frame_change": max(changes, default=0)})
        if changes and max(changes) > .72:
            issues.append({"kind": "visual_pop_spike", "clip": clip, "severity": "medium",
                           "max_frame_change": max(changes)})
        frames_report[clip] = {
            "times": times,
            "frame_changes": changes,
            "frames": [{k: v for k, v in item.items() if k != "image"} for item in rendered],
        }

    fixes = []
    if autofix:
        fixes = _conservative_autofix(data, issues)
        if fixes:
            with open(runtime_json, "w", encoding="utf-8") as handle:
                json.dump(data, handle, separators=(",", ":"))

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
