"""Bridge the shared V6 PSD parser into the legacy rig builder.

The legacy rig builder understands PhotoshopToSpine-style export folders.  This
module turns a PSD scan into that exact layout so the production build path uses
one PSD parser without forcing a risky rewrite of the mature rig engine.
"""
from __future__ import annotations

import json
import os
import shutil

import spine_psd


def prepare_build_source(psd_path: str, work_dir: str, source_group: str = "") -> dict:
    root = os.path.join(work_dir, ".gpt-spine", "prepared-source")
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(root, exist_ok=True)

    parts, draw, images_dir, blend_metadata = spine_psd.extract(
        psd_path, root, source_group or "",
    )
    if not parts:
        raise ValueError(f"no visible PSD parts found in {psd_path}")

    attachments = {}
    slots = []
    for name in draw:
        part = parts[name]
        slots.append({"name": name, "bone": "root", "attachment": name})
        attachments[name] = {
            name: {
                "x": float(part["x"]),
                "y": float(part["y"]),
                "width": float(part["w"]),
                "height": float(part["h"]),
            }
        }

    layout = {
        "skeleton": {"spine": "4.2"},
        "bones": [{"name": "root"}],
        "slots": slots,
        "skins": {"default": attachments},
    }
    layout_path = os.path.join(root, "prepared-layout.json")
    with open(layout_path, "w", encoding="utf-8") as handle:
        json.dump(layout, handle, separators=(",", ":"))

    return {
        "source": root,
        "layout": layout_path,
        "images_dir": images_dir,
        "parts": parts,
        "draw": draw,
        "blend_metadata": blend_metadata,
    }
