"""counter: a number that counts up (or down) to its value, with prefix, suffix and a label.

REFERENCE TEMPLATE. Copy this file to write a new one:
  1. TEMPLATE describes the template (id = file name) and its params; the registry checks it.
  2. render(frame_index, fps, params, box) draws ONE frame on a fresh Canvas and returns it.
     Time is frame_index / fps. `params` is already complete and checked. `box` gives the place
     (design px, 1080 wide), the theme colors and the item length (box.frames).
  3. Optional: sfx(params, fps, frames), suggested_duration(params), settle(params, fps).
Keep the layout math in a cached function (it runs once per item, not once per frame).
"""

from __future__ import annotations

import json
from functools import lru_cache

from visuals import engine as E

TEMPLATE = {'id': 'counter', 'label': 'Counter', 'description': 'A number counts to a target inside a card, with optional prefix, suffix, label, and progress bar. A small bounce marks completion.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'category': 'graphics', 'version': 1, 'params': {'value': {'type': 'number', 'default': 1000, 'min': -10000000000000.0, 'max': 10000000000000.0, 'label': 'Value (-10000000000000.0–10000000000000.0)'}, 'start_value': {'type': 'number', 'default': 0, 'min': -10000000000000.0, 'max': 10000000000000.0, 'label': 'Start Value (-10000000000000.0–10000000000000.0)'}, 'prefix': {'type': 'text', 'default': '', 'max_len': 6, 'label': 'Prefix'}, 'suffix': {'type': 'text', 'default': '', 'max_len': 12, 'label': 'Suffix'}, 'decimals': {'type': 'integer', 'default': None, 'nullable': True, 'min': 0, 'max': 3, 'label': 'Decimals (0–3)'}, 'label': {'type': 'text', 'default': '', 'max_len': 40, 'label': 'Label'}, 'duration': {'type': 'number', 'default': 1.4, 'min': 0.3, 'max': 10, 'label': 'Duration (0.3–10)'}, 'delay': {'type': 'number', 'default': 0.15, 'min': 0, 'max': 5, 'label': 'Delay (0–5)'}, 'ease': {'type': 'choice', 'choices': ['smooth', 'linear'], 'default': 'smooth', 'label': 'Ease'}, 'size': {'type': 'choice', 'choices': ['normal', 'large'], 'default': 'normal', 'label': 'Size'}, 'card': {'type': 'bool', 'default': True, 'label': 'Card'}, 'bar': {'type': 'bool', 'default': True, 'label': 'Bar'}}, 'default_duration': 3.0, 'default_region': 'center', 'sfx': [{'at': 0.0, 'name': 'pop'}, {'at': 0.15, 'name': 'count', 'volume': 1.3}], 'example': {'value': 1250, 'suffix': 'students', 'label': 'in the first class'}, 'notes': 'Validate the parameters, keep text readable, and inspect the exported entrance and exit.'}

LAND = 0.32          # seconds of the landing pop


def _decimals(p: dict) -> int:
    if p["decimals"] is not None:
        return int(p["decimals"])
    money = E.looks_like_money(p["prefix"], p["suffix"])
    d = max(E.auto_decimals(p["value"]), E.auto_decimals(p["start_value"]))
    return 2 if money and d else d


def _affix_scale(text: str) -> float:
    """Small symbols (%, x, +) sit almost at number size; words and currencies are smaller."""
    return 0.82 if len(text.strip()) <= 1 else 0.52


@lru_cache(maxsize=64)
def _layout(params_json: str, box: E.Box) -> dict:
    p = json.loads(params_json)
    dec = _decimals(p)
    prefix, suffix = p["prefix"].strip(), p["suffix"].strip()
    label = p["label"].strip()
    card = p["card"]
    theme = box.theme
    pad_x = 64.0 if card else 12.0
    max_size = 200.0 if p["size"] == "large" else 150.0

    widest = max((E.format_number(v, dec) for v in (p["value"], p["start_value"])),
                 key=lambda s: E.measure(s, "sans_black", 100, tabular=True).width)

    def block(size: float) -> dict:
        num_w = E.measure(widest, "sans_black", size, tabular=True).width
        pre_size, suf_size = size * _affix_scale(prefix), size * _affix_scale(suffix)
        pre_w = E.measure(prefix, "sans_black", pre_size).width if prefix else 0.0
        suf_w = E.measure(suffix, "sans_black", suf_size).width if suffix else 0.0
        gap_p = size * (0.02 if len(prefix) <= 1 else 0.14) if prefix else 0.0
        gap_s = size * (0.02 if len(suffix) <= 1 else 0.14) if suffix else 0.0
        return {"size": size, "num_w": num_w, "pre_size": pre_size, "suf_size": suf_size, "pre_w": pre_w,
                "suf_w": suf_w, "gap_p": gap_p, "gap_s": gap_s,
                "width": pre_w + gap_p + num_w + gap_s + suf_w}

    avail = box.w - 2 * pad_x
    b = block(max_size)
    if b["width"] > avail:
        b = block(max(44.0, max_size * avail / b["width"] * 0.995))
    size = b["size"]
    cap = E.measure("0", "sans_black", size).cap
    label_size = max(36.0, min(54.0, size * 0.40))
    if label:
        label_size = E.fit_text(label, "text_semibold", avail, label_size, 24.0)
    label_cap = E.measure("H", "text_semibold", label_size).cap if label else 0.0
    bar_h = max(8.0, size * 0.07) if p["bar"] else 0.0
    gap_bar = size * 0.30 if p["bar"] else 0.0
    gap_label = (size * 0.26 if p["bar"] else size * 0.30) if label else 0.0
    pad_y = max(52.0, size * 0.50) if card else 0.0
    content_h = cap + gap_bar + bar_h + gap_label + label_cap + (label_size * 0.25 if label else 0.0)
    card_h = content_h + 2 * pad_y
    card_w = min(box.w, max(b["width"], E.measure(label, "text_semibold", label_size).width if label else 0.0)
                 + 2 * pad_x)
    top = (box.h - card_h) / 2.0
    x0 = (box.w - b["width"]) / 2.0
    baseline = top + pad_y + cap
    bar_y = baseline + gap_bar
    number_color = "accent"
    if card and E.contrast(theme.accent, theme.card) < 2.4:
        number_color = "text"          # a light accent on a white card: the bar keeps the accent
    return {
        "dec": dec, "prefix": prefix, "suffix": suffix, "label": label, "card": card,
        "size": size, "cap": cap, "block": b, "x0": x0, "baseline": baseline,
        "bar_y": bar_y, "bar_h": bar_h, "label_size": label_size,
        "label_base": bar_y + bar_h + gap_label + label_cap,
        "card_box": ((box.w - card_w) / 2.0, top, card_w, card_h), "number_color": number_color,
        "radius": min(48.0, card_h * 0.22),
    }


def _count(p: dict, t: float) -> float:
    fn = "linear" if p["ease"] == "linear" else "out_expo"
    return E.eased(t, p["delay"], p["duration"], fn)


def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    cv = E.Canvas(box)
    t = frame_index / fps
    L = _layout(json.dumps(params, sort_keys=True), box)
    b = L["block"]
    t_end = params["delay"] + params["duration"]

    # entrance of the whole card: fade, rise and a small overshoot. It starts one frame early, so
    # frame 0 (where the entrance sound lands) already shows the card.
    t_in = t + 1.0 / fps
    alpha = E.eased(t_in, 0.0, 0.18, "out_quad")
    scale = 0.88 + 0.12 * E.out_back(E.progress(t_in, 0.0, 0.5), 1.4)
    rise = 36.0 * (1.0 - E.eased(t_in, 0.0, 0.45))
    with cv.group(opacity=alpha, scale=scale, dy=rise):
        cx0, cy0, cw, ch = L["card_box"]
        if L["card"]:
            cv.card(cx0, cy0, cw, ch, radius=L["radius"])

        p = _count(params, t)
        value = params["start_value"] + (params["value"] - params["start_value"]) * p
        digits = E.format_number(value, L["dec"])
        size = L["size"]
        prefix, pre_w = L["prefix"], b["pre_w"]
        if prefix and digits.startswith("-"):
            # Put the negative sign before the currency symbol.
            prefix, digits = ("-" if prefix == "+" else "-" + prefix), digits[1:]
            pre_w = E.measure(prefix, "sans_black", b["pre_size"]).width
        num_w = E.measure(digits, "sans_black", size, tabular=True).width
        # the line is centered on what it shows now; tabular digits keep it still once the digit count is final
        x_pre = (cv.w - (pre_w + b["gap_p"] + num_w + b["gap_s"] + b["suf_w"])) / 2.0
        x_num = x_pre + pre_w + b["gap_p"]
        base = L["baseline"]
        outline = {} if L["card"] else {"stroke": size * 0.05, "stroke_color": "black", "shadow": E.TEXT_SHADOW}

        land = E.pulse(t, t_end - 0.02, LAND)
        pivot = (cv.cx, base - L["cap"] / 2.0)

        def number() -> None:
            cv.text(x_num, base, digits, "sans_black", size, color=L["number_color"], tabular=True, **outline)
            if prefix:
                cv.text(x_pre, base, prefix, "sans_black", b["pre_size"], color="text", opacity=0.92, **outline)
            if L["suffix"]:
                cv.text(x_num + num_w + b["gap_s"], base, L["suffix"], "sans_black", b["suf_size"], color="text",
                        opacity=0.92, **outline)

        with cv.group(scale=1.0 + 0.07 * land, pivot=pivot):
            if land > 0.02:
                cv.glow(number, radius=30, opacity=0.5 * land)
            else:
                number()

        if L["bar_h"]:
            bw, bh = b["width"], L["bar_h"]
            bx = L["x0"]
            track = "track" if L["card"] else (0, 0, 0, 110)
            cv.pill(bx, L["bar_y"], bw, bh, fill=track)
            if p > 0:
                cv.pill(bx, L["bar_y"], max(bh, bw * p), bh, fill="accent")

        if L["label"]:
            la = E.eased(t, 0.12, 0.35)
            if cv.visible(la):
                cv.text(cv.cx, L["label_base"] + 14.0 * (1.0 - la), L["label"], "text_semibold", L["label_size"],
                        color="muted" if L["card"] else "text", opacity=la, anchor="ms",
                        **({} if L["card"] else {"stroke": L["label_size"] * 0.08, "stroke_color": "black"}))
    return cv


def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    cues = [{"at": 0.0, "name": "pop", "why": "input"}]
    if params["value"] != params["start_value"]:
        start, dur = params["delay"], params["duration"]
        # the ticking follows the count: once for the fast "smooth" count, every 0.6 s for a linear one
        steps = 1 if params["ease"] != "linear" else max(1, int(dur / 0.6 + 0.5))
        for k in range(steps):
            cues.append({"at": start + k * 0.6, "name": "count", "volume": 1.3, "why": "counting"})
    return [{"frame": E.frame_of(c.pop("at"), fps), **c} for c in cues]


def suggested_duration(params: dict) -> float:
    """Count, landing, and about 1.5 s to read the number."""
    return round(max(TEMPLATE["default_duration"], params["delay"] + params["duration"] + LAND + 1.5), 2)


def settle(params: dict, fps: int) -> float:
    """From here on every frame is the same (until the exit)."""
    return params["delay"] + params["duration"] + LAND + 0.05
