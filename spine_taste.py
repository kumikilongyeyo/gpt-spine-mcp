"""Taste / choreography intelligence for generated Spine animation.

This layer is deliberately conservative: it does not add more effects by default.
It assigns each cue a job, anchor, trigger, phase, intensity budget, and overlap group,
then audits whether the generated plan behaves like a designed sequence instead of a
pile of independent glows and pulses.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable

VERSION = 7

DEFAULT_PROFILE = {
    "name": "production_taste_v1",
    "principles": [
        "every effect must have a clear job",
        "effects should follow artwork geometry or game mechanics",
        "mechanical events drive timing before decorative timing",
        "one dominant event at a time unless the climax explicitly needs stacking",
        "brightness is a budget, not a substitute for motion design",
        "secondary motion reacts to acceleration, impact, and stop events",
        "anticipation leads action; action causes reaction; reaction resolves into settle",
        "do not claim visual polish from file/timeline validation alone",
    ],
    "avoid": [
        "generic radial glow with no anchor",
        "constant pulsing",
        "all effects peaking simultaneously",
        "unrelated particles",
        "winner highlight before the winner is mechanically established",
        "perfectly even timing across the whole sequence",
        "stacked additive layers that wash out text or material definition",
    ],
    "limits": {
        "max_simultaneous_hero_events": 1,
        "max_peak_effects": 2,
        "max_additive_intensity": 0.82,
        "settle_fx_intensity": 0.52,
    },
}


def detect_mechanic(request: str, clip: str, semantic_scene: dict | None = None) -> str:
    text = request.casefold()
    if any(word in text for word in ("wheel", "roulette", "pointer", "slice", "segment", "spinner")):
        return "wheel"
    if any(word in text for word in ("reel", "scatter", "wild", "slot", "jackpot")):
        return "slot"
    if clip in {"attack", "hit", "land", "jump"}:
        return "impact"
    if clip in {"win", "mega_win", "celebration"}:
        return "celebration"
    roles = (semantic_scene or {}).get("role_counts", {})
    if roles.get("prop", 0):
        return "prop"
    return "character"


def _times(beats: Iterable[dict]) -> dict[str, float]:
    return {str(item.get("name", "")): float(item.get("time", 0.0)) for item in beats}


def _cue(*, time: float, cue: str, phase: str, purpose: str, anchor: str,
         trigger: str, motion: str, intensity: float, overlap_group: str,
         material_response: str = "", hero: bool = False) -> dict:
    return {
        "time": round(max(0.0, time), 3),
        "cue": cue,
        "phase": phase,
        "purpose": purpose,
        "anchor": anchor,
        "trigger": trigger,
        "motion": motion,
        "intensity": round(max(0.0, min(1.0, intensity)), 3),
        "overlap_group": overlap_group,
        "material_response": material_response,
        "hero": hero,
    }


def _wheel_choreography(beats: list[dict], fx: float) -> dict:
    t = _times(beats)
    anticipation = t.get("anticipation", 0.12)
    impact = t.get("contact", t.get("impact", t.get("peak", anticipation + 0.28)))
    settle = t.get("settle", impact + 0.42)
    return {
        "mechanic": "wheel",
        "sequence": ["charge", "mechanical_pass", "deceleration", "winner_lock", "settle"],
        "fx_cues": [
            _cue(
                time=max(0.0, anticipation - 0.04), cue="handle_reflection_travel",
                phase="charge", purpose="show energy entering the mechanism",
                anchor="handle_contour", trigger="spin acceleration begins",
                motion="narrow reflection travels along the handle contour toward the wheel",
                intensity=min(0.56, fx * 0.62), overlap_group="mechanical_charge",
                material_response="specular sweep shaped to handle material",
            ),
            _cue(
                time=anticipation, cue="pointer_slice_response",
                phase="mechanical_pass", purpose="make wheel speed readable",
                anchor="pointer", trigger="each slice crosses the pointer",
                motion="short directional tick; cadence follows slice crossing frequency",
                intensity=min(0.48, fx * 0.55), overlap_group="pointer_response",
                material_response="brief local highlight only where pointer flexes",
            ),
            _cue(
                time=max(anticipation, impact - 0.10), cue="winner_energy_travel",
                phase="deceleration", purpose="focus attention on the eventual selected prize",
                anchor="selected_slice", trigger="wheel enters final deceleration window",
                motion="single energy sweep follows wheel direction into selected slice",
                intensity=min(0.72, fx * 0.78), overlap_group="winner_focus",
                material_response="light respects prize/slice material and text silhouette",
            ),
            _cue(
                time=impact, cue="winner_lock_impact",
                phase="winner_lock", purpose="confirm the mechanical stop",
                anchor="selected_slice", trigger="selected slice reaches pointer and wheel stops",
                motion="one compact impact plus pointer reaction; no unrelated radial ring",
                intensity=min(0.82, fx), overlap_group="winner_focus",
                material_response="localized metal/prize response instead of full-screen wash",
                hero=True,
            ),
            _cue(
                time=min(settle, impact + 0.18), cue="winner_material_settle",
                phase="settle", purpose="hold the win without competing with the impact",
                anchor="selected_slice", trigger="after winner lock impact",
                motion="small material shimmer decays into stable readable prize",
                intensity=min(0.50, fx * 0.56), overlap_group="settle",
                material_response="controlled specular shimmer; preserve prize text contrast",
            ),
        ],
        "constraints": [
            "pointer response cadence must follow passing slices rather than a fixed pulse rate",
            "selected-slice effects must not appear before the winner is mechanically plausible",
            "only the winner-lock event may be the dominant brightness peak",
            "handle and wheel lighting should follow their contours/materials",
            "do not stack a generic ring on top of winner-lock unless explicitly requested",
        ],
    }


def _generic_choreography(clip: str, beats: list[dict], fx: float, mechanic: str) -> dict:
    t = _times(beats)
    anticipation = t.get("anticipation")
    action = t.get("contact", t.get("impact", t.get("peak", t.get("launch"))))
    settle = t.get("settle", t.get("loop"))
    cues = []
    if anticipation is not None and fx >= 0.30:
        cues.append(_cue(
            time=anticipation, cue="anchored_charge", phase="anticipation",
            purpose="prepare the primary action", anchor="primary_action_geometry",
            trigger="primary motion begins accelerating", motion="local charge follows silhouette/material",
            intensity=min(0.50, fx * 0.55), overlap_group="charge",
        ))
    if action is not None and fx >= 0.35:
        cues.append(_cue(
            time=action, cue="action_response", phase="action",
            purpose="make the main action legible", anchor="contact_or_hero_area",
            trigger="primary action reaches contact/peak", motion="compact directional response tied to action vector",
            intensity=min(0.82, fx), overlap_group="hero", hero=True,
        ))
    if settle is not None and fx >= 0.55:
        cues.append(_cue(
            time=max(0.0, settle - 0.16), cue="material_settle", phase="settle",
            purpose="resolve energy without stealing the climax", anchor="hero_material",
            trigger="primary action loses energy", motion="short contour-following shimmer or decay",
            intensity=min(0.48, fx * 0.56), overlap_group="settle",
        ))
    return {
        "mechanic": mechanic,
        "sequence": ["anticipation", "action", "reaction", "settle"],
        "fx_cues": cues,
        "constraints": [
            "every cue needs a purpose, anchor and trigger",
            "hero peak should be singular and causally linked to the primary action",
            "decorative FX should not lead mechanical or character action",
            "settle is quieter than the action peak",
            "prefer geometry-following response over generic radial effects",
        ],
    }


def choreograph_clip(request: str, clip: str, beats: list[dict], style: dict,
                      asset_type: str = "biped", semantic_scene: dict | None = None) -> dict:
    mechanic = detect_mechanic(request, clip, semantic_scene)
    fx = float(style.get("fx", 0.45))
    result = _wheel_choreography(beats, fx) if mechanic == "wheel" else _generic_choreography(clip, beats, fx, mechanic)
    result.update({
        "version": VERSION,
        "profile": DEFAULT_PROFILE["name"],
        "asset_type": asset_type,
    })
    result["audit"] = audit_choreography(result)
    return result


def audit_choreography(choreography: dict) -> dict:
    cues = choreography.get("fx_cues", [])
    issues = []
    dimensions = {
        "purpose": 100,
        "anchoring": 100,
        "causality": 100,
        "hierarchy": 100,
        "brightness_restraint": 100,
        "settle_quality": 100,
    }
    required = ("purpose", "anchor", "trigger", "phase", "overlap_group")
    for cue in cues:
        for key in required:
            if not str(cue.get(key, "")).strip():
                dimensions["purpose" if key == "purpose" else "anchoring" if key == "anchor" else "causality"] -= 18
                issues.append(f"{cue.get('cue', 'unnamed cue')} missing {key}")
        if float(cue.get("intensity", 0)) > DEFAULT_PROFILE["limits"]["max_additive_intensity"]:
            dimensions["brightness_restraint"] -= 16
            issues.append(f"{cue.get('cue', 'unnamed cue')} exceeds brightness budget")
    hero = [cue for cue in cues if cue.get("hero")]
    if len(hero) > DEFAULT_PROFILE["limits"]["max_simultaneous_hero_events"]:
        dimensions["hierarchy"] -= 22 * (len(hero) - 1)
        issues.append("multiple hero FX compete for the climax")
    phase_counts = Counter(cue.get("phase") for cue in cues if cue.get("phase"))
    if any(count > DEFAULT_PROFILE["limits"]["max_peak_effects"] for count in phase_counts.values()):
        dimensions["hierarchy"] -= 15
        issues.append("too many effects occupy the same phase")
    settle = [cue for cue in cues if cue.get("phase") == "settle"]
    if settle and any(float(cue.get("intensity", 0)) > DEFAULT_PROFILE["limits"]["settle_fx_intensity"] for cue in settle):
        dimensions["settle_quality"] -= 18
        issues.append("settle FX are too strong relative to the climax")
    if choreography.get("mechanic") == "wheel":
        names = {cue.get("cue") for cue in cues}
        for required_cue in ("pointer_slice_response", "winner_lock_impact", "winner_material_settle"):
            if required_cue not in names:
                dimensions["causality"] -= 20
                issues.append(f"wheel choreography missing {required_cue}")
    dimensions = {key: max(0, value) for key, value in dimensions.items()}
    score = round(sum(dimensions.values()) / len(dimensions), 1)
    return {
        "version": VERSION,
        "score": score,
        "grade": "excellent" if score >= 92 else "good" if score >= 82 else "needs_polish" if score >= 68 else "weak",
        "dimensions": dimensions,
        "issues": issues,
    }


def calibration_template() -> dict:
    """Return a compact structure for recording accepted/rejected animation taste examples."""
    return {
        "version": VERSION,
        "good": [
            "mechanical response is synchronized to the thing causing it",
            "light follows the object's contour/material",
            "one clear hero moment",
            "secondary motion lags primary motion",
            "settle has controlled overshoot/decay",
        ],
        "bad": list(DEFAULT_PROFILE["avoid"]),
        "review_dimensions": [
            "mechanical_sync", "cause_effect", "motion_hierarchy", "material_aware_fx",
            "timing_rhythm", "brightness_restraint", "settle_quality",
        ],
    }
