"""Curve-aware Spine timeline authoring primitives.

Spine curves contain absolute time/value control points, so copying CSS bezier
numbers directly is incorrect. This module converts named motion-design eases
per component and makes staggered, overshooting animation deterministic.
"""
from __future__ import annotations

EASE = {
    "linear": (0.25, 0.25, 0.75, 0.75),
    "out": (0.33, 1.0, 0.68, 1.0),
    "in": (0.32, 0.0, 0.67, 0.0),
    "inout": (0.65, 0.0, 0.35, 1.0),
    "sine": (0.37, 0.0, 0.63, 1.0),
    "outback": (0.34, 1.45, 0.64, 1.0),
    "expo": (0.16, 1.0, 0.30, 1.0),
}

PROPERTIES = {
    "rotate": ("value",), "translate": ("x", "y"),
    "scale": ("x", "y"), "alpha": ("value",),
}


def curve(t0: float, v0: float, t1: float, v1: float, ease: str) -> list[float]:
    x1, y1, x2, y2 = EASE[ease]
    dt, dv = t1 - t0, v1 - v0
    return [round(t0 + x1 * dt, 4), round(v0 + y1 * dv, 4),
            round(t0 + x2 * dt, 4), round(v0 + y2 * dv, 4)]


def timeline(prop: str, keys: list[tuple]) -> list[dict]:
    """Convert ``(time, values..., ease?)`` tuples to Spine JSON keys."""
    names = PROPERTIES[prop]
    output = []
    for index, raw in enumerate(keys):
        ease = raw[-1] if isinstance(raw[-1], str) else None
        values = list(raw[1:-1] if ease else raw[1:])
        if prop == "alpha":
            values = [round(round(max(0, min(1, float(value))) * 255) / 255, 6)
                      for value in values]
        if len(values) != len(names):
            raise ValueError(f"{prop} expects {len(names)} values, got {len(values)}")
        key = {} if raw[0] == 0 else {"time": round(float(raw[0]), 4)}
        key.update({name: round(float(value), 4) for name, value in zip(names, values)})
        if ease == "stepped":
            key["curve"] = "stepped"
        elif ease and ease != "linear" and index + 1 < len(keys):
            nxt = keys[index + 1]
            next_values = list(nxt[1:-1] if isinstance(nxt[-1], str) else nxt[1:])
            if prop == "alpha":
                next_values = [round(round(max(0, min(1, float(value))) * 255) / 255, 6)
                               for value in next_values]
            key["curve"] = sum((curve(float(raw[0]), a, float(nxt[0]), b, ease)
                                for a, b in zip(values, next_values)), [])
        output.append(key)
    return output


class Motion:
    """Small animation composer that emits ordinary Spine 4.2 JSON."""

    def __init__(self):
        self.data: dict = {}

    def bone(self, name: str, prop: str, keys: list[tuple]):
        self.data.setdefault("bones", {}).setdefault(name, {})[prop] = timeline(prop, keys)
        return self

    def slot_alpha(self, name: str, keys: list[tuple]):
        self.data.setdefault("slots", {}).setdefault(name, {})["alpha"] = timeline("alpha", keys)
        return self

    def attachment(self, name: str, keys: list[tuple[float, str | None]]):
        values = []
        for at, attachment in keys:
            key = {} if at == 0 else {"time": round(float(at), 4)}
            key["name"] = attachment
            values.append(key)
        self.data.setdefault("slots", {}).setdefault(name, {})["attachment"] = values
        return self

    def merge(self, other: "Motion"):
        for kind, targets in other.data.items():
            for target, timelines in targets.items():
                self.data.setdefault(kind, {}).setdefault(target, {}).update(timelines)
        return self
