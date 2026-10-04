"""Objective delivery gates for previews and portable editable Spine projects."""
from __future__ import annotations

import json
import os
from pathlib import Path
from statistics import mean

from PIL import Image, ImageChops, ImageStat


def _gif_frames(path: str):
    image = Image.open(path)
    frames = []
    durations = []
    try:
        index = 0
        while True:
            image.seek(index)
            frames.append(image.convert("RGB"))
            durations.append(int(image.info.get("duration", 100)))
            index += 1
    except EOFError:
        pass
    return frames, durations


def audit_preview(path: str, reference: str | None = None) -> dict:
    """Audit a GIF for blank/static output and report comparable visual metrics.

    This does not pretend to judge taste. It catches the mechanical failures that
    previously slipped through: a blank thumbnail, an almost-static animation,
    too little visible time, and a preview structurally unlike the supplied ref.
    """
    frames, durations = _gif_frames(path)
    if not frames:
        return {"ok": False, "errors": ["preview contains no frames"], "warnings": []}
    background = frames[0]
    areas, changes = [], []
    for frame in frames:
        delta = ImageChops.difference(frame, background).convert("L")
        mask = delta.point(lambda value: 255 if value > 14 else 0)
        histogram = mask.histogram()
        areas.append(histogram[255] / (frame.width * frame.height))
    for before, after in zip(frames, frames[1:]):
        stat = ImageStat.Stat(ImageChops.difference(before, after).convert("L"))
        changes.append(stat.mean[0])
    blank = [area < 0.003 for area in areas]
    visible_indexes = [i for i, value in enumerate(blank) if not value]
    total_ms = sum(durations)
    visible_ms = sum(duration for duration, is_blank in zip(durations, blank) if not is_blank)
    lead_ms = sum(durations[:visible_indexes[0]]) if visible_indexes else total_ms
    trail_ms = sum(durations[visible_indexes[-1] + 1:]) if visible_indexes else total_ms
    motion = mean(changes) if changes else 0.0
    visible_motion = mean(changes[max(0, visible_indexes[0]):visible_indexes[-1]]) \
        if len(visible_indexes) > 1 else 0.0
    errors, warnings = [], []
    if not visible_indexes:
        errors.append("preview is blank")
    if visible_ms < 1000:
        errors.append("preview has less than one second of visible content")
    if visible_motion < 0.35:
        errors.append("visible sequence is effectively static")
    if total_ms and (lead_ms + trail_ms) / total_ms > 0.45:
        warnings.append("nearly half the preview is blank entry/exit padding")
    metrics = {
        "width": frames[0].width, "height": frames[0].height,
        "frames": len(frames), "duration_ms": total_ms,
        "visible_ms": visible_ms, "blank_lead_ms": lead_ms,
        "blank_trail_ms": trail_ms, "mean_motion": round(motion, 3),
        "visible_motion": round(visible_motion, 3),
        "peak_occupancy": round(max(areas), 4),
    }
    comparison = None
    if reference:
        ref = audit_preview(reference)
        comparison = {"reference": os.path.abspath(reference), "metrics": ref["metrics"]}
        ref_metrics = ref["metrics"]
        if metrics["visible_ms"] < ref_metrics["visible_ms"] * 0.7:
            warnings.append("visible sequence is over 30% shorter than the reference")
        if metrics["visible_motion"] < ref_metrics["visible_motion"] * 0.45:
            warnings.append("motion is less than half the reference motion")
        if metrics["peak_occupancy"] > min(0.98, ref_metrics["peak_occupancy"] * 1.45):
            warnings.append("foreground occupancy is much larger than the reference; check cropping")
    return {"ok": not errors, "preview": os.path.abspath(path), "errors": errors,
            "warnings": warnings, "metrics": metrics, "comparison": comparison}


def audit_project_assets(runtime_json: str, project: str | None = None) -> dict:
    """Verify all editable-project image references are portable with delivery."""
    runtime_json = os.path.abspath(runtime_json)
    with open(runtime_json, encoding="utf-8") as handle:
        data = json.load(handle)
    json_dir = os.path.dirname(runtime_json)
    images_setting = data.get("skeleton", {}).get("images", "./")
    images_dir = os.path.normpath(os.path.join(json_dir, images_setting))
    referenced = set()
    for skin in data.get("skins", []):
        for slot_attachments in skin.get("attachments", {}).values():
            for name, entry in slot_attachments.items():
                if entry.get("type", "region") not in {"clipping", "boundingbox", "path", "point"}:
                    referenced.add(entry.get("path", name))
    missing = [name for name in sorted(referenced)
               if not any(os.path.isfile(os.path.join(images_dir, name + ext))
                          for ext in (".png", ".jpg", ".jpeg", ".webp"))]
    errors = [f"{len(missing)} attachment images are missing"] if missing else []
    if project:
        project = os.path.abspath(project)
        if not os.path.isfile(project):
            errors.append("editable .spine project is missing")
        elif os.path.dirname(project) != json_dir:
            errors.append("editable .spine project is outside the portable delivery folder")
    return {"ok": not errors, "runtime_json": runtime_json,
            "project": project, "images_dir": images_dir,
            "referenced_images": len(referenced), "missing_images": missing,
            "errors": errors}

