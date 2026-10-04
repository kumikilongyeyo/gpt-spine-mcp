"""Semantic validation and machine-readable reporting for generated Spine rigs."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def validate_rig(runtime_json: str, atlas: str | None = None,
                 texture: str | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    with open(runtime_json, encoding="utf-8") as handle:
        data = json.load(handle)

    bones = data.get("bones", [])
    bone_names = [bone.get("name") for bone in bones]
    if not bones or bone_names[0] != "root":
        errors.append("the first bone must be root")
    if len(bone_names) != len(set(bone_names)):
        errors.append("bone names must be unique")
    for bone in bones[1:]:
        if bone.get("parent") not in bone_names:
            errors.append(f"bone {bone.get('name')!r} has an unknown parent")

    slots = data.get("slots", [])
    slot_names = [slot.get("name") for slot in slots]
    if len(slot_names) != len(set(slot_names)):
        errors.append("slot names must be unique")
    for slot in slots:
        if slot.get("bone") not in bone_names:
            errors.append(f"slot {slot.get('name')!r} references an unknown bone")

    skins = data.get("skins", [])
    if not skins:
        errors.append("no skin was generated")
        attachments = {}
    else:
        attachments = skins[0].get("attachments", {})
    for slot_name in attachments:
        if slot_name not in slot_names:
            errors.append(f"skin references unknown slot {slot_name!r}")

    for constraint in data.get("ik", []):
        if constraint.get("target") not in bone_names:
            errors.append(f"IK {constraint.get('name')!r} has an unknown target")
        for bone in constraint.get("bones", []):
            if bone not in bone_names:
                errors.append(f"IK {constraint.get('name')!r} references unknown bone {bone!r}")

    atlas_regions: set[str] = set()
    if atlas:
        if not os.path.isfile(atlas):
            errors.append(f"atlas is missing: {atlas}")
        else:
            with open(atlas, encoding="utf-8") as handle:
                lines = [line.rstrip() for line in handle]
            atlas_regions = {line for line in lines if line and not line.startswith(" ")
                             and not line.endswith(".png") and ":" not in line}
    if texture and not os.path.isfile(texture):
        errors.append(f"texture is missing: {texture}")

    attachment_regions: set[str] = set()
    for slot_attachments in attachments.values():
        for attachment_name, entry in slot_attachments.items():
            if entry.get("type", "region") != "clipping":
                attachment_regions.add(entry.get("path", attachment_name))
    missing_regions = sorted(attachment_regions - atlas_regions) if atlas_regions else []
    if missing_regions:
        errors.append("attachments missing from atlas: " + ", ".join(missing_regions))
    if not data.get("animations"):
        warnings.append("rig-only output contains no animations")

    return {
        "ok": not errors,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "runtime_json": os.path.abspath(runtime_json),
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "bones": len(bones), "slots": len(slots),
            "attachments": sum(len(value) for value in attachments.values()),
            "animations": sorted(data.get("animations", {})),
            "ik_constraints": len(data.get("ik", [])),
        },
    }


def write_report(report: dict, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    return path
