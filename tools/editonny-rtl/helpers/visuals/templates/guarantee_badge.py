'Circular guarantee badge with a serrated outline, ribbon, and duration. It rotates and scales into place, the ribbon drops behind it, and the number pulses after entry. Use only guarantee terms supported by the supplied material.'

from __future__ import annotations

import json
import math
from functools import lru_cache

from visuals import engine as E

TEMPLATE = {'id': 'guarantee_badge', 'label': 'Guarantee Badge', 'description': 'A circular serrated badge and ribbon display a duration and caption. The badge rotates into place and the number bounces.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'notes': 'Validate the parameters, keep text readable, and inspect the exported entrance and exit.', 'category': 'graphics', 'version': 1, 'params': {'number': {'type': 'text', 'default': '7', 'max_len': 4, 'required_text': True, 'label': 'Number'}, 'unit': {'type': 'text', 'default': 'days', 'max_len': 10, 'label': 'Unit'}, 'title': {'type': 'text', 'default': 'GUARANTEE', 'max_len': 16, 'label': 'Title'}, 'captions': {'type': 'text', 'default': '', 'max_len': 34, 'label': 'Captions'}, 'delay': {'type': 'number', 'default': 0.15, 'min': 0, 'max': 30, 'label': 'Delay (0–30)'}, 'tape': {'type': 'bool', 'default': True, 'label': 'Tape'}}, 'default_duration': 2.4, 'default_region': 'center', 'sfx': [{'at': 0.15, 'name': 'pop'}, {'at': 0.55, 'name': 'success', 'volume': 0.8}], 'example': {'number': '30', 'unit': 'days', 'title': 'GUARANTEE', 'captions': 'to try it *risk free*'}}

NUM_FONT = "sans_black"
TXT_FONT = "text_bold"
ENTERS = 0.42           # Seal entrance: rotate and scale.
JUMP = 0.30            # Small number bounce.
ENDPOINTS = 18            # Serrated edge teeth.
PRE = 0.16             # Start scaling slightly before zero so the opening frame is never empty.


@lru_cache(maxsize=64)
def _layout(params_json: str, box: E.Box) -> dict:
    p = json.loads(params_json)
    caption = p["captions"].strip()
    plain = " ".join(w for w, _ in E.parse_accent(caption)) if caption else ""
    leg_size = leg_cap = 0.0
    if caption:
        leg_size = E.fit_text(plain, TXT_FONT, box.w * 0.9, 52.0, 22.0)
        leg_cap = E.measure("H", TXT_FONT, leg_size).cap
    space_value = box.h - (leg_cap * 2.2 if caption else 0.0)
    r = max(90.0, min(box.w * 0.42, space_value * 0.42))
    num_size = E.fit_text(p["number"], NUM_FONT, r * 1.25, r * 1.12, 40.0)
    uni_size = min(r * 0.30, E.fit_text(p["unit"], TXT_FONT, r * 1.3, r * 0.30, 16.0))
    tit_size = min(r * 0.24, E.fit_text(p["title"], TXT_FONT, r * 1.3, r * 0.24, 14.0))
    cy = box.cy - (leg_cap * 1.1 if caption else 0.0)
    return {"r": r, "cx": box.cx, "cy": cy, "num_size": num_size, "uni_size": uni_size,
            "tit_size": tit_size, "leg_size": leg_size, "leg_cap": leg_cap, "captions": caption,
            "num_cap": E.measure("H", NUM_FONT, num_size).cap}


def _serration(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    'Badge outline with rounded teeth around the circle.'
    pts = []
    steps_value = ENDPOINTS * 6
    for i in range(steps_value):
        a = 2 * math.pi * i / steps_value
        radius_value = r * (1.0 + 0.055 * math.cos(ENDPOINTS * a))
        pts.append((cx + radius_value * math.cos(a), cy + radius_value * math.sin(a)))
    return pts


def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    cv = E.Canvas(box)
    t = frame_index / fps + 1.0 / fps
    L = _layout(json.dumps(params, sort_keys=True), box)
    start_value = max(0.0, float(params["delay"]) - PRE)
    if t < start_value:
        return cv
    ent = E.eased(t, start_value, ENTERS + PRE, "out_back")
    if ent <= 0:
        return cv
    opac = min(1.0, E.eased(t, start_value, (ENTERS + PRE) * 0.5, "out_quad") * 1.4)
    rotation_value = -26.0 * (1.0 - E.clamp01(ent))
    cx, cy, r = L["cx"], L["cy"], L["r"]
    with cv.group(opacity=opac, scale=0.35 + 0.65 * min(1.15, ent), pivot=(cx, cy), rotate=rotation_value):
        if params["tape"]:
            width_value = r * 0.42
            for side_value in (-1, 1):
                x0 = cx + side_value * r * 0.36
                pts = [(x0 - width_value / 2, cy + r * 0.2), (x0 + width_value / 2, cy + r * 0.2),
                       (x0 + width_value / 2 + side_value * r * 0.14, cy + r * 1.25),
                       (x0 + side_value * r * 0.07, cy + r * 1.0),
                       (x0 - width_value / 2 + side_value * r * 0.14, cy + r * 1.25)]
                cv.polygon(pts, fill="danger", shadow=E.Shadow(blur=12.0, dy=6.0, opacity=0.35))
        cv.polygon(_serration(cx, cy, r), fill="accent",
                   shadow=E.Shadow(blur=16.0, dy=8.0, opacity=0.4))
        cv.ring(cx, cy, r * 0.86, max(3.0, r * 0.035), color="on_accent", opacity=0.55)
        cv.text(cx, cy - r * 0.34, params["title"], TXT_FONT, L["tit_size"], color="on_accent",
                anchor="mc", opacity=0.9, upper=True)
        matches = start_value + (ENTERS + PRE) * 0.7
        jump_value = 1.0 + 0.10 * (1.0 - E.clamp01((t - matches) / JUMP)) if matches <= t < matches + JUMP else 1.0
        with cv.group(scale=jump_value, pivot=(cx, cy + r * 0.05)):
            cv.text(cx, cy + L["num_cap"] * 0.5 + r * 0.02, params["number"], NUM_FONT, L["num_size"],
                    color="on_accent", anchor="ms")
        cv.text(cx, cy + r * 0.56, params["unit"], TXT_FONT, L["uni_size"], color="on_accent",
                anchor="mc", opacity=0.95)
    if L["captions"]:
        la = E.eased(t, start_value + (ENTERS + PRE) * 0.6, 0.3, "out_cubic")
        if la > 0:
            y = cy + r * 1.35 + L["leg_cap"]
            words_value = E.parse_accent(L["captions"])
            widths_value = [E.measure(w, TXT_FONT, L["leg_size"]).width for w, _ in words_value]
            space_value = (E.measure("a a", TXT_FONT, L["leg_size"]).width
                      - E.measure("aa", TXT_FONT, L["leg_size"]).width)
            total = sum(widths_value) + space_value * max(0, len(words_value) - 1)
            x = cx - total / 2.0
            for (word_value, accent_value), width_value in zip(words_value, widths_value):
                color_value = "accent" if accent_value else "text"
                cv.text(x, y + (1.0 - la) * 12.0, word_value, TXT_FONT, L["leg_size"], color=color_value,
                        opacity=la, anchor="ls", stroke=3.0, stroke_color=(0, 0, 0, 140))
                x += width_value + space_value
    return cv


def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    d = float(params["delay"])
    return [{"frame": E.frame_of(d, fps), "name": "pop", "why": "seal entrance"},
            {"frame": E.frame_of(d + ENTERS * 0.7, fps), "name": "success", "volume": 0.8,
             "why": "duration appears"}]


def settle(params: dict, fps: int) -> float:
    return float(params["delay"]) + ENTERS + JUMP + 0.05


def suggested_duration(params: dict) -> float:
    return round(max(TEMPLATE["default_duration"], float(params["delay"]) + ENTERS + JUMP + 1.2), 2)
