"Stamp animation for one emphatic word.\n\nA large blurred stamp falls from above, lands with its frame and a brief shake, then remains visible. Use it for a short supplied statement such as APPROVED or SOLD OUT only when factually supported. An optional smaller line provides context. The delay parameter sets the impact time; the stamp then holds through the item's end. Check readability and the actual impact frame before adding sound."

from __future__ import annotations

import json
from functools import lru_cache

from visuals import engine as E

TEMPLATE = {'id': 'stamp', 'label': 'Stamp', 'description': 'A tilted framed word lands like a stamp with optional smaller text and a brief shake.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'notes': 'Validate the parameters, keep text readable, and inspect the exported entrance and exit.', 'category': 'graphics', 'version': 1, 'params': {'text': {'type': 'text', 'default': 'APPROVED', 'max_len': 22, 'required_text': True, 'label': 'Text'}, 'subtitle': {'type': 'text', 'default': '', 'max_len': 28, 'label': 'Subtitle'}, 'delay': {'type': 'number', 'default': 0.12, 'min': 0, 'max': 30, 'label': 'Delay (0–30)'}, 'angle': {'type': 'number', 'default': -7.0, 'min': -20, 'max': 20, 'label': 'Angle (-20–20)'}, 'style': {'type': 'choice', 'choices': ['frame', 'filled'], 'default': 'frame', 'label': 'Style'}, 'shake': {'type': 'bool', 'default': True, 'label': 'Shake'}}, 'default_duration': 2.0, 'default_region': 'center', 'sfx': [{'at': 0.12, 'name': 'bass_drop', 'volume': 0.9}], 'example': {'text': 'THERE IS A COST', 'subtitle': 'hosting, AI, support', 'angle': -5}}

FONT = "sans_black"
SUB_FONT = "text_bold"
MAX_SIZE = 150.0
MIN_SIZE = 44.0
DROP = 0.20           # Seconds from the drop to impact.
SHAKE = 0.22          # Seconds of shaking after impact.
SCALE_INITIAL = 2.6


@lru_cache(maxsize=64)
def _layout(params_json: str, box: E.Box) -> dict:
    p = json.loads(params_json)
    text_value = p["text"].strip()
    sub = p["subtitle"].strip()
    pad_x, pad_y = 46.0, 30.0
    disp = max(220.0, box.w * 0.86) - 2 * pad_x
    size = min(MAX_SIZE, E.fit_text(text_value.upper(), FONT, disp, MAX_SIZE, MIN_SIZE))
    cap = E.measure("H", FONT, size).cap
    width_value = E.measure(text_value.upper(), FONT, size).width
    sub_size = sub_cap = 0.0
    sub_w = 0.0
    if sub:
        sub_size = min(size * 0.34, E.fit_text(sub, SUB_FONT, disp, size * 0.34, 20.0))
        sub_cap = E.measure("H", SUB_FONT, sub_size).cap
        sub_w = E.measure(sub, SUB_FONT, sub_size).width
    content_h_value = cap + (sub_cap + size * 0.28 if sub else 0.0)
    w = min(box.w - 8.0, max(width_value, sub_w) + 2 * pad_x)
    h = content_h_value + 2 * pad_y
    return {"size": size, "cap": cap, "w": w, "h": h, "pad_y": pad_y,
            "sub_size": sub_size, "sub_cap": sub_cap, "text": text_value.upper(), "sub": sub,
            "border": max(7.0, size * 0.085), "radius": max(14.0, size * 0.16)}


def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    cv = E.Canvas(box)
    t = frame_index / fps + 1.0 / fps
    L = _layout(json.dumps(params, sort_keys=True), box)
    matches = float(params["delay"])
    if t < matches - DROP:
        return cv
    drop_value = E.clamp01((t - (matches - DROP)) / DROP)          # Zero at the top, one at impact.
    scale_value = SCALE_INITIAL + (1.0 - SCALE_INITIAL) * (drop_value ** 2.2)   # Accelerate into the landing (in_quart).
    opac = min(1.0, drop_value * 1.8)
    shake_value = 0.0
    if params["shake"] and 0 <= t - matches < SHAKE:
        f = E.clamp01((t - matches) / SHAKE)
        shake_value = (1.0 - f) * 5.0 * (1 if int((t - matches) * fps) % 2 == 0 else -1)
    x = box.cx - L["w"] / 2.0
    y = box.cy - L["h"] / 2.0
    filled_value = params["style"] == "filled"
    with cv.group(opacity=opac, scale=scale_value, pivot=(box.cx, box.cy),
                  rotate=float(params["angle"]) + shake_value * 0.25, dx=shake_value):
        if filled_value:
            cv.rect(x, y, L["w"], L["h"], radius=L["radius"], fill="accent",
                    shadow=E.Shadow(blur=18.0, dy=8.0, opacity=0.45))
        else:
            if box.theme.style != "glass":     # paper, pop, neon: the stamp lands on a card, not on the video
                cv.card(x, y, L["w"], L["h"], radius=L["radius"], border=False)
            cv.rect(x, y, L["w"], L["h"], radius=L["radius"], fill=None, stroke="accent",
                    stroke_width=L["border"], shadow=E.Shadow(blur=14.0, dy=6.0, opacity=0.4))
        color_value = "on_accent" if filled_value else "accent"
        base = y + L["pad_y"] + L["cap"]
        cv.text(box.cx, base, L["text"], FONT, L["size"], color=color_value, anchor="ms",
                stroke=0.0, shadow=E.Shadow(blur=10.0, dy=4.0, opacity=0.35))
        if L["sub"]:
            cv.text(box.cx, base + L["size"] * 0.28 + L["sub_cap"], L["sub"], SUB_FONT, L["sub_size"],
                    color=color_value, opacity=0.9, anchor="ms")
    return cv


def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    return [{"frame": E.frame_of(float(params["delay"]), fps), "name": "bass_drop", "volume": 0.9,
             "why": "stamp lands"}]


def settle(params: dict, fps: int) -> float:
    return float(params["delay"]) + SHAKE + 0.05


def suggested_duration(params: dict) -> float:
    return round(max(TEMPLATE["default_duration"], float(params["delay"]) + 1.4), 2)


def ink(params: dict, box: E.Box) -> tuple[float, float, float, float]:
    L = _layout(json.dumps(params, sort_keys=True), box)
    return (box.cx - L["w"] / 2.0, box.cy - L["h"] / 2.0, box.cx + L["w"] / 2.0, box.cy + L["h"] / 2.0)
