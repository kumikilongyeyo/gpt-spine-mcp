"""PSD semantic intelligence for GPT Spine.

Turns messy nested PSD names into stable rig semantics. It is deterministic,
profile-driven, and deliberately conservative: uncertain names are reported
instead of silently becoming anatomy.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from typing import Iterable


def canon(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", (value or "").casefold())).strip("_")


def tokens(value: str) -> list[str]:
    return [t for t in canon(value).split("_") if t]


ROLE_ALIASES = {
    "torso": ("torso", "chest", "body", "abdomen", "belly", "upperbody"),
    "pelvis": ("pelvis", "hip", "hips", "waist"),
    "head": ("head", "face", "skull", "ulo"),
    "neck": ("neck", "leeg"),
    "eye": ("eye", "eyes", "mata"),
    "pupil": ("pupil", "iris"),
    "eyelid": ("eyelid", "lid", "eyeclosed", "eye_close"),
    "brow": ("brow", "eyebrow", "kilay"),
    "mouth": ("mouth", "lip", "lips", "bibig"),
    "jaw": ("jaw", "muzzle", "panga"),
    "nose": ("nose", "ilong"),
    "ear": ("ear", "ears", "tenga"),
    "hand": ("hand", "hands", "fist", "palm", "kamay"),
    "lower_arm": ("lowerarm", "lower_arm", "forearm", "elbow", "braso_lower"),
    "upper_arm": ("upperarm", "upper_arm", "bicep", "shoulder", "arm_upper", "braso_upper"),
    "foot": ("foot", "feet", "shoe", "boot", "paa"),
    "lower_leg": ("lowerleg", "lower_leg", "calf", "shin", "knee", "binti_lower"),
    "upper_leg": ("upperleg", "upper_leg", "thigh", "leg_upper", "binti_upper"),
    "fore_leg": ("foreleg", "fore_leg", "frontleg", "front_leg"),
    "hind_leg": ("hindleg", "hind_leg", "rearleg", "rear_leg", "backleg", "back_leg"),
    "paw": ("paw", "paws"),
    "hoof": ("hoof", "hooves"),
    "tail": ("tail", "buntot"),
    "wing": ("wing", "wings", "feather", "feathers", "pakpak"),
    "hair": ("hair", "buhok", "bang", "bangs", "fringe", "ponytail", "braid", "beard", "mustache", "moustache"),
    "cloth": ("cloth", "cape", "coat", "dress", "skirt", "sleeve", "scarf", "ribbon", "fabric"),
    "prop": ("weapon", "sword", "gun", "shield", "staff", "coin", "logo", "title", "symbol", "prop", "object"),
    "fx": ("fx", "effect", "effects", "glow", "shine", "spark", "smoke", "fire"),
}

SUBTYPE_ALIASES = {
    "hair": {
        "scalp": ("scalp", "hairbase", "hair_base", "basehair"),
        "front_bang": ("bang", "bangs", "fringe", "frontbang", "front_bang", "hairfront", "hair_front", "buhokfront", "buhok_front"),
        "side_lock": ("sidelock", "side_lock", "sidehair", "side_hair", "temple"),
        "back_mass": ("hairback", "hair_back", "backhair", "back_hair", "backmass", "back_mass", "buhokback", "buhok_back"),
        "ponytail": ("ponytail", "pony", "pigtail"),
        "braid": ("braid", "braided", "plait"),
        "loose_strand": ("strand", "lock", "tuft", "loosehair", "loose_hair"),
        "beard": ("beard", "goatee"),
        "mustache": ("mustache", "moustache"),
    },
    "cloth": {
        "cape": ("cape", "cloak"),
        "scarf": ("scarf",),
        "ribbon": ("ribbon", "bow_tail"),
        "skirt": ("skirt",),
        "sleeve": ("sleeve",),
        "coat": ("coat", "jacket"),
        "dress": ("dress",),
    },
    "wing": {"feather": ("feather", "feathers"), "wing": ("wing", "wings")},
    "tail": {"tail": ("tail", "buntot")},
}

MATERIAL_BY_ROLE = {
    "hair": "hair", "cloth": "cloth", "tail": "organic", "wing": "feather",
    "eye": "skin", "pupil": "skin", "eyelid": "skin", "brow": "hair",
    "mouth": "skin", "jaw": "skin", "head": "skin", "torso": "skin",
    "upper_arm": "skin", "lower_arm": "skin", "hand": "skin",
    "upper_leg": "skin", "lower_leg": "skin", "foot": "rigid",
    "prop": "rigid", "fx": "fx",
}

MATERIAL_ALIASES = {
    "metal": ("metal", "steel", "iron", "gold", "silver", "brass"),
    "jewel": ("gem", "jewel", "crystal", "diamond", "ruby", "emerald"),
    "fur": ("fur", "furry", "tuft"),
    "fire": ("fire", "flame", "lava"),
    "smoke": ("smoke", "mist", "fog"),
    "glass": ("glass", "crystal"),
}

STATE_ALIASES = {
    "base": ("base", "normal", "default", "idle"),
    "powered": ("power", "powered", "powerup", "power_up", "super", "awakened", "awaken"),
    "wind": ("wind", "windy", "windblown", "wind_blown"),
    "glow": ("glow", "glowing", "lit"),
    "angry": ("angry", "rage", "mad"),
    "blink": ("blink", "closed", "close"),
    "win": ("win", "winner", "celebrate"),
    "hurt": ("hurt", "hit", "damage"),
    "alt": ("alt", "alternate", "variant"),
}

PARENT_ROLE = {
    "eye": "head", "pupil": "eye", "eyelid": "eye", "brow": "head", "mouth": "head", "jaw": "head",
    "nose": "head", "ear": "head", "hair": "head", "neck": "torso",
    "upper_arm": "torso", "lower_arm": "upper_arm", "hand": "lower_arm",
    "pelvis": "torso", "upper_leg": "pelvis", "lower_leg": "upper_leg", "foot": "lower_leg",
    "fore_leg": "torso", "hind_leg": "pelvis", "paw": "fore_leg", "hoof": "fore_leg",
    "wing": "torso", "tail": "pelvis", "cloth": "torso", "torso": "body", "head": "torso",
}

FRONT = {"front", "fg", "foreground", "fore"}
BACK = {"back", "bg", "background", "rear", "behind"}
LEFT = {"left", "l", "lf", "lhs"}
RIGHT = {"right", "r", "rt", "rhs"}


def _as_aliases(value) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in (value or []))


def merge_profile(custom: dict | None = None) -> dict:
    profile = {
        "aliases": {key: list(value) for key, value in ROLE_ALIASES.items()},
        "exact": {},
        "states": {key: list(value) for key, value in STATE_ALIASES.items()},
    }
    custom = custom or {}
    for role, values in custom.get("aliases", {}).items():
        profile["aliases"].setdefault(role, [])
        profile["aliases"][role] = list(dict.fromkeys(profile["aliases"][role] + list(_as_aliases(values))))
    profile["exact"].update({canon(key): str(value) for key, value in custom.get("exact", {}).items()})
    for state, values in custom.get("states", {}).items():
        profile["states"].setdefault(state, [])
        profile["states"][state] = list(dict.fromkeys(profile["states"][state] + list(_as_aliases(values))))
    return profile


def profile_candidates(source: str) -> list[str]:
    source = os.path.abspath(os.path.expanduser(source))
    base = os.path.dirname(source) if os.path.isfile(source) else source
    return [os.path.join(base, "spine_naming.json"), os.path.join(base, "_spine_naming.json")]


def load_profile(path: str = "", source: str = "") -> tuple[dict, str | None]:
    chosen = os.path.abspath(os.path.expanduser(path)) if path else None
    if not chosen and source:
        chosen = next((candidate for candidate in profile_candidates(source) if os.path.isfile(candidate)), None)
    custom = {}
    if chosen:
        with open(chosen, encoding="utf-8") as handle:
            custom = json.load(handle)
    return merge_profile(custom), chosen


def save_profile(path: str, mappings: dict) -> dict:
    path = os.path.abspath(os.path.expanduser(path))
    if not path.lower().endswith(".json"):
        raise ValueError("naming profile must be a .json file")
    existing = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            existing = json.load(handle)
    for key in ("aliases", "states", "exact"):
        incoming = mappings.get(key, {})
        if not incoming:
            continue
        existing.setdefault(key, {})
        for name, value in incoming.items():
            if key == "exact":
                existing[key][name] = value
            else:
                old = list(_as_aliases(existing[key].get(name, [])))
                existing[key][name] = list(dict.fromkeys(old + list(_as_aliases(value))))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(existing, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return {"ok": True, "path": path, "profile": existing}


def _match_score(text_tokens: set[str], joined: str, aliases: Iterable[str]) -> tuple[int, list[str]]:
    score, hits = 0, []
    for raw in aliases:
        alias = canon(raw)
        if not alias:
            continue
        alias_tokens = set(alias.split("_"))
        current = 0
        if alias == joined or alias_tokens and alias_tokens.issubset(text_tokens):
            current = 4 if len(alias_tokens) > 1 else 3
        elif len(alias) >= 5 and alias in joined:
            current = 1
        if current:
            score = max(score, current)
            hits.append(raw)
    return score, hits


def _side(text_tokens: set[str]) -> str:
    if text_tokens & LEFT and not text_tokens & RIGHT:
        return "l"
    if text_tokens & RIGHT and not text_tokens & LEFT:
        return "r"
    return ""


def _state(text_tokens: set[str], joined: str, profile: dict) -> tuple[str, list[str]]:
    best = ("base", 0, [])
    for state, aliases in profile.get("states", {}).items():
        score, hits = _match_score(text_tokens, joined, aliases)
        if score > best[1]:
            best = (state, score, hits)
    return best[0], best[2]


def _subtype(role: str, text_tokens: set[str], joined: str) -> tuple[str, list[str]]:
    best = ("", 0, [])
    priority = {"ponytail": 3, "braid": 3, "beard": 3, "mustache": 3, "loose_strand": 2, "side_lock": 2, "front_bang": 2}
    for subtype, aliases in SUBTYPE_ALIASES.get(role, {}).items():
        score, hits = _match_score(text_tokens, joined, aliases)
        score += priority.get(subtype, 0) if score else 0
        if score > best[1]:
            best = (subtype, score, hits)
    if best[0]:
        return best[0], best[2]
    return (role if role in {"tail", "wing"} else "generic"), []


def _family_name(path: str, state: str, profile: dict) -> str:
    value = canon(path)
    for raw in profile.get("states", {}).get(state, []):
        alias = canon(raw)
        if alias:
            value = re.sub(rf"(^|_){re.escape(alias)}($|_)", "_", value)
    return re.sub(r"_+", "_", value).strip("_")


def secondary_spec(role: str, subtype: str, bounds: Iterable[float] | None = None) -> dict:
    bounds = list(bounds or [0, 0, 1, 1])
    width = abs(float(bounds[2])) if len(bounds) > 2 else 1
    height = abs(float(bounds[3])) if len(bounds) > 3 else 1
    aspect = max(width, height) / max(1, min(width, height))
    table = {
        ("hair", "scalp"): (1, .95, .05), ("hair", "front_bang"): (2, .74, .22),
        ("hair", "side_lock"): (3, .52, .40), ("hair", "back_mass"): (3, .50, .42),
        ("hair", "ponytail"): (4, .34, .58), ("hair", "braid"): (4, .66, .32),
        ("hair", "loose_strand"): (3, .38, .55), ("hair", "beard"): (2, .72, .20),
        ("hair", "mustache"): (1, .90, .08), ("hair", "generic"): (2, .70, .24),
        ("cloth", "cape"): (4, .38, .55), ("cloth", "scarf"): (4, .36, .60),
        ("cloth", "ribbon"): (4, .30, .68), ("cloth", "skirt"): (3, .58, .34),
        ("cloth", "sleeve"): (2, .72, .20), ("tail", "tail"): (4, .42, .52),
        ("wing", "feather"): (3, .58, .34), ("wing", "wing"): (3, .70, .26),
    }
    segments, stiffness, drag = table.get((role, subtype), table.get((role, "generic"), (1, .85, .10)))
    if role in {"hair", "cloth", "tail"} and aspect > 3.2:
        segments = min(5, max(segments, 4))
    return {"enabled": segments > 1, "segments": segments, "stiffness": stiffness, "drag": drag, "settle": round(.18 + drag * .5, 3)}


def classify_path(path: str, bounds: Iterable[float] | None = None,
                  canvas: Iterable[float] | None = None, draw_index: int | None = None,
                  draw_count: int | None = None, profile: dict | None = None) -> dict:
    profile = profile or merge_profile()
    joined = canon(path)
    text_tokens = set(tokens(path))
    evidence = []
    exact = profile.get("exact", {})
    role = ""
    subtype = ""
    exact_value = exact.get(joined)
    if not exact_value:
        for segment in re.split(r"[/\\]+", path):
            if canon(segment) in exact:
                exact_value = exact[canon(segment)]
    if exact_value:
        parts = canon(exact_value).split("_")
        role = parts[0]
        subtype = "_".join(parts[1:]) if len(parts) > 1 else ""
        evidence.append(f"exact naming profile -> {exact_value}")
    if not role:
        best = ("", 0, -1, [])
        token_list = tokens(path)
        segments = [canon(segment) for segment in re.split(r"[/\\]+", path) if canon(segment)]
        tail = "_".join(segments[-2:]) if len(segments) > 1 else ""
        tail_tokens = set(tokens(tail))
        for candidate, aliases in profile.get("aliases", {}).items():
            score, hits = _match_score(text_tokens, joined, aliases)
            if tail:
                tail_score, tail_hits = _match_score(tail_tokens, tail, aliases)
                score += tail_score * 2
                hits = list(dict.fromkeys(hits + tail_hits))
            latest = -1
            for raw in aliases:
                for token in canon(raw).split("_"):
                    if token in token_list:
                        latest = max(latest, max(index for index, value in enumerate(token_list) if value == token))
            if (score, latest) > (best[1], best[2]):
                best = (candidate, score, latest, hits)
        role = best[0] or "unknown"
        if best[3]:
            evidence.append("role aliases: " + ", ".join(best[3][:3]))
    if not subtype and role != "unknown":
        subtype, hits = _subtype(role, text_tokens, joined)
        if hits:
            evidence.append("subtype aliases: " + ", ".join(hits[:3]))
    side = _side(text_tokens)
    state, state_hits = _state(text_tokens, joined, profile)
    if state_hits and state != "base":
        evidence.append("state aliases: " + ", ".join(state_hits[:2]))
    if text_tokens & FRONT:
        depth = "front"
    elif text_tokens & BACK:
        depth = "back"
    elif role == "hair" and subtype == "front_bang":
        depth = "front"
    elif role == "hair" and subtype == "back_mass":
        depth = "back"
    else:
        depth = "mid"
    material = MATERIAL_BY_ROLE.get(role, "unknown")
    for material_name, aliases in MATERIAL_ALIASES.items():
        score, hits = _match_score(text_tokens, joined, aliases)
        if score:
            material = material_name
            evidence.append("material aliases: " + ", ".join(hits[:2]))
            break
    family = _family_name(path, state, profile)
    parent = PARENT_ROLE.get(role, "root")
    secondary = secondary_spec(role, subtype, bounds)
    confidence = .30
    if role != "unknown":
        confidence += .34
    if evidence:
        confidence += min(.18, .05 * len(evidence))
    if "/" in path or "\\" in path:
        confidence += .06
    if side:
        confidence += .03
    if subtype and subtype != "generic":
        confidence += .05
    confidence = round(min(.98, confidence), 2)
    warnings = []
    if role == "unknown":
        warnings.append("unrecognized layer role")
    if confidence < .65:
        warnings.append("low semantic confidence")
    return {
        "path": path, "canonical": joined, "role": role, "subtype": subtype or "generic",
        "side": side, "depth": depth, "material": material, "state": state,
        "family": family, "parent_role": parent, "secondary": secondary,
        "confidence": confidence, "evidence": evidence, "warnings": warnings,
        "bounds": list(bounds or []),
    }


def transformation_families(items: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for item in items:
        if item["role"] == "unknown":
            continue
        groups[(item["family"], item["role"], item["side"])].append(item)
    output = []
    for (family, role, side), members in groups.items():
        states = defaultdict(list)
        for item in members:
            states[item["state"]].append(item["path"])
        if any(state != "base" for state in states):
            output.append({
                "family": family, "role": role, "side": side,
                "base": states.get("base", [members[0]["path"]])[0],
                "states": dict(states),
            })
    return output


def analyze_inspection(inspection: dict, profile: dict | None = None) -> dict:
    profile = profile or merge_profile()
    raw = inspection.get("layers") or [{"attachment": part, "layer_path": part, "bounds": []} for part in inspection.get("parts", [])]
    items = []
    for index, layer in enumerate(raw):
        path = layer.get("layer_path") or layer.get("attachment") or layer.get("name") or ""
        item = classify_path(path, layer.get("bounds"), inspection.get("canvas"), index, len(raw), profile)
        item["attachment"] = layer.get("attachment", path)
        items.append(item)
    families = transformation_families(items)
    counts = Counter(item["role"] for item in items)
    unknown = [item["path"] for item in items if item["role"] == "unknown"]
    secondary = [item for item in items if item["secondary"]["enabled"]]
    return {
        "version": 3,
        "layers": items,
        "role_counts": dict(counts),
        "transformation_families": families,
        "secondary_motion_layers": [{"path": item["path"], "role": item["role"], "subtype": item["subtype"], **item["secondary"]} for item in secondary],
        "unknown_layers": unknown,
        "confidence": round(sum(item["confidence"] for item in items) / max(1, len(items)), 2),
        "warnings": ([f"{len(unknown)} layers remain semantically unknown"] if unknown else []),
    }
