"""spine_preview — render a keyframe-montage PNG of a rig's animations WITHOUT a
browser or game. Composites atlas parts at chosen anim times applying bone
translate/rotate/scale + attachment swaps. Not pivot-exact (parts rotate about
their image centre, the runtime pivots about bones) — a fast sanity check of
assembly, draw order and face-swaps, not a final render.
"""
from __future__ import annotations
import json, os
from PIL import Image, ImageDraw


def _bone_world(bones, name):
    X = Y = 0.0
    n = name
    while n and n != "root":
        b = bones[n]; X += b.get("x", 0); Y += b.get("y", 0); n = b.get("parent")
    return X, Y


def _lerp(kf, t):
    if t <= kf[0]["time"]:
        return kf[0]
    if t >= kf[-1]["time"]:
        return kf[-1]
    for i in range(len(kf) - 1):
        a, b = kf[i], kf[i + 1]
        if a["time"] <= t <= b["time"]:
            f = (t - a["time"]) / (b["time"] - a["time"])
            return {k: (a.get(k, 0) + (b.get(k, 0) - a.get(k, 0)) * f
                        if isinstance(a.get(k, 0), (int, float)) and isinstance(b.get(k, 0), (int, float))
                        else a.get(k))
                    for k in (set(a) | set(b)) - {"curve"}}
    return kf[-1]


def _render(d, images_dir, anim, t, maxpx):
    sk = d["skeleton"]; bones = {b["name"]: b for b in d["bones"]}
    slots = d["slots"]; att = d["skins"][0]["attachments"]; A = d["animations"][anim]
    W, H = int(sk["width"]), int(sk["height"])
    cv = Image.new("RGBA", (W, H), (24, 20, 32, 255))
    cur = {s["name"]: s.get("attachment") for s in slots}
    for sn, ad in A.get("slots", {}).items():
        if "attachment" in ad:
            name = ad["attachment"][0]["name"]
            for k in ad["attachment"]:
                if k["time"] <= t:
                    name = k["name"]
            cur[sn] = name
    bd = {}
    for bn, tl in A.get("bones", {}).items():
        e = {"rot": 0, "tx": 0, "ty": 0, "sx": 1, "sy": 1}
        if "rotate" in tl:
            e["rot"] = _lerp(tl["rotate"], t).get("value", 0)
        if "translate" in tl:
            v = _lerp(tl["translate"], t); e["tx"], e["ty"] = v.get("x", 0), v.get("y", 0)
        if "scale" in tl:
            v = _lerp(tl["scale"], t); e["sx"], e["sy"] = v.get("x", 1), v.get("y", 1)
        bd[bn] = e
    for s in slots:
        region = cur[s["name"]]
        if not region:
            continue
        ent = att[s["name"]][region]; path = ent.get("path", region)
        p = os.path.join(images_dir, f"{path}.png")
        if not os.path.exists(p):
            continue
        im = Image.open(p).convert("RGBA")
        # attachment w/h give the DISPLAY size — the png itself may be larger
        if (im.width, im.height) != (ent.get("width", im.width), ent.get("height", im.height)):
            im = im.resize((max(1, int(ent.get("width", im.width))),
                            max(1, int(ent.get("height", im.height)))), Image.LANCZOS)
        bx, by = _bone_world(bones, s["bone"]); e = bd.get(s["bone"], {"rot": 0, "tx": 0, "ty": 0, "sx": 1, "sy": 1})
        if e["sx"] != 1 or e["sy"] != 1:
            im = im.resize((max(1, int(im.width * e["sx"])), max(1, int(im.height * e["sy"]))))
        if e["rot"]:
            im = im.rotate(e["rot"], expand=True, resample=Image.BICUBIC)
        px = bx + ent.get("x", 0) + e["tx"]; py = by + ent.get("y", 0) + e["ty"]
        cv.alpha_composite(im, (int(px + W / 2 - im.width / 2), int(H - py - im.height / 2)))
    sc = maxpx / max(W, H)
    return cv.resize((max(1, int(W * sc)), max(1, int(H * sc))))


# default poses to sample per animation (time in seconds)
_POSES = {"idle": [0.0, 1.4], "win": [0.1, 0.28, 0.5], "blink": [0.15], "pop": [0.0]}


def montage(rig_json: str, images_dir: str, out_png: str, maxpx: int = 200) -> str:
    d = json.load(open(rig_json))
    shots = []
    for a in d["animations"]:
        for t in _POSES.get(a, [0.0]):
            shots.append((a, t))
    cells = [(_render(d, images_dir, a, t, maxpx), f"{a}@{t}") for a, t in shots]
    cw, ch = cells[0][0].size
    cols = len(cells)
    mont = Image.new("RGBA", (cols * cw + 8 * (cols + 1), ch + 22), (0, 0, 0, 255))
    dr = ImageDraw.Draw(mont)
    for i, (im, lbl) in enumerate(cells):
        x = 8 + i * (cw + 8)
        mont.paste(im, (x + (cw - im.width) // 2, 10 + (ch - im.height) // 2))
        dr.text((x + 2, ch + 8), lbl, fill=(255, 255, 255, 255))
    os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
    mont.convert("RGB").save(out_png)
    return out_png
