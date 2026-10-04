"""End-to-end GPT Spine build workflow used by both CLI and MCP."""
from __future__ import annotations

import os

import spine_cli
import spine_preview
import spine_rig
import spine_quality
from spine_validate import validate_rig, write_report


def run_pipeline(source: str, out_dir: str, name: str | None = None,
                 *, rig_only: bool = False, animations: list[str] | None = None,
                 clean_mesh: bool = False, auto_weight: bool = False,
                 ik: bool = False, clipping: bool = False,
                 slot_presets: list[str] | None = None,
                 source_group: str | None = None,
                 fx_presets: list[str] | None = None,
                 make_editable: bool = True, make_preview: bool = True,
                 export_project: bool = True) -> dict:
    source = os.path.abspath(os.path.expanduser(source))
    out_dir = os.path.abspath(os.path.expanduser(out_dir))
    if not os.path.exists(source):
        raise FileNotFoundError(source)
    requested = [] if rig_only else animations
    result = spine_rig.build_rig(
        source, out_dir, name, anims=requested, clean_mesh=clean_mesh,
        auto_weight=auto_weight, ik=ik, clipping=clipping,
        slot_presets=[] if rig_only else slot_presets, source_group=source_group,
        fx_presets=[] if rig_only else fx_presets,
    )

    images_dir = os.path.join(out_dir, "images")

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
    }
    report["spine_cli"] = {"available": spine_cli.available(), "path": spine_cli.SPINE_BIN}
    report_path = write_report(report, os.path.join(out_dir, "rig_report.json"))
    result["validation"] = report
    result["files"]["report"] = report_path
    if not report["ok"]:
        raise ValueError("generated rig failed validation: " + "; ".join(report["errors"]))
    return result
