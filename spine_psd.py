"""Shared PSD metadata and extraction helpers for GPT Spine V6.

One parser owns group selection, visible leaf traversal, safe attachment naming,
blend metadata, bounds and optional explicit [pivot] markers. Inspection can stay
metadata-only; extraction composites pixels exactly once for the build.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterator

from PIL import Image


PIVOT_TAG = "[pivot]"


@dataclass
class PsdLeaf:
    attachment: str
    layer_path: str
    bbox: tuple[int, int, int, int]
    blend_mode: object
    role: str
    layer: object


@dataclass
class PsdScan:
    width: int
    height: int
    groups: list[str]
    leaves: list[PsdLeaf]
    pivot_markers: dict[str, tuple[float, float]]
    source_group: str


def safe_part_name(path: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.strip()).strip("_.") or "part"
    candidate, index = base, 2
    while candidate.casefold() in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate.casefold())
    return candidate


def semantic_role(path: str) -> str:
    value = path.casefold().replace("-", "_").replace(" ", "_")
    rules = (
        ("face", ("face", "eye", "mouth", "smile", "brow")),
        ("hand_money", ("money_hand", "hand_money", "cash_hand", "arm_money")),
        ("hand_steering", ("steering_hand", "hand_steering", "wheel_hand")),
        ("steering_wheel", ("steering", "wheel")),
        ("coin_fx", ("coin", "gold")),
        ("shine_fx", ("shine", "sweep", "windshield")),
        ("glow_fx", ("glow", "burst", "ray", "spark", "flash")),
        ("title", ("mega_win", "title", "headline")),
        ("number_display", ("number", "digits", "counter", "amount")),
        ("vehicle", ("jeep", "vehicle", "car", "truck")),
        ("driver", ("driver", "body", "torso", "head")),
    )
    for role, words in rules:
        if any(word in value for word in words):
            return role
    return "unclassified"


def _walk(parent, prefix="") -> Iterator[tuple[str, object]]:
    for layer in parent:
        name = layer.name.strip()
        path = f"{prefix}/{name}" if prefix else name
        if not layer.is_visible():
            continue
        if layer.is_group():
            yield from _walk(layer, path)
        else:
            yield path, layer


def _all_groups(parent, prefix="") -> list[str]:
    output = []
    for layer in parent:
        name = layer.name.strip()
        path = f"{prefix}/{name}" if prefix else name
        if layer.is_group():
            output.append(path)
            output.extend(_all_groups(layer, path))
    return output


def find_group(psd, requested: str):
    wanted = requested.strip().casefold().strip("/")
    matches = []

    def visit(parent, prefix=""):
        for layer in parent:
            name = layer.name.strip()
            path = f"{prefix}/{name}" if prefix else name
            if layer.is_group():
                if name.casefold() == wanted or path.casefold() == wanted:
                    matches.append((path, layer))
                visit(layer, path)

    visit(psd)
    if not matches:
        raise ValueError(f"PSD group {requested!r} was not found")
    if len(matches) > 1:
        raise ValueError(
            f"PSD group {requested!r} is ambiguous; use one of: " +
            ", ".join(path for path, _ in matches)
        )
    return matches[0]


def scan_psd(psd_path: str, source_group: str = "") -> PsdScan:
    """Read structure and blend metadata without compositing layer pixels."""
    from psd_tools import PSDImage

    psd = PSDImage.open(psd_path)
    parent, prefix = psd, ""
    if source_group:
        prefix, parent = find_group(psd, source_group)
    used: set[str] = set()
    leaves: list[PsdLeaf] = []
    pivots: dict[str, tuple[float, float]] = {}
    for path, layer in _walk(parent, prefix):
        bbox = tuple(layer.bbox)
        if bbox == (0, 0, 0, 0):
            continue
        leaf_name = layer.name.strip()
        if leaf_name.casefold().endswith(PIVOT_TAG):
            base = leaf_name[: -len(PIVOT_TAG)].strip()
            left, top, right, bottom = bbox
            pivots[base.casefold()] = ((left + right) / 2.0, (top + bottom) / 2.0)
            continue
        attachment = safe_part_name(path, used)
        leaves.append(PsdLeaf(
            attachment=attachment,
            layer_path=path,
            bbox=bbox,
            blend_mode=getattr(layer, "blend_mode", None),
            role=semantic_role(path),
            layer=layer,
        ))
    return PsdScan(
        width=psd.width,
        height=psd.height,
        groups=_all_groups(psd),
        leaves=leaves,
        pivot_markers=pivots,
        source_group=source_group,
    )


def inspection(psd_path: str, source_group: str = "") -> dict:
    scan = scan_psd(psd_path, source_group)
    layers, roles = [], {}
    for leaf in scan.leaves:
        l, t, r, b = leaf.bbox
        w, h = r - l, b - t
        cx = (l + r) / 2 - scan.width / 2
        cy = scan.height - (t + b) / 2
        roles.setdefault(leaf.role, []).append(leaf.attachment)
        layers.append({
            "attachment": leaf.attachment,
            "layer_path": leaf.layer_path,
            "role": leaf.role,
            "bounds": [cx, cy, float(w), float(h)],
            "blend_mode": getattr(leaf.blend_mode, "name", str(leaf.blend_mode or "normal")).casefold(),
        })
    warnings = []
    if not source_group and scan.groups:
        warnings.append("PSD contains groups; use source_group when only one folder is the asset set")
    if roles.get("unclassified"):
        warnings.append(f"{len(roles['unclassified'])} layers need explicit art-direction roles")
    return {
        "canvas": [scan.width, scan.height],
        "source_group": source_group or None,
        "groups": scan.groups,
        "layers": layers,
        "roles": roles,
        "pivot_markers": scan.pivot_markers,
        "count": len(layers),
        "warnings": warnings,
    }


def extract(psd_path: str, out_dir: str, source_group: str = ""):
    """Composite selected PSD leaves once and return rig parts/draw metadata."""
    scan = scan_psd(psd_path, source_group)
    images_dir = os.path.join(out_dir, "images")
    os.makedirs(images_dir, exist_ok=True)
    parts, draw, blend_metadata = {}, [], {}
    for leaf in scan.leaves:
        try:
            image = leaf.layer.composite()
        except (ImportError, ModuleNotFoundError):
            image = leaf.layer.topil()
        if image is None:
            continue
        image = image.convert("RGBA")
        image.save(os.path.join(images_dir, f"{leaf.attachment}.png"))
        l, t, r, b = leaf.bbox
        w, h = r - l, b - t
        cx = (l + r) / 2 - scan.width / 2
        cy = scan.height - (t + b) / 2
        pivot = None
        short_name = leaf.layer_path.rsplit("/", 1)[-1].casefold()
        if short_name in scan.pivot_markers:
            px, py = scan.pivot_markers[short_name]
            pivot = [px - scan.width / 2, scan.height - py]
        parts[leaf.attachment] = {
            "x": cx, "y": cy, "w": float(w), "h": float(h), "file": leaf.attachment,
            "layer_path": leaf.layer_path, "role": leaf.role, "pivot": pivot,
        }
        blend_metadata[leaf.attachment] = {
            "layer_path": leaf.layer_path,
            "blend_mode": leaf.blend_mode,
        }
        draw.append(leaf.attachment)
    return parts, draw, images_dir, blend_metadata
