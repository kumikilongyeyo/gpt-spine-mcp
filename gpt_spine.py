"""Command line interface for GPT Spine MCP."""
from __future__ import annotations

import argparse
import json
import os
import sys

import spine_cli
import spine_quality
import spine_spec
from workflow import run_pipeline


def _csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gpt-spine", description="Build Spine 2D rigs and animations")
    sub = parser.add_subparsers(dest="command", required=True)
    rig = sub.add_parser("rig", help="build, validate, preview, and optionally export a rig")
    rig.add_argument("source", help="layered PSD or PhotoshopToSpine export directory")
    rig.add_argument("--out-dir", help="output directory; defaults to ./<name>-spine")
    rig.add_argument("--name", help="skeleton/output basename")
    rig.add_argument("--rig-only", action="store_true", help="emit no animation timelines")
    rig.add_argument("--animations", default=None, help="comma-separated states")
    rig.add_argument("--clean-mesh", action="store_true", help="convert regions to clean quad meshes")
    rig.add_argument("--auto-weight", action="store_true", help="bind generated mesh vertices to slot bones")
    rig.add_argument("--ik", action="store_true", help="add an editable IK target and constraint")
    rig.add_argument("--clipping", action="store_true", help="add a full-bounds clipping attachment")
    rig.add_argument("--slot-presets", default=None, help="comma-separated pulse,flash,flicker tracks")
    rig.add_argument("--fx-presets", default=None,
                     help="comma-separated coin_splash,glow_flash,particle_explosion,bomb_explosion,fire,splash,clipped_shine")
    rig.add_argument("--source-group", help="nested PSD group containing the intended asset set")
    rig.add_argument("--no-editable", action="store_true", help="do not import an editable .spine project")
    rig.add_argument("--no-preview", action="store_true", help="skip preview montage")
    rig.add_argument("--no-export", action="store_true", help="do not export the generated .spine project")

    sub.add_parser("doctor", help="show dependency and Spine CLI status")
    audit = sub.add_parser("audit-preview", help="reject blank/static previews and compare a reference")
    audit.add_argument("preview")
    audit.add_argument("--reference")
    spec = sub.add_parser("apply-spec", help="compile a numeric motion spec into Spine JSON")
    spec.add_argument("runtime_json")
    spec.add_argument("spec_json")
    spec.add_argument("out_json")
    agent = sub.add_parser("agent", help="run an OpenAI Agents SDK client against the local MCP server")
    agent.add_argument("prompt", nargs="+", help="instruction for the agent")
    agent.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.4"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        print(json.dumps({"spine_cli": spine_cli.available(), "spine_bin": spine_cli.SPINE_BIN,
                          "spine_version": spine_cli.version() if spine_cli.available() else None,
                          "python": sys.version.split()[0]}, indent=2))
        return 0
    if args.command == "agent":
        from openai_agent import run_agent
        print(run_agent(" ".join(args.prompt), args.model))
        return 0
    if args.command == "audit-preview":
        report = spine_quality.audit_preview(args.preview, args.reference)
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 2
    if args.command == "apply-spec":
        with open(args.spec_json, encoding="utf-8") as handle:
            spec_data = json.load(handle)
        report = spine_spec.compile_motion_spec(args.runtime_json, args.out_json, spec_data)
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 2

    source_name = os.path.splitext(os.path.basename(os.path.abspath(args.source).rstrip(os.sep)))[0]
    name = args.name or source_name
    out_dir = args.out_dir or os.path.abspath(f"{name}-spine")
    result = run_pipeline(
        args.source, out_dir, name, rig_only=args.rig_only,
        animations=_csv(args.animations), clean_mesh=args.clean_mesh,
        auto_weight=args.auto_weight, ik=args.ik, clipping=args.clipping,
        slot_presets=_csv(args.slot_presets), make_editable=not args.no_editable,
        fx_presets=_csv(args.fx_presets),
        source_group=args.source_group,
        make_preview=not args.no_preview, export_project=not args.no_export,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
