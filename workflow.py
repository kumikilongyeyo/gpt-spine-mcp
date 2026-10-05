"""End-to-end GPT Spine build workflow used by both CLI and MCP."""
from __future__ import annotations

import json
import os

import spine_ad_gauntlet
import spine_cli
import spine_guard
import spine_juice
import spine_performance
import spine_preview
import spine_psd_bridge
import spine_quality
import spine_rig
import spine_smart_rig
import spine_v4
import spine_v5
from spine_validate import validate_rig, write_report


def _wants_juice(motion_plan: dict | None, prompt_intent: dict | None = None) -> bool:
    style = (motion_plan or {}).get("style", {})
    presets = set(style.get("presets", []))
    intent = prompt_intent or {}
    intent_styles = set(intent.get("styles", []))
    return (
        bool(presets & {"premium_slot", "punchy", "cute"})
        or bool(intent_styles & {"premium_slot", "casual_game", "punchy", "cute"})
        or float(style.get("fx", 0)) >= .65
        or intent.get("quality_target") == "industry_grade"
        or float(intent.get("energy", 0) or 0) >= .82
    )


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
                 motion_plan: dict | None = None,
                 prompt_intent: dict | None = None,
                 visual_qa: bool = True) -> dict:
    source = os.path.abspath(os.path.expanduser(source))
    out_dir = os.path.abspath(os.path.expanduser(out_dir))
    if not os.path.exists(source):
        raise FileNotFoundError(source)

    requested = [] if rig_only else animations
    smart_enabled = not rig_only and rig_profile not in {"", "simple", "legacy", None}
    resolved_name = name or os.path.splitext(os.path.basename(source.rstrip("/")))[0]

    # V6: layered PSDs are parsed/extracted by one shared implementation, then
    # adapted to the mature PhotoshopToSpine folder contract. The old PSD reader
    # remains only as a legacy direct-call fallback outside this validated flow.
    build_source = source
    build_source_group = source_group
    source_bridge = None
    if source.lower().endswith(".psd"):
        source_bridge = spine_psd_bridge.prepare_build_source(
            source, out_dir, source_group or "",
        )
        build_source = source_bridge["source"]
        build_source_group = None

    result = spine_rig.build_rig(
        build_source, out_dir, resolved_name, anims=requested,
        clean_mesh=(clean_mesh if not smart_enabled else False),
        auto_weight=(auto_weight if not smart_enabled else False),
        ik=(ik if not smart_enabled else False), clipping=clipping,
        slot_presets=[] if rig_only else slot_presets, source_group=build_source_group,
        fx_presets=[] if rig_only else fx_presets,
    )
    if source_bridge:
        result["source_bridge"] = {
            "shared_psd_parser": True,
            "prepared_layout": source_bridge["layout"],
            "parts": len(source_bridge["draw"]),
        }

    runtime_json = result["files"]["json"]
    images_dir = os.path.join(out_dir, "images")
    transactions = []
    smart_report = None

    if smart_enabled:
        def smart_stage(staged_json: str):
            return spine_smart_rig.enhance(
                staged_json, profile=rig_profile,
                mesh_quality=mesh_quality,
                max_influences=max(1, min(2, int(max_weight_influences))),
                add_ik=ik,
                images_dir=images_dir,
                naming_profile=naming_profile,
                naming_source=source,
            )

        smart_report, transaction = spine_guard.run_json_stage(
            runtime_json, "smart-rig", smart_stage,
        )
        transactions.append(transaction)
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

        def v4_stage(staged_json: str):
            return spine_v4.apply(
                staged_json, images_dir=images_dir,
                naming_profile=naming_profile, naming_source=source,
                motion_plan=motion_plan, smart_report=smart_report,
            )

        v4_report, transaction = spine_guard.run_json_stage(
            runtime_json, "animation-intelligence-v4", v4_stage,
        )
        transactions.append(transaction)
        result["animation_intelligence"] = v4_report
        if v4_report.get("visual_bones_added"):
            result["bones"] = result.get("bones", []) + [item["bone"] for item in v4_report["visual_bones_added"]]

        # Premium casual/slot prompts get a bounded character-life pass after the
        # primary posing exists. It adds overlap/asymmetry/settle accents but is
        # deliberately skipped for restrained styles.
        if motion_plan and _wants_juice(motion_plan, prompt_intent):
            def juice_stage(staged_json: str):
                with open(staged_json, encoding="utf-8") as handle:
                    data = json.load(handle)
                juiced, report = spine_juice.apply(data, motion_plan, intensity=1.0)
                with open(staged_json, "w", encoding="utf-8") as handle:
                    json.dump(juiced, handle, separators=(",", ":"))
                return report

            juice_report, transaction = spine_guard.run_json_stage(
                runtime_json, "character-juice-v1", juice_stage,
            )
            transactions.append(transaction)
            result["character_juice"] = juice_report

        # Facial/performance acting is a separate layer from generic juice. It uses
        # the semantic eye/pupil/brow/mouth/jaw controls created by the smart rig and
        # stays a no-op when those controls do not exist.
        if motion_plan and prompt_intent:
            def performance_stage(staged_json: str):
                with open(staged_json, encoding="utf-8") as handle:
                    data = json.load(handle)
                acted, report = spine_performance.apply(
                    data, motion_plan, smart_report, prompt_intent,
                )
                with open(staged_json, "w", encoding="utf-8") as handle:
                    json.dump(acted, handle, separators=(",", ":"))
                return report

            performance_report, transaction = spine_guard.run_json_stage(
                runtime_json, "performance-acting-v1", performance_stage,
            )
            transactions.append(transaction)
            result["performance_acting"] = performance_report

    # V5 runs before .spine creation so PSD compositing semantics and safe
    # visual-QA fixes are already present when the licensed CLI imports JSON.
    if visual_qa and not rig_only:
        def v5_stage(staged_json: str):
            return spine_v5.apply(
                source, staged_json, images_dir, out_dir,
                source_group=source_group or "", motion_plan=motion_plan,
            )

        visual_report, transaction = spine_guard.run_json_stage(
            runtime_json, "visual-intelligence-v5", v5_stage,
        )
        transactions.append(transaction)
        result["visual_intelligence"] = visual_report

    # The animator/art-director gate is intentionally stricter than file validation.
    # It can mark a technically valid build as WIP and provides a ranked revision queue.
    if visual_qa and not rig_only and motion_plan and os.path.isdir(images_dir):
        result["ad_gauntlet"] = spine_ad_gauntlet.review(
            runtime_json, images_dir, out_dir,
            motion_plan=motion_plan,
            secondary_chains=(smart_report or {}).get("secondary_chains", {}),
            performance_report=result.get("performance_acting"),
            prompt_intent=prompt_intent,
        )

    result["gauntlet"] = {
        "version": 9,
        "transactions": transactions,
        "runtime_state": spine_guard.file_state(runtime_json),
        "backup_count": len(spine_guard.list_backups(runtime_json)),
        "shared_psd_parser": bool(source_bridge),
        "character_juice": bool(result.get("character_juice")),
        "performance_acting": bool(result.get("performance_acting")),
        "presentation_ready": bool(result.get("ad_gauntlet", {}).get("presentation_ready")),
        "rules": [
            "layered PSD builds use one shared parser/extractor",
            "each mutating JSON stage runs on a temporary copy",
            "canonical runtime is atomically replaced only after valid JSON is produced",
            "bounded backups permit rollback without accumulating unbounded files",
            "SHA-256 state pins make stale edits detectable",
            "premium casual/slot juice is added after primary posing and before rendered QA",
            "facial acting is staged separately from generic body juice",
            "technical validation never upgrades a WIP to presentation-ready; only the senior animation gauntlet can do that",
        ],
    }

    if make_editable:
        if spine_cli.available():
            project = os.path.join(out_dir, f"{result['name']}.spine")
            created = spine_cli.make_project(runtime_json, project)
            result["editable_project"] = created
            result["asset_portability"] = spine_quality.audit_project_assets(runtime_json, project)
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
            runtime_json, images_dir,
            os.path.join(preview_dir, "montage.png"), 240,
        )

    report = validate_rig(runtime_json, result["files"]["atlas"], result["files"]["png"])
    report["options"] = {
        "rig_only": rig_only, "clean_mesh": clean_mesh, "auto_weight": auto_weight,
        "ik": ik, "clipping": clipping, "slot_presets": slot_presets or [],
        "source_group": source_group,
        "fx_presets": fx_presets or [],
        "rig_profile": rig_profile, "mesh_quality": mesh_quality,
        "max_weight_influences": max_weight_influences,
        "naming_profile": naming_profile,
        "animation_intelligence_v4": bool(motion_plan),
        "character_juice_v1": bool(result.get("character_juice")),
        "performance_acting_v1": bool(result.get("performance_acting")),
        "art_director_gauntlet_v1": bool(result.get("ad_gauntlet")),
        "presentation_ready": bool(result.get("ad_gauntlet", {}).get("presentation_ready")),
        "visual_intelligence_v5": visual_qa,
        "gauntlet_hardening_v9": True,
        "shared_psd_parser_v6": bool(source_bridge),
    }
    if result.get("smart_rig"):
        report["smart_rig"] = result["smart_rig"]
    if result.get("animation_intelligence"):
        report["animation_intelligence"] = result["animation_intelligence"]
    if result.get("character_juice"):
        report["character_juice"] = result["character_juice"]
    if result.get("performance_acting"):
        report["performance_acting"] = result["performance_acting"]
    if result.get("ad_gauntlet"):
        report["ad_gauntlet"] = result["ad_gauntlet"]
    if result.get("visual_intelligence"):
        report["visual_intelligence"] = result["visual_intelligence"]
    report["gauntlet"] = result["gauntlet"]
    report["spine_cli"] = {"available": spine_cli.available(), "path": spine_cli.SPINE_BIN}
    report_path = write_report(report, os.path.join(out_dir, "rig_report.json"))
    result["validation"] = report
    result["files"]["report"] = report_path
    if not report["ok"]:
        raise ValueError("generated rig failed validation: " + "; ".join(report["errors"]))
    return result
