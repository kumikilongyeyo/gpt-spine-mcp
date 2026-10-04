"""PSD -> Spine blend-mode intelligence for V5.

Photoshop has many blend modes; Spine slots have a smaller practical set:
normal, additive, multiply and screen.  This module preserves exact matches,
chooses conservative approximations for common FX/shadow modes, and marks
context-sensitive modes as bake-required instead of silently turning them into
normal layers.
"""
from __future__ import annotations

from typing import Any

from PIL import Image


_SPINE_EXACT = {
    "normal": "normal",
    "pass_through": "normal",
    "screen": "screen",
    "multiply": "multiply",
    "linear_dodge": "additive",  # Photoshop Linear Dodge (Add)
}

# Modes with a visually safe runtime approximation for isolated 2D game FX.
_LIGHT_APPROX = {"color_dodge", "lighten", "lighter_color"}
_DARK_APPROX = {"color_burn", "linear_burn", "darken", "darker_color"}
_CONTEXT_MODES = {
    "overlay", "soft_light", "hard_light", "vivid_light", "linear_light",
    "pin_light", "hard_mix", "difference", "exclusion", "subtract", "divide",
    "hue", "saturation", "color", "luminosity", "dissolve",
}

_CODE_NAMES = {
    "pass": "pass_through", "norm": "normal", "diss": "dissolve",
    "dark": "darken", "mul": "multiply", "idiv": "color_burn",
    "lbrn": "linear_burn", "dkcl": "darker_color", "lite": "lighten",
    "scrn": "screen", "div": "color_dodge", "lddg": "linear_dodge",
    "lgcl": "lighter_color", "over": "overlay", "slit": "soft_light",
    "hlit": "hard_light", "vlit": "vivid_light", "llit": "linear_light",
    "plit": "pin_light", "hmix": "hard_mix", "diff": "difference",
    "smud": "exclusion", "fsub": "subtract", "fdiv": "divide",
    "hue": "hue", "sat": "saturation", "colr": "color", "lum": "luminosity",
}


def blend_mode_name(value: Any) -> str:
    """Normalize a psd-tools BlendMode/bytes/string into a stable snake_case name."""
    if value is None:
        return "normal"
    name = getattr(value, "name", None)
    if name:
        return str(name).casefold()
    raw = getattr(value, "value", value)
    if isinstance(raw, bytes):
        raw = raw.decode("latin1", "ignore")
    text = str(raw).strip().casefold().replace("-", "_").replace(" ", "_")
    return _CODE_NAMES.get(text.replace("_", ""), text or "normal")


def _looks_like_fx(role: str = "", layer_name: str = "") -> bool:
    text = f"{role} {layer_name}".casefold()
    return any(word in text for word in (
        "fx", "glow", "shine", "spark", "flash", "light", "ray", "flare",
        "fire", "energy", "magic", "aura", "beam", "smoke", "particle",
    ))


def _looks_like_shadow(role: str = "", layer_name: str = "") -> bool:
    text = f"{role} {layer_name}".casefold()
    return any(word in text for word in ("shadow", "shade", "ao", "ambient_occlusion"))


def translate_psd_blend(value: Any, *, role: str = "", layer_name: str = "") -> dict:
    """Translate Photoshop blend semantics to a Spine slot strategy.

    The returned `spine` value is always a valid slot blend. `bake_required`
    means there is no faithful standalone Spine equivalent and a future baking
    pass should merge the look into a stable underlay when exact appearance is
    more important than keeping that FX layer independently editable.
    """
    psd = blend_mode_name(value)
    if psd in _SPINE_EXACT:
        spine = _SPINE_EXACT[psd]
        return {
            "psd": psd, "spine": spine, "exact": True,
            "strategy": "direct", "bake_required": False, "warning": "",
        }
    if psd in _LIGHT_APPROX:
        spine = "additive" if _looks_like_fx(role, layer_name) else "screen"
        return {
            "psd": psd, "spine": spine, "exact": False,
            "strategy": "lighten_approx", "bake_required": False,
            "warning": f"Photoshop {psd} approximated with Spine {spine}",
        }
    if psd in _DARK_APPROX:
        return {
            "psd": psd, "spine": "multiply", "exact": False,
            "strategy": "darken_approx", "bake_required": False,
            "warning": f"Photoshop {psd} approximated with Spine multiply",
        }
    if psd in _CONTEXT_MODES:
        # A glow/shadow layer can often be kept editable with a useful fallback,
        # but we still mark it bake-required because the result is not equivalent.
        if _looks_like_fx(role, layer_name):
            fallback = "screen"
        elif _looks_like_shadow(role, layer_name):
            fallback = "multiply"
        else:
            fallback = "normal"
        return {
            "psd": psd, "spine": fallback, "exact": False,
            "strategy": "context_fallback", "bake_required": True,
            "warning": f"Photoshop {psd} has no exact Spine slot blend; using {fallback} fallback",
        }
    return {
        "psd": psd, "spine": "normal", "exact": False,
        "strategy": "unknown_fallback", "bake_required": True,
        "warning": f"Unknown PSD blend mode {psd}; using normal and flagging for review",
    }


def sanitize_transparent_rgb(image: Image.Image, spine_blend: str) -> Image.Image:
    """Give fully transparent texels a blend-neutral RGB value.

    Linear filtering can sample RGB from zero-alpha texels near a soft FX edge.
    Screen/additive want black as the neutral color; multiply wants white.
    This does not modify any visible pixel (alpha > 0).
    """
    rgba = image.convert("RGBA")
    px = rgba.load()
    neutral = (255, 255, 255) if spine_blend == "multiply" else (0, 0, 0)
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = px[x, y]
            if a == 0 and (r, g, b) != neutral:
                px[x, y] = (*neutral, 0)
    return rgba


def source_risk(image: Image.Image, spine_blend: str) -> dict:
    """Estimate whether an FX texture would show a black/white matte if blended wrong."""
    rgba = image.convert("RGBA")
    pixels = list(rgba.getdata())
    visible = [(r, g, b, a) for r, g, b, a in pixels if a >= 32]
    if not visible:
        return {"visible_pixels": 0, "neutral_matte_fraction": 0.0, "matte_risk": False}
    if spine_blend in {"screen", "additive"}:
        neutralish = sum(1 for r, g, b, a in visible if max(r, g, b) <= 18)
    elif spine_blend == "multiply":
        neutralish = sum(1 for r, g, b, a in visible if min(r, g, b) >= 237)
    else:
        neutralish = 0
    fraction = neutralish / len(visible)
    return {
        "visible_pixels": len(visible),
        "neutral_matte_fraction": round(fraction, 4),
        "matte_risk": bool(fraction >= .30 and spine_blend != "normal"),
    }


def audit_slots(slots: list[dict], blend_metadata: dict[str, dict] | None = None) -> dict:
    metadata = blend_metadata or {}
    translated = []
    bake_required = []
    approximated = []
    for slot in slots:
        name = slot.get("name", "")
        meta = metadata.get(name, {})
        psd = meta.get("psd_blend", "normal")
        expected = meta.get("spine_blend", "normal")
        actual = slot.get("blend", "normal")
        item = {"slot": name, "psd": psd, "expected": expected, "actual": actual}
        if psd != "normal" or actual != "normal":
            translated.append(item)
        if meta.get("bake_required"):
            bake_required.append(name)
        if meta.get("blend_exact") is False:
            approximated.append(name)
    return {
        "translated": translated,
        "translated_count": len(translated),
        "approximated": approximated,
        "bake_required": bake_required,
        "ok": all(item["expected"] == item["actual"] for item in translated),
    }
