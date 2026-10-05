"""Natural-language animation director for GPT Spine MCP.

This module deliberately does not call an LLM. The OpenAI agent is already the
language model; this layer gives it a deterministic vocabulary and production
rules so vague or typo-heavy art direction maps to consistent Spine operations.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

import spine_prompt_intelligence


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _words(value: str) -> list[str]:
    return [word for word in _norm(value).split() if word]


def _fuzzy_has(text: str, aliases: Iterable[str], threshold: float = 0.82) -> bool:
    normal = _norm(text)
    tokens = _words(text)
    for alias in aliases:
        a = _norm(alias)
        if not a:
            continue
        if a in normal:
            return True
        aw = a.split()
        if len(aw) == 1:
            if len(a) <= 4:
                if a in tokens:
                    return True
            elif any(SequenceMatcher(None, token, a).ratio() >= threshold for token in tokens):
                return True
        elif len(aw) <= len(tokens):
            width = len(aw)
            for i in range(len(tokens) - width + 1):
                phrase = " ".join(tokens[i:i + width])
                if SequenceMatcher(None, phrase, a).ratio() >= threshold:
                    return True
    return False


ASSET_ALIASES = {
    "biped": ("human", "humanoid", "person", "character", "hero", "fighter", "warrior", "bipedal"),
    "quadruped": ("animal", "quadruped", "dog", "cat", "wolf", "horse", "lion", "tiger", "beast", "creature"),
    "winged": ("bird", "wing", "winged", "dragon", "bat", "vulture", "eagle", "crow"),
    "prop": ("symbol", "logo", "coin", "prop", "object", "ui", "button", "title", "icon"),
}

ANIMATION_ALIASES = {
    "idle": ("idle", "breathe", "breathing", "loop", "alive", "living"),
    "walk": ("walk", "walking"),
    "run": ("run", "running", "sprint"),
    "jump": ("jump", "jumping", "leap"),
    "land": ("land", "landing"),
    "attack": ("attack", "attacking", "slash", "punch", "kick", "bite", "shoot", "strike"),
    "hit": ("hit", "hurt", "damage", "impact", "recoil"),
    "death": ("death", "die", "dying", "dead", "defeat"),
    "win": ("win", "winning", "celebrate", "celebration", "victory"),
    "mega_win": ("mega win", "huge win", "massive win", "jackpot win", "super win"),
    "blink": ("blink", "blinking"),
    "anticipation": ("anticipation", "anticipate", "charge up", "wind up", "windup"),
    "depth_flip": ("flip image with depth", "depth flip", "3d flip", "card flip", "flip"),
    "depth_shimmer": ("depth shine shimmer", "depth shimmer", "depth shine", "parallax shimmer", "2 5d shimmer"),
}

FX_ALIASES = {
    "clipped_shine": ("shine", "shimmer", "glint", "specular sweep", "light sweep"),
    "glow_flash": ("glow", "glowing", "flash", "bloom"),
    "particle_explosion": ("particle", "particles", "spark", "sparks", "sparkle", "sparkles"),
    "bomb_explosion": ("explosion", "explode", "blast", "burst explosion"),
    "fire": ("fire", "flame", "flames", "burn"),
    "splash": ("splash", "water", "liquid"),
    "coin_splash": ("coin splash", "coin burst", "coins"),
}


def _infer_from_parts(parts: Iterable[str]) -> tuple[str | None, list[str]]:
    joined = " ".join(parts).casefold()
    evidence: list[str] = []
    biped_terms = ("upper_arm", "lower_arm", "forearm", "hand", "thigh", "calf", "shin", "foot", "leg")
    quad_terms = ("foreleg", "hindleg", "front_leg", "back_leg", "paw", "hoof", "tail")
    wing_terms = ("wing", "feather")
    if sum(term in joined for term in biped_terms) >= 3:
        evidence.append("source parts look bipedal")
        return "biped", evidence
    if sum(term in joined for term in quad_terms) >= 2:
        evidence.append("source parts look quadrupedal")
        return "quadruped", evidence
    if any(term in joined for term in wing_terms):
        evidence.append("source contains wing/feather parts")
        return "winged", evidence
    return None, evidence


def plan_animation(request: str, source_parts: Iterable[str] | None = None) -> dict:
    """Translate loose art direction into a deterministic production plan.

    V3 first expands terse art direction into inspectable intent, then feeds that
    expanded wording through the existing fuzzy vocabulary. The original prompt is
    preserved so wrong assumptions are visible and calibratable.
    """
    source_parts = list(source_parts or [])
    prompt_intent = spine_prompt_intelligence.interpret(request, source_parts=source_parts)
    effective_request = prompt_intent["expanded_request"]
    evidence: list[str] = []
    assumptions: list[str] = list(prompt_intent.get("assumptions", []))

    asset_type = None
    for kind, aliases in ASSET_ALIASES.items():
        if _fuzzy_has(effective_request, aliases):
            asset_type = kind
            evidence.append(f"request implies {kind}")
            break
    if asset_type is None and source_parts:
        asset_type, part_evidence = _infer_from_parts(source_parts)
        evidence.extend(part_evidence)
    if asset_type is None:
        # Short character-animation prompts often omit the noun after source art is supplied.
        # If performance language is present, prefer biped only when source-part evidence hints
        # at character anatomy; otherwise keep the conservative prop fallback.
        joined = " ".join(source_parts).casefold()
        character_hint = any(token in joined for token in ("head", "eye", "mouth", "torso", "arm", "leg", "hand", "foot"))
        asset_type = "biped" if character_hint else "prop"
        assumptions.append(
            "asset type was inferred from source anatomy" if character_hint
            else "asset type was unclear, so the safe prop/symbol rig was selected"
        )

    layered_25d = _fuzzy_has(effective_request, ("2.5d", "2 5d", "2d depth", "parallax", "depth layered", "layered depth"), .76)
    if layered_25d:
        evidence.append("2.5D/depth treatment requested")

    animations = [name for name, aliases in ANIMATION_ALIASES.items() if _fuzzy_has(effective_request, aliases)]
    for inferred in prompt_intent.get("states", []):
        if inferred not in animations:
            animations.append(inferred)
    if not animations:
        animations = ["idle"]
        assumptions.append("no animation state was named, so idle was added as a safe editable starter")
    elif "idle" not in animations and asset_type in {"biped", "quadruped", "winged"}:
        animations.insert(0, "idle")

    fx_presets = [name for name, aliases in FX_ALIASES.items() if _fuzzy_has(effective_request, aliases)]
    if prompt_intent.get("fx", {}).get("enabled") and "glow_flash" not in fx_presets:
        fx_presets.append("glow_flash")
    if prompt_intent.get("fx", {}).get("particle_burst") and "particle_explosion" not in fx_presets:
        fx_presets.append("particle_explosion")
    if "depth_shimmer" in animations and "clipped_shine" not in fx_presets:
        fx_presets.append("clipped_shine")
    if "depth_flip" in animations:
        layered_25d = True

    wants_mesh = _fuzzy_has(effective_request, ("mesh", "clean mesh", "deform", "deformation", "skin", "weights", "weighting"))
    wants_weights = _fuzzy_has(effective_request, ("weight", "weights", "weighted", "weighting", "skin", "skinning"))
    organic = asset_type in {"biped", "quadruped", "winged"}
    clean_mesh = bool(wants_mesh or wants_weights or organic or layered_25d)
    auto_weight = bool(wants_weights or organic or layered_25d)
    ik = bool(organic and _fuzzy_has(effective_request, ("rig", "rigging", "ik", "walk", "run", "attack", "character", "animal")))
    clipping = bool(layered_25d or "clipped_shine" in fx_presets)

    rig_profile = asset_type
    if layered_25d:
        rig_profile += "_2_5d"

    slot_presets: list[str] = []
    if _fuzzy_has(effective_request, ("glow", "shimmer", "shine", "pulse", "premium slot")):
        slot_presets.append("pulse")
    if _fuzzy_has(effective_request, ("flash", "impact", "explosion", "reward peak")):
        slot_presets.append("flash")

    principles = [
        "preserve the setup pose and return one-shots cleanly for mixing",
        "put more topology only where silhouettes bend; keep rigid art cheap",
        "normalize skin weights and blend across joints instead of weighting whole art to one bone",
        "use overlap/follow-through on secondary pieces rather than moving every part at once",
        "infer missing production details from intent while keeping assumptions inspectable",
    ]
    if asset_type == "biped":
        principles += [
            "use pelvis/chest counter-rotation and opposing arm/leg phases for locomotion",
            "keep feet planted through contact frames and use IK only where it improves editability",
            "use eyes/brows/jaw as acting controls when source layers exist; facial motion supports the body beat",
        ]
    elif asset_type == "quadruped":
        principles += [
            "separate fore and hind limb rhythm; let spine/head/tail overlap the body beat",
            "keep paws/hooves planted during contact and avoid rubbery torso deformation",
        ]
    elif asset_type == "winged":
        principles += [
            "drive wing motion from shoulder/root, then feather/tip follow-through with delayed arcs",
        ]
    if layered_25d:
        principles += [
            "fake depth with small scale/translation/parallax offsets; avoid large perspective cheats that break the artwork",
            "clip shine passes to the visible artwork and stagger front/mid/back layers for depth shimmer",
        ]
    if prompt_intent.get("quality_target") == "industry_grade":
        principles += [
            "industry-grade polish means pose contrast, staged overlap, performance acting, readable FX and render review—not motion everywhere",
        ]

    confidence = .64
    confidence += min(.16, .04 * len(evidence))
    confidence += min(.10, .02 * len(animations))
    confidence += min(.06, .02 * len(fx_presets))
    if prompt_intent.get("quality_target") != "standard":
        confidence += .02
    confidence = round(min(.97, confidence), 2)

    return {
        "version": 3,
        "request": request,
        "effective_request": effective_request,
        "prompt_intent": prompt_intent,
        "asset_type": asset_type,
        "rig_profile": rig_profile,
        "layered_2_5d": layered_25d,
        "animations": animations,
        "fx_presets": fx_presets,
        "slot_presets": slot_presets,
        "clean_mesh": clean_mesh,
        "auto_weight": auto_weight,
        "ik": ik,
        "clipping": clipping,
        "mesh_quality": "adaptive" if clean_mesh else "none",
        "max_weight_influences": 2 if auto_weight else 1,
        "principles": principles,
        "evidence": evidence,
        "assumptions": assumptions,
        "confidence": confidence,
    }
