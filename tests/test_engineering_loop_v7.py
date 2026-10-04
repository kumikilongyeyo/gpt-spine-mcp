import json

import spine_engineering_loop
import spine_motion
import spine_v4


def test_spine_motion_uses_real_control_points():
    keys = spine_motion.timeline("rotate", [
        (0.0, 0.0, "out"),
        (0.2, 30.0, "out"),
        (0.4, 0.0),
    ])
    assert isinstance(keys[0]["curve"], list)
    assert len(keys[0]["curve"]) == 4
    assert keys[0]["curve"] != "bezier"


def test_v4_setters_snap_to_30fps_and_use_real_bezier():
    animation = {}
    spine_v4._set_rotate(animation, "torso", [
        (0.0, 0.0),
        (0.107, 20.0),
        (0.219, 0.0),
    ], "out")
    track = animation["bones"]["torso"]["rotate"]
    assert all(abs(float(key.get("time", 0)) * 30 - round(float(key.get("time", 0)) * 30)) < 1e-3
               for key in track)
    assert isinstance(track[0].get("curve"), list)
    assert all(key.get("curve") != "bezier" for key in track)


def test_engineering_diagnosis_turns_visual_problems_into_actions():
    visual = {
        "issues": [
            {"kind": "visually_static", "clip": "win", "severity": "medium"},
            {"kind": "visual_pop_spike", "clip": "attack", "severity": "medium"},
        ]
    }
    structural = {
        "clips": {
            "attack": {"issues": ["bilateral timing is perfectly mirrored; add asymmetry"]}
        }
    }
    report = spine_engineering_loop.diagnose_visual_report(visual, structural)
    causes = {item["cause"] for item in report["actions"]}
    assert "weak_pose_contrast" in causes
    assert "discontinuity" in causes
    assert "mechanical_symmetry" in causes
    assert any(item["id"] == "real_bezier" for item in report["known_traps"])
