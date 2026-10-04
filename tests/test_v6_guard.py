import json
import os

import pytest

import spine_guard
import spine_psd


def test_transaction_failure_leaves_original_unchanged(tmp_path):
    path = tmp_path / "rig.json"
    path.write_text('{"value":1}', encoding="utf-8")
    before = spine_guard.file_state(str(path))["sha256"]

    def broken(staged):
        with open(staged, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        spine_guard.run_json_stage(str(path), "broken", broken)
    assert spine_guard.file_state(str(path))["sha256"] == before
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 1}


def test_transaction_success_promotes_and_records_state(tmp_path):
    path = tmp_path / "rig.json"
    path.write_text('{"value":1}', encoding="utf-8")

    def mutate(staged):
        with open(staged, "w", encoding="utf-8") as handle:
            json.dump({"value": 2}, handle)
        return {"ok": True}

    result, tx = spine_guard.run_json_stage(str(path), "mutate", mutate)
    assert result == {"ok": True}
    assert tx["changed"] is True
    assert tx["before_sha256"] != tx["after_sha256"]
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 2}
    assert spine_guard.list_backups(str(path))


def test_restore_rejects_stale_expected_hash(tmp_path):
    path = tmp_path / "rig.json"
    path.write_text('{"value":1}', encoding="utf-8")
    backup = spine_guard.backup_file(str(path), "manual")
    old_hash = spine_guard.file_state(str(path))["sha256"]
    path.write_text('{"value":3}', encoding="utf-8")
    with pytest.raises(ValueError):
        spine_guard.restore_backup(str(path), backup["id"], expected_sha256=old_hash)


def test_safe_part_names_are_stable_and_collision_safe():
    used = set()
    assert spine_psd.safe_part_name("FX/Glow Soft", used) == "FX_Glow_Soft"
    assert spine_psd.safe_part_name("FX/Glow Soft", used) == "FX_Glow_Soft_2"
