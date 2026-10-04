import copy

import spine_critic
import spine_motion


def _base_data():
    return {
        "skeleton": {"fps": 30},
        "bones": [
            {"name": "root"},
            {"name": "torso", "parent": "root", "length": 80},
            {"name": "hand", "parent": "torso", "x": 70, "length": 45},
            {"name": "hair_root", "parent": "torso", "length": 30},
            {"name": "hair_mid", "parent": "hair_root", "x": 28, "length": 28},
            {"name": "hair_tip", "parent": "hair_mid", "x": 26, "length": 24},
        ],
        "slots": [],
        "skins": [],
        "animations": {},
    }


def _plan():
    return {
        "clips": {
            "attack": {
                "beats": [
                    {"name": "setup", "time": 0.0},
                    {"name": "anticipation", "time": 0.2},
                    {"name": "contact", "time": 0.3333},
                    {"name": "overshoot", "time": 0.4667},
                    {"name": "settle", "time": 0.8},
                ]
            }
        }
    }


def _good_animation():
    motion = spine_motion.Motion()
    motion.bone("torso", "rotate", [
        (0.0, 0.0, "in"),
        (0.2, -12.0, "in"),
        (0.3333, 60.0, "out"),
        (0.4667, 66.0, "out"),
        (0.8, 0.0),
    ])
    motion.bone("hand", "rotate", [
        (0.0, 0.0, "in"),
        (0.2, -18.0, "in"),
        (0.3333, 82.0, "out"),
        (0.4667, 88.0, "out"),
        (0.8, 0.0),
    ])
    motion.bone("hair_root", "rotate", [
        (0.0, 0.0, "out"), (0.3667, -6.0, "out"), (0.5333, 8.0, "out"), (0.8, 0.0)
    ])
    motion.bone("hair_mid", "rotate", [
        (0.0, 0.0, "out"), (0.4333, -7.0, "out"), (0.6, 10.0, "out"), (0.8, 0.0)
    ])
    motion.bone("hair_tip", "rotate", [
        (0.0, 0.0, "out"), (0.5, -8.0, "out"), (0.6667, 13.0, "out"), (0.8, 0.0)
    ])
    return motion.data


def test_sampler_uses_real_bezier_control_points():
    track = spine_motion.timeline("rotate", [
        (0.0, 0.0, "out"),
        (1.0, 100.0),
    ])
    midpoint = spine_critic.sample_timeline(track, "rotate", 0.5)
    assert midpoint > 50.0


def test_good_attack_passes_core_mutation_targets():
    data = _base_data()
    data["animations"]["attack"] = _good_animation()
    report = spine_critic.audit(
        data, _plan(),
        {"hair": {"bones": ["hair_root", "hair_mid", "hair_tip"]}},
    )
    bad = {finding["check"] for finding in report["findings"]}
    assert "drag_order" not in bad
    assert "overshoot_ratio" not in bad


def test_mutation_constant_velocity_is_caught():
    data = _base_data()
    attack = _good_animation()
    attack["bones"]["torso"]["rotate"] = [
        {"value": 0.0},
        {"time": 0.8, "value": 80.0},
    ]
    data["animations"]["attack"] = attack
    report = spine_critic.audit(data, _plan())
    assert any(item["check"] == "spacing_acceleration" and item["metric"] == "constant_velocity_run_frames"
               for item in report["findings"])


def test_mutation_simultaneous_chain_peaks_is_caught():
    data = _base_data()
    attack = _good_animation()
    same = copy.deepcopy(attack["bones"]["hair_root"]["rotate"])
    attack["bones"]["hair_mid"]["rotate"] = copy.deepcopy(same)
    attack["bones"]["hair_tip"]["rotate"] = copy.deepcopy(same)
    data["animations"]["attack"] = attack
    report = spine_critic.audit(
        data, _plan(),
        {"hair": {"bones": ["hair_root", "hair_mid", "hair_tip"]}},
    )
    assert any(item["check"] == "drag_order" and item["metric"] == "peak_delay_frames"
               for item in report["findings"])


def test_mutation_40_percent_overshoot_is_caught():
    data = _base_data()
    attack = _good_animation()
    attack["bones"]["torso"]["rotate"] = spine_motion.timeline("rotate", [
        (0.0, 0.0, "in"),
        (0.2, -12.0, "in"),
        (0.3333, 60.0, "out"),
        (0.4667, 84.0, "out"),
        (0.8, 0.0),
    ])
    data["animations"]["attack"] = attack
    report = spine_critic.audit(data, _plan())
    assert any(item["check"] == "overshoot_ratio" for item in report["findings"])


def test_mutation_missing_anticipation_is_caught():
    data = _base_data()
    attack = _good_animation()
    attack["bones"]["torso"]["rotate"] = spine_motion.timeline("rotate", [
        (0.0, 0.0, "in"),
        (0.2, 6.0, "in"),
        (0.3333, 60.0, "out"),
        (0.4667, 66.0, "out"),
        (0.8, 0.0),
    ])
    data["animations"]["attack"] = attack
    report = spine_critic.audit(data, _plan())
    assert any(item["check"] == "anticipation_vs_impact" and item["metric"] == "anticipation_ratio"
               for item in report["findings"])
