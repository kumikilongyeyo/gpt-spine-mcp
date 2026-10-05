"""Facial acting and performance pass for Spine character animation.

Works on semantic facial controls created by ``spine_smart_rig``. The pass is small on
purpose: face/eyes lead or support the body beat, expressions reflect prompt intent,
blinks avoid the impact frame, and win states escalate through more than one acting beat.
It does not replace primary body animation or blindly animate every facial part.
"""
from __future__ import annotations

from copy import deepcopy

import spine_motion

ACTIVE = {"attack", "hit", "jump", "land", "win", "mega_win", "celebration", "pop"}
FPS = 30


def _beat_map(spec: dict) -> dict[str, float]:
    return {str(beat.get("name")): float(beat.get("time", 0)) for beat in spec.get("beats", [])}


def _primary(times: dict) -> float | None:
    for name in ("contact", "impact", "peak", "launch", "compression", "rebound"):
        if name in times:
            return times[name]
    return None


def _settle(times: dict, primary: float) -> float:
    return times.get("settle") or times.get("loop") or max(times.values(), default=primary + .5)


def _timeline(prop: str, keys: list[tuple], ease: str = "out") -> list[dict]:
    authored = [(*key, ease) if index + 1 < len(keys) else key for index, key in enumerate(keys)]
    return spine_motion.timeline(prop, authored)


def _set_rotate(animation: dict, bone: str | None, keys: list[tuple], ease: str = "out") -> bool:
    if not bone:
        return False
    animation.setdefault("bones", {}).setdefault(bone, {})["rotate"] = _timeline("rotate", keys, ease)
    return True


def _set_translate(animation: dict, bone: str | None, keys: list[tuple], ease: str = "out") -> bool:
    if not bone:
        return False
    animation.setdefault("bones", {}).setdefault(bone, {})["translate"] = _timeline("translate", keys, ease)
    return True


def _slot_rgba(animation: dict, slot: str, keys: list[tuple[float, str]]) -> None:
    track = []
    for at, color in keys:
        key = {"color": color}
        if at > 0:
            key["time"] = round(at, 4)
        track.append(key)
    animation.setdefault("slots", {}).setdefault(slot, {})["rgba"] = track


def _control_bone(controls: dict, role: str, side: str = "") -> str | None:
    bones = controls.get("bones", {})
    if side:
        for key in (f"{role}_{side}", f"{role}_left" if side == "l" else f"{role}_right"):
            if key in bones:
                return bones[key]
    return bones.get(role)


def _face_strength(intent: dict, spec: dict) -> float:
    energy = float(intent.get("energy", .62))
    exaggeration = float(intent.get("acting", {}).get("pose_exaggeration", .65))
    style = 1.08 if "premium_slot" in intent.get("styles", []) else 1.0
    return max(.4, min(1.25, (.55 + energy * .45) * (.72 + exaggeration * .28) * style))


def _expression_values(emotion: str, strength: float) -> dict:
    base = {
        "brow_y": 0.0, "brow_rot": 0.0, "jaw_rot": 0.0,
        "pupil_x": 0.0, "pupil_y": 0.0, "asymmetry": .10,
    }
    if emotion in {"excited", "heroic"}:
        base.update(brow_y=2.8*strength, brow_rot=-3.0*strength, jaw_rot=9.0*strength,
                    pupil_y=.8*strength, asymmetry=.12)
    elif emotion == "confident":
        base.update(brow_y=.8*strength, brow_rot=-2.0*strength, jaw_rot=2.5*strength,
                    pupil_x=1.0*strength, asymmetry=.28)
    elif emotion in {"angry", "menacing"}:
        base.update(brow_y=-1.2*strength, brow_rot=7.0*strength, jaw_rot=5.0*strength,
                    pupil_y=-.5*strength, asymmetry=.08)
    elif emotion == "cute":
        base.update(brow_y=2.0*strength, brow_rot=-2.0*strength, jaw_rot=3.5*strength,
                    pupil_x=1.3*strength, pupil_y=.8*strength, asymmetry=.16)
    elif emotion == "goofy":
        base.update(brow_y=1.8*strength, brow_rot=-6.0*strength, jaw_rot=7.0*strength,
                    pupil_x=2.2*strength, pupil_y=.7*strength, asymmetry=.35)
    elif emotion == "nervous":
        base.update(brow_y=2.2*strength, brow_rot=2.0*strength, jaw_rot=1.5*strength,
                    pupil_x=1.7*strength, asymmetry=.22)
    elif emotion == "elegant":
        base.update(brow_y=.7*strength, brow_rot=-1.0*strength, jaw_rot=.8*strength,
                    pupil_x=.5*strength, asymmetry=.08)
    return base


def _animate_brows(animation: dict, controls: dict, emotion: str, primary: float,
                   anticipation: float, settle: float, strength: float) -> list[str]:
    values = _expression_values(emotion, strength)
    touched = []
    for side, sign in (("l", -1), ("r", 1)):
        bone = _control_bone(controls, "brow", side)
        if not bone:
            continue
        asym = values["asymmetry"] if side == "l" else 0.0
        peak = min(settle, primary + asym / FPS * 3)
        y = values["brow_y"] * (.88 if side == "l" else 1.0)
        rot = values["brow_rot"] * sign * (.8 if side == "l" else 1.0)
        _set_translate(animation, bone, [(0,0,0), (anticipation,0,-y*.25), (peak,0,y), (settle,0,0)], "out")
        _set_rotate(animation, bone, [(0,0), (anticipation,-rot*.25), (peak,rot), (settle,0)], "out")
        touched.append(bone)
    if not touched:
        bone = _control_bone(controls, "brow")
        if bone:
            _set_translate(animation, bone, [(0,0,0), (anticipation,0,-values["brow_y"]*.2),
                                             (primary,0,values["brow_y"]), (settle,0,0)], "out")
            _set_rotate(animation, bone, [(0,0), (primary,values["brow_rot"]), (settle,0)], "out")
            touched.append(bone)
    return touched


def _animate_pupils(animation: dict, controls: dict, emotion: str, primary: float,
                    settle: float, intent: dict, strength: float) -> list[str]:
    values = _expression_values(emotion, strength)
    lead = max(1, int(intent.get("face", {}).get("eye_lead_frames", 2))) / FPS
    target = max(0.0, primary - lead)
    secondary = min(settle, primary + .12)
    touched = []
    for side, sign in (("l", -1), ("r", 1)):
        bone = _control_bone(controls, "pupil", side)
        if not bone:
            continue
        x = values["pupil_x"] + (.5 * sign if emotion in {"goofy", "cute", "nervous"} else 0)
        y = values["pupil_y"]
        _set_translate(animation, bone, [(0,0,0), (target,x,y), (primary,x*.75,y*.75),
                                         (secondary,-x*.20,y*.35), (settle,0,0)], "out")
        touched.append(bone)
    if not touched:
        bone = _control_bone(controls, "pupil")
        if bone:
            _set_translate(animation, bone, [(0,0,0), (target,values["pupil_x"],values["pupil_y"]),
                                             (settle,0,0)], "out")
            touched.append(bone)
    return touched


def _animate_jaw(animation: dict, controls: dict, primary: float,
                 settle: float, emotion: str, strength: float, clip: str) -> list[str]:
    bone = _control_bone(controls, "jaw") or _control_bone(controls, "mouth")
    if not bone:
        return []
    amount = _expression_values(emotion, strength)["jaw_rot"]
    if clip in {"win", "mega_win", "celebration"}:
        second = min(settle, primary + .16)
        _set_rotate(animation, bone, [(0,0), (max(0,primary-.04),amount*.18), (primary,amount),
                                      (second,amount*.52), (settle,0)], "outback")
    elif clip in {"attack", "hit"}:
        _set_rotate(animation, bone, [(0,0), (primary,amount), (min(settle,primary+.10),amount*.35), (settle,0)], "out")
    else:
        _set_rotate(animation, bone, [(0,0), (primary,amount*.65), (settle,0)], "out")
    return [bone]


def _blink_in_clip(animation: dict, controls: dict, at: float, fps: int = FPS) -> list[str]:
    eyelids = controls.get("eyelids", [])
    eyes = controls.get("eyes", [])
    if not eyelids or not eyes:
        return []
    frame = 1 / max(1, fps)
    start = max(0.0, at - frame)
    end = at + 2*frame
    for slot in eyelids:
        _slot_rgba(animation, slot, [(0,"ffffff00"), (start,"ffffff00"), (at,"ffffffff"), (end,"ffffff00")])
    for slot in eyes:
        _slot_rgba(animation, slot, [(0,"ffffffff"), (start,"ffffffff"), (at,"ffffff00"), (end,"ffffffff")])
    return list(eyelids) + list(eyes)


def _animate_prop_accent(data: dict, animation: dict, primary: float, settle: float,
                         strength: float) -> list[str]:
    # Only touch an explicitly named prop-like bone and only if body animation did not already author it.
    tokens = ("prop", "weapon", "sword", "gun", "staff", "wand", "coin", "item", "tool")
    for bone in data.get("bones", []):
        name = bone.get("name", "")
        low = name.casefold()
        if not any(token in low for token in tokens):
            continue
        channels = animation.get("bones", {}).get(name, {})
        if "rotate" in channels or "translate" in channels:
            continue
        amount = min(9.0, 4.0 + 4.0*strength)
        _set_rotate(animation, name, [(0,0), (max(0,primary-.05),-amount*.35),
                                      (min(settle,primary+.05),amount), (settle,0)], "outback")
        return [name]
    return []


def _performance_clip(data: dict, animation: dict, clip: str, spec: dict,
                      controls: dict, intent: dict) -> dict:
    times = _beat_map(spec)
    primary = _primary(times)
    if primary is None:
        return {"clip": clip, "applied": False, "reason": "no primary performance beat"}
    settle = _settle(times, primary)
    anticipation = times.get("anticipation", max(0, primary - .10))
    emotion = intent.get("emotion", "neutral")
    strength = _face_strength(intent, spec)

    facial_bones = []
    facial_bones += _animate_brows(animation, controls, emotion, primary, anticipation, settle, strength)
    facial_bones += _animate_pupils(animation, controls, emotion, primary, settle, intent, strength)
    facial_bones += _animate_jaw(animation, controls, primary, settle, emotion, strength, clip)

    # Never blink on the impact/peak itself. A short transition blink 3-5 frames after the main read
    # preserves the money pose while still giving the face life.
    blink_time = min(settle, primary + (4 / FPS))
    blink_slots = _blink_in_clip(animation, controls, blink_time)
    props = _animate_prop_accent(data, animation, primary, settle, strength)

    escalation = None
    if clip in {"win", "mega_win", "celebration"}:
        secondary = times.get("secondary_peak", min(settle, primary + .18))
        escalation = {
            "first_read": round(primary, 4),
            "second_read": round(secondary, 4),
            "rule": "second beat changes facial intent/eye target rather than repeating the same bounce",
        }
        # A second pupil target creates a deliberate performance change without touching the body pose.
        for side in ("l", "r"):
            pupil = _control_bone(controls, "pupil", side)
            if pupil:
                sign = -1 if side == "l" else 1
                _set_translate(animation, pupil, [(0,0,0), (max(0,primary-2/FPS),1.2*sign,0.4),
                                                  (primary,.8*sign,.2), (secondary,-.7*sign,.5),
                                                  (settle,0,0)], "out")

    return {
        "clip": clip,
        "applied": bool(facial_bones or blink_slots or props),
        "emotion": emotion,
        "strength": round(strength, 3),
        "facial_bones": sorted(set(facial_bones)),
        "blink_slots": blink_slots,
        "prop_bones": props,
        "escalation": escalation,
        "principles": [
            "eyes can lead the body action by a few frames",
            "brows and jaw communicate emotion instead of adding generic wobble",
            "blink occurs on transition/recovery rather than erasing the impact pose",
            "win/celebration uses a changed second acting read, not a repeated first beat",
            "prop accent is added only when the prop has no authored motion already",
        ],
    }


def _idle_face(animation: dict, controls: dict, intent: dict, duration: float) -> dict:
    emotion = intent.get("emotion", "neutral")
    touched = []
    pupils = []
    for side, sign in (("l", -1), ("r", 1)):
        bone = _control_bone(controls, "pupil", side)
        if bone:
            _set_translate(animation, bone, [(0,0,0), (duration*.36,.65*sign,.2),
                                             (duration*.62,.30*sign,.1), (duration,0,0)], "sine")
            pupils.append(bone)
    brow = _control_bone(controls, "brow")
    if brow:
        lift = .6 if emotion in {"excited", "cute", "heroic"} else .25
        _set_translate(animation, brow, [(0,0,0), (duration*.45,0,lift), (duration,0,0)], "sine")
        touched.append(brow)
    blink_slots = _blink_in_clip(animation, controls, duration*.72)
    return {"clip": "idle", "applied": bool(pupils or touched or blink_slots),
            "pupil_bones": pupils, "facial_bones": touched, "blink_slots": blink_slots,
            "rule": "idle face uses one quiet eye thought and a sparse blink; it does not constantly fidget"}


def apply(data: dict, motion_plan: dict | None, smart_report: dict | None,
          prompt_intent: dict | None) -> tuple[dict, dict]:
    """Return a performance-enhanced copy and report."""
    out = deepcopy(data)
    controls = (smart_report or {}).get("facial_controls", {}) or {}
    intent = prompt_intent or {"emotion": "neutral", "energy": .62, "styles": ["balanced"], "face": {}}
    planned = (motion_plan or {}).get("clips", {})
    reports = []

    for clip, animation in out.get("animations", {}).items():
        spec = planned.get(clip, {})
        if not spec:
            continue
        if clip == "idle":
            times = _beat_map(spec)
            duration = times.get("exhale") or times.get("settle") or max(times.values(), default=2.2)
            reports.append(_idle_face(animation, controls, intent, duration))
        elif clip in ACTIVE:
            reports.append(_performance_clip(out, animation, clip, spec, controls, intent))

    available = {key: bool(controls.get(key)) for key in ("eyes", "pupils", "brows", "mouth", "jaw", "eyelids")}
    return out, {
        "version": 1,
        "target": "facial acting / performance animation",
        "emotion": intent.get("emotion", "neutral"),
        "available_controls": available,
        "clips": reports,
        "limitations": [key for key, present in available.items() if not present],
        "guardrails": [
            "facial performance supports the body beat and never replaces clear posing",
            "eye darts are tiny and lead/support intent rather than wandering continuously",
            "impact/peak frames stay visually readable; transition blinks happen afterward",
            "facial amplitudes remain small enough for casual/mobile readability",
            "existing authored prop motion is never overwritten by the automatic accent pass",
        ],
    }
