"""V7 animation engineering loop helpers.

Turns rendered QA into actionable, deterministic revision instructions. The goal is not
opaque auto-animation; it is a bounded build -> render -> diagnose -> revise loop that a
model or human can inspect and repeat.
"""
from __future__ import annotations

import json
import os

import spine_critic
import spine_motion_intelligence
import spine_v5


KNOWN_TRAPS = [
    {"id": "real_bezier", "rule": "never write curve='bezier'; emit Spine control-point arrays"},
    {"id": "frame_grid", "rule": "author key times on the project FPS grid, normally 30 fps"},
    {"id": "one_change", "rule": "revise one diagnosed cause at a time; do not random-walk parameters"},
    {"id": "render_truth", "rule": "judge motion from rendered frames, not from key counts alone"},
    {"id": "loop_seam", "rule": "loop endings must reproduce the opening pose/value state"},
    {"id": "secondary_causality", "rule": "hair/cloth/tails react to primary acceleration and stops"},
    {"id": "fx_hierarchy", "rule": "FX supports the pose; it must not wash out the subject or frame"},
]


def diagnose_visual_report(visual_report: dict, structural_report: dict | None = None,
                           critic_report: dict | None = None) -> dict:
    """Convert rendered, structural, and deterministic critic QA into revision actions."""
    issues = list(visual_report.get("issues", []))
    actions = []
    for issue in issues:
        kind = issue.get("kind", "")
        clip = issue.get("clip", "")
        if kind == "blank_frame":
            actions.append({"clip": clip, "priority": 100, "cause": "visibility_or_attachment",
                            "change": "repair attachment/alpha state before touching timing"})
        elif kind == "possible_crop":
            actions.append({"clip": clip, "priority": 75, "cause": "bounds_or_overshoot",
                            "change": "reduce only the offending travel/scale or enlarge render bounds"})
        elif kind == "visually_static":
            actions.append({"clip": clip, "priority": 70, "cause": "weak_pose_contrast",
                            "change": "increase key-pose contrast; change silhouette or spacing, not random FX"})
        elif kind == "visual_pop_spike":
            actions.append({"clip": clip, "priority": 80, "cause": "discontinuity",
                            "change": "inspect attachment swaps/scale/alpha near spike and smooth only that transition"})
    for clip, report in (structural_report or {}).get("clips", {}).items():
        for problem in report.get("issues", []):
            if "too few timing beats" in problem or "planned pose beats" in problem:
                actions.append({"clip": clip, "priority": 65, "cause": "missing_beats",
                                "change": "restore setup/anticipation/action/overshoot/settle contrast"})
            if "perfectly mirrored" in problem:
                actions.append({"clip": clip, "priority": 45, "cause": "mechanical_symmetry",
                                "change": "offset left/right timing and amplitude while preserving intent"})
    for finding in (critic_report or {}).get("findings", []):
        priority = 85 if finding.get("severity") == "high" else 60
        actions.append({
            "clip": finding.get("clip", ""),
            "priority": priority,
            "cause": finding.get("check", "deterministic_critic"),
            "bone": finding.get("bone", ""),
            "metric": finding.get("metric"),
            "value": finding.get("value"),
            "threshold": finding.get("threshold"),
            "change": finding.get("suggested_patch", {}),
        })
    actions.sort(key=lambda item: (-item["priority"], item.get("clip", "")))
    return {"ok": not actions, "actions": actions, "known_traps": KNOWN_TRAPS}


def inspect_build(runtime_json: str, images_dir: str, out_dir: str,
                  motion_plan: dict | None = None,
                  secondary_chains: dict | None = None) -> dict:
    """Render representative beats and produce one combined revision report."""
    visual = spine_v5.visual_self_critique(
        runtime_json, images_dir, os.path.join(out_dir, "engineering_review"),
        motion_plan=motion_plan, autofix=False,
    )
    with open(runtime_json, encoding="utf-8") as handle:
        data = json.load(handle)
    structural = spine_motion_intelligence.audit_animation(data, motion_plan)
    critic = spine_critic.audit(
        data, motion_plan=motion_plan,
        secondary_chains=secondary_chains,
        fps=int(data.get("skeleton", {}).get("fps") or 30),
    )
    diagnosis = diagnose_visual_report(visual, structural, critic)
    return {
        "version": 7,
        "visual": visual,
        "structural": structural,
        "critic": critic,
        "diagnosis": diagnosis,
        "ready": (
            visual.get("ok", False)
            and structural.get("score", 0) >= 80
            and critic.get("ok", False)
            and not diagnosis["actions"]
        ),
    }
