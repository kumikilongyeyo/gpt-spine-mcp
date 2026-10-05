from pathlib import Path

from PIL import Image

import render_qa
import spine_juice


def _save_square(path: Path, size: int, alpha: int = 255):
    image = Image.new("RGBA", (size, size), (255, 255, 255, alpha))
    image.save(path)


def test_render_qa_detects_fx_swallowing_art(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    _save_square(images / "body.png", 48)
    _save_square(images / "glow_fx.png", 80, 220)
    data = {
        "skeleton": {"width": 100, "height": 100, "fps": 30},
        "bones": [{"name": "root"}, {"name": "body", "parent": "root"}],
        "slots": [
            {"name": "body", "bone": "body", "attachment": "body"},
            {"name": "glow_fx", "bone": "body", "attachment": "glow_fx", "blend": "additive"},
        ],
        "skins": [{"name": "default", "attachments": {
            "body": {"body": {"width": 48, "height": 48}},
            "glow_fx": {"glow_fx": {"width": 80, "height": 80}},
        }}],
        "animations": {"win": {"bones": {"body": {"rotate": [{"value": 0}, {"time": .3, "value": 15}]}}}},
    }
    plan = {"clips": {"win": {"beats": [{"name": "setup", "time": 0}, {"name": "peak", "time": .3}], "fx_beats": []}}}
    report = render_qa.audit(data, str(images), str(tmp_path / "qa"), plan)
    assert "glow_fx" in report["fx_slots"]
    assert any(item["kind"] in {"fx_swallowing_art", "fx_coverage_too_high"} for item in report["findings"])


def test_render_qa_fixed_bounds_prevents_false_crop(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    _save_square(images / "body.png", 30)
    data = {
        "skeleton": {"width": 60, "height": 60, "fps": 30},
        "bones": [{"name": "root"}, {"name": "body", "parent": "root"}],
        "slots": [{"name": "body", "bone": "body", "attachment": "body"}],
        "skins": [{"name": "default", "attachments": {"body": {"body": {"width": 30, "height": 30}}}}],
        "animations": {"idle": {}},
    }
    report = render_qa.audit(data, str(images), str(tmp_path / "qa"))
    assert not any(item["kind"] == "padded_bounds_clip" for item in report["findings"])


def test_character_juice_adds_counter_rotation_and_lag():
    data = {
        "bones": [
            {"name": "root"}, {"name": "pelvis", "parent": "root"},
            {"name": "torso", "parent": "pelvis"}, {"name": "head", "parent": "torso"},
            {"name": "upper_arm_l", "parent": "torso"}, {"name": "upper_arm_r", "parent": "torso"},
        ],
        "animations": {"win": {}},
    }
    plan = {"clips": {"win": {
        "beats": [
            {"name": "setup", "time": 0.0}, {"name": "anticipation", "time": .12},
            {"name": "peak", "time": .32}, {"name": "secondary_peak", "time": .50},
            {"name": "settle", "time": .86},
        ],
        "snappiness": .8, "weight": .55, "overlap": .7, "asymmetry": .16,
    }}}
    juiced, report = spine_juice.apply(data, plan)
    tracks = juiced["animations"]["win"]["bones"]
    assert "torso" in tracks and "pelvis" in tracks and "head" in tracks
    assert tracks["torso"]["rotate"][2]["value"] > 0
    assert tracks["pelvis"]["rotate"][2]["value"] < 0
    left_times = [key.get("time", 0) for key in tracks["upper_arm_l"]["rotate"]]
    right_times = [key.get("time", 0) for key in tracks["upper_arm_r"]["rotate"]]
    assert left_times != right_times
    assert report["target"] == "premium casual/slot character animation"


def test_character_juice_idle_is_subtle():
    data = {
        "bones": [{"name": "root"}, {"name": "pelvis", "parent": "root"},
                  {"name": "torso", "parent": "pelvis"}, {"name": "head", "parent": "torso"}],
        "animations": {"idle": {}},
    }
    plan = {"clips": {"idle": {"beats": [
        {"name": "rest", "time": 0.0}, {"name": "inhale", "time": 1.0}, {"name": "exhale", "time": 2.2}
    ]}}}
    juiced, _ = spine_juice.apply(data, plan)
    torso_translate = juiced["animations"]["idle"]["bones"]["torso"]["translate"]
    assert max(abs(key.get("y", 0)) for key in torso_translate) <= 2.2
