from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

import spine_cli
from workflow import run_pipeline


def make_export(root: Path) -> Path:
    source = root / "source"
    images = source / "images"
    images.mkdir(parents=True)
    parts = {
        "body": (0, 45, 80, 90),
        "head": (0, 115, 58, 58),
        "head_win": (0, 115, 58, 58),
        "head_blink": (0, 115, 58, 58),
        "fx_glow": (0, 45, 44, 44),
        "coin": (25, 15, 24, 24),
    }
    for name, (_, _, width, height) in parts.items():
        Image.new("RGBA", (width, height), (220, 90, 120, 255)).save(images / f"{name}.png")
    layout = {
        "slots": [{"name": name} for name in parts],
        "skins": {"default": {
            name: {name: {"x": x, "y": y, "width": width, "height": height}}
            for name, (x, y, width, height) in parts.items()
        }},
    }
    (source / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    return source


def test_rig_only_has_no_animations(tmp_path: Path):
    source = make_export(tmp_path)
    result = run_pipeline(str(source), str(tmp_path / "out"), "hero",
                          rig_only=True, make_editable=False)
    data = json.loads(Path(result["files"]["json"]).read_text())
    assert data["animations"] == {}
    assert result["validation"]["ok"]
    assert "rig-only" in result["validation"]["warnings"][0]


def test_full_workflow_emits_mesh_constraints_states_and_report(tmp_path: Path):
    source = make_export(tmp_path)
    out = tmp_path / "out"
    states = ["intro", "steering", "wave", "mega_win", "celebration"]
    result = run_pipeline(
        str(source), str(out), "hero", animations=states,
        clean_mesh=True, auto_weight=True, ik=True, clipping=True,
        slot_presets=["pulse", "flash", "flicker"], make_editable=False,
    )
    data = json.loads((out / "hero.json").read_text())
    assert set(states).issubset(data["animations"])
    assert {"slot_pulse", "slot_flash", "slot_flicker"}.issubset(data["animations"])
    assert data["ik"][0]["name"] == "head_ik"
    assert data["slots"][0]["name"] == "__gpt_spine_clip"
    head_mesh = data["skins"][0]["attachments"]["head"]["head"]
    assert head_mesh["type"] == "mesh"
    assert len(head_mesh["triangles"]) == 6
    assert len(head_mesh["vertices"]) == 20
    assert (out / "rig_report.json").is_file()
    assert (out / "preview" / "montage.png").is_file()
    assert result["validation"]["ok"]


def test_spine_bin_environment_override(monkeypatch, tmp_path: Path):
    executable = tmp_path / "Spine"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("SPINE_BIN", str(executable))
    assert spine_cli.detect_spine_bin() == str(executable)


def test_fx_packs_emit_source_coins_curves_and_portable_images(tmp_path: Path):
    source = make_export(tmp_path)
    out = tmp_path / "fx"
    presets = ["coin_splash", "glow_flash", "particle_explosion",
               "bomb_explosion", "fire", "splash", "clipped_shine"]
    result = run_pipeline(str(source), str(out), "fxhero", animations=["idle"],
                          fx_presets=presets, make_editable=False, make_preview=False)
    data = json.loads((out / "fxhero.json").read_text())
    assert set(result["fx"]["presets"]) == set(presets)
    assert {"fx_coin_splash", "fx_glow_flash", "fx_particle_explosion",
            "fx_bomb_explosion", "fx_fire", "fx_splash"}.issubset(data["animations"])
    assert "fx_clipped_shine" in data["animations"]
    assert data["skins"][0]["attachments"]["__fx_coin_0"]["__fx_coin_0"]["path"] == "coin"
    spark = data["animations"]["fx_particle_explosion"]["bones"]["__fx_spark_bone_0"]
    assert any("curve" in key for key in spark["translate"])
    assert (out / "images" / "body.png").is_file()
    assert (out / "images" / "__fx_flash.png").is_file()
    assert all(slot.get("color", "").endswith("00") for slot in data["slots"]
               if slot["name"].startswith("__fx_") and slot["name"] != "__fx_shine_clip")


def test_windows_spine_cli_candidates_prefer_console_executable():
    candidates = spine_cli.spine_candidates("win32", {
        "ProgramFiles": r"C:\Program Files",
        "ProgramFiles(x86)": r"C:\Program Files (x86)",
        "LOCALAPPDATA": r"C:\Users\artist\AppData\Local",
    })
    assert candidates[0] == r"C:\Program Files\Spine\Spine.com"
    assert r"C:\Program Files\Spine\Spine.exe" in candidates
    assert r"C:\Users\artist\AppData\Local\Esoteric Software\Spine\Spine.com" in candidates
