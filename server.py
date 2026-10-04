#!/usr/bin/env python
"""Spine MCP server — production Spine 2D rigging, motion, FX, and PSD semantics."""
from __future__ import annotations

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp.server.fastmcp import FastMCP

import spine_brain
import spine_cli
import spine_motion_intelligence
import spine_preview
import spine_quality
import spine_rig
import spine_semantics
import spine_spec
import workflow
from spine_validate import validate_rig

mcp = FastMCP("gpt-spine")


@mcp.tool()
def spine_doctor() -> dict:
    """Report whether the Spine CLI and Python deps are available."""
    deps = {}
    for module in ("PIL", "psd_tools"):
        try:
            __import__(module)
            deps[module] = True
        except Exception:
            deps[module] = False
    return {
        "spine_cli": spine_cli.available(),
        "spine_bin": spine_cli.SPINE_BIN,
        "spine_version": spine_cli.version() if spine_cli.available() else None,
        "deps": deps,
        "smart_animation_director": True,
        "animation_intelligence": 4,
        "psd_semantic_intelligence": 3,
        "visual_anatomy": True,
        "pose_beat_planning": True,
        "animation_quality_audit": True,
        "smart_rig_profiles": ["biped", "quadruped", "winged", "prop", "*_2_5d"],
        "secondary_systems": ["hair", "cloth", "tail", "wing"],
    }


@mcp.tool()
def inspect_source(source: str, source_group: str = "", naming_profile: str = "") -> dict:
    """Inspect source art and return layer structure plus semantic interpretation."""
    if source.lower().endswith(".psd"):
        import tempfile
        inspection = spine_rig.inspect_psd(source, tempfile.mkdtemp(), source_group or None)
    else:
        parts, draw, _ = spine_rig._read_photoshop_export(source)
        families = {}
        for name in draw:
            base = name
            for suffix in spine_rig.SUFFIX:
                if name.lower().endswith(suffix):
                    base = name[:-len(suffix)]
            families.setdefault(base, []).append(name)
        inspection = {
            "parts": draw,
            "count": len(draw),
            "head_state_families": {base: values for base, values in families.items() if len(values) > 1},
        }
    naming, used = spine_semantics.load_profile(naming_profile, source)
    inspection["semantic_scene"] = spine_semantics.analyze_inspection(inspection, naming)
    inspection["naming_profile"] = used
    return inspection


@mcp.tool()
def inspect_psd_semantics(source: str, source_group: str = "", naming_profile: str = "") -> dict:
    """Return the semantic scene graph for a PSD/export before rigging."""
    inspection = inspect_source(source, source_group, naming_profile)
    return inspection["semantic_scene"]


@mcp.tool()
def save_naming_profile(path: str, aliases: dict | None = None,
                        exact: dict | None = None, states: dict | None = None) -> dict:
    """Teach GPT Spine a studio/user PSD naming vocabulary."""
    return spine_semantics.save_profile(path, {
        "aliases": aliases or {}, "exact": exact or {}, "states": states or {},
    })


def _inspection_parts(inspection: dict) -> list[str]:
    if inspection.get("layers"):
        return [layer.get("layer_path") or layer.get("attachment", "")
                for layer in inspection["layers"] if layer.get("layer_path") or layer.get("attachment")]
    if inspection.get("parts"):
        return list(inspection["parts"])
    return []


@mcp.tool()
def understand_animation(request: str, source: str = "", source_group: str = "",
                         naming_profile: str = "") -> dict:
    """Interpret loose animation art direction and return rig + V4 motion decisions."""
    inspection = inspect_source(source, source_group, naming_profile) if source else None
    plan = spine_brain.plan_animation(request, _inspection_parts(inspection or {}))
    semantic_scene = (inspection or {}).get("semantic_scene")
    plan["motion_plan"] = spine_motion_intelligence.build_motion_plan(
        request, plan["asset_type"], plan["animations"], semantic_scene,
    )
    if inspection:
        plan["inspection"] = inspection
        plan["semantic_scene"] = semantic_scene
    return plan


@mcp.tool()
def plan_motion(request: str, asset_type: str = "biped",
                animations: list[str] | None = None) -> dict:
    """Plan V4 style, pose beats, timing, energy hierarchy, asymmetry, contacts and FX cues."""
    return spine_motion_intelligence.build_motion_plan(
        request, asset_type, animations or ["idle"], None,
    )


@mcp.tool()
def smart_build(source: str, out_dir: str, request: str, name: str = "",
                source_group: str = "", make_editable: bool = True,
                make_preview: bool = True, naming_profile: str = "") -> dict:
    """Natural-language V4 build: semantics, visual anatomy, polished motion and QA."""
    inspection = inspect_source(source, source_group, naming_profile)
    plan = spine_brain.plan_animation(request, _inspection_parts(inspection))
    motion_plan = spine_motion_intelligence.build_motion_plan(
        request, plan["asset_type"], plan["animations"], inspection.get("semantic_scene"),
    )
    result = workflow.run_pipeline(
        source, out_dir, name or None,
        animations=plan["animations"], clean_mesh=plan["clean_mesh"],
        auto_weight=plan["auto_weight"], ik=plan["ik"], clipping=plan["clipping"],
        slot_presets=plan["slot_presets"], fx_presets=plan["fx_presets"],
        source_group=source_group or None, make_editable=make_editable,
        make_preview=make_preview, rig_profile=plan["rig_profile"],
        mesh_quality=plan["mesh_quality"],
        max_weight_influences=plan["max_weight_influences"],
        naming_profile=naming_profile, motion_plan=motion_plan,
    )
    plan["motion_plan"] = motion_plan
    result["director_plan"] = plan
    result["source_inspection"] = inspection
    return result


@mcp.tool()
def rig_and_animate(source: str, out_dir: str, name: str = "", kind: str = "symbol",
                    anims: list[str] | None = None, make_editable: bool = True,
                    clean_mesh: bool = False, auto_weight: bool = False,
                    ik: bool = False, clipping: bool = False,
                    slot_presets: list[str] | None = None,
                    fx_presets: list[str] | None = None,
                    source_group: str = "") -> dict:
    """Build a deterministic legacy/simple rig. Use smart_build for V4 semantic rigs."""
    result = spine_rig.build_rig(
        source, out_dir, name or None, kind, anims,
        clean_mesh=clean_mesh, auto_weight=auto_weight,
        ik=ik, clipping=clipping, slot_presets=slot_presets,
        source_group=source_group or None, fx_presets=fx_presets,
    )
    if make_editable and spine_cli.available():
        project = os.path.join(out_dir, f"{result['name']}.spine")
        result["editable_project"] = spine_cli.make_project(result["files"]["json"], project)
        result["asset_portability"] = spine_quality.audit_project_assets(result["files"]["json"], project)
        if not result["asset_portability"]["ok"]:
            raise ValueError("editable project is not portable")
    return result


@mcp.tool()
def build_workflow(source: str, out_dir: str, name: str = "",
                   rig_only: bool = False, animations: list[str] | None = None,
                   clean_mesh: bool = False, auto_weight: bool = False,
                   ik: bool = False, clipping: bool = False,
                   slot_presets: list[str] | None = None,
                   fx_presets: list[str] | None = None,
                   source_group: str = "", make_editable: bool = True,
                   make_preview: bool = True, rig_profile: str = "simple",
                   mesh_quality: str = "adaptive", max_weight_influences: int = 2,
                   naming_profile: str = "", motion_plan: dict | None = None) -> dict:
    """Run the complete validated workflow, optionally with Animation Intelligence V4."""
    return workflow.run_pipeline(
        source, out_dir, name or None,
        rig_only=rig_only, animations=animations,
        clean_mesh=clean_mesh, auto_weight=auto_weight,
        ik=ik, clipping=clipping, slot_presets=slot_presets,
        fx_presets=fx_presets, source_group=source_group or None,
        make_editable=make_editable, make_preview=make_preview,
        rig_profile=rig_profile, mesh_quality=mesh_quality,
        max_weight_influences=max_weight_influences,
        naming_profile=naming_profile, motion_plan=motion_plan,
    )


@mcp.tool()
def audit_preview(preview_gif: str, reference_gif: str = "") -> dict:
    """Reject blank/static previews and compare against an optional reference."""
    return spine_quality.audit_preview(preview_gif, reference_gif or None)


@mcp.tool()
def apply_motion_spec(runtime_json: str, out_json: str, motion_spec: dict) -> dict:
    """Compile an explicit numeric motion spec into a Spine skeleton."""
    return spine_spec.compile_motion_spec(runtime_json, out_json, motion_spec)


@mcp.tool()
def validate_motion(runtime_json: str, clips: list[str] | None = None,
                    handoffs: list[dict] | None = None) -> dict:
    """Audit authored clips for frame-grid, curve, handoff, and loop-seam errors."""
    return spine_spec.validate_motion_spec(runtime_json, clips, handoffs)


@mcp.tool()
def validate_output(runtime_json: str, atlas: str = "", texture: str = "") -> dict:
    """Validate bone, slot, skin, IK, atlas, texture, and animation references."""
    return validate_rig(runtime_json, atlas or None, texture or None)


@mcp.tool()
def pack_atlas(images_dir: str, out_dir: str, name: str) -> dict:
    """Pack PNGs into a Spine atlas with the licensed Spine CLI."""
    return spine_cli.pack_atlas(images_dir, out_dir, name)


@mcp.tool()
def make_project(runtime_json: str, out_spine: str) -> dict:
    """Import runtime skeleton JSON into an editable .spine project."""
    return spine_cli.make_project(runtime_json, out_spine)


@mcp.tool()
def export_project(project: str, out_dir: str, fmt: str = "json+pack") -> dict:
    """Export a .spine project to runtime JSON/binary and optional atlas."""
    return spine_cli.export_project(project, out_dir, fmt)


@mcp.tool()
def project_info(project_or_json: str) -> str:
    """Show bones, slots, and animations from a .spine project or runtime JSON."""
    return spine_cli.info(project_or_json)


@mcp.tool()
def preview(rig_dir: str, images_dir: str = "", out_png: str = "", maxpx: int = 200) -> dict:
    """Render a keyframe montage PNG for a built rig."""
    rig_json = sorted(glob.glob(f"{rig_dir}/*.json"))[0]
    name = os.path.splitext(os.path.basename(rig_json))[0]
    if not images_dir:
        for candidate in (os.path.join(rig_dir, "images"), rig_dir):
            if glob.glob(os.path.join(candidate, "*.png")):
                images_dir = candidate
                break
    out = out_png or os.path.join(rig_dir, "_preview.png")
    path = spine_preview.montage(rig_json, images_dir, out, maxpx)
    return {"preview": path, "name": name}


@mcp.tool()
def batch(roster_dir: str, out_root: str, kind: str = "symbol",
          make_editable: bool = True) -> dict:
    """Rig each PhotoshopToSpine export subfolder under roster_dir."""
    results, errors = [], []
    for directory in sorted(glob.glob(f"{roster_dir}/*/")):
        directory = directory.rstrip("/")
        if not (glob.glob(f"{directory}/*.json") and os.path.isdir(f"{directory}/images")):
            continue
        name = os.path.basename(directory)
        try:
            results.append(rig_and_animate(directory, os.path.join(out_root, name), name,
                                           kind, None, make_editable))
        except Exception as exc:
            errors.append({"name": name, "error": str(exc)})
    return {"rigged": [result["name"] for result in results],
            "count": len(results), "errors": errors, "results": results}


def main() -> None:
    """Console entry point for uvx/Codex/OpenAI Agents SDK."""
    mcp.run()


if __name__ == "__main__":
    main()
