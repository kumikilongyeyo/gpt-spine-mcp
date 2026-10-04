import spine_motion_intelligence
import spine_taste


def test_wheel_choreography_is_mechanic_anchored():
    plan = spine_motion_intelligence.build_motion_plan(
        "premium slot wheel spin with pointer, metal handle and selected prize slice",
        "prop", ["win"], None,
    )
    assert plan["version"] == 7
    clip = plan["clips"]["win"]
    taste = clip["taste_choreography"]
    assert taste["mechanic"] == "wheel"
    names = [cue["cue"] for cue in taste["fx_cues"]]
    assert "handle_reflection_travel" in names
    assert "pointer_slice_response" in names
    assert "winner_lock_impact" in names
    assert taste["audit"]["score"] >= 90
    assert clip["fx_beats"] == taste["fx_cues"]


def test_wheel_has_one_hero_peak_and_quieter_settle():
    plan = spine_motion_intelligence.build_motion_plan(
        "premium wheel celebration with pointer and selected slice",
        "prop", ["celebration"], None,
    )
    cues = plan["clips"]["celebration"]["fx_beats"]
    hero = [cue for cue in cues if cue.get("hero")]
    assert len(hero) == 1
    settle = [cue for cue in cues if cue.get("phase") == "settle"]
    assert settle
    assert settle[0]["intensity"] < hero[0]["intensity"]


def test_generic_choreography_requires_purpose_anchor_trigger():
    plan = spine_motion_intelligence.build_motion_plan(
        "punchy heavy attack with restrained FX",
        "biped", ["attack"], None,
    )
    cues = plan["clips"]["attack"]["fx_beats"]
    assert cues
    for cue in cues:
        assert cue["purpose"]
        assert cue["anchor"]
        assert cue["trigger"]
        assert cue["overlap_group"]


def test_taste_audit_rejects_unanchored_overbright_stack():
    bad = {
        "mechanic": "wheel",
        "fx_cues": [
            {"cue": "ring", "time": .2, "phase": "winner_lock", "purpose": "", "anchor": "", "trigger": "", "overlap_group": "hero", "intensity": 1.0, "hero": True},
            {"cue": "flash", "time": .2, "phase": "winner_lock", "purpose": "flash", "anchor": "screen", "trigger": "timer", "overlap_group": "hero", "intensity": 1.0, "hero": True},
            {"cue": "pulse", "time": .2, "phase": "winner_lock", "purpose": "pulse", "anchor": "wheel", "trigger": "timer", "overlap_group": "hero", "intensity": .9},
        ],
    }
    audit = spine_taste.audit_choreography(bad)
    assert audit["score"] < 80
    assert audit["issues"]


def test_calibration_template_captures_user_taste_rules():
    template = spine_taste.calibration_template()
    assert "one clear hero moment" in template["good"]
    assert "generic radial glow with no anchor" in template["bad"]
    assert "material_aware_fx" in template["review_dimensions"]
