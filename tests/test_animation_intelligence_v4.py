import json

import spine_anatomy
import spine_motion_intelligence
import spine_v4


def test_visual_anatomy_uses_symmetry_for_unnamed_arms():
    items = [
        {"attachment": "torso", "role": "torso", "side": "", "bounds": [0, 0, 120, 170], "confidence": .95, "evidence": [], "warnings": []},
        {"attachment": "Layer_12", "role": "unknown", "side": "", "bounds": [-105, 35, 42, 95], "confidence": .1, "evidence": [], "warnings": ["unrecognized layer role"]},
        {"attachment": "Layer_13", "role": "unknown", "side": "", "bounds": [105, 35, 42, 95], "confidence": .1, "evidence": [], "warnings": ["unrecognized layer role"]},
    ]
    report = spine_anatomy.enrich_semantics(items)
    inferred = {item["attachment"]: item for item in report["layers"]}
    assert inferred["Layer_12"]["role"] == "upper_arm"
    assert inferred["Layer_13"]["role"] == "upper_arm"
    assert {inferred["Layer_12"]["side"], inferred["Layer_13"]["side"]} == {"l", "r"}
    assert report["bilateral_pairs"] == 1


def test_visual_anatomy_keeps_ambiguous_garbage_unknown():
    items = [
        {"attachment": "torso", "role": "torso", "side": "", "bounds": [0, 0, 120, 170], "confidence": .95, "evidence": [], "warnings": []},
        {"attachment": "Layer_copy_9", "role": "unknown", "side": "", "bounds": [340, 310, 18, 18], "confidence": .1, "evidence": [], "warnings": []},
    ]
    report = spine_anatomy.enrich_semantics(items)
    layer = next(item for item in report["layers"] if item["attachment"] == "Layer_copy_9")
    assert layer["role"] == "unknown"


def test_motion_plan_has_pose_beats_energy_and_fx():
    plan = spine_motion_intelligence.build_motion_plan(
        "heavy brute attack, make it punchy and premium slot polished with sparks",
        "biped", ["attack", "win"], {"role_counts": {"torso": 1, "upper_arm": 2}},
    )
    assert plan["version"] == 4
    assert plan["archetype"] == "brute"
    assert "heavy" in plan["style"]["presets"]
    attack = plan["clips"]["attack"]
    names = [beat["name"] for beat in attack["beats"]]
    assert names == ["setup", "anticipation", "contact", "overshoot", "settle"]
    assert attack["beats"][2]["time"] - attack["beats"][1]["time"] < attack["beats"][-1]["time"] - attack["beats"][3]["time"]
    assert attack["energy_hierarchy"]["primary"]
    assert any(cue["cue"] == "impact_flash" for cue in attack["fx_beats"])


def test_v4_applies_asymmetric_pose_beats_and_quality_audit(tmp_path):
    data = {
        "bones": [
            {"name": "root"},
            {"name": "body", "parent": "root"},
            {"name": "smart_torso", "parent": "body"},
            {"name": "smart_pelvis", "parent": "smart_torso"},
            {"name": "smart_head", "parent": "smart_torso"},
            {"name": "smart_upper_arm_l", "parent": "smart_torso"},
            {"name": "smart_lower_arm_l", "parent": "smart_upper_arm_l"},
            {"name": "smart_upper_arm_r", "parent": "smart_torso"},
            {"name": "smart_lower_arm_r", "parent": "smart_upper_arm_r"},
        ],
        "slots": [
            {"name": "torso", "bone": "smart_torso"},
            {"name": "pelvis", "bone": "smart_pelvis"},
            {"name": "head", "bone": "smart_head"},
            {"name": "upper_arm_l", "bone": "smart_upper_arm_l"},
            {"name": "lower_arm_l", "bone": "smart_lower_arm_l"},
            {"name": "upper_arm_r", "bone": "smart_upper_arm_r"},
            {"name": "lower_arm_r", "bone": "smart_lower_arm_r"},
        ],
        "skins": {"default": {
            name: {name: {"x": 0, "y": 0, "width": 40, "height": 80}}
            for name in ("torso", "pelvis", "head", "upper_arm_l", "lower_arm_l", "upper_arm_r", "lower_arm_r")
        }},
        "animations": {"attack": {"bones": {}}},
    }
    path = tmp_path / "rig.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    plan = spine_motion_intelligence.build_motion_plan("heavy punchy premium attack", "biped", ["attack"])
    report = spine_v4.apply(str(path), motion_plan=plan)
    output = json.loads(path.read_text(encoding="utf-8"))
    attack = output["animations"]["attack"]["bones"]
    assert len(attack["smart_torso"]["rotate"]) >= 5
    assert attack["smart_upper_arm_l"]["rotate"] != attack["smart_upper_arm_r"]["rotate"]
    assert report["quality_audit"]["score"] >= 80
