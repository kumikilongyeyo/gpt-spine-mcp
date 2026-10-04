"""Animation Intelligence V4 integration pass.

Runs after the proven V3 semantic rig. It recovers safely inferred unnamed anatomy,
applies pose-beat-driven polish, acceleration-driven overlap, lightweight contact support,
and returns a structural quality audit. Existing V3 output remains the fallback.
"""
from __future__ import annotations

import json
import math
import os

import spine_anatomy
import spine_motion_intelligence
import spine_semantics


def _attachments(skins) -> dict:
    if isinstance(skins, dict):
        return skins.get("default", {})
    if not skins:
        return {}
    skin = next((item for item in skins if item.get("name") == "default"), skins[0])
    return skin.get("attachments", {})


def _entry(attachments: dict, name: str):
    values = attachments.get(name, {})
    if name in values:
        return values[name]
    return next((value for value in values.values()
                 if isinstance(value, dict) and value.get("type", "region") not in
                 {"clipping", "boundingbox", "path", "point"}), None)


def _worlds(bones: list[dict]) -> dict[str, tuple[float, float]]:
    by_name = {bone["name"]: bone for bone in bones}
    output = {}

    def world(name):
        if name in output:
            return output[name]
        bone = by_name[name]
        parent = bone.get("parent")
        px, py = world(parent) if parent in by_name else (0.0, 0.0)
        output[name] = (px + float(bone.get("x", 0)), py + float(bone.get("y", 0)))
        return output[name]

    for name in by_name:
        world(name)
    return output


def _safe(value: str) -> str:
    return spine_semantics.canon(value).strip("_") or "part"


def _build_scene(data: dict, images_dir: str, naming_profile: str, naming_source: str) -> dict:
    attachments = _attachments(data.get("skins", []))
    naming, _ = spine_semantics.load_profile(naming_profile, naming_source)
    layers = []
    for slot in data.get("slots", []):
        name = slot.get("name", "")
        entry = _entry(attachments, name)
        bounds = [0, 0, 0, 0]
        if entry:
            bone_world = _worlds(data.get("bones", [])).get(slot.get("bone", "root"), (0, 0))
            bounds = [bone_world[0] + float(entry.get("x", 0)),
                      bone_world[1] + float(entry.get("y", 0)),
                      float(entry.get("width", 0)), float(entry.get("height", 0))]
        item = spine_semantics.classify_path(name, bounds=bounds, profile=naming)
        item["attachment"] = name
        layers.append(item)
    return spine_anatomy.enrich_semantics(layers, images_dir)


def _role_slot_bones(data: dict, scene: dict) -> dict[tuple[str, str], str]:
    slots = {slot.get("name"): slot for slot in data.get("slots", [])}
    mapping = {}
    for item in scene.get("layers", []):
        slot = slots.get(item.get("attachment"))
        if not slot or item.get("role") == "unknown":
            continue
        role, side = item.get("role", "unknown"), item.get("side", "")
        bone = slot.get("bone")
        if bone:
            mapping.setdefault((role, side), bone)
            mapping.setdefault((role, ""), bone)
    return mapping


def _add_visual_anatomy(data: dict, scene: dict) -> list[dict]:
    """Add bones only for layers that V4 inferred and V3 left on generic root/body bones."""
    bones = data.get("bones", [])
    names = {bone["name"] for bone in bones}
    slots = {slot.get("name"): slot for slot in data.get("slots", [])}
    attachments = _attachments(data.get("skins", []))
    role_bones = _role_slot_bones(data, scene)
    worlds = _worlds(bones)
    added = []
    body = "body" if "body" in names else "root"

    for item in scene.get("layers", []):
        visual = item.get("visual_inference")
        if not visual or visual.get("confidence", 0) < .72:
            continue
        slot = slots.get(item.get("attachment"))
        if not slot:
            continue
        old_bone = slot.get("bone", body)
        if old_bone.startswith("smart_") or old_bone.startswith("visual_"):
            continue
        entry = _entry(attachments, item["attachment"])
        if not entry:
            continue
        role, side = item["role"], item.get("side", "")
        parent_role = item.get("parent_role", "body")
        parent = (role_bones.get((parent_role, side)) or role_bones.get((parent_role, "")) or body)
        worlds = _worlds(bones)
        old_world = worlds.get(old_bone, (0, 0))
        parent_world = worlds.get(parent, (0, 0))
        cx = old_world[0] + float(entry.get("x", 0))
        cy = old_world[1] + float(entry.get("y", 0))
        bone_name = f"visual_{_safe(item['attachment'])}"
        suffix = 2
        while bone_name in names:
            bone_name = f"visual_{_safe(item['attachment'])}_{suffix}"
            suffix += 1
        bones.append({"name": bone_name, "parent": parent,
                      "x": round(cx - parent_world[0], 3), "y": round(cy - parent_world[1], 3)})
        names.add(bone_name)
        for value in attachments.get(item["attachment"], {}).values():
            if not isinstance(value, dict) or value.get("type") in {"clipping", "boundingbox", "path", "point"}:
                continue
            value["x"] = round(old_world[0] + float(value.get("x", 0)) - cx, 3)
            value["y"] = round(old_world[1] + float(value.get("y", 0)) - cy, 3)
        slot["bone"] = bone_name
        role_bones.setdefault((role, side), bone_name)
        role_bones.setdefault((role, ""), bone_name)
        added.append({"bone": bone_name, "attachment": item["attachment"], "role": role,
                      "side": side, "confidence": visual["confidence"], "parent": parent})
    return added


def _set_rotate(animation: dict, bone: str | None, keys: list[tuple[float, float]]) -> None:
    if not bone:
        return
    channel = animation.setdefault("bones", {}).setdefault(bone, {})
    channel["rotate"] = [({"value": round(value, 3)} if time == 0 else
                          {"time": round(time, 3), "value": round(value, 3), "curve": "bezier"})
                         for time, value in keys]


def _set_translate(animation: dict, bone: str | None, keys: list[tuple[float, float, float]]) -> None:
    if not bone:
        return
    channel = animation.setdefault("bones", {}).setdefault(bone, {})
    channel["translate"] = [({"x": round(x, 3), "y": round(y, 3)} if time == 0 else
                             {"time": round(time, 3), "x": round(x, 3), "y": round(y, 3), "curve": "bezier"})
                            for time, x, y in keys]


def _bone_for(role_bones: dict, role: str, side: str = "") -> str | None:
    return role_bones.get((role, side)) or role_bones.get((role, ""))


def _beat_map(spec: dict) -> dict[str, float]:
    return {beat["name"]: float(beat["time"]) for beat in spec.get("beats", [])}


def _apply_one_shot(animation: dict, clip: str, spec: dict, role_bones: dict) -> list[str]:
    times = _beat_map(spec)
    asym = float(spec.get("asymmetry", .12))
    weight = float(spec.get("weight", .55))
    touched = []
    torso = _bone_for(role_bones, "torso")
    pelvis = _bone_for(role_bones, "pelvis")
    head = _bone_for(role_bones, "head")

    if clip == "attack" and all(name in times for name in ("anticipation", "contact", "overshoot", "settle")):
        a, c, o, s = times["anticipation"], times["contact"], times["overshoot"], times["settle"]
        power = 18 + 12 * weight
        _set_rotate(animation, torso, [(0, 0), (a, -power * .48), (c, power * .65), (o, power * .26), (s, 0)])
        _set_rotate(animation, pelvis, [(0, 0), (a, power * .24), (c, -power * .34), (o, -power * .10), (s, 0)])
        _set_rotate(animation, head, [(0, 0), (a + .02, power * .13), (c + .025, -power * .20), (s, 0)])
        for side, sign in (("r", 1), ("l", -1)):
            delay = asym * (.05 if side == "l" else 0)
            upper = _bone_for(role_bones, "upper_arm", side)
            lower = _bone_for(role_bones, "lower_arm", side)
            amount = power * (1.55 if side == "r" else .62)
            _set_rotate(animation, upper, [(0, 0), (a + delay, -amount * sign), (c + delay, amount * 1.5 * sign),
                                           (o + delay, amount * .38 * sign), (s, 0)])
            _set_rotate(animation, lower, [(0, 0), (a + delay + .015, -amount * .48 * sign),
                                           (c + delay + .012, amount * .72 * sign), (s, 0)])
        touched += [name for name in (torso, pelvis, head) if name]

    elif clip in {"win", "mega_win", "celebration"} and all(name in times for name in ("anticipation", "peak", "settle")):
        a, p, s = times["anticipation"], times["peak"], times["settle"]
        power = 12 + 10 * float(spec.get("snappiness", .6))
        _set_rotate(animation, torso, [(0, 0), (a, -power * .34), (p, power * .52), (p + .18, -power * .16), (s, 0)])
        _set_translate(animation, pelvis, [(0, 0, 0), (a, 0, -3 - 4 * weight), (p, 0, 6 + 8 * (1 - weight)),
                                           (p + .20, 0, 1), (s, 0, 0)])
        for side, sign in (("l", -1), ("r", 1)):
            upper = _bone_for(role_bones, "upper_arm", side)
            offset = .018 if side == "l" else 0
            _set_rotate(animation, upper, [(0, 0), (a + offset, -18 * sign), (p + offset, 58 * sign),
                                           (p + .20 + offset, 44 * sign), (s, 0)])
        touched += [name for name in (torso, pelvis) if name]

    elif clip == "hit" and all(name in times for name in ("impact", "recoil", "settle")):
        i, r, s = times["impact"], times["recoil"], times["settle"]
        _set_rotate(animation, torso, [(0, 0), (i, -20 - 10 * weight), (r, 9), (s, 0)])
        _set_rotate(animation, head, [(0, 0), (i + .02, -14), (r + .03, 6), (s, 0)])
        touched += [name for name in (torso, head) if name]

    elif clip == "land" and all(name in times for name in ("contact", "compression", "rebound", "settle")):
        c, comp, r, s = times["contact"], times["compression"], times["rebound"], times["settle"]
        _set_translate(animation, pelvis, [(c, 0, 0), (comp, 0, -8 - 10 * weight), (r, 0, 3), (s, 0, 0)])
        _set_rotate(animation, torso, [(c, 0), (comp, 8 + 7 * weight), (r, -3), (s, 0)])
        touched += [name for name in (pelvis, torso) if name]
    return touched


def _apply_locomotion(animation: dict, clip: str, spec: dict, role_bones: dict) -> list[str]:
    if clip not in {"walk", "run"}:
        return []
    beats = spec.get("beats", [])
    if len(beats) < 5:
        return []
    t = [float(beat["time"]) for beat in beats]
    pelvis = _bone_for(role_bones, "pelvis")
    torso = _bone_for(role_bones, "torso")
    rise = 3.0 if clip == "walk" else 5.5
    _set_translate(animation, pelvis, [(t[0], 0, 0), (t[1], 0, rise), (t[2], 0, 0),
                                       (t[3], 0, rise), (t[4], 0, 0)])
    _set_rotate(animation, torso, [(t[0], -2.2), (t[1], 1.5), (t[2], 2.2), (t[3], -1.5), (t[4], -2.2)])
    return [name for name in (pelvis, torso) if name]


def _apply_secondary_causality(animation: dict, clip: str, spec: dict,
                               secondary_chains: dict) -> int:
    if not secondary_chains:
        return 0
    times = _beat_map(spec)
    action_time = (times.get("contact") or times.get("impact") or times.get("peak") or
                   times.get("launch") or times.get("contact_l"))
    settle = times.get("settle") or times.get("loop") or max(times.values(), default=.8)
    if action_time is None:
        return 0
    overlap = float(spec.get("overlap", .55))
    count = 0
    for meta in secondary_chains.values():
        bones = meta.get("bones", [])
        drag = float(meta.get("drag", .55))
        for index, bone in enumerate(bones[1:] or bones):
            delay = .025 + index * (.025 + .035 * drag)
            amp = (5 + index * 3.2) * (.55 + overlap) * (.75 + drag)
            _set_rotate(animation, bone, [
                (0, 0),
                (max(0, action_time - .05 + delay), -amp * .36),
                (action_time + .08 + delay, amp),
                (min(settle, action_time + .24 + delay), -amp * .32),
                (settle, 0),
            ])
            count += 1
    return count


def _apply_fx_cues(animation: dict, spec: dict, data: dict) -> list[dict]:
    """Synchronize existing FX-like slots when possible; otherwise keep cues as handoff metadata."""
    slots = [slot.get("name", "") for slot in data.get("slots", [])]
    fx_slots = [name for name in slots if any(token in name.casefold() for token in ("fx", "glow", "flash", "spark", "shine"))]
    applied = []
    for cue in spec.get("fx_beats", []):
        if not fx_slots:
            applied.append({**cue, "applied": False, "reason": "no compatible FX slot"})
            continue
        target = fx_slots[0]
        time = float(cue["time"])
        intensity = max(.15, min(1.0, float(cue.get("intensity", .6))))
        alpha = max(20, min(255, round(255 * intensity)))
        color = f"ffffff{alpha:02x}"
        animation.setdefault("slots", {}).setdefault(target, {})["rgba"] = [
            {"color": "ffffff00"},
            {"time": round(max(0, time - .04), 3), "color": "ffffff00"},
            {"time": round(time, 3), "color": color},
            {"time": round(time + .12, 3), "color": "ffffff00"},
        ]
        applied.append({**cue, "applied": True, "slot": target})
    return applied


def apply(runtime_json: str, *, images_dir: str = "", naming_profile: str = "",
          naming_source: str = "", motion_plan: dict | None = None,
          smart_report: dict | None = None) -> dict:
    runtime_json = os.path.abspath(os.path.expanduser(runtime_json))
    with open(runtime_json, encoding="utf-8") as handle:
        data = json.load(handle)

    scene = _build_scene(data, images_dir, naming_profile, naming_source or runtime_json)
    visual_bones = _add_visual_anatomy(data, scene)
    role_bones = _role_slot_bones(data, scene)
    secondary_chains = (smart_report or {}).get("secondary_chains", {})
    touched = {}
    fx_report = {}

    if motion_plan:
        for clip, spec in motion_plan.get("clips", {}).items():
            animation = data.get("animations", {}).get(clip)
            if not isinstance(animation, dict):
                continue
            clip_touched = []
            clip_touched += _apply_one_shot(animation, clip, spec, role_bones)
            clip_touched += _apply_locomotion(animation, clip, spec, role_bones)
            secondary = _apply_secondary_causality(animation, clip, spec, secondary_chains)
            fx_report[clip] = _apply_fx_cues(animation, spec, data)
            touched[clip] = {"bones": sorted(set(clip_touched)), "secondary_tracks": secondary}

    quality = spine_motion_intelligence.audit_animation(data, motion_plan)
    with open(runtime_json, "w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))

    return {
        "ok": True,
        "version": 4,
        "visual_anatomy": scene,
        "visual_bones_added": visual_bones,
        "motion_plan": motion_plan or {},
        "motion_applied": touched,
        "fx_choreography": fx_report,
        "quality_audit": quality,
        "warnings": (["visual anatomy left ambiguous layers unresolved"] if scene.get("unresolved") else []),
    }
