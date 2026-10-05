import spine_ad_gauntlet
import spine_performance
import spine_prompt_intelligence


def test_terse_prompt_expands_industry_slot_intent():
    intent = spine_prompt_intelligence.interpret(
        "big cocky win premium slot industry grade, make it alive"
    )
    assert intent["quality_target"] == "industry_grade"
    assert "premium_slot" in intent["styles"]
    assert "win" in intent["states"]
    assert intent["energy"] >= .9
    assert intent["fx"]["enabled"] is True
    assert intent["acting"]["secondary_delay_frames"] == [2, 4]
    assert "clear silhouette" in intent["expanded_request"]


def test_short_alive_prompt_does_not_invent_attack_or_death():
    intent = spine_prompt_intelligence.interpret("animate this, polished and alive")
    assert "idle" in intent["states"]
    assert "attack" not in intent["states"]
    assert "death" not in intent["states"]
    assert intent["implicit_state"] is False or intent["states"] == ["idle"]


def _performance_fixture():
    data = {
        "bones": [
            {"name": "root"},
            {"name": "smart_brow_l", "parent": "root"},
            {"name": "smart_brow_r", "parent": "root"},
            {"name": "smart_pupil_l", "parent": "root"},
            {"name": "smart_pupil_r", "parent": "root"},
            {"name": "smart_jaw", "parent": "root"},
            {"name": "smart_prop_coin", "parent": "root"},
        ],
        "slots": [
            {"name": "eye_l", "bone": "root"},
            {"name": "eye_r", "bone": "root"},
            {"name": "lid_l", "bone": "root"},
            {"name": "lid_r", "bone": "root"},
        ],
        "animations": {"win": {"bones": {}, "slots": {}}},
    }
    motion_plan = {
        "clips": {
            "win": {
                "beats": [
                    {"name": "setup", "time": 0.0},
                    {"name": "anticipation", "time": .12},
                    {"name": "peak", "time": .32},
                    {"name": "secondary_peak", "time": .50},
                    {"name": "settle", "time": .92},
                ],
                "snappiness": .82,
                "weight": .55,
                "overlap": .65,
                "asymmetry": .16,
            }
        }
    }
    smart_report = {
        "facial_controls": {
            "eyes": ["eye_l", "eye_r"],
            "pupils": ["pupil_l", "pupil_r"],
            "brows": ["brow_l", "brow_r"],
            "mouth": [],
            "jaw": ["jaw"],
            "eyelids": ["lid_l", "lid_r"],
            "bones": {
                "brow_l": "smart_brow_l", "brow_r": "smart_brow_r",
                "pupil_l": "smart_pupil_l", "pupil_r": "smart_pupil_r",
                "jaw": "smart_jaw",
            },
        }
    }
    intent = {
        "emotion": "confident", "energy": .9, "styles": ["premium_slot"],
        "quality_target": "industry_grade",
        "acting": {"pose_exaggeration": .9},
        "face": {"eye_lead_frames": 2},
    }
    return data, motion_plan, smart_report, intent


def test_performance_pass_adds_face_thought_blink_and_second_win_read():
    data, motion_plan, smart_report, intent = _performance_fixture()
    acted, report = spine_performance.apply(data, motion_plan, smart_report, intent)
    clip = acted["animations"]["win"]

    assert clip["bones"]["smart_pupil_l"]["translate"]
    assert clip["bones"]["smart_brow_r"]["rotate"]
    assert clip["bones"]["smart_jaw"]["rotate"]
    assert clip["slots"]["lid_l"]["rgba"]
    assert clip["slots"]["eye_l"]["rgba"]
    assert report["clips"][0]["escalation"] is not None
    assert report["clips"][0]["applied"] is True


def _good_engineering():
    return {
        "visual": {"ok": True, "issues": []},
        "structural": {"score": 96, "clips": {}},
        "critic": {"ok": True, "score": 96, "findings": []},
        "render_qa": {
            "ok": True, "score": 96, "findings": [],
            "clips": {"win": [{"time": .3}]}, "render_dir": "/tmp/render",
        },
        "diagnosis": {"ok": True, "actions": []},
        "ready": True,
    }


def test_ad_gauntlet_requires_visual_quality_not_only_validity(monkeypatch):
    bad = _good_engineering()
    bad["render_qa"]["findings"] = [{
        "kind": "fx_swallowing_art", "clip": "win", "time": .3,
        "severity": "high", "metric": "art_region_ssim", "value": .51,
        "threshold": {"min": .8},
        "suggested_patch": {"operation": "reduce_fx_alpha_or_scale"},
    }]
    bad["diagnosis"]["actions"] = [{
        "clip": "win", "priority": 92, "cause": "fx_swallowing_art",
        "change": {"operation": "reduce_fx_alpha_or_scale"},
    }]
    monkeypatch.setattr(spine_ad_gauntlet.spine_engineering_loop, "inspect_build", lambda *a, **k: bad)

    result = spine_ad_gauntlet.review(
        "runtime.json", "images", "out",
        motion_plan={"clips": {"win": {}}},
        performance_report={"available_controls": {}, "clips": []},
        prompt_intent={"quality_target": "industry_grade", "capabilities": {"face": False}},
    )
    assert result["presentation_ready"] is False
    assert result["status"] == "BLOCKED_WIP"
    assert any(item["cause"] == "fx_swallowing_art" for item in result["blockers"])
    assert result["revision_queue"][0]["cause"] == "fx_swallowing_art"


def test_ad_gauntlet_can_approve_only_a_high_scoring_rendered_build(monkeypatch):
    monkeypatch.setattr(
        spine_ad_gauntlet.spine_engineering_loop, "inspect_build",
        lambda *a, **k: _good_engineering(),
    )
    performance = {
        "available_controls": {"eyes": True, "pupils": True, "brows": True, "mouth": False, "jaw": True, "eyelids": True},
        "limitations": ["mouth"],
        "clips": [{"clip": "win", "applied": True, "escalation": {"first_read": .3, "second_read": .5}}],
    }
    result = spine_ad_gauntlet.review(
        "runtime.json", "images", "out",
        motion_plan={"clips": {"win": {}}},
        performance_report=performance,
        prompt_intent={"quality_target": "industry_grade", "capabilities": {"face": True}},
    )
    assert result["overall_score"] >= 88
    assert result["presentation_ready"] is True
    assert result["status"] == "PRESENTATION_READY"
