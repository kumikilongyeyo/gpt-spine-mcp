"""Industry-style character juice pass for casual/slot animation.

Adds bounded, readable polish on top of an existing authored animation. This is not
random motion: accents follow the primary beat, preserve pose intent, stagger support
parts, and decay quickly. The pass only touches bones it can identify confidently.
"""
from __future__ import annotations

from copy import deepcopy

import spine_motion

ACTIVE = {"attack", "hit", "land", "jump", "win", "mega_win", "celebration", "pop"}


def _beat_map(spec: dict) -> dict[str, float]:
    return {str(item.get("name")): float(item.get("time", 0)) for item in spec.get("beats", [])}


def _find_bone(data: dict, *tokens: str) -> str | None:
    names = [bone.get("name", "") for bone in data.get("bones", [])]
    low_tokens = tuple(token.casefold() for token in tokens)
    for name in names:
        low = name.casefold()
        if all(token in low for token in low_tokens):
            return name
    return None


def _find_side(data: dict, role: str, side: str) -> str | None:
    candidates = []
    for bone in data.get("bones", []):
        name = bone.get("name", "")
        low = name.casefold()
        if role.casefold() not in low:
            continue
        side_match = (
            low.endswith("_" + side) or ("_" + side + "_") in low or
            (side == "l" and ("left" in low or low.startswith("l_"))) or
            (side == "r" and ("right" in low or low.startswith("r_")))
        )
        if side_match:
            candidates.append(name)
    return candidates[0] if candidates else None


def _set_rotate(animation: dict, bone: str | None, keys: list[tuple], ease: str = "out"):
    if not bone:
        return
    authored = []
    for index, key in enumerate(keys):
        authored.append((*key, ease) if index + 1 < len(keys) else key)
    animation.setdefault("bones", {}).setdefault(bone, {})["rotate"] = spine_motion.timeline("rotate", authored)


def _set_translate(animation: dict, bone: str | None, keys: list[tuple], ease: str = "out"):
    if not bone:
        return
    authored = []
    for index, key in enumerate(keys):
        authored.append((*key, ease) if index + 1 < len(keys) else key)
    animation.setdefault("bones", {}).setdefault(bone, {})["translate"] = spine_motion.timeline("translate", authored)


def _primary_time(times: dict) -> float | None:
    for key in ("contact", "impact", "peak", "launch", "compression", "rebound"):
        if key in times:
            return times[key]
    return None


def _settle_time(times: dict, primary: float) -> float:
    return times.get("settle") or times.get("loop") or max(times.values(), default=primary + .45)


def _juice_profile(spec: dict) -> dict:
    snap = float(spec.get("snappiness", .7))
    weight = float(spec.get("weight", .55))
    overlap = float(spec.get("overlap", .62))
    return {
        "micro_anticipation": round(.06 + .04 * (1 - weight), 3),
        "support_lag": round(.045 + .045 * overlap, 3),
        "head_lag": round(.055 + .035 * overlap, 3),
        "settle_echo": round(.16 + .08 * weight, 3),
        "torso_amp": round(7 + 5 * snap, 2),
        "head_amp": round(3 + 3 * snap, 2),
        "arm_amp": round(10 + 8 * snap, 2),
        "pelvis_px": round(3 + 4 * (1 - weight), 2),
    }


def _juice_active_clip(data: dict, animation: dict, clip: str, spec: dict) -> dict:
    times = _beat_map(spec)
    primary = _primary_time(times)
    if primary is None:
        return {"clip": clip, "applied": False, "reason": "no primary beat"}
    settle = _settle_time(times, primary)
    p = _juice_profile(spec)
    anticipation = times.get("anticipation", max(0.0, primary - p["micro_anticipation"]))
    echo = min(settle, primary + p["settle_echo"])

    torso = _find_bone(data, "torso") or _find_bone(data, "chest")
    pelvis = _find_bone(data, "pelvis") or _find_bone(data, "hip")
    head = _find_bone(data, "head")
    arm_l = _find_side(data, "arm", "l")
    arm_r = _find_side(data, "arm", "r")

    touched = []
    if torso:
        _set_rotate(animation, torso, [
            (0.0, 0.0),
            (anticipation, -p["torso_amp"] * .35),
            (primary, p["torso_amp"]),
            (echo, -p["torso_amp"] * .22),
            (settle, 0.0),
        ], "outback")
        touched.append(torso)
    if pelvis:
        _set_translate(animation, pelvis, [
            (0.0, 0.0, 0.0),
            (anticipation, 0.0, -p["pelvis_px"] * .45),
            (primary, 0.0, p["pelvis_px"]),
            (echo, 0.0, -p["pelvis_px"] * .18),
            (settle, 0.0, 0.0),
        ], "out")
        _set_rotate(animation, pelvis, [
            (0.0, 0.0),
            (anticipation, p["torso_amp"] * .16),
            (primary, -p["torso_amp"] * .38),
            (echo, p["torso_amp"] * .10),
            (settle, 0.0),
        ], "out")
        touched.append(pelvis)
    if head:
        lag = p["head_lag"]
        _set_rotate(animation, head, [
            (0.0, 0.0),
            (min(primary, anticipation + lag), p["head_amp"] * .35),
            (min(settle, primary + lag), -p["head_amp"]),
            (min(settle, echo + lag), p["head_amp"] * .22),
            (settle, 0.0),
        ], "out")
        touched.append(head)

    asym = float(spec.get("asymmetry", .14))
    for side, bone, sign in (("l", arm_l, -1), ("r", arm_r, 1)):
        if not bone:
            continue
        lag = p["support_lag"] + (p["support_lag"] * asym if side == "l" else 0.0)
        amp = p["arm_amp"] * (0.88 if side == "l" else 1.0)
        _set_rotate(animation, bone, [
            (0.0, 0.0),
            (min(primary, anticipation + lag), -amp * .25 * sign),
            (min(settle, primary + lag), amp * sign),
            (min(settle, echo + lag), -amp * .18 * sign),
            (settle, 0.0),
        ], "outback")
        touched.append(bone)

    return {
        "clip": clip,
        "applied": bool(touched),
        "bones": touched,
        "profile": p,
        "principles": [
            "primary pose remains dominant",
            "pelvis counter-rotates against torso",
            "head and support limbs lag the main action",
            "left/right support timing is deliberately asymmetric",
            "settle echo decays instead of looping random wobble",
        ],
    }


def _juice_idle(data: dict, animation: dict, clip: str, spec: dict) -> dict:
    times = _beat_map(spec)
    end = times.get("exhale") or times.get("settle") or max(times.values(), default=2.2)
    mid = times.get("inhale", end * .46)
    torso = _find_bone(data, "torso") or _find_bone(data, "chest")
    pelvis = _find_bone(data, "pelvis") or _find_bone(data, "hip")
    head = _find_bone(data, "head")
    touched = []
    if torso:
        _set_translate(animation, torso, [(0, 0, 0), (mid, 0, 2.2), (end, 0, 0)], "sine")
        _set_rotate(animation, torso, [(0, -0.6), (mid, 0.8), (end, -0.6)], "sine")
        touched.append(torso)
    if pelvis:
        _set_translate(animation, pelvis, [(0, 0, 0), (mid, 0, -0.8), (end, 0, 0)], "sine")
        touched.append(pelvis)
    if head:
        _set_rotate(animation, head, [(0, 0.5), (min(end, mid + .10), -0.8), (end, 0.5)], "sine")
        touched.append(head)
    return {"clip": clip, "applied": bool(touched), "bones": touched,
            "principles": ["quiet breathing", "sub-pixel body drift", "head follows chest late", "no constant fidgeting"]}


def apply(data: dict, motion_plan: dict | None = None, intensity: float = 1.0) -> tuple[dict, dict]:
    """Return a juiced copy plus a report.

    ``intensity`` is intentionally clamped to 0.5..1.25. Industry-grade juice is
    controlled contrast, not infinite amplitude.
    """
    out = deepcopy(data)
    intensity = max(.5, min(1.25, float(intensity)))
    reports = []
    clips = (motion_plan or {}).get("clips", {})
    for clip, animation in out.get("animations", {}).items():
        spec = deepcopy(clips.get(clip, {}))
        if not spec:
            continue
        for key in ("snappiness", "overlap", "asymmetry"):
            if key in spec:
                spec[key] = min(1.0, float(spec[key]) * intensity)
        if clip == "idle":
            reports.append(_juice_idle(out, animation, clip, spec))
        elif clip in ACTIVE:
            reports.append(_juice_active_clip(out, animation, clip, spec))
    return out, {
        "version": 1,
        "intensity": intensity,
        "target": "premium casual/slot character animation",
        "clips": reports,
        "guardrails": [
            "juice follows primary action; it never replaces pose design",
            "support motion is staggered, not simultaneous",
            "amplitude is capped so silhouette remains readable at small sizes",
            "idle life stays subtle so win/attack states retain contrast",
            "FX remains a separate hierarchy pass and may be reduced by render QA",
        ],
    }
