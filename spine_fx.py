"""Reusable, mixable Spine FX packs with authored timing and easing."""
from __future__ import annotations

import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter

from spine_motion import Motion

SUPPORTED = ("coin_splash", "glow_flash", "particle_explosion",
             "bomb_explosion", "fire", "splash")


def _radial(size: int, color, power=2.0):
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = image.load()
    c = (size - 1) / 2
    for y in range(size):
        for x in range(size):
            distance = min(1.0, math.hypot(x - c, y - c) / c)
            alpha = round(color[3] * (1 - distance) ** power)
            pixels[x, y] = (*color[:3], alpha)
    return image


def _asset_images(required: set[str]):
    images = {}
    if required & {"glow_flash", "particle_explosion", "bomb_explosion"}:
        images["__fx_flash"] = _radial(128, (255, 250, 220, 255), 1.8)
        ring = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse((18, 18, 174, 174), outline=(255, 224, 96, 220), width=12)
        images["__fx_ring"] = ring.filter(ImageFilter.GaussianBlur(4))
        spark = Image.new("RGBA", (96, 24), (0, 0, 0, 0))
        draw = ImageDraw.Draw(spark)
        draw.polygon([(0, 12), (72, 3), (96, 12), (72, 21)], fill=(255, 238, 128, 245))
        images["__fx_spark"] = spark.filter(ImageFilter.GaussianBlur(1.2))
    if "bomb_explosion" in required:
        images["__fx_smoke"] = _radial(96, (92, 80, 88, 210), 1.7).filter(ImageFilter.GaussianBlur(5))
    if "fire" in required:
        flame = Image.new("RGBA", (72, 128), (0, 0, 0, 0))
        px = flame.load()
        for y in range(128):
            v = 1 - y / 127
            width = (10 + 24 * math.sin(math.pi * v)) * (0.55 + 0.45 * v)
            for x in range(72):
                d = abs(x - 36) / max(1, width)
                if d < 1:
                    px[x, y] = (255, round(90 + 150 * v), 24,
                                round(230 * (1 - d) ** 1.8 * math.sin(math.pi * v) ** .7))
        images["__fx_flame"] = flame.filter(ImageFilter.GaussianBlur(2))
    if "splash" in required:
        drop = Image.new("RGBA", (36, 64), (0, 0, 0, 0))
        ImageDraw.Draw(drop).ellipse((6, 16, 30, 58), fill=(175, 235, 255, 235))
        ImageDraw.Draw(drop).polygon([(18, 2), (8, 28), (28, 28)], fill=(205, 248, 255, 235))
        images["__fx_drop"] = drop.filter(ImageFilter.GaussianBlur(.8))
    return images


def generate_assets(images_dir: str, presets: list[str], width: float, height: float) -> dict:
    requested = {value.strip().lower() for value in presets}
    unknown = requested - set(SUPPORTED)
    if unknown:
        raise ValueError("unknown FX preset(s): " + ", ".join(sorted(unknown)))
    generated = {}
    for name, image in _asset_images(requested).items():
        image.save(os.path.join(images_dir, name + ".png"))
        generated[name] = {"cx": 0.0, "cy": height * .52,
                           "w": float(image.width), "h": float(image.height), "file": name}
    return generated


def install(presets: list[str], bones: list[dict], slots: list[dict], attachments: dict,
            animations: dict, norm: dict, width: float, height: float) -> dict:
    """Install independent FX animations that can be mixed on separate tracks."""
    requested = [value.strip().lower() for value in presets]
    if not requested:
        return {"presets": [], "warnings": []}
    warnings = []
    center_y = round(height * .52, 2)

    def bone(name, x=0, y=center_y):
        if not any(item["name"] == name for item in bones):
            bones.append({"name": name, "parent": "root", "x": round(x, 2), "y": round(y, 2)})

    def sprite(slot_name, bone_name, region, *, blend="additive", scale=1.0):
        bone(bone_name)
        slots.append({"name": slot_name, "bone": bone_name, "attachment": slot_name, "blend": blend})
        source = norm[region]
        attachments[slot_name] = {slot_name: {
            "path": region, "width": round(source["w"]), "height": round(source["h"]),
            "scaleX": scale, "scaleY": scale,
        }}

    structures = set(requested)
    if structures & {"glow_flash", "particle_explosion", "bomb_explosion"}:
        sprite("__fx_flash_slot", "__fx_flash_bone", "__fx_flash")
        sprite("__fx_ring_slot", "__fx_ring_bone", "__fx_ring")
    if structures & {"particle_explosion", "bomb_explosion"}:
        for index in range(14):
            sprite(f"__fx_spark_{index}", f"__fx_spark_bone_{index}", "__fx_spark", scale=.8)
    if "bomb_explosion" in structures:
        for index in range(7):
            sprite(f"__fx_smoke_{index}", f"__fx_smoke_bone_{index}", "__fx_smoke", blend="normal")
    if "fire" in structures:
        for index in range(7):
            sprite(f"__fx_flame_{index}", f"__fx_flame_bone_{index}", "__fx_flame", scale=.8)
    if "splash" in structures:
        for index in range(12):
            sprite(f"__fx_drop_{index}", f"__fx_drop_bone_{index}", "__fx_drop", blend="normal", scale=.7)

    coin_regions = [name for name in norm if "coin" in name.casefold() and not name.startswith("__fx")]
    if "coin_splash" in structures:
        if not coin_regions:
            warnings.append("coin_splash skipped: no source attachment name contains 'coin'")
        else:
            for index in range(12):
                sprite(f"__fx_coin_{index}", f"__fx_coin_bone_{index}",
                       coin_regions[index % len(coin_regions)], blend="normal", scale=.65)

    def burst_motion(prefix, count, radius, duration, seed, *, gravity=0, spin=240):
        rng = random.Random(seed)
        motion = Motion()
        for index in range(count):
            delay = index * .018
            angle = (index / count) * math.tau + rng.uniform(-.16, .16)
            distance = radius * rng.uniform(.72, 1.15)
            dx, dy = math.cos(angle) * distance, math.sin(angle) * distance
            bn, sl = f"{prefix}_bone_{index}", f"{prefix}_{index}"
            motion.bone(bn, "translate", [(0, 0, 0), (delay, 0, 0, "expo"),
                                            (duration, dx, dy - gravity, "out")])
            motion.bone(bn, "scale", [(0, .05, .05), (delay + .08, 1.15, .82, "outback"),
                                        (duration, .22, .22, "in")])
            motion.bone(bn, "rotate", [(0, rng.uniform(-25, 25)),
                                         (duration, rng.choice((-1, 1)) * spin, "out")])
            motion.slot_alpha(sl, [(0, 0), (delay, 0), (delay + .05, 1, "out"),
                                    (duration * .72, 1, "in"), (duration, 0)])
        return motion

    def flash_motion(power=1.0):
        return (Motion()
                .bone("__fx_flash_bone", "scale", [(0, .05, .05), (.09, 5 * power, 5 * power, "expo"),
                                                      (.38, 9 * power, 9 * power, "out")])
                .slot_alpha("__fx_flash_slot", [(0, 0), (.025, .95), (.12, .42, "out"), (.38, 0)])
                .bone("__fx_ring_bone", "scale", [(0, .08, .08), (.08, .08, .08, "expo"),
                                                    (.62, 5 * power, 5 * power, "out")])
                .slot_alpha("__fx_ring_slot", [(0, 0), (.07, .9), (.34, .5, "out"), (.62, 0)]))

    for preset in requested:
        if preset == "glow_flash":
            animations["fx_glow_flash"] = flash_motion(.7).data
        elif preset == "particle_explosion":
            animations["fx_particle_explosion"] = flash_motion(.75).merge(
                burst_motion("__fx_spark", 14, min(width, height) * .38, 1.05, 41, gravity=35)).data
        elif preset == "bomb_explosion":
            motion = flash_motion(1.2).merge(
                burst_motion("__fx_spark", 14, min(width, height) * .48, 1.2, 73, gravity=90, spin=360))
            rng = random.Random(91)
            for index in range(7):
                delay = .10 + index * .035
                dx = rng.uniform(-width * .17, width * .17)
                motion.bone(f"__fx_smoke_bone_{index}", "translate",
                            [(0, 0, 0), (delay, 0, 0, "out"), (1.45, dx, rng.uniform(90, 210), "out")])
                motion.bone(f"__fx_smoke_bone_{index}", "scale",
                            [(0, .1, .1), (delay, .1, .1, "outback"), (.55, 1.5, 1.2, "out"),
                             (1.45, 2.4, 2.0, "out")])
                motion.slot_alpha(f"__fx_smoke_{index}", [(0, 0), (delay, 0), (.3, .65), (1.45, 0)])
            animations["fx_bomb_explosion"] = motion.data
        elif preset == "coin_splash" and coin_regions:
            animations["fx_coin_splash"] = burst_motion(
                "__fx_coin", 12, min(width, height) * .46, 1.45, 19, gravity=130, spin=420).data
        elif preset == "splash":
            animations["fx_splash"] = burst_motion(
                "__fx_drop", 12, min(width, height) * .38, 1.05, 29, gravity=180, spin=90).data
        elif preset == "fire":
            motion = Motion()
            for index in range(7):
                delay = index * .09
                x = (index - 3) * width * .045
                motion.bone(f"__fx_flame_bone_{index}", "translate",
                            [(0, x, -35), (delay, x, -35, "out"),
                             (.55 + delay, x + (-1) ** index * 18, 120, "sine"),
                             (1.2, x, -35)])
                motion.bone(f"__fx_flame_bone_{index}", "scale",
                            [(0, .2, .2), (delay + .12, 1.15, .82, "outback"),
                             (.72, .72, 1.3, "sine"), (1.2, .2, .2)])
                motion.slot_alpha(f"__fx_flame_{index}", [(0, 0), (delay, 0),
                                                            (delay + .1, .9), (.9, .65), (1.2, 0)])
            animations["fx_fire"] = motion.data
    return {"presets": requested, "warnings": warnings,
            "animations": [name for name in animations if name.startswith("fx_")]}

