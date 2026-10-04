"""Transactional file-state guard for GPT Spine V6.

The animation passes intentionally operate on plain Spine JSON.  This module keeps
that simplicity while making mutations safer: every stage runs against a temporary
copy, the result is JSON-validated, and only then is it atomically promoted.  Small,
bounded backups plus SHA-256 state pins make rollback and stale-state detection
possible without adding a database or background watcher.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")
_DEFAULT_BACKUPS = 8


def file_state(path: str) -> dict:
    """Return a compact content-addressed state record for a file."""
    target = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(target):
        return {"path": target, "exists": False, "sha256": "", "size": 0, "mtime_ns": 0}
    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = os.stat(target)
    return {
        "path": target,
        "exists": True,
        "sha256": digest.hexdigest(),
        "short_sha": digest.hexdigest()[:12],
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _backup_dir(path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(path)), ".gpt-spine", "backups")


def _safe_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-.") or "snapshot"


def list_backups(path: str) -> list[dict]:
    """List bounded backups belonging to ``path``, newest first."""
    target = os.path.abspath(os.path.expanduser(path))
    directory = _backup_dir(target)
    prefix = os.path.basename(target) + "."
    if not os.path.isdir(directory):
        return []
    output = []
    for name in os.listdir(directory):
        if not (name.startswith(prefix) and name.endswith(".bak")):
            continue
        full = os.path.join(directory, name)
        state = file_state(full)
        output.append({
            "id": name,
            "path": full,
            "sha256": state["sha256"],
            "short_sha": state.get("short_sha", ""),
            "size": state["size"],
            "mtime_ns": state["mtime_ns"],
        })
    output.sort(key=lambda item: item["mtime_ns"], reverse=True)
    return output


def backup_file(path: str, label: str = "snapshot", max_backups: int = _DEFAULT_BACKUPS) -> dict:
    """Create a timestamped content-addressed backup and prune old snapshots."""
    target = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(target):
        raise FileNotFoundError(target)
    before = file_state(target)
    directory = _backup_dir(target)
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    name = f"{os.path.basename(target)}.{stamp}.{_safe_label(label)}.{before['short_sha']}.bak"
    destination = os.path.join(directory, name)
    shutil.copy2(target, destination)

    keep = max(1, int(max_backups))
    backups = list_backups(target)
    for old in backups[keep:]:
        try:
            os.remove(old["path"])
        except OSError:
            pass
    return {"id": name, "path": destination, "source_sha256": before["sha256"]}


def _validate_json(path: str) -> None:
    with open(path, encoding="utf-8") as handle:
        json.load(handle)


def atomic_write_json(path: str, data: object, *, compact: bool = True) -> dict:
    """Write JSON beside the target then atomically replace it."""
    target = os.path.abspath(os.path.expanduser(path))
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{os.path.basename(target)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            if compact:
                json.dump(data, handle, separators=(",", ":"), ensure_ascii=False)
            else:
                json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        _validate_json(temporary)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass
    return file_state(target)


def run_json_stage(path: str, label: str, action: Callable[[str], T], *,
                   keep_backup: bool = True, max_backups: int = _DEFAULT_BACKUPS) -> tuple[T, dict]:
    """Run a mutating pipeline pass transactionally.

    ``action`` receives a temporary JSON path.  The canonical file is untouched
    until the action succeeds *and* the result parses as JSON.  A crash or Python
    exception therefore leaves the last valid stage in place.
    """
    target = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(target):
        raise FileNotFoundError(target)
    before = file_state(target)
    backup = backup_file(target, label=f"before-{label}", max_backups=max_backups) if keep_backup else None
    directory = os.path.dirname(target) or "."
    fd, staged = tempfile.mkstemp(prefix=f".{os.path.basename(target)}.{_safe_label(label)}.",
                                  suffix=".stage.json", dir=directory)
    os.close(fd)
    shutil.copy2(target, staged)
    try:
        result = action(staged)
        _validate_json(staged)
        os.replace(staged, target)
        after = file_state(target)
        return result, {
            "stage": label,
            "before_sha256": before["sha256"],
            "before_short_sha": before.get("short_sha", ""),
            "after_sha256": after["sha256"],
            "after_short_sha": after.get("short_sha", ""),
            "changed": before["sha256"] != after["sha256"],
            "backup_id": backup["id"] if backup else "",
        }
    finally:
        if os.path.exists(staged):
            try:
                os.remove(staged)
            except OSError:
                pass


def restore_backup(path: str, backup_id: str, expected_sha256: str = "") -> dict:
    """Restore one owned backup, refusing stale-state replacement when requested."""
    target = os.path.abspath(os.path.expanduser(path))
    current = file_state(target)
    if expected_sha256 and current.get("sha256") != expected_sha256:
        raise ValueError(
            "state changed since it was inspected; re-read project_guard status before restoring"
        )
    safe_id = os.path.basename(backup_id)
    if safe_id != backup_id:
        raise ValueError("backup_id must be a backup filename, not a path")
    known = {item["id"]: item for item in list_backups(target)}
    if safe_id not in known:
        raise FileNotFoundError(f"backup {safe_id!r} does not belong to {target}")

    pre_restore = backup_file(target, label="pre-restore") if current.get("exists") else None
    source = known[safe_id]["path"]
    _validate_json(source)
    directory = os.path.dirname(target) or "."
    fd, staged = tempfile.mkstemp(prefix=f".{os.path.basename(target)}.restore.", suffix=".json", dir=directory)
    os.close(fd)
    try:
        shutil.copy2(source, staged)
        _validate_json(staged)
        os.replace(staged, target)
    finally:
        if os.path.exists(staged):
            try:
                os.remove(staged)
            except OSError:
                pass
    return {
        "ok": True,
        "restored": safe_id,
        "pre_restore_backup": pre_restore["id"] if pre_restore else "",
        "state": file_state(target),
    }


def project_guard(path: str, action: str = "status", backup_id: str = "",
                  expected_sha256: str = "") -> dict:
    """Small MCP-friendly facade for status, backup listing and restore."""
    action = (action or "status").strip().casefold()
    if action in {"status", "list", "list_backups"}:
        return {"ok": True, "state": file_state(path), "backups": list_backups(path)}
    if action in {"restore", "rollback"}:
        if not backup_id:
            raise ValueError("backup_id is required for restore")
        return restore_backup(path, backup_id, expected_sha256)
    if action == "backup":
        return {"ok": True, "backup": backup_file(path, "manual"), "state": file_state(path)}
    raise ValueError("action must be status, backup, or restore")
