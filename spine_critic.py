"""Deterministic animation critic for Spine runtime JSON.

This module implements the cheap, testable critic slice first: anticipation/impact,
spacing/acceleration, arcs, drag order, and overshoot. It samples Spine timeline
curves rather than assuming linear interpolation, so the critic evaluates the motion
that the runtime actually receives.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import median

_COMPONENTS = {
    "rotate": ("value",),
    "translate": ("x", "y"),
    "scale": ("x", "y"),
}


def _time(key: dict) -> float:
    return float(key.get("time", 0.0))


def _value(key: dict, prop: str, component: int) -> float:
    name = _COMPONENTS[prop][component]
    default = 1.0 if prop == "scale" else 0.0
    return float(key.get(name, default))


def _cubic(a: float, b: float, c: float, d: float, u: float) -> float:
    v = 1.0 - u
    return v * v * v * a + 3 * v * v * u * b + 3 * v * u * u * c + u * u * u * d


def _bezier_value(at: float, t0: float, v0: float, t1: float, v1: float,
                  curve: list[float], component: int) -> float:
    base = component * 4
    if len(curve) < base + 4:
        f = (at - t0) / max(1e-9, t1 - t0)
        return v0 + (v1 - v0) * f
    cx1, cy1, cx2, cy2 = map(float, curve[base:base + 4])
    lo, hi = 0.0, 1.0
    for _ in range(28):
        u = (lo + hi) * 0.5
        x = _cubic(t0, cx1, cx2, t1, u)
        if x < at:
            lo = u
        else:
            hi = u
    u = (lo + hi) * 0.5
    return _cubic(v0, cy1, cy2, v1, u)


def sample_timeline(track: list[dict], prop: str, at: float, component: int = 0) -> float:
    """Sample one Spine timeline component at ``at`` seconds.

    Supports stepped, linear/default, and Spine absolute Bezier control-point arrays.
    Invalid string values such as ``curve='bezier'`` intentionally fall back to linear,
    matching the practical failure mode this critic is meant to expose.
    """
    if not track:
        return 1.0 if prop == "scale" else 0.0
    if at <= _time(track[0]):
        return _value(track[0], prop, component)
    if at >= _time(track[-1]):
        return _value(track[-1], prop, component)
    for index in range(len(track) - 1):
        left, right = track[index], track[index + 1]
        t0, t1 = _time(left), _time(right)
        if t0 <= at <= t1:
            v0, v1 = _value(left, prop, component), _value(right, prop, component)
            curve = left.get("curve")
            if curve == "stepped":
                return v0
            if isinstance(curve, list):
                return _bezier_value(at, t0, v0, t1, v1, curve, component)
            f = (at - t0) / max(1e-9, t1 - t0)
            return v0 + (v1 - v0) * f
    return _value(track[-1], prop, component)


def _duration(animation: dict) -> float:
    end = 0.0
    for channels in animation.get("bones", {}).values():
        for prop, track in channels.items():
            if prop in _COMPONENTS and isinstance(track, list) and track:
                end = max(end, _time(track[-1]))
    return end


def _frames(duration: float, fps: int) -> list[float]:
    count = max(1, int(math.ceil(duration * fps)))
    return [frame / fps for frame in range(count + 1)]


def _scalar_tracks(animation: dict):
    for bone, channels in animation.get("bones", {}).items():
        for prop, track in channels.items():
            if prop not in _COMPONENTS or not isinstance(track, list) or len(track) < 2:
                continue
            for component, component_name in enumerate(_COMPONENTS[prop]):
                values = [_value(key, prop, component) for key in track]
                amplitude = max(values) - min(values)
                if amplitude > 1e-5:
                    yield {
                        "bone": bone,
                        "prop": prop,
                        "component": component,
                        "component_name": component_name,
                        "track": track,
                        "amplitude": amplitude,
                    }


def _dominant_track(animation: dict) -> dict | None:
    tracks = list(_scalar_tracks(animation))
    return max(tracks, key=lambda item: item["amplitude"], default=None)


def _beat_times(spec: dict) -> dict[str, float]:
    return {beat["name"]: float(beat["time"]) for beat in spec.get("beats", [])}


def _finding(check: str, clip: str, *, bone: str = "", metric: str,
             value, threshold, severity: str = "medium", suggested_patch: dict | None = None) -> dict:
    return {
        "check": check,
        "clip": clip,
        "bone": bone,
        "metric": metric,
        "value": value,
        "threshold": threshold,
        "severity": severity,
        "suggested_patch": suggested_patch or {},
    }


def check_anticipation_impact(animation: dict, clip: str, spec: dict, fps: int = 30) -> list[dict]:
    beats = _beat_times(spec)
    anticipation = beats.get("anticipation")
    impact = beats.get("contact") or beats.get("impact") or beats.get("peak") or beats.get("launch")
    track_meta = _dominant_track(animation)
    if anticipation is None or impact is None or not track_meta:
        return []
    track = track_meta["track"]
    prop = track_meta["prop"]
    component = track_meta["component"]
    start = sample_timeline(track, prop, 0.0, component)
    ant = sample_timeline(track, prop, anticipation, component)
    hit = sample_timeline(track, prop, impact, component)
    primary = hit - start
    anti = ant - start
    if abs(primary) < 1e-5:
        return []
    ratio = abs(anti) / abs(primary)
    findings = []
    opposite = anti * primary < 0
    if not opposite or ratio < 0.10:
        findings.append(_finding(
            "anticipation_vs_impact", clip, bone=track_meta["bone"],
            metric="anticipation_ratio", value=round(ratio, 4), threshold={"min": 0.10, "opposite_sign": True},
            severity="high", suggested_patch={"operation": "move_anticipation_extreme", "direction": "opposite_action", "target_ratio": [0.10, 0.25]},
        ))

    def peak_speed(a: float, b: float) -> float:
        start_frame = int(round(a * fps))
        end_frame = int(round(b * fps))
        values = [sample_timeline(track, prop, frame / fps, component)
                  for frame in range(start_frame, max(start_frame + 1, end_frame) + 1)]
        if len(values) < 2:
            return 0.0
        return max(abs((right - left) * fps) for left, right in zip(values, values[1:]))

    ant_speed = peak_speed(0.0, anticipation)
    hit_speed = peak_speed(anticipation, impact)
    speed_ratio = hit_speed / max(1e-6, ant_speed)
    if ant_speed > 1e-6 and speed_ratio < 3.0:
        findings.append(_finding(
            "anticipation_vs_impact", clip, bone=track_meta["bone"],
            metric="impact_to_anticipation_peak_speed", value=round(speed_ratio, 4), threshold={"min": 3.0},
            suggested_patch={"operation": "tighten_action_spacing", "preserve_anticipation_pose": True},
        ))
    return findings


def check_spacing(animation: dict, clip: str, fps: int = 30,
                  constant_run_frames: int = 5, spike_factor: float = 8.0) -> list[dict]:
    findings = []
    duration = _duration(animation)
    if duration <= 0:
        return findings
    times = _frames(duration, fps)
    for meta in _scalar_tracks(animation):
        values = [sample_timeline(meta["track"], meta["prop"], at, meta["component"]) for at in times]
        velocities = [(b - a) * fps for a, b in zip(values, values[1:])]
        if len(velocities) >= constant_run_frames:
            for index in range(len(velocities) - constant_run_frames + 1):
                run = velocities[index:index + constant_run_frames]
                if min(abs(value) for value in run) < 1e-5:
                    continue
                if not (all(value > 0 for value in run) or all(value < 0 for value in run)):
                    continue
                spread = max(run) - min(run)
                scale = max(abs(median(run)), 1e-6)
                if abs(spread) / scale <= 0.03:
                    findings.append(_finding(
                        "spacing_acceleration", clip, bone=meta["bone"], metric="constant_velocity_run_frames",
                        value=constant_run_frames, threshold={"max": constant_run_frames - 1},
                        suggested_patch={"operation": "add_or_strengthen_ease", "around_frame": index + constant_run_frames // 2},
                    ))
                    break
        accelerations = [(b - a) * fps for a, b in zip(velocities, velocities[1:])]
        nonzero = [abs(value) for value in accelerations if abs(value) > 1e-5]
        if len(nonzero) >= 3:
            med = median(nonzero)
            peak = max(nonzero)
            if med > 1e-6 and peak > spike_factor * med:
                findings.append(_finding(
                    "spacing_acceleration", clip, bone=meta["bone"], metric="acceleration_spike_ratio",
                    value=round(peak / med, 4), threshold={"max": spike_factor},
                    suggested_patch={"operation": "insert_inbetween_or_adjust_bezier", "preserve_extreme": True},
                ))
    return findings


def _sample_local(animation: dict, bone: dict, at: float) -> tuple[float, float, float]:
    x, y = float(bone.get("x", 0)), float(bone.get("y", 0))
    rotation = float(bone.get("rotation", 0))
    channels = animation.get("bones", {}).get(bone["name"], {})
    if channels.get("translate"):
        x += sample_timeline(channels["translate"], "translate", at, 0)
        y += sample_timeline(channels["translate"], "translate", at, 1)
    if channels.get("rotate"):
        rotation += sample_timeline(channels["rotate"], "rotate", at, 0)
    return x, y, rotation


def _world_pose(data: dict, animation: dict, at: float) -> dict[str, tuple[float, float, float]]:
    by_name = {bone["name"]: bone for bone in data.get("bones", [])}
    memo = {}

    def world(name: str):
        if name in memo:
            return memo[name]
        bone = by_name[name]
        lx, ly, lrot = _sample_local(animation, bone, at)
        parent = bone.get("parent")
        if parent and parent in by_name:
            px, py, prot = world(parent)
            angle = math.radians(prot)
            wx = px + math.cos(angle) * lx - math.sin(angle) * ly
            wy = py + math.sin(angle) * lx + math.cos(angle) * ly
            result = (wx, wy, prot + lrot)
        else:
            result = (lx, ly, lrot)
        memo[name] = result
        return result

    for name in by_name:
        world(name)
    return memo


def trace_bone_tip(data: dict, clip: str, bone_name: str, fps: int = 30) -> list[tuple[float, float]]:
    animation = data.get("animations", {}).get(clip, {})
    duration = _duration(animation)
    bone = next((item for item in data.get("bones", []) if item.get("name") == bone_name), None)
    if not bone or duration <= 0:
        return []
    length = float(bone.get("length", 0))
    points = []
    for at in _frames(duration, fps):
        x, y, rotation = _world_pose(data, animation, at)[bone_name]
        angle = math.radians(rotation)
        points.append((x + math.cos(angle) * length, y + math.sin(angle) * length))
    return points


def check_arcs(data: dict, clip: str, fps: int = 30) -> list[dict]:
    animation = data.get("animations", {}).get(clip, {})
    findings = []
    candidates = []
    for bone in data.get("bones", []):
        name = bone.get("name", "")
        channels = animation.get("bones", {}).get(name, {})
        if not channels:
            continue
        meaningful_name = any(token in name.casefold() for token in ("hand", "head", "prop", "weapon", "tip", "foot"))
        if meaningful_name or (float(bone.get("length", 0)) > 0 and channels.get("rotate")):
            candidates.append(bone)
    for bone in candidates:
        points = trace_bone_tip(data, clip, bone["name"], fps)
        if len(points) < 5:
            continue
        signs = []
        for a, b, c in zip(points, points[1:], points[2:]):
            v1 = (b[0] - a[0], b[1] - a[1])
            v2 = (c[0] - b[0], c[1] - b[1])
            n1, n2 = math.hypot(*v1), math.hypot(*v2)
            if n1 < 1e-5 or n2 < 1e-5:
                continue
            cross = v1[0] * v2[1] - v1[1] * v2[0]
            if abs(cross) < 0.02 * n1 * n2:
                continue
            signs.append(1 if cross > 0 else -1)
        sign_flips = sum(a != b for a, b in zip(signs, signs[1:]))
        if sign_flips >= 2:
            findings.append(_finding(
                "arcs", clip, bone=bone["name"], metric="curvature_sign_flips", value=sign_flips,
                threshold={"max": 1}, suggested_patch={"operation": "smooth_tip_path", "preserve_key_extremes": True},
            ))
        path_length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        chord = math.dist(points[0], points[-1])
        rotate = animation.get("bones", {}).get(bone["name"], {}).get("rotate", [])
        if rotate:
            rotations = [sample_timeline(rotate, "rotate", at) for at in _frames(_duration(animation), fps)]
            rotation_range = max(rotations) - min(rotations)
            if rotation_range >= 15 and chord > 1 and path_length / chord < 1.03:
                findings.append(_finding(
                    "arcs", clip, bone=bone["name"], metric="straight_path_while_rotating",
                    value=round(path_length / chord, 4), threshold={"min_path_to_chord": 1.03},
                    suggested_patch={"operation": "restore_rotational_arc", "check_pivot": True},
                ))
    return findings


def check_drag_order(animation: dict, clip: str, secondary_chains: dict, fps: int = 30) -> list[dict]:
    findings = []
    duration = _duration(animation)
    if duration <= 0:
        return findings
    times = _frames(duration, fps)
    for chain_name, meta in (secondary_chains or {}).items():
        bones = meta.get("bones", []) if isinstance(meta, dict) else list(meta)
        measured = []
        for bone in bones:
            track = animation.get("bones", {}).get(bone, {}).get("rotate")
            if not track:
                continue
            start = sample_timeline(track, "rotate", 0.0)
            samples = [(at, abs(sample_timeline(track, "rotate", at) - start)) for at in times]
            peak_time, amplitude = max(samples, key=lambda item: item[1])
            measured.append((bone, peak_time, amplitude))
        for left, right in zip(measured, measured[1:]):
            if right[1] - left[1] < 1 / fps:
                findings.append(_finding(
                    "drag_order", clip, bone=right[0], metric="peak_delay_frames",
                    value=round((right[1] - left[1]) * fps, 3), threshold={"min": 1},
                    suggested_patch={"operation": "delay_child_peak", "after_bone": left[0], "target_delay_frames": [2, 4]},
                ))
            if right[2] + 1e-5 < left[2]:
                findings.append(_finding(
                    "drag_order", clip, bone=right[0], metric="tip_amplitude_ratio",
                    value=round(right[2] / max(1e-6, left[2]), 4), threshold={"min": 1.0},
                    suggested_patch={"operation": "increase_tip_amplitude", "relative_to": left[0]},
                ))
    return findings


def check_overshoot(animation: dict, clip: str, spec: dict) -> list[dict]:
    beats = _beat_times(spec)
    target_time = beats.get("contact") or beats.get("impact") or beats.get("peak") or beats.get("launch")
    overshoot_time = beats.get("overshoot") or beats.get("secondary_peak")
    track_meta = _dominant_track(animation)
    if target_time is None or overshoot_time is None or not track_meta:
        return []
    track, prop, component = track_meta["track"], track_meta["prop"], track_meta["component"]
    start = sample_timeline(track, prop, 0.0, component)
    target = sample_timeline(track, prop, target_time, component)
    overshoot = sample_timeline(track, prop, overshoot_time, component)
    move = target - start
    if abs(move) < 1e-5:
        return []
    ratio = abs(overshoot - target) / abs(move)
    if ratio <= 0.25:
        return []
    return [_finding(
        "overshoot_ratio", clip, bone=track_meta["bone"], metric="overshoot_ratio",
        value=round(ratio, 4), threshold={"preferred": [0.05, 0.20], "max": 0.25},
        suggested_patch={"operation": "reduce_overshoot_amplitude", "target_ratio": [0.08, 0.15]},
    )]


def audit(data_or_path, motion_plan: dict | None = None,
          secondary_chains: dict | None = None, fps: int = 30) -> dict:
    """Run deterministic critic checks #2, #3, #5, #7 and #8.

    Returns machine-actionable findings for the repair loop. No render/VLM judgement is
    included here; those belong to the later critic slices.
    """
    if isinstance(data_or_path, (str, Path)):
        with open(data_or_path, encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        data = data_or_path
    findings = []
    clips = (motion_plan or {}).get("clips", {})
    for clip, animation in data.get("animations", {}).items():
        spec = clips.get(clip, {})
        if spec:
            findings.extend(check_anticipation_impact(animation, clip, spec, fps))
            findings.extend(check_overshoot(animation, clip, spec))
        findings.extend(check_spacing(animation, clip, fps))
        findings.extend(check_arcs(data, clip, fps))
        if secondary_chains:
            findings.extend(check_drag_order(animation, clip, secondary_chains, fps))
    high = sum(item["severity"] == "high" for item in findings)
    score = max(0, 100 - high * 20 - (len(findings) - high) * 6)
    return {
        "version": 1,
        "checks": [2, 3, 5, 7, 8],
        "fps": fps,
        "ok": high == 0,
        "score": score,
        "findings": findings,
        "note": "Deterministic curve critic only; silhouette/FX render QA and blind VLM action-read are separate stages.",
    }
