from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

import spine_semantics
from spine_smart_rig import enhance


def test_nested_psd_path_prefers_specific_hair_role_and_subtype():
    item = spine_semantics.classify_path("Character/Head/Hair/Front/Bang_L")
    assert item["role"] == "hair"
    assert item["subtype"] == "front_bang"
    assert item["side"] == "l"
    assert item["depth"] == "front"
    assert item["parent_role"] == "head"
    assert item["secondary"]["segments"] == 2
    assert item["confidence"] >= .8


def test_hair_types_have_different_secondary_behavior():
    pony = spine_semantics.classify_path("Character/Head/Hair/Back/Ponytail_R")
    braid = spine_semantics.classify_path("Character/Head/Hair/Back/Braid_R")
    assert pony["subtype"] == "ponytail"
    assert braid["subtype"] == "braid"
    assert pony["secondary"]["segments"] == 4
    assert braid["secondary"]["segments"] == 4
    assert pony["secondary"]["drag"] > braid["secondary"]["drag"]
    assert pony["secondary"]["stiffness"] < braid["secondary"]["stiffness"]


def test_tagalog_and_custom_naming_profile(tmp_path: Path):
    profile_path = tmp_path / "spine_naming.json"
    saved = spine_semantics.save_profile(str(profile_path), {
        "aliases": {"hand": ["kamayko"], "hair": ["hrx"]},
        "exact": {"HFRONT": "hair_front_bang"},
    })
    assert saved["ok"]
    profile, used = spine_semantics.load_profile(str(profile_path))
    assert used == str(profile_path)
    assert spine_semantics.classify_path("kamayko_L", profile=profile)["role"] == "hand"
    exact = spine_semantics.classify_path("HFRONT", profile=profile)
    assert exact["role"] == "hair"
    assert exact["subtype"] == "front_bang"
    assert spine_semantics.classify_path("buhokback_wind", profile=profile)["subtype"] == "back_mass"


def _write_hair_png(path: Path) -> None:
    image = Image.new("RGBA", (80, 180), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.polygon([(28, 5), (55, 12), (62, 70), (50, 175), (28, 165), (18, 70)],
                 fill=(255, 255, 255, 255))
    image.save(path)


def test_hair_rig_builds_chain_silhouette_mesh_and_power_transform(tmp_path: Path):
    images = tmp_path / "images"
    images.mkdir()
    base = "Character_Head_Hair_Back_Ponytail_R"
    powered = "Character_Head_Hair_Back_Ponytail_power_R"
    _write_hair_png(images / f"{base}.png")
    _write_hair_png(images / f"{powered}.png")

    skeleton = tmp_path / "hair.json"
    slots = [
        {"name": "head", "bone": "body", "attachment": "head"},
        {"name": base, "bone": "head", "attachment": base},
        {"name": powered, "bone": "head", "attachment": powered},
    ]
    attachments = {
        "head": {"head": {"x": 0, "y": 100, "width": 80, "height": 80}},
        base: {base: {"x": 0, "y": 10, "width": 80, "height": 180}},
        powered: {powered: {"x": 0, "y": 10, "width": 80, "height": 180}},
    }
    skeleton.write_text(json.dumps({
        "bones": [
            {"name": "root"},
            {"name": "body", "parent": "root", "y": 100},
            {"name": "head", "parent": "body", "y": 80},
        ],
        "slots": slots,
        "skins": [{"name": "default", "attachments": attachments}],
        "animations": {"idle": {"bones": {}}, "run": {"bones": {}}, "attack": {"bones": {}}},
    }), encoding="utf-8")

    report = enhance(str(skeleton), "biped", images_dir=str(images))
    data = json.loads(skeleton.read_text(encoding="utf-8"))

    assert report["ok"]
    assert report["silhouette_meshes"] == 2
    assert len(report["secondary_chains"][base]["bones"]) == 4
    assert report["secondary_chains"][base]["subtype"] == "ponytail"
    assert "transform_powered" in report["transformation_clips"]
    powered_slot = next(slot for slot in data["slots"] if slot["name"] == powered)
    assert powered_slot["color"] == "ffffff00"
    assert "transform_powered" in data["animations"]
    mesh = data["skins"][0]["attachments"][base][base]
    assert mesh["type"] == "mesh"
    assert mesh["hull"] >= 6
    assert len(mesh["triangles"]) == mesh["hull"] * 3
    assert "rotate" in data["animations"]["attack"]["bones"][report["secondary_chains"][base]["bones"][1]]


def test_semantic_scene_reports_unknowns_instead_of_guessing():
    scene = spine_semantics.analyze_inspection({"parts": ["Layer_43_copy_8", "upper_arm_l"]})
    assert "Layer_43_copy_8" in scene["unknown_layers"]
    arm = next(layer for layer in scene["layers"] if layer["path"] == "upper_arm_l")
    assert arm["role"] == "upper_arm"
