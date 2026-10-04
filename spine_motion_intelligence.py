"""Animation Intelligence V4 + V7 taste-aware choreography planning.

The V4 planner still owns pose beats, style, energy hierarchy, asymmetry and overlap.
V7 adds a conservative taste pass that replaces generic FX timing with purpose-driven,
mechanic-aware choreography while keeping the runtime format editable and auditable.
"""
from __future__ import annotations

from typing import Iterable

import spine_taste


STYLE_PRESETS = {
    "balanced": {"snappiness": .55, "weight": .55, "elasticity": .35, "overlap": .55, "exaggeration": .55, "fx": .45},
    "punchy": {"snappiness": .88, "weight": .62, "elasticity": .44, "overlap": .58, "exaggeration": .82, "fx": .70},
    "heavy": {"snappiness": .46, "weight": .92, "elasticity": .18, "overlap": .62, "exaggeration": .66, "fx": .55},
    "cute": {"snappiness": .72, "weight": .30, "elasticity": .82, "overlap": .70, "exaggeration": .80, "fx": .60},
    "graceful": {"snappiness": .38, "weight": .36, "elasticity": .28, "overlap": .72, "exaggeration": .42, "fx": .35},
    "menacing": {"snappiness": .48, "weight": .78, "elasticity": .16, "overlap": .48, "exaggeration": .62, "fx": .48},
    "premium_slot": {"snappiness": .78, "weight": .56, "elasticity": .48, "overlap": .62, "exaggeration": .74, "fx": .88},
    "subtle": {"snappiness": .28, "weight": .48, "elasticity": .16, "overlap": .38, "exaggeration": .22, "fx": .18},
}

STYLE_ALIASES = {
    "punchy": ("punchy", "snappy", "crisp", "impactful", "fast attack"),
    "heavy": ("heavy", "weighty", "massive", "brute", "powerful", "grounded"),
    "cute": ("cute", "bouncy", "playful", "chibi", "cartoony", "elastic"),
    "graceful": ("graceful", "elegant", "soft", "flowing", "delicate"),
    "menacing": ("menacing", "intimidating", "threatening", "sinister"),
    "premium_slot": ("premium", "slot", "juicy", "polished", "casino", "celebration"),
    "subtle": ("subtle", "restrained", "minimal", "do not overanimate", "don't overanimate", "small motion"),
}


def _contains(text: str, aliases: Iterable[str]) -> bool:
    low = text.casefold()
    return any(alias.casefold() in low for alias in aliases)


def infer_style(request: str) -> dict:
    hits = [name for name, aliases in STYLE_ALIASES.items() if _contains(request, aliases)]
    if not hits:
        hits = ["balanced"]
    values = {key: 0.0 for key in next(iter(STYLE_PRESETS.values()))}
    for hit in hits:
        for key, value in STYLE_PRESETS[hit].items():
            values[key] += value / len(hits)
    return {"presets": hits, **{key: round(value, 3) for key, value in values.items()}}


def character_archetype(asset_type: str, request: str, semantic_scene: dict | None = None) -> str:
    text = request.casefold()
    if any(word in text for word in ("brute", "heavy", "giant", "massive", "tank")):
        return "brute"
    if any(word in text for word in ("cute", "chibi", "small", "playful", "mascot")):
        return "mascot"
    if any(word in text for word in ("graceful", "elegant", "dancer", "queen", "goddess")):
        return "graceful"
    if any(word in text for word in ("menacing", "monster", "villain", "beast")):
        return "menacing"
    if asset_type == "quadruped":
        return "quadruped"
    if asset_type == "winged":
        return "winged"
    roles = (semantic_scene or {}).get("role_counts", {})
    if roles.get("tail", 0) and roles.get("fore_leg", 0):
        return "quadruped"
    return "hero"


def _beats_for(clip: str, style: dict, archetype: str) -> list[dict]:
    weight = style["weight"]
    snap = style["snappiness"]
    exaggeration = style["exaggeration"]
    if clip == "attack":
        anticipation = .20 + .14 * weight - .07 * snap
        contact = anticipation + .10 - .045 * snap
        overshoot = contact + .12 + .05 * weight
        settle = overshoot + .22 + .22 * weight
        if archetype == "brute":
            anticipation += .08
            settle += .16
        return [
            {"name": "setup", "time": 0.0, "energy": .12},
            {"name": "anticipation", "time": round(anticipation, 3), "energy": round(.52 + .18 * exaggeration, 2)},
            {"name": "contact", "time": round(contact, 3), "energy": 1.0},
            {"name": "overshoot", "time": round(overshoot, 3), "energy": round(.72 + .16 * exaggeration, 2)},
            {"name": "settle", "time": round(settle, 3), "energy": .0},
        ]
    if clip in {"win", "mega_win", "celebration"}:
        pop = .16 - .05 * snap
        peak = pop + .22
        settle = peak + .42 + .18 * weight
        return [
            {"name": "setup", "time": 0.0, "energy": .15},
            {"name": "anticipation", "time": round(pop, 3), "energy": .45},
            {"name": "peak", "time": round(peak, 3), "energy": 1.0},
            {"name": "secondary_peak", "time": round(peak + .18, 3), "energy": .76},
            {"name": "settle", "time": round(settle, 3), "energy": .0},
        ]
    if clip == "hit":
        return [{"name": "setup", "time": 0.0, "energy": .1}, {"name": "impact", "time": .06, "energy": 1.0},
                {"name": "recoil", "time": round(.20 + .08 * weight, 3), "energy": .72},
                {"name": "settle", "time": round(.48 + .18 * weight, 3), "energy": .0}]
    if clip == "land":
        return [{"name": "contact", "time": 0.0, "energy": .9}, {"name": "compression", "time": .10, "energy": 1.0},
                {"name": "rebound", "time": .25, "energy": .55}, {"name": "settle", "time": round(.48 + .12 * weight, 3), "energy": .0}]
    if clip == "jump":
        return [{"name": "anticipation", "time": 0.0, "energy": .45}, {"name": "launch", "time": .16, "energy": 1.0},
                {"name": "apex", "time": .40, "energy": .2}, {"name": "fall", "time": .62, "energy": .55}]
    if clip in {"walk", "run"}:
        duration = .82 if clip == "walk" else .52
        return [{"name": "contact_l", "time": 0.0, "energy": .55}, {"name": "passing_l", "time": round(duration * .25, 3), "energy": .35},
                {"name": "contact_r", "time": round(duration * .5, 3), "energy": .55}, {"name": "passing_r", "time": round(duration * .75, 3), "energy": .35},
                {"name": "loop", "time": duration, "energy": .55}]
    if clip == "idle":
        return [{"name": "rest", "time": 0.0, "energy": .08}, {"name": "inhale", "time": 1.05, "energy": .20}, {"name": "exhale", "time": 2.25, "energy": .08}]
    return [{"name": "setup", "time": 0.0, "energy": .1}, {"name": "settle", "time": .6, "energy": 0.0}]


def _energy_hierarchy(clip: str, asset_type: str, archetype: str) -> dict:
    if clip == "attack":
        if asset_type == "quadruped":
            return {"primary": ["torso", "head", "fore_leg"], "support": ["pelvis", "hind_leg"], "tertiary": ["tail", "hair", "cloth", "fx"]}
        if archetype == "brute":
            return {"primary": ["pelvis", "torso", "upper_arm"], "support": ["head", "lower_arm", "hand"], "tertiary": ["hair", "cloth", "prop", "fx"]}
        return {"primary": ["torso", "upper_arm", "lower_arm"], "support": ["pelvis", "head", "hand"], "tertiary": ["hair", "cloth", "prop", "fx"]}
    if clip in {"walk", "run"}:
        return {"primary": ["pelvis", "upper_leg", "lower_leg"], "support": ["torso", "upper_arm", "head"], "tertiary": ["hair", "cloth", "tail", "fx"]}
    if clip in {"win", "mega_win", "celebration"}:
        return {"primary": ["torso", "head", "upper_arm"], "support": ["pelvis", "hand"], "tertiary": ["hair", "cloth", "fx"]}
    return {"primary": ["torso", "pelvis"], "support": ["head", "limbs"], "tertiary": ["hair", "cloth", "tail", "fx"]}


def build_motion_plan(request: str, asset_type: str, animations: Iterable[str], semantic_scene: dict | None = None) -> dict:
    style = infer_style(request)
    archetype = character_archetype(asset_type, request, semantic_scene)
    clips = {}
    taste_scores = []
    for clip in animations:
        beats = _beats_for(clip, style, archetype)
        choreography = spine_taste.choreograph_clip(request, clip, beats, style, asset_type, semantic_scene)
        taste_scores.append(choreography["audit"]["score"])
        clips[clip] = {
            "beats": beats,
            "energy_hierarchy": _energy_hierarchy(clip, asset_type, archetype),
            "asymmetry": round(.08 + style["exaggeration"] * .12, 3),
            "overlap": style["overlap"],
            "weight": style["weight"],
            "snappiness": style["snappiness"],
            "fx_beats": choreography["fx_cues"],
            "contacts": [beat for beat in beats if beat["name"].startswith("contact")],
            "taste_choreography": choreography,
        }
    taste_score = round(sum(taste_scores) / max(1, len(taste_scores)), 1)
    return {
        "version": 7,
        "motion_core_version": 4,
        "style": style,
        "archetype": archetype,
        "taste_profile": spine_taste.DEFAULT_PROFILE["name"],
        "taste_score": taste_score,
        "clips": clips,
        "rules": [
            "primary motion leads support motion; tertiary pieces react afterward",
            "one-shots use setup/anticipation/action/overshoot/settle timing instead of evenly spaced keys",
            "bilateral motion gets small timing/value offsets unless symmetry is part of the design",
            "feet/paws should hold contact during planted phases",
            "secondary motion responds to acceleration and stop events instead of idling independently",
            *spine_taste.DEFAULT_PROFILE["principles"],
        ],
    }


def _track_times(track: list[dict]) -> list[float]:
    return [float(key.get("time", 0)) for key in track if isinstance(key, dict)]


def audit_animation(data: dict, motion_plan: dict | None = None) -> dict:
    """Structural quality audit plus planned taste/choreography checks."""
    animations = data.get("animations", {})
    clips_report = {}
    scores = []
    taste_scores = []
    for clip, animation in animations.items():
        bone_tracks = animation.get("bones", {}) if isinstance(animation, dict) else {}
        timeline_count = 0
        key_count = 0
        distinct_times = set()
        bilateral = {"l": set(), "r": set()}
        for bone, channels in bone_tracks.items():
            side = "l" if bone.endswith("_l") or "_l_" in bone else "r" if bone.endswith("_r") or "_r_" in bone else ""
            for track in channels.values():
                if not isinstance(track, list):
                    continue
                timeline_count += 1
                key_count += len(track)
                times = _track_times(track)
                distinct_times.update(round(time, 4) for time in times)
                if side:
                    bilateral[side].update(round(time, 4) for time in times)
        issues = []
        score = 100
        if timeline_count == 0:
            issues.append("no bone motion")
            score -= 45
        if key_count and len(distinct_times) < 3 and clip not in {"blink"}:
            issues.append("too few timing beats")
            score -= 20
        if bilateral["l"] and bilateral["r"] and bilateral["l"] == bilateral["r"] and clip in {"attack", "win", "celebration"}:
            issues.append("bilateral timing is perfectly mirrored; add asymmetry")
            score -= 10
        planned = (motion_plan or {}).get("clips", {}).get(clip, {})
        planned_beats = planned.get("beats", [])
        if planned_beats and len(distinct_times) < min(4, len(planned_beats)):
            issues.append("authored motion does not expose enough planned pose beats")
            score -= 12
        if clip in {"attack", "hit", "land", "win", "mega_win", "celebration"} and len(distinct_times) < 4:
            issues.append("one-shot lacks anticipation/impact/settle contrast")
            score -= 12
        taste = planned.get("taste_choreography", {}).get("audit")
        if taste:
            taste_scores.append(float(taste.get("score", 0)))
            if taste.get("score", 0) < 82:
                issues.extend(taste.get("issues", []))
        score = max(0, score)
        scores.append(score)
        clips_report[clip] = {"score": score, "issues": issues, "timelines": timeline_count,
                              "keys": key_count, "distinct_times": sorted(distinct_times), "taste": taste or {}}
    structural = round(sum(scores) / max(1, len(scores)), 1)
    taste = round(sum(taste_scores) / max(1, len(taste_scores)), 1) if taste_scores else None
    overall = round((structural * .65 + taste * .35), 1) if taste is not None else structural
    return {
        "version": 7,
        "structural_score": structural,
        "taste_score": taste,
        "score": overall,
        "grade": "excellent" if overall >= 90 else "good" if overall >= 80 else "needs_polish" if overall >= 65 else "weak",
        "clips": clips_report,
        "note": "Structural/taste audit only; true visual polish still requires playback review, preferably from Spine itself.",
    }
