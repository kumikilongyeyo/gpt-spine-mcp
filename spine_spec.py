"""Compile a model-authored numeric motion spec into Spine runtime JSON."""
from __future__ import annotations

import json
import math
import os

from spine_motion import Motion


def _keys(values: list[dict]) -> list[tuple]:
    output = []
    for key in values:
        at = float(key["at"])
        value = key.get("value", [])
        value = value if isinstance(value, list) else [value]
        ease = key.get("ease")
        output.append(tuple([at, *value, ease] if ease else [at, *value]))
    return output


def compile_motion_spec(runtime_json: str, out_json: str, spec: dict) -> dict:
    """Apply explicit clips to an existing skeleton.

    The model is responsible for translating art direction into numbers. This
    compiler validates names, frame alignment, curves, and handoffs; it does not
    claim to infer good taste from a preset name.
    """
    with open(runtime_json, encoding="utf-8") as handle:
        data = json.load(handle)
    fps = int(spec.get("fps") or data.get("skeleton", {}).get("fps") or 30)
    data.setdefault("skeleton", {})["fps"] = fps
    bone_names = {item["name"] for item in data.get("bones", [])}
    slot_names = {item["name"] for item in data.get("slots", [])}
    compiled = {}
    errors, warnings = [], []

    for clip_name, clip in spec.get("clips", {}).items():
        motion = Motion()
        for bone_name, properties in clip.get("bones", {}).items():
            if bone_name not in bone_names:
                errors.append(f"{clip_name}: unknown bone {bone_name!r}")
                continue
            for prop, values in properties.items():
                if prop not in {"rotate", "translate", "scale"}:
                    errors.append(f"{clip_name}: unsupported bone property {prop!r}")
                    continue
                motion.bone(bone_name, prop, _keys(values))
        for slot_name, properties in clip.get("slots", {}).items():
            if slot_name not in slot_names:
                errors.append(f"{clip_name}: unknown slot {slot_name!r}")
                continue
            for prop, values in properties.items():
                if prop == "alpha":
                    motion.slot_alpha(slot_name, _keys(values))
                elif prop == "attachment":
                    motion.attachment(slot_name, [(float(item["at"]), item.get("name"))
                                                  for item in values])
                else:
                    errors.append(f"{clip_name}: unsupported slot property {prop!r}")
        compiled[clip_name] = motion.data

    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}
    data.setdefault("animations", {}).update(compiled)
    os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    report = validate_motion_spec(out_json, list(compiled), spec.get("handoffs", []), fps)
    report.update({"runtime_json": os.path.abspath(out_json), "compiled_clips": list(compiled)})
    return report


def _tracks(animation: dict):
    for kind in ("bones", "slots"):
        for target, properties in animation.get(kind, {}).items():
            for prop, keys in properties.items():
                if isinstance(keys, list) and keys:
                    yield (kind, target, prop), keys


def _signature(key: dict):
    return {name: value for name, value in key.items() if name not in {"time", "curve"}}


def validate_motion_spec(runtime_json: str, clips: list[str] | None = None,
                         handoffs: list[dict] | None = None, fps: int | None = None) -> dict:
    with open(runtime_json, encoding="utf-8") as handle:
        data = json.load(handle)
    fps = fps or int(data.get("skeleton", {}).get("fps") or 30)
    animations = data.get("animations", {})
    selected = clips or list(animations)
    errors, warnings = [], []
    summary = {}
    for name in selected:
        animation = animations.get(name)
        if animation is None:
            errors.append(f"missing clip {name!r}")
            continue
        duration, curve_keys, off_frame = 0.0, 0, []
        for track, keys in _tracks(animation):
            for key in keys:
                at = float(key.get("time", 0))
                duration = max(duration, at)
                curve_keys += int("curve" in key)
                if not math.isclose(at * fps, round(at * fps), abs_tol=1e-3):
                    off_frame.append({"track": "/".join(track), "time": at})
        if off_frame:
            warnings.append(f"{name}: {len(off_frame)} keys are off the {fps} fps frame grid")
        summary[name] = {"duration": round(duration, 4), "curve_keys": curve_keys,
                         "off_frame_keys": off_frame}

    for handoff in handoffs or []:
        source, target = handoff["from"], handoff["to"]
        a, b = animations.get(source, {}), animations.get(target, {})
        ta, tb = dict(_tracks(a)), dict(_tracks(b))
        mismatches = []
        for track in set(ta) & set(tb):
            if _signature(ta[track][-1]) != _signature(tb[track][0]):
                mismatches.append("/".join(track))
        if mismatches:
            errors.append(f"handoff {source}->{target} mismatches: " + ", ".join(mismatches))
    for name in selected:
        if name.casefold().endswith(("loop", "_loop")) and name in animations:
            mismatches = ["/".join(track) for track, keys in _tracks(animations[name])
                          if _signature(keys[0]) != _signature(keys[-1])]
            if mismatches:
                errors.append(f"loop seam {name} mismatches: " + ", ".join(mismatches))
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "fps": fps, "clips": summary}

