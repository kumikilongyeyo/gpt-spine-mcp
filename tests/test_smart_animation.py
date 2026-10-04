from __future__ import annotations

import json
from pathlib import Path

from spine_brain import plan_animation
from spine_smart_rig import enhance


def test_director_understands_typo_heavy_character_fx_request():
    plan = plan_animation(
        "rig this humann 2.5d clean meshe wieghts, run attack, depth shine simmer, "
        "particles glow explosion and flip image with depth"
    )
    assert plan["asset_type"] == "biped"
    assert plan["rig_profile"] == "biped_2_5d"
    assert {"idle", "run", "attack", "depth_shimmer", "depth_flip"}.issubset(plan["animations"])
    assert {"clipped_shine", "glow_flash", "particle_explosion", "bomb_explosion"}.issubset(plan["fx_presets"])
    assert plan["clean_mesh"] and plan["auto_weight"] and plan["ik"] and plan["clipping"]
    assert "land" not in plan["animations"]


def test_director_can_infer_animal_from_source_parts():
    plan = plan_animation("make idle and run with clean weights", [
        "body", "head", "front_leg_l", "front_leg_r", "hind_leg_l",
        "hind_leg_r", "paw_l", "paw_r", "tail",
    ])
    assert plan["asset_type"] == "quadruped"
    assert plan["rig_profile"] == "quadruped"
    assert plan["auto_weight"]


def _write_character_json(path: Path) -> None:
    coordinates = {
        "torso": (0, 60, 90, 110), "head": (0, 145, 65, 65), "pelvis": (0, 15, 70, 45),
        "upper_arm_l": (-55, 75, 28, 65), "lower_arm_l": (-75, 35, 24, 60), "hand_l": (-82, 0, 24, 28),
        "upper_arm_r": (55, 75, 28, 65), "lower_arm_r": (75, 35, 24, 60), "hand_r": (82, 0, 24, 28),
        "upper_leg_l": (-25, -20, 34, 80), "lower_leg_l": (-25, -80, 30, 75), "foot_l": (-25, -125, 45, 24),
        "upper_leg_r": (25, -20, 34, 80), "lower_leg_r": (25, -80, 30, 75), "foot_r": (25, -125, 45, 24),
    }
    slots, attachments = [], {}
    for name, (x, y, width, height) in coordinates.items():
        slots.append({"name": name, "bone": "head" if name == "head" else "body", "attachment": name})
        attachments[name] = {name: {"x": x, "y": y, "width": width, "height": height}}
    path.write_text(json.dumps({
        "bones": [{"name": "root"}, {"name": "body", "parent": "root", "y": 150},
                  {"name": "head", "parent": "body", "y": 80}],
        "slots": slots,
        "skins": [{"name": "default", "attachments": attachments}],
        "animations": {
            "idle": {"bones": {}}, "run": {"bones": {}}, "attack": {"bones": {}},
            "depth_flip": {"bones": {}}, "depth_shimmer": {"bones": {}},
        },
    }), encoding="utf-8")


def test_smart_rig_adds_joint_bones_adaptive_weighted_meshes_and_limb_ik(tmp_path: Path):
    skeleton = tmp_path / "hero.json"
    _write_character_json(skeleton)
    report = enhance(str(skeleton), "biped_2_5d", add_ik=True)
    data = json.loads(skeleton.read_text(encoding="utf-8"))

    assert report["ok"]
    assert report["meshes_upgraded"] >= 10
    assert set(report["ik_added"]) == {
        "smart_ik_arm_l", "smart_ik_arm_r", "smart_ik_leg_l", "smart_ik_leg_r",
    }
    assert any(bone["name"] == "smart_upper_arm_r" for bone in data["bones"])
    assert data["slots"][3]["bone"].startswith("smart_")

    mesh = data["skins"][0]["attachments"]["upper_arm_r"]["upper_arm_r"]
    assert mesh["type"] == "mesh"
    assert mesh["hull"] == 8
    assert len(mesh["uvs"]) == 18
    assert len(mesh["triangles"]) == 24
    assert len(mesh["vertices"]) > len(mesh["uvs"])
    assert 2 in mesh["vertices"]

    assert "rotate" in data["animations"]["run"]["bones"]["smart_upper_leg_l"]
    assert data["animations"]["depth_flip"]["bones"]["body"]["scale"][2]["x"] == -1
