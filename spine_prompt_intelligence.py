"""Deterministic prompt expansion for terse Spine art direction.

The OpenAI agent remains the language model. This module gives it a stable, testable
production vocabulary so short prompts such as "big cocky win, premium slot" can be
expanded into animation intent without forcing the user to specify timing, facial acting,
secondary motion and FX every time.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _tokens(value: str) -> list[str]:
    return [token for token in _norm(value).split() if token]


def _has(text: str, aliases, threshold: float = .82) -> bool:
    normal = _norm(text)
    words = _tokens(text)
    for alias in aliases:
        candidate = _norm(alias)
        if not candidate:
            continue
        if candidate in normal:
            return True
        parts = candidate.split()
        if len(parts) == 1:
            if len(candidate) <= 4:
                if candidate in words:
                    return True
            elif any(SequenceMatcher(None, word, candidate).ratio() >= threshold for word in words):
                return True
        elif len(parts) <= len(words):
            for index in range(len(words) - len(parts) + 1):
                phrase = " ".join(words[index:index + len(parts)])
                if SequenceMatcher(None, phrase, candidate).ratio() >= threshold:
                    return True
    return False


EMOTIONS = {
    "confident": ("confident", "cocky", "smug", "proud", "swagger", "cool", "boss", "dominant"),
    "excited": ("excited", "hype", "hyped", "happy", "joy", "celebrate", "celebration", "victory", "win"),
    "angry": ("angry", "mad", "furious", "rage", "aggressive", "pissed"),
    "menacing": ("menacing", "sinister", "evil", "threatening", "scary", "villain", "intimidating"),
    "cute": ("cute", "adorable", "playful", "bubbly", "chibi", "mascot"),
    "goofy": ("goofy", "funny", "comedic", "silly", "wacky", "cartoony"),
    "heroic": ("heroic", "hero", "brave", "triumphant", "epic", "powerful"),
    "nervous": ("nervous", "scared", "afraid", "worried", "shy", "timid"),
    "elegant": ("elegant", "graceful", "royal", "queen", "goddess", "refined"),
    "neutral": ("neutral", "plain", "normal"),
}

STATE_ALIASES = {
    "idle": ("idle", "breathe", "breathing", "stand", "standing", "alive", "living"),
    "win": ("win", "victory", "celebrate", "celebration", "jackpot", "big win", "reward"),
    "mega_win": ("mega win", "huge win", "massive win", "jackpot win", "super win"),
    "attack": ("attack", "punch", "kick", "slash", "strike", "shoot", "smash", "hit enemy"),
    "hit": ("get hit", "hurt", "damage", "recoil", "take hit"),
    "jump": ("jump", "leap", "hop"),
    "land": ("land", "landing"),
    "walk": ("walk", "walking"),
    "run": ("run", "running", "sprint", "dash"),
    "death": ("die", "death", "defeat", "dead"),
}

STYLE_HINTS = {
    "premium_slot": ("slot", "slots", "casino", "premium", "jackpot", "big win", "juicy", "juice", "reward"),
    "casual_game": ("casual game", "mobile game", "supercell", "playful game", "game animation"),
    "punchy": ("punchy", "snappy", "crisp", "impactful", "fast", "sharp"),
    "heavy": ("heavy", "weighty", "massive", "grounded", "brute"),
    "graceful": ("graceful", "elegant", "flowing", "soft"),
    "subtle": ("subtle", "small", "restrained", "minimal", "calm"),
}

QUALITY_ALIASES = (
    "industry grade", "industry quality", "production quality", "polished", "premium",
    "high quality", "professional", "final quality", "client ready", "game ready",
)

JUICE_ALIASES = (
    "juice", "juicy", "alive", "lively", "dynamic", "energetic", "not boring",
    "more life", "more alive", "punchy", "satisfying", "impactful",
)


def _pick_emotion(request: str) -> str:
    scores = []
    for emotion, aliases in EMOTIONS.items():
        hits = sum(1 for alias in aliases if _has(request, (alias,)))
        if hits:
            scores.append((hits, emotion))
    return max(scores, default=(0, "neutral"))[1]


def _energy(request: str) -> float:
    if _has(request, ("extreme", "huge", "massive", "crazy", "wild", "explosive", "mega", "big")):
        return .95
    if _has(request, JUICE_ALIASES) or _has(request, ("slot", "premium", "celebration", "attack")):
        return .82
    if _has(request, ("subtle", "soft", "calm", "small", "restrained")):
        return .35
    return .62


def _performance_style(request: str) -> list[str]:
    output = [name for name, aliases in STYLE_HINTS.items() if _has(request, aliases)]
    if _has(request, QUALITY_ALIASES) and "premium_slot" not in output and _has(request, ("slot", "win", "casino")):
        output.append("premium_slot")
    if _has(request, JUICE_ALIASES) and "punchy" not in output:
        output.append("punchy")
    return output or ["balanced"]


def _states(request: str) -> tuple[list[str], bool]:
    named = [state for state, aliases in STATE_ALIASES.items() if _has(request, aliases)]
    if named:
        return named, False
    # Do not invent an attack/death state from generic quality language. For characters,
    # a living idle is the safest implicit animation when the user only says "animate it".
    if _has(request, ("animate", "animation", "make it move", "bring it to life", "alive", "lively")):
        return ["idle"], True
    return [], False


def _facial_plan(emotion: str, energy: float, states: list[str]) -> dict:
    active = any(state in {"win", "mega_win", "attack", "hit", "jump", "land"} for state in states)
    profile = {
        "emotion": emotion,
        "eye_lead_frames": 2 if active else 0,
        "head_follow_frames": 2 if active else 3,
        "blink_policy": "blink_on_transition_not_impact" if active else "natural_sparse",
        "jaw_open": 0.0,
        "brow": "neutral",
        "pupil": "hold",
    }
    if emotion in {"excited", "heroic"}:
        profile.update({"jaw_open": .55 + .25 * energy, "brow": "lift", "pupil": "target_then_open"})
    elif emotion == "confident":
        profile.update({"jaw_open": .16, "brow": "one_up_or_relaxed", "pupil": "deliberate_target"})
    elif emotion in {"angry", "menacing"}:
        profile.update({"jaw_open": .30 + .25 * energy, "brow": "compress_inward", "pupil": "hard_target"})
    elif emotion == "cute":
        profile.update({"jaw_open": .25, "brow": "lift_soft", "pupil": "quick_dart"})
    elif emotion == "goofy":
        profile.update({"jaw_open": .42, "brow": "asymmetric", "pupil": "overshoot_dart"})
    elif emotion == "nervous":
        profile.update({"jaw_open": .10, "brow": "lift_inner", "pupil": "small_darts"})
    elif emotion == "elegant":
        profile.update({"jaw_open": .08, "brow": "soft_arch", "pupil": "smooth_target"})
    return profile


def _acting_plan(emotion: str, energy: float, states: list[str], styles: list[str]) -> dict:
    slot_like = "premium_slot" in styles
    casual = "casual_game" in styles
    return {
        "pose_exaggeration": round(min(1.0, .48 + .45 * energy + (.06 if slot_like else 0)), 3),
        "anticipation_ratio": [0.12, 0.22] if energy >= .7 else [0.10, 0.18],
        "overshoot_ratio": [0.08, 0.16] if slot_like or casual else [0.06, 0.13],
        "secondary_delay_frames": [2, 4],
        "settle_echoes": 2 if energy >= .65 else 1,
        "asymmetry": round(.10 + .12 * energy, 3),
        "emotion": emotion,
        "performance_arc": "anticipation -> decisive read -> reaction -> clean settle",
    }


def _fx_plan(request: str, energy: float, styles: list[str], states: list[str]) -> dict:
    reward = any(state in {"win", "mega_win"} for state in states)
    wants_fx = _has(request, ("fx", "vfx", "glow", "flash", "spark", "shine", "particle"))
    enabled = wants_fx or reward or "premium_slot" in styles
    return {
        "enabled": enabled,
        "impact_flash_frames": 1 if energy < .9 else 2,
        "build_before_peak": bool(enabled and (reward or "attack" in states)),
        "particle_burst": bool(enabled and energy >= .72),
        "shine_settle": bool(enabled and reward),
        "guardrail": "FX must reveal and support the pose; never permanently wash out the character",
    }


def interpret(request: str, *, source_parts: list[str] | None = None,
              semantic_scene: dict | None = None) -> dict:
    """Expand terse art direction into inspectable production intent."""
    source_parts = list(source_parts or [])
    emotion = _pick_emotion(request)
    energy = _energy(request)
    styles = _performance_style(request)
    states, implicit_state = _states(request)
    quality = "industry_grade" if _has(request, QUALITY_ALIASES) else "production" if _has(request, ("polish", "polished")) else "standard"
    acting = _acting_plan(emotion, energy, states, styles)
    face = _facial_plan(emotion, energy, states)
    fx = _fx_plan(request, energy, styles, states)
    role_counts = (semantic_scene or {}).get("role_counts", {})
    capabilities = {
        "face": any(role_counts.get(role, 0) for role in ("eye", "pupil", "brow", "mouth", "jaw", "eyelid")),
        "secondary": any(role_counts.get(role, 0) for role in ("hair", "cloth", "tail", "wing")),
        "props": role_counts.get("prop", 0) > 0 or any("prop" in part.casefold() for part in source_parts),
    }
    assumptions = []
    if implicit_state:
        assumptions.append("no explicit state was named; inferred a living idle rather than inventing an attack/win")
    if quality == "industry_grade":
        assumptions.append("industry-grade means stronger pose contrast, staged overlap, facial acting, controlled FX and render review—not simply larger motion")
    if "premium_slot" in styles:
        assumptions.append("premium slot/casino language implies short readable anticipation, strong reward peak, fast FX accent and clean settle")

    phrases = [request.strip()]
    if styles != ["balanced"]:
        phrases.append("style=" + ",".join(styles))
    if emotion != "neutral":
        phrases.append("emotion=" + emotion)
    phrases.append(f"energy={energy:.2f}")
    if quality != "standard":
        phrases.append("quality=" + quality)
    if fx["enabled"]:
        phrases.append("controlled readable FX")
    phrases.append("staggered secondary motion, clear silhouette, facial lead/support when controls exist")

    return {
        "version": 1,
        "raw_request": request,
        "expanded_request": "; ".join(part for part in phrases if part),
        "emotion": emotion,
        "energy": energy,
        "styles": styles,
        "quality_target": quality,
        "states": states,
        "implicit_state": implicit_state,
        "acting": acting,
        "face": face,
        "fx": fx,
        "capabilities": capabilities,
        "assumptions": assumptions,
        "rules": [
            "infer production details from intent, not only literal animation jargon",
            "never invent destructive or semantically unrelated states from vague prompts",
            "emotion affects face, timing and pose hierarchy—not random whole-body wobble",
            "slot/casual juice means contrast and staged accents, not motion everywhere",
            "source capabilities constrain the plan; missing facial layers remain a reported limitation",
        ],
    }
