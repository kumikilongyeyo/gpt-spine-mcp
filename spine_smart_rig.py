"""Semantic post-rig cleanup for GPT Spine.

V3 adds PSD naming intelligence, alpha-silhouette meshes, secondary chains for
hair/cloth/tails/wings, transformation families, and lightweight facial controls
while preserving the V2 body/limb behavior.
"""
from __future__ import annotations

import json
import math
import os
from typing import Iterable

from PIL import Image

import spine_semantics

FLEX = {
    "torso", "pelvis", "upper_arm", "lower_arm", "upper_leg", "lower_leg",
    "fore_leg", "hind_leg", "tail", "wing", "hair", "cloth", "neck", "jaw",
    "brow", "ear",
}


def _canon(value: str) -> str:
    return spine_semantics.canon(value)


def _side(value: str) -> str:
    return spine_semantics.classify_path(value)["side"]


def semantic_role(name: str) -> str:
    return spine_semantics.classify_path(name)["role"]


def infer_profile(names: Iterable[str], requested: str = "auto") -> str:
    if requested and requested.casefold() not in {"auto", "smart"}:
        return requested
    roles = [semantic_role(name) for name in names]
    if "wing" in roles:
        return "winged"
    if any(role in roles for role in ("fore_leg", "hind_leg", "paw", "hoof")):
        return "quadruped"
    if sum(role in {"upper_arm", "lower_arm", "hand", "upper_leg", "lower_leg", "foot"} for role in roles) >= 2:
        return "biped"
    return "prop"


def _worlds(bones: list[dict]) -> dict[str, tuple[float, float]]:
    by_name = {bone["name"]: bone for bone in bones}
    output = {}

    def world(name):
        if name in output:
            return output[name]
        bone = by_name[name]
        parent = bone.get("parent")
        px, py = world(parent) if parent in by_name else (0.0, 0.0)
        output[name] = (px + float(bone.get("x", 0)), py + float(bone.get("y", 0)))
        return output[name]

    for name in by_name:
        world(name)
    return output


def _attachments(skins) -> dict:
    if isinstance(skins, dict):
        return skins.get("default", {})
    if not skins:
        return {}
    skin = next((item for item in skins if item.get("name") == "default"), skins[0])
    return skin.get("attachments", {})


def _entry(attachments: dict, name: str):
    values = attachments.get(name, {})
    if name in values:
        return values[name]
    return next((value for value in values.values()
                 if isinstance(value, dict) and value.get("type", "region") not in
                 {"clipping", "boundingbox", "path", "point"}), None)


def _pivot(cx, cy, width, height, tx, ty):
    dx, dy = tx - cx, ty - cy
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return cx, cy
    distance = math.hypot(dx, dy)
    ux, uy = dx / distance, dy / distance
    a = (width / 2) / abs(ux) if abs(ux) > 1e-6 else 1e9
    b = (height / 2) / abs(uy) if abs(uy) > 1e-6 else 1e9
    amount = min(a, b)
    return cx + ux * amount, cy + uy * amount


def _generic_mesh(entry: dict, role: str, child_index: int, parent_index: int | None,
                  child_world, parent_world, max_influences: int) -> bool:
    if entry.get("type") in {"clipping", "boundingbox", "path", "point"}:
        return False
    width, height = float(entry.get("width", 0)), float(entry.get("height", 0))
    if width <= 0 or height <= 0:
        return False
    cx, cy = float(entry.get("x", 0)), float(entry.get("y", 0))
    segments = 3 if role in {"tail", "wing", "cloth", "hair"} else 2 if role in FLEX else 1
    left, right, bottom, top = cx - width / 2, cx + width / 2, cy - height / 2, cy + height / 2
    hull = []
    for index in range(segments + 1):
        hull.append((left + (right - left) * index / segments, top))
    for index in range(1, segments + 1):
        hull.append((right, top + (bottom - top) * index / segments))
    for index in range(1, segments + 1):
        hull.append((right + (left - right) * index / segments, bottom))
    for index in range(1, segments):
        hull.append((left, bottom + (top - bottom) * index / segments))
    points = hull + [(cx, cy)]
    count = len(hull)
    uvs = []
    for x, y in points:
        uvs += [round((x - left) / width, 6), round((top - y) / height, 6)]
    triangles = []
    for index in range(count):
        triangles += [index, (index + 1) % count, count]
    vertices = []
    blend = max_influences > 1 and parent_index is not None and parent_world is not None
    for x, y in points:
        distance = math.hypot(x, y)
        parent_weight = max(0, min(.42, .42 * (1 - distance / max(1, min(width, height) * .6)))) if blend else 0
        child_weight = 1 - parent_weight
        wx, wy = child_world[0] + x, child_world[1] + y
        if parent_weight > .015:
            vertices += [
                2, child_index, round(x, 3), round(y, 3), round(child_weight, 4),
                parent_index, round(wx - parent_world[0], 3), round(wy - parent_world[1], 3),
                round(parent_weight, 4),
            ]
        else:
            vertices += [1, child_index, round(x, 3), round(y, 3), 1]
    path = entry.get("path")
    keep = {key: entry[key] for key in ("width", "height") if key in entry}
    entry.clear()
    if path:
        entry["path"] = path
    entry.update(keep)
    entry.update({"type": "mesh", "uvs": uvs, "triangles": triangles,
                  "vertices": vertices, "hull": count})
    return True


def _sample_outline(image_path: str, entry: dict, samples: int = 7) -> list[tuple[float, float]] | None:
    """Sample a PNG alpha envelope without a heavyweight CV dependency."""
    try:
        alpha = Image.open(image_path).convert("RGBA").getchannel("A")
    except (OSError, ValueError):
        return None
    bbox = alpha.getbbox()
    if not bbox:
        return None
    x0, y0, x1, y1 = bbox
    box_width, box_height = max(1, x1 - x0), max(1, y1 - y0)
    points_px = []
    pixels = alpha.load()
    if box_height >= box_width:
        rows = [round(y0 + (box_height - 1) * index / max(1, samples - 1)) for index in range(samples)]
        lefts, rights = [], []
        for y in rows:
            xs = [x for x in range(x0, x1) if pixels[x, y] > 24]
            if xs:
                lefts.append((min(xs), y))
                rights.append((max(xs), y))
        points_px = lefts + list(reversed(rights))
    else:
        columns = [round(x0 + (box_width - 1) * index / max(1, samples - 1)) for index in range(samples)]
        tops, bottoms = [], []
        for x in columns:
            ys = [y for y in range(y0, y1) if pixels[x, y] > 24]
            if ys:
                tops.append((x, min(ys)))
                bottoms.append((x, max(ys)))
        points_px = tops + list(reversed(bottoms))
    dedup = []
    for point in points_px:
        if point not in dedup:
            dedup.append(point)
    if len(dedup) < 6:
        return None
    image_width, image_height = alpha.size
    width, height = float(entry.get("width", image_width)), float(entry.get("height", image_height))
    cx, cy = float(entry.get("x", 0)), float(entry.get("y", 0))
    output = []
    for px, py in dedup:
        x = cx + ((px - (image_width - 1) / 2) / max(1, image_width - 1)) * width
        y = cy + (((image_height - 1) / 2 - py) / max(1, image_height - 1)) * height
        output.append((round(x, 3), round(y, 3)))
    return output


def _nearest_weights(world_point, chain: list[str], worlds: dict[str, tuple[float, float]], limit: int):
    distances = []
    for bone in chain:
        bx, by = worlds[bone]
        distances.append((math.hypot(world_point[0] - bx, world_point[1] - by), bone))
    distances.sort(key=lambda item: item[0])
    chosen = distances[:max(1, min(limit, 2, len(distances)))]
    if len(chosen) == 1 or chosen[0][0] < 1e-6:
        return [(chosen[0][1], 1.0)]
    inverse = [1 / max(.001, distance) for distance, _ in chosen]
    total = sum(inverse)
    return [(chosen[index][1], inverse[index] / total) for index in range(len(chosen))]


def _silhouette_mesh(entry: dict, image_path: str, chain: list[str], bone_indexes: dict[str, int],
                     worlds: dict[str, tuple[float, float]], max_influences: int) -> bool:
    outline = _sample_outline(image_path, entry)
    if not outline:
        return False
    cx = sum(point[0] for point in outline) / len(outline)
    cy = sum(point[1] for point in outline) / len(outline)
    points = outline + [(cx, cy)]
    count = len(outline)
    width, height = float(entry.get("width", 1)), float(entry.get("height", 1))
    ex, ey = float(entry.get("x", 0)), float(entry.get("y", 0))
    left, top = ex - width / 2, ey + height / 2
    uvs = []
    for x, y in points:
        uvs += [round((x - left) / max(1, width), 6), round((top - y) / max(1, height), 6)]
    triangles = []
    for index in range(count):
        triangles += [index, (index + 1) % count, count]
    host = chain[0]
    host_world = worlds[host]
    vertices = []
    for x, y in points:
        wx, wy = host_world[0] + x, host_world[1] + y
        influences = _nearest_weights((wx, wy), chain, worlds, max_influences)
        vertices.append(len(influences))
        for bone, weight in influences:
            bx, by = worlds[bone]
            vertices += [bone_indexes[bone], round(wx - bx, 3), round(wy - by, 3), round(weight, 4)]
    path = entry.get("path")
    keep = {key: entry[key] for key in ("width", "height") if key in entry}
    entry.clear()
    if path:
        entry["path"] = path
    entry.update(keep)
    entry.update({"type": "mesh", "uvs": uvs, "triangles": triangles,
                  "vertices": vertices, "hull": count})
    return True


def _rotate(data: dict, clip: str, bone: str | None, values):
    if bone and clip in data.get("animations", {}):
        data["animations"][clip].setdefault("bones", {}).setdefault(bone, {})["rotate"] = [
            ({"value": value} if time == 0 else {"time": time, "value": value})
            for time, value in values
        ]


def _primary_motion(data: dict, role_bones: dict, profile: str):
    done = []
    bone_for = lambda role, side="": role_bones.get((role, side)) or role_bones.get((role, ""))
    for clip in ("walk", "run"):
        if clip in data.get("animations", {}):
            amplitude, duration = (23, .8) if clip == "walk" else (34, .52)
            for side, phase in (("l", 1), ("r", -1)):
                _rotate(data, clip, bone_for("upper_leg", side), [(0, amplitude * phase), (duration / 2, -amplitude * phase), (duration, amplitude * phase)])
                _rotate(data, clip, bone_for("lower_leg", side), [(0, -8 * phase), (duration / 2, 26 * phase), (duration, -8 * phase)])
                _rotate(data, clip, bone_for("upper_arm", side), [(0, -amplitude * .65 * phase), (duration / 2, amplitude * .65 * phase), (duration, -amplitude * .65 * phase)])
            done.append(clip)
    if "attack" in data.get("animations", {}):
        arm = bone_for("upper_arm", "r") or bone_for("upper_arm", "l")
        forearm = bone_for("lower_arm", "r") or bone_for("lower_arm", "l")
        _rotate(data, "attack", arm, [(0, -12), (.16, -42), (.31, 52), (.55, 0)])
        _rotate(data, "attack", forearm, [(0, 0), (.16, -28), (.31, 36), (.55, 0)])
        done.append("attack")
    body = "body" if any(bone.get("name") == "body" for bone in data.get("bones", [])) else "root"
    if "depth_flip" in data.get("animations", {}):
        data["animations"]["depth_flip"].setdefault("bones", {}).setdefault(body, {})["scale"] = [
            {"x": 1, "y": 1}, {"time": .22, "x": .08, "y": .94},
            {"time": .44, "x": -1, "y": 1}, {"time": .66, "x": -.08, "y": .94},
            {"time": .88, "x": 1, "y": 1},
        ]
        done.append("depth_flip")
    if "depth_shimmer" in data.get("animations", {}):
        data["animations"]["depth_shimmer"].setdefault("bones", {}).setdefault(body, {}).update({
            "rotate": [{"value": -1.2}, {"time": .55, "value": 1.2}, {"time": 1.1, "value": -1.2}],
            "scale": [{"x": .985, "y": 1.01}, {"time": .55, "x": 1.015, "y": .99}, {"time": 1.1, "x": .985, "y": 1.01}],
        })
        done.append("depth_shimmer")
    return sorted(set(done))


def _secondary_motion(data: dict, chains: dict[str, dict]) -> list[str]:
    touched = set()
    animations = data.get("animations", {})
    for meta in chains.values():
        chain = meta["bones"]
        drag = float(meta["semantic"]["secondary"]["drag"])
        for clip in list(animations):
            if clip.startswith("transform_"):
                continue
            if clip == "idle":
                duration = 2.4
                for index, bone in enumerate(chain[1:] or chain):
                    amplitude = (1.6 + 2.0 * index) * (.6 + drag)
                    _rotate(data, clip, bone, [(0, 0), (.65, amplitude), (1.35, -amplitude * .8), (duration, 0)])
                touched.add(clip)
            elif clip in {"walk", "run"}:
                duration = .8 if clip == "walk" else .52
                base = 5 if clip == "walk" else 9
                for index, bone in enumerate(chain[1:] or chain):
                    amplitude = (base + index * 2.3) * (.65 + drag)
                    phase = .08 * min(index, 3)
                    _rotate(data, clip, bone, [(0, -amplitude * .3), (duration * (.28 + phase), amplitude),
                                               (duration * (.72 + phase / 2), -amplitude), (duration, 0)])
                touched.add(clip)
            elif clip in {"attack", "hit", "land", "jump", "win", "mega_win", "celebration", "depth_flip"}:
                base = {"attack": 12, "hit": 10, "land": 14, "jump": 8, "win": 10,
                        "mega_win": 14, "celebration": 12, "depth_flip": 9}.get(clip, 9)
                for index, bone in enumerate(chain[1:] or chain):
                    amplitude = (base + index * 3) * (.6 + drag)
                    _rotate(data, clip, bone, [(0, 0), (.16, -amplitude * .45), (.34, amplitude),
                                               (.58, -amplitude * .35), (.84, 0)])
                touched.add(clip)
    return sorted(touched)


def _apply_transformations(data: dict, semantic_items: list[dict]) -> tuple[list[dict], list[str]]:
    slots = {slot["name"]: slot for slot in data.get("slots", [])}
    groups = {}
    for item in semantic_items:
        if item["role"] == "unknown":
            continue
        groups.setdefault((item["family"], item["role"], item["side"]), []).append(item)
    families, by_state = [], {}
    for (family, role, side), members in groups.items():
        states = {}
        for item in members:
            states.setdefault(item["state"], []).append(item["attachment"])
        non_base = [state for state in states if state != "base"]
        if not non_base:
            continue
        base = (states.get("base") or [members[0]["attachment"]])[0]
        families.append({"family": family, "role": role, "side": side, "base": base, "states": states})
        for state in non_base:
            for target in states[state]:
                if target in slots:
                    slots[target]["color"] = "ffffff00"
                by_state.setdefault(state, []).append((base, target))
    created = []
    for state, pairs in by_state.items():
        clip = f"transform_{state}"
        slot_tracks = {}
        for base, target in pairs:
            slot_tracks[base] = {"rgba": [{"color": "ffffffff"}, {"time": .12, "color": "ffffffff"}, {"time": .22, "color": "ffffff00"}]}
            slot_tracks[target] = {"rgba": [{"color": "ffffff00"}, {"time": .10, "color": "ffffff00"}, {"time": .22, "color": "ffffffff"}]}
        body = "body" if any(bone.get("name") == "body" for bone in data.get("bones", [])) else "root"
        data.setdefault("animations", {})[clip] = {
            "slots": slot_tracks,
            "bones": {body: {"scale": [{"x": 1, "y": 1}, {"time": .10, "x": .96, "y": 1.04},
                                        {"time": .24, "x": 1.04, "y": .97}, {"time": .42, "x": 1, "y": 1}]}},
        }
        created.append(clip)
    return families, created


def _facial_controls(data: dict, semantic_items: list[dict], role_bones: dict) -> dict:
    slots = {slot["name"]: slot for slot in data.get("slots", [])}
    controls = {"eyes": [], "pupils": [], "brows": [], "mouth": [], "jaw": [], "eyelids": []}
    role_to_key = {"eye": "eyes", "pupil": "pupils", "brow": "brows", "mouth": "mouth", "jaw": "jaw", "eyelid": "eyelids"}
    for item in semantic_items:
        key = role_to_key.get(item["role"])
        if key:
            controls[key].append(item["attachment"])
    if controls["eyelids"]:
        for name in controls["eyelids"]:
            if name in slots:
                slots[name]["color"] = "ffffff00"
        blink = data.setdefault("animations", {}).setdefault("blink", {})
        tracks = blink.setdefault("slots", {})
        for name in controls["eyelids"]:
            tracks[name] = {"rgba": [{"color": "ffffff00"}, {"time": .06, "color": "ffffffff"},
                                      {"time": .13, "color": "ffffffff"}, {"time": .20, "color": "ffffff00"}]}
        for name in controls["eyes"]:
            tracks[name] = {"rgba": [{"color": "ffffffff"}, {"time": .06, "color": "ffffff00"},
                                      {"time": .13, "color": "ffffff00"}, {"time": .20, "color": "ffffffff"}]}
    controls["bones"] = {
        role + (f"_{side}" if side else ""): bone
        for (role, side), bone in role_bones.items()
        if role in {"eye", "pupil", "brow", "mouth", "jaw", "eyelid", "ear", "nose"}
    }
    return controls


def _image_path(images_dir: str, slot_name: str, entry: dict) -> str:
    return os.path.join(images_dir, (entry.get("path") or slot_name) + ".png")


def enhance(runtime_json: str, profile: str = "auto", mesh_quality: str = "adaptive",
            max_influences: int = 2, add_ik: bool = False, images_dir: str = "",
            naming_profile: str = "", naming_source: str = "") -> dict:
    runtime_json = os.path.abspath(os.path.expanduser(runtime_json))
    with open(runtime_json, encoding="utf-8") as handle:
        data = json.load(handle)
    slots = data.get("slots", [])
    bones = data.get("bones", [])
    attachments = _attachments(data.get("skins", []))
    naming, naming_path = spine_semantics.load_profile(naming_profile, naming_source or runtime_json)
    semantic_items = []
    for slot in slots:
        name = slot.get("name", "")
        entry = _entry(attachments, name)
        bounds = []
        if entry:
            bounds = [float(entry.get("x", 0)), float(entry.get("y", 0)),
                      float(entry.get("width", 0)), float(entry.get("height", 0))]
        item = spine_semantics.classify_path(name, bounds, profile=naming)
        item["attachment"] = name
        semantic_items.append(item)
    profile_name = infer_profile([slot.get("name", "") for slot in slots], profile)
    if profile_name.startswith("prop"):
        scene = spine_semantics.analyze_inspection({"parts": [slot.get("name", "") for slot in slots]}, naming)
        return {
            "ok": True, "profile": profile_name, "bones_added": [], "meshes_upgraded": 0,
            "weighted_vertices": False, "roles": {}, "motion_upgraded": [], "ik_added": [],
            "secondary_chains": {}, "transformations": [], "facial_controls": {},
            "semantic_scene": scene, "naming_profile": naming_path,
            "warnings": ["smart rig kept prop/symbol topology conservative"],
        }

    before = _worlds(bones)
    names = {bone["name"] for bone in bones}
    role_bones, meta, added, roles, candidates = {}, {}, [], {}, []
    semantic_by_name = {item["attachment"]: item for item in semantic_items}
    for slot in slots:
        name = slot.get("name", "")
        item = semantic_by_name.get(name) or spine_semantics.classify_path(name, profile=naming)
        role = item["role"]
        entry = _entry(attachments, name)
        if not name or name.startswith("__") or role in {"unknown", "prop", "fx"} or not entry:
            continue
        old_bone = slot.get("bone", "root")
        old_world = before.get(old_bone, (0, 0))
        width, height = float(entry.get("width", 0)), float(entry.get("height", 0))
        center = (old_world[0] + float(entry.get("x", 0)), old_world[1] + float(entry.get("y", 0)))
        candidates.append((name, slot, item, entry, old_bone, center, width, height))
    ordered_roles = ("torso", "pelvis", "neck", "head", "eye", "pupil", "eyelid", "brow", "mouth", "jaw", "nose", "ear",
                     "upper_arm", "lower_arm", "hand", "upper_leg", "lower_leg", "foot", "fore_leg", "hind_leg",
                     "paw", "hoof", "wing", "tail", "hair", "cloth")
    order = {role: index for index, role in enumerate(ordered_roles)}
    candidates.sort(key=lambda value: order.get(value[2]["role"], 999))

    def parent_bone(item):
        wanted = item["parent_role"]
        side = item["side"]
        body = "body" if "body" in names else "root"
        if wanted == "body":
            return body
        return (role_bones.get((wanted, side)) or role_bones.get((wanted, "")) or
                ("head" if wanted == "head" and "head" in names else body))

    chains = {}
    for name, slot, item, entry, old_bone, center, width, height in candidates:
        role, side = item["role"], item["side"]
        parent = parent_bone(item)
        worlds_now = _worlds(bones)
        parent_world = worlds_now.get(parent, (0, 0))
        pivot = _pivot(center[0], center[1], max(width, 1), max(height, 1), *parent_world)
        bone_name = f"smart_{_canon(name)}"
        suffix = 2
        while bone_name in names:
            bone_name = f"smart_{_canon(name)}_{suffix}"
            suffix += 1
        bones.append({"name": bone_name, "parent": parent, "x": round(pivot[0] - parent_world[0], 2),
                      "y": round(pivot[1] - parent_world[1], 2), "rotation": 0})
        names.add(bone_name)
        added.append(bone_name)
        role_bones.setdefault((role, side), bone_name)
        role_bones.setdefault((role, ""), bone_name)
        roles[name] = role + (f"_{side}" if side else "")
        old_world = before.get(old_bone, (0, 0))
        for value in attachments.get(name, {}).values():
            if isinstance(value, dict) and value.get("type") not in {"clipping", "boundingbox", "path", "point"}:
                value["x"] = round(old_world[0] + float(value.get("x", entry.get("x", 0))) - pivot[0], 3)
                value["y"] = round(old_world[1] + float(value.get("y", entry.get("y", 0))) - pivot[1], 3)
        slot["bone"] = bone_name
        meta[name] = {"role": role, "bone": bone_name, "parent": parent, "semantic": item}
        secondary = item["secondary"]
        if secondary["enabled"] and secondary["segments"] > 1:
            chain = [bone_name]
            tip = (center[0] + (center[0] - pivot[0]), center[1] + (center[1] - pivot[1]))
            total = max(2, int(secondary["segments"]))
            previous = bone_name
            step = ((tip[0] - pivot[0]) / (total - 1), (tip[1] - pivot[1]) / (total - 1))
            for index in range(1, total):
                child = f"{bone_name}_seg{index}"
                bones.append({"name": child, "parent": previous, "x": round(step[0], 2),
                              "y": round(step[1], 2), "rotation": 0})
                names.add(child)
                added.append(child)
                chain.append(child)
                previous = child
            chains[name] = {"bones": chain, "semantic": item}

    worlds = _worlds(bones)
    ik = []
    if add_ik:
        constraints = data.setdefault("ik", [])
        for label, upper, lower, end in (("arm", "upper_arm", "lower_arm", "hand"), ("leg", "upper_leg", "lower_leg", "foot")):
            for side in ("l", "r"):
                up, low, tip = role_bones.get((upper, side)), role_bones.get((lower, side)), role_bones.get((end, side))
                if not (up and low):
                    continue
                target_world = worlds.get(tip) or worlds.get(low)
                target = f"smart_ik_{label}_{side}_target"
                bones.append({"name": target, "parent": "root", "x": round(target_world[0], 2), "y": round(target_world[1], 2)})
                constraint = f"smart_ik_{label}_{side}"
                constraints.append({"name": constraint, "target": target, "bones": [up, low], "mix": 1, "bendPositive": True})
                ik.append(constraint)

    worlds = _worlds(bones)
    indexes = {bone["name"]: index for index, bone in enumerate(bones)}
    mesh_count = silhouette_count = 0
    weighted = False
    images_dir = os.path.abspath(os.path.expanduser(images_dir)) if images_dir else ""
    if mesh_quality != "none":
        for slot in slots:
            name = slot.get("name", "")
            item_meta = meta.get(name)
            if not item_meta:
                continue
            role = item_meta["role"]
            chain = chains.get(name, {}).get("bones", [item_meta["bone"]])
            for entry in attachments.get(name, {}).values():
                if not isinstance(entry, dict):
                    continue
                used_silhouette = False
                if images_dir and len(chain) > 1:
                    path = _image_path(images_dir, name, entry)
                    if os.path.isfile(path):
                        used_silhouette = _silhouette_mesh(entry, path, chain, indexes, worlds, max_influences)
                if used_silhouette:
                    mesh_count += 1
                    silhouette_count += 1
                    weighted = weighted or max_influences > 1
                elif _generic_mesh(entry, role, indexes[item_meta["bone"]], indexes.get(item_meta["parent"]),
                                   worlds[item_meta["bone"]], worlds.get(item_meta["parent"]), max_influences):
                    mesh_count += 1
                    weighted = weighted or max_influences > 1

    primary = _primary_motion(data, role_bones, profile_name)
    secondary = _secondary_motion(data, chains)
    families, transform_clips = _apply_transformations(data, semantic_items)
    face = _facial_controls(data, semantic_items, role_bones)

    with open(runtime_json, "w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))

    unknown = [item["attachment"] for item in semantic_items if item["role"] == "unknown"]
    warnings = []
    if not added:
        warnings.append("no semantic anatomy slots were recognized; check layer naming")
    if unknown:
        warnings.append(f"{len(unknown)} slots remain semantically unknown")
    scene = {
        "version": 3,
        "role_counts": {},
        "unknown_layers": unknown,
        "confidence": round(sum(item["confidence"] for item in semantic_items) / max(1, len(semantic_items)), 2),
        "layers": semantic_items,
    }
    for item in semantic_items:
        scene["role_counts"][item["role"]] = scene["role_counts"].get(item["role"], 0) + 1
    return {
        "ok": True, "profile": profile_name, "bones_added": added,
        "meshes_upgraded": mesh_count, "silhouette_meshes": silhouette_count,
        "weighted_vertices": weighted, "roles": roles,
        "motion_upgraded": sorted(set(primary + secondary)), "ik_added": ik,
        "secondary_chains": {name: {"bones": value["bones"], "subtype": value["semantic"]["subtype"],
                                     **value["semantic"]["secondary"]} for name, value in chains.items()},
        "transformations": families, "transformation_clips": transform_clips,
        "facial_controls": face, "semantic_scene": scene, "naming_profile": naming_path,
        "warnings": warnings,
    }
