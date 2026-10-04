from __future__ import annotations

from pathlib import Path

import pytest

import spine_cli
from test_workflow import make_export
from workflow import run_pipeline


@pytest.mark.skipif(not spine_cli.available(), reason="licensed Spine CLI is not installed")
def test_installed_spine_can_import_and_export(tmp_path: Path):
    source = make_export(tmp_path)
    out = tmp_path / "real-spine"
    result = run_pipeline(
        str(source), str(out), "smoke", animations=["intro", "mega_win"],
        clean_mesh=True, auto_weight=True, ik=True, clipping=True,
        slot_presets=["pulse"], fx_presets=["glow_flash", "particle_explosion"],
        make_editable=True, export_project=True,
    )
    assert result["editable_project"]["ok"], result["editable_project"]
    assert (out / "smoke.spine").is_file()
    assert result["spine_export"]["ok"], result["spine_export"]
