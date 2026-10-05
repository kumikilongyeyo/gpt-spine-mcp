"""Animator / art-director quality gate for generated Spine animation.

Validation answers "is the file technically valid?". This module answers the much harder
production question: "would a senior animator/art director allow this to be presented?"
It combines render evidence, curve QA, structural checks, acting coverage and FX hierarchy
into a strict scorecard. A technically valid build may still be explicitly marked WIP.
"""
from __future__ import annotations

import os

import spine_engineering_loop

CATEGORY_ORDER = (
    "pose_readability",
    "timing_spacing",
    "weight_arcs",
    "secondary_motion",
    "performance_acting",
    "fx_polish",
)

CATEGORY_WEIGHTS = {
    "pose_readability": .24,
    "timing_spacing": .19,
    "weight_arcs": .17,
    "secondary_motion": .14,
    "performance_acting": .16,
    "fx_polish": .10,
}

BLOCKING_CAUSES = {
    "visibility_or_attachment",
    "discontinuity",
    "padded_bounds_clip",
    "fx_swallowing_art",
    "anticipation_vs_impact",
}


def _clip_count(motion_plan: dict | None) -> int:
    return len((motion_plan or {}).get("clips", {}))


def _score_categories(engineering: dict, performance_report: dict | None,
                      prompt_intent: dict | None) -> dict:
    scores = {name: 100.0 for name in CATEGORY_ORDER}
    visual = engineering.get("visual", {})
    structural = engineering.get("structural", {})
    critic = engineering.get("critic", {})
    render = engineering.get("render_qa", {})

    structural_score = float(structural.get("score", 0) or 0)
    if structural_score:
        scores["timing_spacing"] = min(scores["timing_spacing"], structural_score)
        scores["secondary_motion"] = min(scores["secondary_motion"], structural_score + 4)
    else:
        scores["timing_spacing"] -= 28
        scores["secondary_motion"] -= 20

    for issue in visual.get("issues", []):
        kind = issue.get("kind", "")
        if kind == "blank_frame":
            scores["pose_readability"] -= 55
        elif kind == "possible_crop":
            scores["pose_readability"] -= 28
        elif kind == "visually_static":
            scores["pose_readability"] -= 22
            scores["timing_spacing"] -= 10
        elif kind == "visual_pop_spike":
            scores["timing_spacing"] -= 28
            scores["pose_readability"] -= 8

    for finding in critic.get("findings", []):
        severity = finding.get("severity", "medium")
        penalty = 22 if severity == "high" else 10
        check = finding.get("check", "")
        if check in {"anticipation_vs_impact", "spacing_acceleration"}:
            scores["timing_spacing"] -= penalty
        elif check in {"arcs", "overshoot_ratio"}:
            scores["weight_arcs"] -= penalty
        elif check == "drag_order":
            scores["secondary_motion"] -= penalty

    for finding in render.get("findings", []):
        severity = finding.get("severity", "medium")
        penalty = 28 if severity == "high" else 11
        kind = finding.get("kind", "")
        if kind in {"silhouette_too_similar", "negative_space_loss", "padded_bounds_clip"}:
            scores["pose_readability"] -= penalty
        elif kind in {"fx_swallowing_art", "fx_coverage_too_high"}:
            scores["fx_polish"] -= penalty
            scores["pose_readability"] -= penalty * .3

    perf = performance_report or {}
    intended_character = bool((prompt_intent or {}).get("capabilities", {}).get("face"))
    quality_target = (prompt_intent or {}).get("quality_target")
    active_reports = [item for item in perf.get("clips", []) if item.get("applied")]
    available = perf.get("available_controls", {})
    if intended_character or any(available.values()):
        if not perf:
            scores["performance_acting"] -= 32 if quality_target == "industry_grade" else 20
        elif not active_reports:
            scores["performance_acting"] -= 25
        else:
            missing = len(perf.get("limitations", []))
            scores["performance_acting"] -= min(18, missing * 3)
            if any(item.get("escalation") for item in active_reports):
                scores["performance_acting"] += 3
    elif quality_target == "industry_grade":
        # Do not punish art that genuinely has no facial controls, but surface the limitation.
        scores["performance_acting"] = min(scores["performance_acting"], 88)

    rendered_evidence = bool(render.get("clips")) and bool(render.get("render_dir"))
    if not rendered_evidence:
        scores["pose_readability"] = min(scores["pose_readability"], 45)
        scores["fx_polish"] = min(scores["fx_polish"], 55)

    return {key: max(0, min(100, round(value, 1))) for key, value in scores.items()}


def _overall(scores: dict) -> float:
    return round(sum(scores[name] * CATEGORY_WEIGHTS[name] for name in CATEGORY_ORDER), 1)


def _blockers(engineering: dict, scores: dict, prompt_intent: dict | None) -> list[dict]:
    blockers = []
    render = engineering.get("render_qa", {})
    critic = engineering.get("critic", {})
    visual = engineering.get("visual", {})
    diagnosis = engineering.get("diagnosis", {})

    if not render.get("clips"):
        blockers.append({"cause": "no_render_evidence", "note": "No rendered beat evidence exists; animation cannot be approved from JSON alone."})
    for issue in visual.get("issues", []):
        if issue.get("kind") in {"blank_frame", "possible_crop", "visual_pop_spike"}:
            blockers.append({"cause": issue.get("kind"), "clip": issue.get("clip", ""),
                             "note": "Visible presentation defect must be fixed before approval."})
    for finding in render.get("findings", []):
        if finding.get("severity") == "high":
            blockers.append({"cause": finding.get("kind"), "clip": finding.get("clip", ""),
                             "time": finding.get("time"), "note": "High-severity render defect."})
    for finding in critic.get("findings", []):
        if finding.get("severity") == "high":
            blockers.append({"cause": finding.get("check"), "clip": finding.get("clip", ""),
                             "bone": finding.get("bone", ""), "note": "High-severity motion defect."})

    quality_target = (prompt_intent or {}).get("quality_target", "standard")
    floor = 82 if quality_target == "industry_grade" else 76
    for category, value in scores.items():
        if value < floor:
            blockers.append({"cause": "category_below_floor", "category": category, "score": value,
                             "required": floor, "note": "Quality category is below presentation floor."})

    # Engineering diagnoses may include medium issues that are not individually fatal,
    # but several unresolved notes mean the animation is still a review WIP.
    severe_diagnoses = [action for action in diagnosis.get("actions", [])
                        if action.get("priority", 0) >= 80 or action.get("cause") in BLOCKING_CAUSES]
    for action in severe_diagnoses:
        blockers.append({"cause": action.get("cause", "diagnosis"), "clip": action.get("clip", ""),
                         "note": "Senior-review diagnosis remains unresolved."})

    # Deduplicate compactly.
    seen = set()
    unique = []
    for item in blockers:
        key = (item.get("cause"), item.get("clip"), item.get("category"), item.get("time"), item.get("bone"))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _revision_queue(engineering: dict, scores: dict, performance_report: dict | None,
                    prompt_intent: dict | None) -> list[dict]:
    queue = []
    for action in engineering.get("diagnosis", {}).get("actions", []):
        queue.append({
            "priority": int(action.get("priority", 50)),
            "discipline": "animation" if action.get("cause") not in {"fx_swallowing_art", "fx_coverage_too_high"} else "fx",
            "clip": action.get("clip", ""),
            "cause": action.get("cause", "unknown"),
            "change": action.get("change"),
            "review_rule": "change the smallest responsible parameter, then re-render the same beat",
        })

    category_repairs = {
        "pose_readability": "push the key pose/silhouette and restore negative space; do not hide the problem with FX",
        "timing_spacing": "fix anticipation/action spacing and remove linear or poppy timing before adding more motion",
        "weight_arcs": "repair center-of-mass feel, arcs and overshoot; heavier motion needs slower anticipation and smaller rebound",
        "secondary_motion": "restore causal overlap: root moves first, downstream pieces peak later and settle with decay",
        "performance_acting": "add an intentional eye/brow/jaw thought that supports the body beat; avoid constant face fidgeting",
        "fx_polish": "reduce FX coverage/alpha/scale and time the strongest accent to a 1–2 frame impact window",
    }
    for category, value in scores.items():
        if value < 88:
            queue.append({"priority": round(88 - value) + 55, "discipline": category,
                          "cause": "category_polish_gap", "score": value,
                          "change": category_repairs[category],
                          "review_rule": "re-render key beats and compare against the previous pass"})

    intent = prompt_intent or {}
    if intent.get("quality_target") == "industry_grade" and not performance_report:
        queue.append({"priority": 72, "discipline": "performance_acting", "cause": "missing_performance_pass",
                      "change": "run the facial/performance pass when controls exist; otherwise explicitly report the source limitation",
                      "review_rule": "do not call the build industry-grade until the acting layer is assessed"})

    queue.sort(key=lambda item: (-int(item.get("priority", 0)), item.get("clip", "")))
    return queue[:18]


def review(runtime_json: str, images_dir: str, out_dir: str,
           motion_plan: dict | None = None, secondary_chains: dict | None = None,
           performance_report: dict | None = None,
           prompt_intent: dict | None = None) -> dict:
    """Run the strict senior animator / art-director gate.

    ``presentation_ready`` is intentionally harder than ordinary validation. A missing
    render, a high-severity visible defect, or any weak core category blocks approval.
    """
    engineering = spine_engineering_loop.inspect_build(
        runtime_json, images_dir, os.path.join(out_dir, "ad_gauntlet"),
        motion_plan=motion_plan, secondary_chains=secondary_chains,
    )
    scores = _score_categories(engineering, performance_report, prompt_intent)
    overall = _overall(scores)
    blockers = _blockers(engineering, scores, prompt_intent)
    quality_target = (prompt_intent or {}).get("quality_target", "standard")
    required_overall = 88 if quality_target == "industry_grade" else 82
    revision_queue = _revision_queue(engineering, scores, performance_report, prompt_intent)

    presentation_ready = not blockers and overall >= required_overall and not revision_queue[:1]
    # A small non-blocking polish queue is still useful, but it must not make a genuinely
    # excellent build impossible to approve. Allow approval when all categories >= 92.
    if not blockers and overall >= required_overall and min(scores.values()) >= 92:
        presentation_ready = True

    if presentation_ready:
        status = "PRESENTATION_READY"
        verdict = "Senior-review gate passed: rendered evidence and all core animation disciplines are presentation quality."
    elif blockers:
        status = "BLOCKED_WIP"
        verdict = "Do not present as finished. Resolve the blocking visual/motion defects and rerun the same gauntlet."
    else:
        status = "REVIEWABLE_NOT_FINAL"
        verdict = "Technically reviewable, but it still lacks final animation/art-direction polish."

    return {
        "version": 1,
        "status": status,
        "presentation_ready": presentation_ready,
        "overall_score": overall,
        "required_overall": required_overall,
        "category_scores": scores,
        "blockers": blockers,
        "revision_queue": revision_queue,
        "verdict": verdict,
        "engineering": engineering,
        "art_director_rules": [
            "technical validity is never equivalent to visual approval",
            "no rendered evidence means no approval",
            "a strong effect cannot compensate for a weak pose or weak timing",
            "fix one diagnosed cause, rerender the same beat, then compare before changing something else",
            "small/mobile readability matters: the action must survive at thumbnail scale",
            "presentation-ready means the piece feels intentionally acted, weighted, staged and polished—not merely animated",
        ],
    }
