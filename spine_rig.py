"""spine_rig — turn a cut-up character into a rigged + animated Spine 4.2 skeleton.

Input is EITHER:
  - a PhotoshopToSpine export folder  (<dir>/<name>.json + <dir>/images/*.png), or
  - a layered .psd                    (each top-level layer = one part; read via psd-tools)

Output (written to out_dir):
  <name>.json   runtime skeleton (bones/slots/skin/animations), Spine 4.2 format
  <name>.atlas  texture atlas (shelf-packed; region name == attachment name)
  <name>.png    atlas page

Rig: body(root) + head(neck) [+ rot]; collar/fire ride the body. Head-state
families (head/head_win/head_blink or face/face_win/face_blink) collapse into ONE
slot with attachment-swap inside the win/blink timelines.

Anims: idle(loop) · win(squash-stretch pop + face-swap) · blink(face-swap) ·
pop(squash landing). One-shots end at the setup pose so they mix back cleanly.

This is the engine behind the Spine MCP server's `rig_and_animate` tool; it is a
pure function (no MCP, no globals) so it can also be imported or run standalone.
"""
from __future__ import annotations
import json, os, glob, re, shutil
from PIL import Image

SUFFIX = ("_win", "_blink")


# ---------------------------------------------------------------- input readers
def _read_photoshop_export(export_dir: str):
    """Return (parts, draw_order, images_dir). parts[slot]=dict(cx,cy,w,h,file)."""
    exp = None
    for cand in sorted(glob.glob(f"{export_dir}/*.json")):
        try:
            data = json.load(open(cand))
        except Exception:
            continue
        if isinstance(data, dict) and "skins" in data:   # the layout, not bulges.json etc.
            exp = data
            break
    if exp is None:
        raise ValueError(f"no layout json with 'skins' in {export_dir}")
    images_dir = f"{export_dir}/images"
    skin = exp["skins"]["default"] if isinstance(exp["skins"], dict) else \
        next(s for s in exp["skins"] if s["name"] == "default")["attachments"]
    draw = [s["name"] for s in exp["slots"]]                       # back→front
    parts = {}
    for slot, atts in skin.items():
        an, pl = next(iter(atts.items()))
        parts[slot] = dict(x=float(pl.get("x", 0)), y=float(pl.get("y", 0)),
                            w=float(pl["width"]), h=float(pl["height"]), file=an)
    return parts, draw, images_dir


def _walk_psd_layers(parent, prefix=""):
    """Yield visible leaf layers as ``(path, layer)`` in PSD draw order."""
    for layer in parent:
        path = f"{prefix}/{layer.name.strip()}" if prefix else layer.name.strip()
        if not layer.is_visible():
            continue
        if layer.is_group():
            yield from _walk_psd_layers(layer, path)
        else:
            yield path, layer


def _find_psd_group(psd, requested: str):
    wanted = requested.strip().casefold().strip("/")
    matches = []

    def visit(parent, prefix=""):
        for layer in parent:
            path = f"{prefix}/{layer.name.strip()}" if prefix else layer.name.strip()
            if layer.is_group():
                if layer.name.strip().casefold() == wanted or path.casefold() == wanted:
                    matches.append((path, layer))
                visit(layer, path)

    visit(psd)
    if not matches:
        raise ValueError(f"PSD group {requested!r} was not found")
    if len(matches) > 1:
        paths = ", ".join(path for path, _ in matches)
        raise ValueError(f"PSD group {requested!r} is ambiguous; use one of: {paths}")
    return matches[0]


def _safe_part_name(path: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.strip()).strip("_.") or "part"
    candidate, index = base, 2
    while candidate.casefold() in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate.casefold())
    return candidate


def _semantic_role(path: str) -> str:
    value = path.casefold().replace("-", "_").replace(" ", "_")
    rules = (
        ("face", ("face", "eye", "mouth", "smile", "brow")),
        ("hand_money", ("money_hand", "hand_money", "cash_hand", "arm_money")),
        ("hand_steering", ("steering_hand", "hand_steering", "wheel_hand")),
        ("steering_wheel", ("steering", "wheel")),
        ("coin_fx", ("coin", "gold")),
        ("shine_fx", ("shine", "sweep", "windshield")),
        ("glow_fx", ("glow", "burst", "ray", "spark", "flash")),
        ("title", ("mega_win", "title", "headline")),
        ("number_display", ("number", "digits", "counter", "amount")),
        ("vehicle", ("jeep", "vehicle", "car", "truck")),
        ("driver", ("driver", "body", "torso", "head")),
    )
    for role, words in rules:
        if any(word in value for word in words):
            return role
    return "unclassified"


def _read_psd(psd_path: str, work_dir: str, source_group: str | None = None):
    """Flatten visible leaf layers from a PSD (or selected nested group).

    Layer paths become unique, filesystem-safe attachment names. This avoids the
    old failure mode where a top-level folder was flattened into one giant image
    and duplicate nested layer names silently overwrote each other.
    """
    from psd_tools import PSDImage
    psd = PSDImage.open(psd_path)
    images_dir = os.path.join(work_dir, "images")
    os.makedirs(images_dir, exist_ok=True)
    Wc, Hc = psd.width, psd.height
    parts, draw, used = {}, [], set()
    parent, prefix = psd, ""
    if source_group:
        prefix, parent = _find_psd_group(psd, source_group)
    for path, layer in _walk_psd_layers(parent, prefix):
        if layer.bbox == (0, 0, 0, 0):
            continue
        name = _safe_part_name(path, used)
        try:
            img = layer.composite()
        except (ImportError, ModuleNotFoundError):
            img = layer.topil()
        if img is None:
            continue
        img.save(os.path.join(images_dir, f"{name}.png"))
        l, t, r, b = layer.bbox
        w, h = r - l, b - t
        cx = (l + r) / 2 - Wc / 2                                 # centre, origin mid-top
        cy = Hc - (t + b) / 2                                     # +Y up from bottom
        parts[name] = dict(x=cx, y=cy, w=float(w), h=float(h), file=name,
                           layer_path=path, role=_semantic_role(path))
        draw.append(name)
    return parts, draw, images_dir


def inspect_psd(psd_path: str, work_dir: str, source_group: str | None = None) -> dict:
    """Return a semantic build plan without hiding nested PSD structure."""
    from psd_tools import PSDImage
    psd = PSDImage.open(psd_path)
    groups = []

    def visit(parent, prefix=""):
        for layer in parent:
            path = f"{prefix}/{layer.name.strip()}" if prefix else layer.name.strip()
            if layer.is_group():
                groups.append(path)
                visit(layer, path)

    visit(psd)
    parts, draw, _ = _read_psd(psd_path, work_dir, source_group)
    role_map: dict[str, list[str]] = {}
    layers = []
    for name in draw:
        part = parts[name]
        role_map.setdefault(part["role"], []).append(name)
        layers.append({"attachment": name, "layer_path": part["layer_path"],
                       "role": part["role"], "bounds": [part["x"], part["y"],
                                                          part["w"], part["h"]]})
    warnings = []
    if not source_group and groups:
        warnings.append("PSD contains groups; select source_group when only one folder is the asset set")
    if role_map.get("unclassified"):
        warnings.append(f"{len(role_map['unclassified'])} layers need explicit art-direction roles")
    return {"canvas": [psd.width, psd.height], "source_group": source_group,
            "groups": groups, "layers": layers, "roles": role_map,
            "count": len(layers), "warnings": warnings}


# ------------------------------------------------------------------- rig engine
# Which part names count as the head, beyond the ones detected as a state family.
#
# A heuristic, and one tuned on one studio's art — "crown" and "tooth" are head
# parts for a slot symbol and would be nothing of the kind on a knight. A layer
# called kopf, cabeza or tete lands in the body with no complaint, which is the
# failure worth knowing about rather than pretending away.
#
# Overridable so a different naming convention does not need a fork:
#   SPINE_HEAD_WORDS=kopf,gesicht,krone
HEAD_WORDS = tuple(
    w.strip().lower()
    for w in os.environ.get("SPINE_HEAD_WORDS", "head,face,golova,crown,tooth").split(",")
    if w.strip()
)


def _classify(name: str, state_bases) -> str:
    s = name.lower()
    if s in state_bases:
        return "head"
    if any(k in s for k in HEAD_WORDS):
        return "head"
    if "rot" in s:
        return "rot"
    return "body"


def build_rig(source: str, out_dir: str, name: str | None = None,
              kind: str = "symbol", anims: list[str] | None = None,
              *, clean_mesh: bool = False, auto_weight: bool = False,
              ik: bool = False, clipping: bool = False,
              slot_presets: list[str] | None = None,
              source_group: str | None = None) -> dict:
    """Build the skeleton. `source` is an export folder or a .psd. Returns a
    summary dict {name, width, height, bones, slots, head_slot, variants, anims,
    files}."""
    if name is None:
        name = os.path.splitext(os.path.basename(source.rstrip("/")))[0]
    os.makedirs(out_dir, exist_ok=True)

    if source.lower().endswith(".psd"):
        parts, draw, images_dir = _read_psd(source, out_dir, source_group)
    else:
        parts, draw, images_dir = _read_photoshop_export(source)
    if not parts:
        raise ValueError(f"no parts found in {source}")

    # detect head-state families: base + base_win + base_blink
    fam = {}
    for n in draw:
        base = n
        for s in SUFFIX:
            if n.lower().endswith(s):
                base = n[: -len(s)]
                break
        state = next((s[1:] for s in SUFFIX if n.lower().endswith(s)), "base")
        fam.setdefault(base, {})[state] = n
    STATE_FAM = {b: v for b, v in fam.items() if len(v) > 1 and "base" in v}
    variant2base = {sl: b for b, st in STATE_FAM.items() for k, sl in st.items() if k != "base"}
    draw_final = [n for n in draw if n not in variant2base]
    state_bases = {b.lower() for b in STATE_FAM}

    # normalize: root at bottom-centre, +Y up
    minX = min(p["x"] - p["w"] / 2 for p in parts.values())
    maxX = max(p["x"] + p["w"] / 2 for p in parts.values())
    minY = min(p["y"] - p["h"] / 2 for p in parts.values())
    maxY = max(p["y"] + p["h"] / 2 for p in parts.values())
    cx0 = (minX + maxX) / 2
    W, H = maxX - minX, maxY - minY
    norm = {n: dict(cx=p["x"] - cx0, cy=p["y"] - minY, w=p["w"], h=p["h"], file=p["file"])
            for n, p in parts.items()}

    cls = {n: _classify(n, state_bases) for n in draw_final}
    FIRE = {n for n in draw_final if "fire" in n.lower()}
    # light-emitting FX layers render additive but do NOT behave like fire
    # (no ignite/idle heat pulse — the animator lights them in win/destroy)
    _ADD = ("fx_flash", "fx_spark", "fx_bolt", "fx_ring", "fx_star", "fx_glow")
    ADDITIVE_FX = {n for n in draw_final if any(k in n.lower() for k in _ADD)}
    has_head = any(v == "head" for v in cls.values())
    has_rot = any(v == "rot" for v in cls.values())

    heads = [n for n in draw_final if cls[n] == "head"]
    hx = sum(norm[n]["cx"] for n in heads) / len(heads) if heads else 0
    hy = (sum(norm[n]["cy"] for n in heads) / len(heads)
          - max(norm[n]["h"] for n in heads) * 0.42) if heads else H * 0.6
    rots = [n for n in draw_final if cls[n] == "rot"]
    BONES = {"body": ("root", 0.0, round(H * 0.30, 2))}
    if has_head:
        BONES["head"] = ("body", round(hx, 2), round(hy, 2))
    if has_rot:
        BONES["rot"] = ("root", round(norm[rots[0]]["cx"], 2), round(norm[rots[0]]["cy"], 2))

    def slotbone(n):
        if has_rot and cls[n] == "rot":
            return "rot"
        if has_head and cls[n] == "head":
            return "head"
        return "body"

    def bworld(n):
        return (0.0, 0.0) if n == "root" else (BONES[n][1], BONES[n][2])

    # ---- atlas (shelf-pack; region name == attachment name) ------------------
    all_regions = list(norm.keys())
    MAXW, PAD = 1024, 2
    imgs = {n: Image.open(f"{images_dir}/{norm[n]['file']}.png").convert("RGBA") for n in all_regions}
    # Editable Spine projects reference loose images, not pixels embedded inside
    # the .spine file. Always place those images beside the final project so a
    # copied delivery does not retain a hidden dependency on the source folder.
    portable_images = os.path.join(out_dir, "images")
    os.makedirs(portable_images, exist_ok=True)
    for n in all_regions:
        source_image = os.path.abspath(f"{images_dir}/{norm[n]['file']}.png")
        target_image = os.path.abspath(os.path.join(portable_images, f"{norm[n]['file']}.png"))
        if source_image != target_image:
            shutil.copy2(source_image, target_image)
    # A part WIDER than the page used to be pasted anyway: PIL crops silently at
    # the page edge while the .atlas still declares the full region size, so the
    # overhanging columns sample outside the texture and the renderer clamps them
    # into a smeared strip (Soul Siphon: a 1070px frame on a 1024px page → the
    # right 48px of the bezel was a stretched streak). Grow the page instead.
    MAXW = max(MAXW, max((im.width for im in imgs.values()), default=0) + 2 * PAD)
    place, x, y, rowh = {}, PAD, PAD, 0
    for n in sorted(all_regions, key=lambda k: -imgs[k].height):
        w, h = imgs[n].size
        if x + w + PAD > MAXW:
            x, y, rowh = PAD, y + rowh + PAD, 0
        place[n] = (x, y); x += w + PAD; rowh = max(rowh, h)
    pageh = y + rowh + PAD
    page = Image.new("RGBA", (MAXW, pageh), (0, 0, 0, 0))
    for n, (px, py) in place.items():
        page.paste(imgs[n], (px, py))
    page.save(f"{out_dir}/{name}.png")
    al = ["", f"{name}.png", f"size: {MAXW},{pageh}", "filter: Linear,Linear", "repeat: none"]
    for n in all_regions:
        w, h = imgs[n].size; px, py = place[n]
        al += [n, "  rotate: false", f"  xy: {px}, {py}", f"  size: {w}, {h}",
               f"  orig: {w}, {h}", "  offset: 0, 0", "  index: -1"]
    open(f"{out_dir}/{name}.atlas", "w").write("\n".join(al) + "\n")

    # ---- bones / slots / skin ------------------------------------------------
    bones = [{"name": "root"}]
    for bn, (p, bx, by) in BONES.items():
        pw = bworld(p)
        bones.append({"name": bn, "parent": p, "x": round(bx - pw[0], 2),
                      "y": round(by - pw[1], 2), "rotation": 0,
                      "scaleX": 1.0, "scaleY": 1.0, "length": 0})
    GLOW = "Layer 2" if "Layer 2" in draw_final else None
    # molten lava / fire parts → additive blend so a colour pulse reads as heat
    GLOWSET = ([GLOW] if GLOW else []) + sorted(FIRE)
    slots = []
    for n in draw_final:
        s = {"name": n, "bone": slotbone(n), "attachment": n}
        if n in GLOWSET or n in ADDITIVE_FX:
            s["blend"] = "additive"
        slots.append(s)

    def att_entry(region, host_slot):
        bw = bworld(slotbone(host_slot))
        e = {"x": round(norm[region]["cx"] - bw[0], 2), "y": round(norm[region]["cy"] - bw[1], 2),
             "width": int(norm[region]["w"]), "height": int(norm[region]["h"])}
        if region != host_slot:
            e["path"] = region
        return e

    attachments = {}
    for n in draw_final:
        entry = {n: att_entry(n, n)}
        if n in STATE_FAM:
            for st, variant in STATE_FAM[n].items():
                if st != "base":
                    entry[variant] = att_entry(variant, n)
        attachments[n] = entry
    skins = [{"name": "default", "attachments": attachments}]

    HEAD_SLOT = next((n for n in draw_final if n in STATE_FAM), None)
    winface = STATE_FAM.get(HEAD_SLOT, {}).get("win") if HEAD_SLOT else None
    blinkface = STATE_FAM.get(HEAD_SLOT, {}).get("blink") if HEAD_SLOT else None

    # ---- animations ----------------------------------------------------------
    requested = ["idle", "win", "blink", "pop"] if anims is None else list(anims)
    want = set(requested)
    animations = {}

    D = 2.8
    ib = {"body": {"scale": [{"time": 0, "x": 1, "y": 1}, {"time": 1.4, "x": 1.045, "y": 0.985}, {"time": D, "x": 1, "y": 1}],
                   "rotate": [{"time": 0, "value": 0}, {"time": 0.9, "value": 1.7}, {"time": 1.9, "value": -1.4}, {"time": D, "value": 0}]}}
    if has_head:
        ib["head"] = {"rotate": [{"time": 0, "value": 0}, {"time": 0.9, "value": 3}, {"time": 1.9, "value": -2.2}, {"time": D, "value": 0}]}
    if has_rot:
        ib["rot"] = {"rotate": [{"time": 0, "value": 0}, {"time": D / 2, "value": 180}, {"time": D, "value": 360}]}
    idle = {"bones": ib}
    # idle lava/fire: slow molten breathe (slightly irregular dim↔bright, loop closes)
    if GLOWSET:
        idle["slots"] = {n: {"rgba": [
            {"time": 0, "color": "ffffffff"}, {"time": 0.7, "color": "ffcc99ff"},
            {"time": 1.3, "color": "fff2e0ff"}, {"time": 1.9, "color": "ffc285ff"},
            {"time": 2.4, "color": "fff7ecff"}, {"time": D, "color": "ffffffff"}]} for n in GLOWSET}
    if "idle" in want:
        animations["idle"] = idle

    Wd = 0.72
    wb = {"body": {
        "scale": [{"time": 0, "x": 1, "y": 1}, {"time": 0.1, "x": 1.32, "y": 0.76}, {"time": 0.28, "x": 0.8, "y": 1.26}, {"time": 0.46, "x": 1.14, "y": 0.9}, {"time": 0.6, "x": 0.98, "y": 1.03}, {"time": Wd, "x": 1, "y": 1}],
        "rotate": [{"time": 0, "value": 0}, {"time": 0.16, "value": -10}, {"time": 0.36, "value": 10}, {"time": 0.54, "value": -4}, {"time": Wd, "value": 0}],
        "translate": [{"time": 0, "x": 0, "y": 0}, {"time": 0.28, "x": 0, "y": 22}, {"time": 0.5, "x": 0, "y": 0}, {"time": Wd, "x": 0, "y": 0}]}}
    if has_head:
        wb["head"] = {"rotate": [{"time": 0, "value": 0}, {"time": 0.3, "value": 11}, {"time": 0.52, "value": -5}, {"time": Wd, "value": 0}]}
    win = {"bones": wb, "slots": {}}
    if winface:
        win["slots"][HEAD_SLOT] = {"attachment": [{"time": 0, "name": winface}, {"time": Wd, "name": HEAD_SLOT}]}
    for n in GLOWSET:   # lava flares white-hot with a quick flicker on a win
        win["slots"][n] = {"rgba": [{"time": 0, "color": "ffffffff"}, {"time": 0.12, "color": "fff2e0ff"},
                                    {"time": 0.26, "color": "ffd9a0ff"}, {"time": 0.4, "color": "ffffffff"},
                                    {"time": Wd, "color": "ffffffff"}]}
    if not win["slots"]:
        del win["slots"]
    if "win" in want:
        animations["win"] = win

    if "blink" in want and blinkface:
        animations["blink"] = {"slots": {HEAD_SLOT: {"attachment": [
            {"time": 0, "name": HEAD_SLOT}, {"time": 0.12, "name": blinkface}, {"time": 0.22, "name": HEAD_SLOT}]}}}

    if "pop" in want:
        Pd = 0.36
        animations["pop"] = {"bones": {"body": {
            "scale": [{"time": 0, "x": 1.12, "y": 0.84}, {"time": 0.16, "x": 0.94, "y": 1.08}, {"time": Pd, "x": 1, "y": 1}],
            "translate": [{"time": 0, "x": 0, "y": 10}, {"time": 0.16, "x": 0, "y": -3}, {"time": Pd, "x": 0, "y": 0}]}}}

    # Names outside the built-in set are still useful workflow states.  Generate
    # deterministic, editable starter motion instead of silently dropping them.
    for state in requested:
        if state in animations or state == "blink" and not blinkface:
            continue
        key = state.lower().replace("-", "_")
        if key in {"intro", "enter", "spawn"}:
            animations[state] = {"bones": {"body": {
                "scale": [{"time": 0, "x": 0.15, "y": 0.15}, {"time": .32, "x": 1.12, "y": .92}, {"time": .55, "x": 1, "y": 1}],
                "translate": [{"time": 0, "x": 0, "y": -24}, {"time": .32, "x": 0, "y": 8}, {"time": .55, "x": 0, "y": 0}],
            }}}
        elif key in {"steering", "steer", "turn"}:
            animations[state] = {"bones": {"body": {"rotate": [
                {"time": 0, "value": 0}, {"time": .35, "value": -8},
                {"time": .7, "value": 8}, {"time": 1.05, "value": 0},
            ]}}}
        elif key in {"wave", "waving"}:
            target = "head" if has_head else "body"
            animations[state] = {"bones": {target: {"rotate": [
                {"time": 0, "value": 0}, {"time": .18, "value": 12},
                {"time": .36, "value": -12}, {"time": .54, "value": 12},
                {"time": .75, "value": 0},
            ]}}}
        elif key in {"mega_win", "celebration", "celebrate"}:
            animations[state] = {"bones": {"body": {
                "scale": [{"time": 0, "x": 1, "y": 1}, {"time": .18, "x": 1.35, "y": .72},
                          {"time": .42, "x": .82, "y": 1.3}, {"time": .72, "x": 1.12, "y": .92},
                          {"time": 1.05, "x": 1, "y": 1}],
                "rotate": [{"time": 0, "value": 0}, {"time": .25, "value": -14},
                           {"time": .55, "value": 14}, {"time": 1.05, "value": 0}],
                "translate": [{"time": 0, "x": 0, "y": 0}, {"time": .42, "x": 0, "y": 36},
                              {"time": .75, "x": 0, "y": 0}, {"time": 1.05, "x": 0, "y": 0}],
            }}}
        else:
            animations[state] = {"bones": {"body": {"scale": [
                {"time": 0, "x": 1, "y": 1}, {"time": .3, "x": 1.04, "y": .97},
                {"time": .6, "x": 1, "y": 1},
            ]}}}

    # ignite — lava "catches fire": cold dark ember → red → orange → white-hot with
    # a flicker burst, ending at the bright idle baseline so it mixes back; a small
    # body scale-pop punctuates the heat surge. Only when there's a glow/lava part.
    if GLOWSET and (anims is None or "ignite" in want):
        Ig = 1.35
        glow_ignite = {"rgba": [
            {"time": 0, "color": "2e160bff"}, {"time": 0.18, "color": "7a3010ff"},
            {"time": 0.4, "color": "d9702aff"}, {"time": 0.62, "color": "ffffffff"},
            {"time": 0.76, "color": "ffd49bff"}, {"time": 0.9, "color": "ffffffff"},
            {"time": 1.05, "color": "ffe3b8ff"}, {"time": 1.18, "color": "ffffffff"},
            {"time": Ig, "color": "ffffffff"}]}
        animations["ignite"] = {"slots": {n: dict(glow_ignite) for n in GLOWSET},
                                "bones": {"body": {"scale": [
                                    {"time": 0, "x": 1, "y": 1}, {"time": 0.55, "x": 0.99, "y": 1.0},
                                    {"time": 0.64, "x": 1.07, "y": 0.95}, {"time": 0.8, "x": 0.98, "y": 1.02},
                                    {"time": Ig, "x": 1, "y": 1}]}}}

    # Optional slot presets are emitted as independent animations so games can
    # mix them on a separate track. Prefer FX slots; fall back to every slot.
    preset_targets = sorted(set(GLOWSET) | ADDITIVE_FX | FIRE) or list(draw_final)
    for preset in slot_presets or []:
        p = preset.strip().lower()
        if p == "pulse":
            timeline = {"rgba": [{"time": 0, "color": "ffffffff"},
                                  {"time": .4, "color": "ffffff66"},
                                  {"time": .8, "color": "ffffffff"}]}
        elif p == "flash":
            timeline = {"rgba": [{"time": 0, "color": "ffffffff"},
                                  {"time": .08, "color": "ffffffff"},
                                  {"time": .16, "color": "ffffff00"},
                                  {"time": .28, "color": "ffffffff"}]}
        elif p == "flicker":
            timeline = {"rgba": [{"time": 0, "color": "ffffffff"},
                                  {"time": .09, "color": "ffffff77"},
                                  {"time": .17, "color": "ffffffff"},
                                  {"time": .31, "color": "ffffff99"},
                                  {"time": .45, "color": "ffffffff"}]}
        else:
            raise ValueError(f"unknown slot preset {preset!r}; use pulse, flash, or flicker")
        animations[f"slot_{p}"] = {"slots": {n: {k: list(v) for k, v in timeline.items()}
                                                for n in preset_targets}}

    # A four-corner mesh is deliberately conservative: it preserves artwork and
    # removes degenerate geometry while making every part deformable in Spine.
    if clean_mesh or auto_weight:
        bone_indexes = {b["name"]: i for i, b in enumerate(bones)}
        for slot_name, slot_atts in attachments.items():
            bone_index = bone_indexes[slotbone(slot_name)]
            for entry in slot_atts.values():
                w, h = float(entry["width"]), float(entry["height"])
                x, y = float(entry.get("x", 0)), float(entry.get("y", 0))
                xy = [(x - w / 2, y - h / 2), (x + w / 2, y - h / 2),
                      (x + w / 2, y + h / 2), (x - w / 2, y + h / 2)]
                entry.update({"type": "mesh", "uvs": [0, 1, 1, 1, 1, 0, 0, 0],
                              "triangles": [0, 1, 2, 2, 3, 0], "hull": 4})
                if auto_weight:
                    entry["vertices"] = [value for vx, vy in xy
                                         for value in (1, bone_index, vx, vy, 1)]
                else:
                    entry["vertices"] = [value for point in xy for value in point]

    constraints = []
    if ik:
        constrained = "head" if has_head else "body"
        target_world = bworld(constrained)
        target = f"{constrained}_ik_target"
        bones.append({"name": target, "parent": "root", "x": round(target_world[0], 2),
                      "y": round(target_world[1], 2), "rotation": 0})
        constraints.append({"name": f"{constrained}_ik", "target": target,
                            "bones": [constrained], "mix": 1, "bendPositive": True})

    if clipping and draw_final:
        clip_name = "__gpt_spine_clip"
        slots.insert(0, {"name": clip_name, "bone": "root", "attachment": clip_name})
        attachments[clip_name] = {clip_name: {
            "type": "clipping", "end": draw_final[-1], "vertexCount": 4,
            "vertices": [round(-W / 2, 2), 0, round(W / 2, 2), 0,
                         round(W / 2, 2), round(H, 2), round(-W / 2, 2), round(H, 2)],
        }}

    skel = {"skeleton": {"hash": f"gpt-spine-mcp-{name}", "spine": "4.2.00",
                         "x": round(-W / 2, 2), "y": 0, "width": round(W, 2), "height": round(H, 2),
                         "images": "./images/", "audio": ""},
            "bones": bones, "slots": slots, "skins": skins, "animations": animations}
    if constraints:
        skel["ik"] = constraints
    open(f"{out_dir}/{name}.json", "w").write(json.dumps(skel))

    return {
        "name": name, "width": round(W), "height": round(H),
        "bones": [b["name"] for b in bones], "slots": [s["name"] for s in slots],
        "head_slot": HEAD_SLOT, "variants": list(STATE_FAM.get(HEAD_SLOT, {})) if HEAD_SLOT else [],
        "anims": list(animations), "mesh": clean_mesh or auto_weight,
        "weighted": auto_weight, "ik": bool(constraints), "clipping": clipping,
        "source_group": source_group,
        "files": {"json": f"{out_dir}/{name}.json", "atlas": f"{out_dir}/{name}.atlas", "png": f"{out_dir}/{name}.png"},
    }
