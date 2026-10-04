from __future__ import annotations

import json
from pathlib import Path

from spine_validate import validate_rig


def test_validation_catches_unknown_bone(tmp_path: Path):
    runtime = tmp_path / "bad.json"
    runtime.write_text(json.dumps({
        "bones": [{"name": "root"}],
        "slots": [{"name": "body", "bone": "missing"}],
        "skins": [{"name": "default", "attachments": {}}],
        "animations": {},
    }))
    report = validate_rig(str(runtime))
    assert not report["ok"]
    assert "unknown bone" in report["errors"][0]
