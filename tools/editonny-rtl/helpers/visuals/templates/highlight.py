"""highlight: a marker stroke that draws itself at a place on the screen: a circle around it, an underline
under it or an arrow pointing at it, with an optional handwritten note.

The place is `target` = [x, y, w, h] in design px of the output canvas (1080x1920 for vertical video),
measured on a frame of the video. The template never moves (`movable: False`), draws on the whole safe
area ("screen") and uses a large bleed so a target near the edges is still covered.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache

from visuals import engine as E
from visuals.engine import VisualError

TEMPLATE = {'id': 'highlight', 'label': 'Highlight', 'description': 'A marker stroke draws a circle, underline, or arrow around a measured target, with an optional handwritten label.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'notes': 'The annotation stays at its measured target and never moves away from a face. Use a stationary interval; it does not track a moving target.', 'category': 'annotations', 'version': 1, 'params': {'target': {'type': 'box', 'default': [290, 1040, 500, 160], 'label': 'Target'}, 'mode': {'type': 'choice', 'choices': ['circle', 'underline', 'arrow'], 'default': 'circle', 'label': 'Mode'}, 'side': {'type': 'choice', 'choices': ['auto', 'bottom', 'top', 'left', 'right'], 'default': 'auto', 'label': 'Side'}, 'label': {'type': 'text', 'default': '', 'max_len': 22, 'label': 'Label'}, 'thickness': {'type': 'number', 'default': 12, 'min': 4, 'max': 32, 'label': 'Thickness (4–32)'}, 'duration': {'type': 'number', 'default': 0.6, 'min': 0.2, 'max': 3, 'label': 'Duration (0.2–3)'}, 'shadow': {'type': 'bool', 'default': True, 'label': 'Shadow'}}, 'default_duration': 2.0, 'default_region': 'screen', 'movable': False, 'bleed': 160, 'sfx': [{'at': 0.15, 'name': 'whoosh', 'volume': 0.7}], 'example': {'target': [240, 900, 600, 200], 'mode': 'circle', 'label': 'look here'}}

LABEL_FONT = "marker"
LABEL_SIZE = 72.0
EDGE = 14.0            # the stroke keeps this far from the screen edges
LABEL_IN = 0.32
SIDES = ("bottom", "top", "left", "right")
MIN_ROOM = 90.0        # a forced arrow side with less room than this falls back to the side with the most room
LABEL_APPEAR = {"circle": 0.55, "underline": 0.7, "arrow": 0.0}   # fraction of the draw time


def _smooth(x: float) -> float:
    x = E.clamp01(x)
    return x * x * (3 - 2 * x)


def _draw_curve(u: float) -> float:
    """A hand stroke: leaves fast and eases into the end."""
    return 0.6 * E.out_cubic(u) + 0.4 * E.in_out_cubic(u)


def _taper(n: int, start: float = 0.55, end: float = 0.55) -> list[float]:
    """Width factor per point: thin at the pen-down, full in the middle, thinning at the lift."""
    out = []
    for k in range(n):
        u = k / max(1, n - 1)
        out.append((start + (1 - start) * _smooth(u / 0.14)) * (1 - (1 - end) * _smooth((u - 0.82) / 0.18)))
    return out


def _rotate(points, cx: float, cy: float, deg: float):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in points]


def _check_target(target, design) -> None:
    x, y, w, h = target
    dw, dh = design
    if x + w <= 0 or y + h <= 0 or x >= dw or y >= dh:
        raise VisualError(f'highlight.params.target [{x:g}, {y:g}, {w:g}, {h:g}] lies outside the canvas ({dw:.0f}x{dh:.0f}); measure the target in a video frame')


def _pick_side(side: str, target, safe) -> tuple[str, dict]:
    x, y, w, h = target
    sx0, sy0, sx1, sy1 = safe
    room = {"bottom": sy1 - (y + h), "top": y - sy0, "left": x - sx0, "right": sx1 - (x + w)}
    if side != "auto" and room[side] >= MIN_ROOM:
        return side, room
    # vertical arrows read more naturally on a phone: horizontal ones need clearly more room to win
    weight = {"bottom": 1.0, "top": 0.95, "left": 0.8, "right": 0.8}
    return max(SIDES, key=lambda k: (room[k] * weight[k], -SIDES.index(k))), room


@lru_cache(maxsize=64)
def _geometry(params_json: str, box: E.Box) -> dict:
    """Stroke paths and the label place, in CANVAS design px (converted to box-local when drawing)."""
    p = json.loads(params_json)
    target = p["target"]
    dw, dh = box.design
    _check_target(target, (dw, dh))
    tx, ty, tw, th = target
    cx, cy = tx + tw / 2.0, ty + th / 2.0
    thick = p["thickness"]
    seed = E.stable_seed("accent", tuple(target), p["mode"])
    safe = E.safe_area(box.canvas)
    label = p["label"].strip()
    lsize = 0.0
    lw = lcap = 0.0
    if label:
        lsize = E.fit_text(label, LABEL_FONT, (safe[2] - safe[0]) * 0.9, LABEL_SIZE, 30.0)
        m = E.measure(label, LABEL_FONT, lsize)
        lw, lcap = m.width, m.cap
    strokes = []           # (points, width factors, start fraction, end fraction of the draw time)
    label_at = None        # (x center, y baseline, rotation, appear time fraction)

    if p["mode"] == "circle":
        pad = thick * 0.5 + 14.0
        rx = tw / 2.0 * 1.22 + pad
        ry = th / 2.0 * 1.22 + pad
        rx = min(rx, max(tw / 2.0 + thick, min(cx, dw - cx) - EDGE))
        ry = min(ry, max(th / 2.0 + thick, min(cy, dh - cy) - EDGE))
        pts = E.ellipse_points(cx, cy, rx, ry, start_deg=-128.0, sweep_deg=382.0, n=72, wobble=0.035, seed=seed)
        pts = _rotate(pts, cx, cy, -3.0)
        strokes.append((pts, _taper(len(pts), 0.5, 0.45), 0.0, 1.0))
        if label:
            above = cy - ry - 22.0
            if above - lcap - 30.0 >= safe[1]:
                label_at = (cx + rx * 0.25, above, -5.0, LABEL_APPEAR["circle"])
            else:
                label_at = (cx + rx * 0.25, cy + ry + 22.0 + lcap, -5.0, LABEL_APPEAR["circle"])
    elif p["mode"] == "underline":
        gap = thick * 0.6 + 12.0
        x0 = max(EDGE, tx - tw * 0.03)
        x1 = min(dw - EDGE, tx + tw * 1.03)
        rise = min(24.0, (x1 - x0) * 0.045)
        y = ty + th + gap + rise          # the rising end stays under the target
        pts = E.bezier_points((x0, y + rise * 0.15), ((x0 + x1) / 2.0, y + rise * 0.75), (x1, y - rise), n=40)
        pts = E.wobble_points(pts, max(0.8, thick * 0.10), seed=seed, step=14.0)
        strokes.append((pts, _taper(len(pts), 0.55, 0.5), 0.0, 0.78))
        # a second, shorter swash under the first: the classic marker emphasis
        y2 = y + thick * 1.35 + 6.0
        a2, b2 = x0 + (x1 - x0) * 0.10, x0 + (x1 - x0) * 0.86
        pts2 = E.bezier_points((a2, y2 + rise * 0.1), ((a2 + b2) / 2.0, y2 + rise * 0.55), (b2, y2 - rise * 0.7), n=30)
        pts2 = E.wobble_points(pts2, max(0.8, thick * 0.08), seed=seed + 1, step=14.0)
        strokes.append((pts2, [w * 0.72 for w in _taper(len(pts2), 0.55, 0.45)], 0.62, 1.0))
        if label:
            below = y2 + thick + 22.0 + lcap
            if below + 20.0 <= safe[3]:
                label_at = (cx, below, -4.0, LABEL_APPEAR["underline"])
            else:
                label_at = (cx, ty - 24.0, -4.0, LABEL_APPEAR["underline"])
    else:
        side, room = _pick_side(p["side"], target, safe)
        g = 16.0 + thick
        label_room = (lcap + 40.0) if label else 10.0
        length = max(110.0, min(300.0, room[side] - g - label_room))
        right_half = cx > dw / 2.0
        if side in ("bottom", "top"):
            sign = 1.0 if side == "bottom" else -1.0
            tip = (cx, (ty + th + g) if side == "bottom" else (ty - g))
            lateral = -1.0 if right_half else 1.0
            tail = (tip[0] + lateral * length * 0.42, tip[1] + sign * length)
        else:
            sign = -1.0 if side == "left" else 1.0
            tip = ((tx - g) if side == "left" else (tx + tw + g), cy)
            down = 1.0 if room["bottom"] >= room["top"] else -1.0
            tail = (tip[0] + sign * length, tip[1] + down * length * 0.38)
        # keep the tail on the screen (shorten along the arrow)
        tail = (min(max(tail[0], safe[0] + EDGE), safe[2] - EDGE), min(max(tail[1], EDGE), dh - EDGE))
        L = math.dist(tail, tip)
        mx, my = (tail[0] + tip[0]) / 2.0, (tail[1] + tip[1]) / 2.0
        nx, ny = -(tip[1] - tail[1]) / max(L, 1e-6), (tip[0] - tail[0]) / max(L, 1e-6)
        bend = 0.22 * L * (1.0 if (side in ("bottom", "right")) != right_half else -1.0)
        shaft = E.bezier_points(tail, (mx + nx * bend, my + ny * bend), tip, n=40)
        shaft = E.wobble_points(shaft, max(0.6, thick * 0.08), seed=seed, step=16.0)
        shaft[-1] = tip
        strokes.append((shaft, _taper(len(shaft), 0.6, 1.0), 0.0, 0.72))
        (x_a, y_a), (x_b, y_b) = shaft[-4], shaft[-1]
        ang = math.atan2(y_b - y_a, x_b - x_a)
        hl = max(thick * 3.4, 40.0)
        w1 = (tip[0] + hl * math.cos(ang + math.pi - math.radians(30)), tip[1] + hl * math.sin(ang + math.pi - math.radians(30)))
        w2 = (tip[0] + hl * math.cos(ang + math.pi + math.radians(33)), tip[1] + hl * math.sin(ang + math.pi + math.radians(33)))
        strokes.append(([w1, tip, w2], [0.8, 1.0, 0.85], 0.66, 1.0))
        if label:
            # on the far side of the tail from the shaft, nudged away from the tip on horizontal arrows
            lx = tail[0] if side in ("bottom", "top") else tail[0] + sign * lw * 0.25
            if tail[1] < tip[1] - 1.0:
                label_at = (lx, tail[1] - 24.0, -4.0, LABEL_APPEAR["arrow"])
            else:
                label_at = (lx, tail[1] + 22.0 + lcap, -4.0, LABEL_APPEAR["arrow"])
    if label_at is not None:
        lx, ly, rot, appear = label_at
        lx = min(max(lx, safe[0] + lw / 2.0 + 6.0), safe[2] - lw / 2.0 - 6.0)
        ly = min(max(ly, EDGE + lcap), dh - EDGE)
        label_at = (lx, ly, rot, appear)
    return {"strokes": strokes, "label": label, "label_size": lsize, "label_cap": lcap, "label_at": label_at,
            "thick": thick}


def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    cv = E.Canvas(box)
    t = frame_index / fps
    t_in = t + 1.0 / fps
    G = _geometry(json.dumps(params, sort_keys=True), box)
    dur = params["duration"]
    shadow = E.Shadow(blur=8.0, dy=3.0, opacity=0.45) if params["shadow"] else None
    thick = G["thick"]
    u = E.clamp01(t_in / dur)
    for pts, widths, a, b in G["strokes"]:
        prog = _draw_curve(E.remap(u, a, b))
        if prog <= 0:
            continue
        local = [box.from_canvas(x, y) for x, y in pts]
        cv.polyline(local, thick, color="accent", progress=prog, widths=[thick * w for w in widths],
                    shadow=shadow)
    if G["label_at"] is not None:
        lx, ly, rot, appear = G["label_at"]
        la = E.progress(t_in, appear * dur, LABEL_IN)
        if la > 0:
            x, y = box.from_canvas(lx, ly)
            pivot = (x, y - G["label_cap"] / 2.0)
            with cv.group(opacity=E.out_quad(min(1.0, la * 1.6)), scale=0.85 + 0.15 * E.out_back(la, 2.0),
                          pivot=pivot, rotate=rot):
                cv.text(x, y, G["label"], LABEL_FONT, G["label_size"], color="accent", anchor="ms",
                        stroke=4.0, stroke_color=(0, 0, 0, 150),
                        shadow=E.Shadow(blur=8.0, dy=3.0, opacity=0.5) if params["shadow"] else None)
    return cv


def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    dur = params["duration"]
    if params["mode"] == "arrow":
        cues = [{"at": dur * 0.2, "name": "whoosh", "volume": 0.7, "why": "arrow drawing"},
                {"at": dur * 0.72, "name": "pop", "volume": 0.8, "why": "arrow tip"}]
    else:
        cues = [{"at": dur * 0.25, "name": "whoosh", "volume": 0.7, "why": 'drawing stroke'}]
    return [{"frame": E.frame_of(c.pop("at"), fps), **c} for c in cues]


def suggested_duration(params: dict) -> float:
    return round(max(TEMPLATE["default_duration"], params["duration"] + LABEL_IN + 1.4), 2)


def settle(params: dict, fps: int) -> float:
    """The drawing runs on t + 1/fps: it is complete one frame before `end`; half a frame of margin for rounding."""
    end = params["duration"]
    if params["label"].strip():
        end = max(end, LABEL_APPEAR[params["mode"]] * params["duration"] + LABEL_IN)
    return end - 0.5 / fps + 0.01
