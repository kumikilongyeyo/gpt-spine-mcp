import json

from spine_spec import compile_motion_spec


def test_numeric_spec_compiles_curves_and_checks_handoff(tmp_path):
    source = tmp_path / "base.json"
    source.write_text(json.dumps({
        "skeleton": {"spine": "4.2.00", "fps": 30},
        "bones": [{"name": "root"}, {"name": "hero", "parent": "root"}],
        "slots": [{"name": "flash", "bone": "hero"}],
        "skins": [{"name": "default", "attachments": {}}], "animations": {},
    }))
    output = tmp_path / "authored.json"
    spec = {"fps": 30, "handoffs": [{"from": "activated_in", "to": "activated_loop"}],
            "clips": {
                "activated_in": {"bones": {"hero": {"scale": [
                    {"at": 0, "value": [.1, .1], "ease": "outback"},
                    {"at": 1, "value": [1, 1]}]}},
                    "slots": {"flash": {"alpha": [
                        {"at": 0, "value": 0, "ease": "expo"},
                        {"at": .0666667, "value": 1, "ease": "out"},
                        {"at": 1, "value": 0}]}}},
                "activated_loop": {"bones": {"hero": {"scale": [
                    {"at": 0, "value": [1, 1], "ease": "sine"},
                    {"at": .5, "value": [1.05, .98], "ease": "sine"},
                    {"at": 1, "value": [1, 1]}]}}},
            }}
    report = compile_motion_spec(str(source), str(output), spec)
    assert report["ok"], report
    data = json.loads(output.read_text())
    first = data["animations"]["activated_in"]["bones"]["hero"]["scale"][0]
    assert len(first["curve"]) == 8
    assert "alpha" in data["animations"]["activated_in"]["slots"]["flash"]
