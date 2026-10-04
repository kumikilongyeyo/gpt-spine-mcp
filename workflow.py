"""End-to-end GPT Spine build workflow used by both CLI and MCP."""
from __future__ import annotations

import os

import spine_cli
import spine_preview
import spine_rig
import spine_quality
import spine_smart_rig
import spine_v4
from spine_validate import validate_rig, write_report


def run_pipeline(source: str, out_dir: str, name: str | None = None,
                 *, rig_only: bool = False, animations: list[str] | None = None,
                 clean_mesh: bool = False, auto_weight: bool = False,
                 ik: bool = False, clipping: bool = False,
                 slot_presets: list[str] | None = None,
                 source_group: str | None = None,
                 fx_presets: list[str] | None = None,
                 make_editable: bool = True, make_preview: bool = True,
                 export_project: bool = True,
                 rig_profile: str = "simple", mesh_quality: str = "adaptive",
                 max_weight_influences: int = 2,
                 naming_profile: str = "",
                 motion_plan: dict | None = None) -> dict:
    source = os.path.abspath(os.path.expanduser(source))
    out_dir = os.path.abspath(os.path.expanduser(out_dir))
    if not os.path.exists(source):
        raise FileNotFoundError(source)
    requested = [] if rig_only else animations
    smart_enabled = not rig_only and rig_profile not in {"", "simple", "legacy", None}
    result = spine_rig.build_rig(
        source, out_dir, name, anims=requested,
        clean_mesh=(clean_mesh if not smart_enabled else False),
        auto_weight=(auto_weight if not smart_enabled else False),
        ik=(ik if not smart_enabled else False), clipping=clipping,
        slot_presets=[] if rig_only else slot_presets, source_group=source_group,
        fx_presets=[] if rig_only else fx_presets,
    )
    images_dir = os.path.join(out_dir, "images")

    # V3 remains the proven semantic-rig foundation.
    if smart_enabled:
        smart_report = spine_smart_rig.enhance(
            result["files"]["json"], profile=rig_profile,
            mesh_quality=mesh_quality,
            max_influences=max(1, min(2, int(max_weight_influences))),
            add_ik=ik,
            images_dir=images_dir,
            naming_profile=naming_profile,
            naming_source=source,
        )
        result["smart_rig"] = smart_report
        if smart_report.get("bones_added"):
            result["bones"] = result.get("bones", []) + smart_report["bones_added"]
        result["mesh"] = result.get("mesh", False) or smart_report.get("meshes_upgraded", 0) > 0
        result["weighted"] = result.get("weighted", False) or smart_report.get("weighted_vertices", False)
        for clip in smart_report.get("transformation_clips", []):
            if clip not in result.setdefault("anims", []):
                result["anims"].append(clip)
        if smart_report.get("facial_controls", {}).get("eyelids") and "blink" not in result.setdefault("anims", []):
            result["anims"].append("blink")

        # V4 is deliberately a post-pass: visual anatomy rescues weak naming,
        # then pose-beat timing, overlap, asymmetry, contacts and FX are polished.
        v4_report = spine_v4.apply(
            result["files"]["json"], images_dir=images_dir,
            naming_profile=naming_profile, naming_source=source,
            motion_plan=motion_plan, smart_report=smart_report,
        )
        result["animation_intelligence"] = v4_report
        if v4_report.get("visual_bones_added"):
            result["bones"] = result.get("bones", []) + [item["bone"] for item in v4_report["visual_bones_added"]]

    if make_editable:
        if spine_cli.available():
            project = os.path.join(out_dir, f"{result['name']}.spine")
            created = spine_cli.make_project(result["files"]["json"], project)
            result["editable_project"] = created
            result["asset_portability"] = spine_quality.audit_project_assets(
                result["files"]["json"], project)
            if not result["asset_portability"]["ok"]:
                raise ValueError("editable project is not portable: " +
                                 "; ".join(result["asset_portability"]["errors"]))
            if export_project and created.get("ok"):
                exported_dir = os.path.join(out_dir, "export")
                result["spine_export"] = spine_cli.export_project(project, exported_dir)
        else:
            result["editable_project"] = {
                "ok": False, "skipped": True,
                "reason": "Spine CLI not found; runtime output is still complete",
            }

    preview_dir = os.path.join(out_dir, "preview")
    if make_preview and os.path.isdir(images_dir) and result["anims"]:
        os.makedirs(preview_dir, exist_ok=True)
        result["preview"] = spine_preview.montage(
            result["files"]["json"], images_dir,
            os.path.join(preview_dir, "montage.png"), 240,
        )

    report = validate_rig(result["files"]["json"], result["files"]["atlas"],
                          result["files"]["png"])
    report["options"] = {
        "rig_only": rig_only, "clean_mesh": clean_mesh, "auto_weight": auto_weight,
        "ik": ik, "clipping": clipping, "slot_presets": slot_presets or [],
        "source_group": source_group,
        "fx_presets": fx_presets or [],
        "rig_profile": rig_profile, "mesh_quality": mesh_quality,
        "max_weight_influences": max_weight_influences,
        "naming_profile": naming_profile,
        "animation_intelligence_v4": bool(motion_plan),
    }
    if result.get("smart_rig"):
        report["smart_rig"] = result["smart_rig"]
    if result.get("animation_intelligence"):
        report["animation_intelligence"] = result["animation_intelligence"]
    report["spine_cli"] = {"available": spine_cli.available(), "path": spine_cli.SPINE_BIN}
    report_path = write_report(report, os.path.join(out_dir, "rig_report.json"))
    result["validation"] = report
    result["files"]["report"] = report_path
    if not report["ok"]:
        raise ValueError("generated rig failed validation: " + "; ".join(report["errors"]))
    return result
