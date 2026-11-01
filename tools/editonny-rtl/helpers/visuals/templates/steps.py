"""steps: a 2 to 4 step flow. Numbered circles light up one by one, joined by arrows that draw themselves.

Frame 0 already shows the card and the steps as faint placeholders (the shape of the flow). Each step then
pops in the accent color with a pulse ring and its name rises underneath (or beside it, vertically). The
arrow into a step draws during the moment before that step lights up, so the eye travels along the flow.
Timing: `delay` + `stagger`, or `times` (one second per step, counted from the item start).
"""

from __future__ import annotations

import json
from functools import lru_cache

from visuals import engine as E
from visuals.engine import VisualError

TEMPLATE = {'id': 'steps', 'label': 'Steps', 'description': 'Two to four numbered steps connect through animated arrows. Each step highlights in sequence, horizontally or vertically.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'notes': 'Validate the parameters, keep text readable, and inspect the exported entrance and exit.', 'category': 'graphics', 'version': 1, 'params': {'items': {'type': 'list', 'item': {'type': 'text', 'max_len': 20, 'required_text': True, 'label': 'Item'}, 'min_items': 2, 'max_items': 4, 'default': ['Purchased', 'Accessed', 'Used'], 'label': 'Items'}, 'title': {'type': 'text', 'default': '', 'max_len': 36, 'label': 'Title'}, 'direction': {'type': 'choice', 'choices': ['horizontal', 'vertical'], 'default': 'horizontal', 'label': 'Direction'}, 'icon': {'type': 'choice', 'choices': ['number', 'check'], 'default': 'number', 'label': 'Icon'}, 'delay': {'type': 'number', 'default': 0.1, 'min': 0, 'max': 30, 'label': 'Delay (0–30)'}, 'stagger': {'type': 'number', 'default': 0.6, 'min': 0.15, 'max': 5, 'label': 'Stagger (0.15–5)'}, 'times': {'type': 'list', 'item': {'type': 'number', 'min': 0, 'max': 60, 'label': 'Item (0–60)'}, 'nullable': True, 'default': None, 'min_items': 2, 'max_items': 4, 'label': 'Times'}, 'size': {'type': 'choice', 'choices': ['normal', 'large'], 'default': 'normal', 'label': 'Size'}, 'card': {'type': 'bool', 'default': True, 'label': 'Card'}}, 'default_duration': 3.0, 'default_region': [90, 240, 900, 330], 'sfx': [{'at': 0.1, 'name': 'pop'}, {'at': 0.7, 'name': 'pop'}, {'at': 1.3, 'name': 'pop'}], 'example': {'items': ['Buy', 'Connect', 'Use'], 'title': '*That* simple:'}}

LABEL_FONT = "text_bold"
TITLE_FONT = "sans_black"
NUM_FONT = "sans_black"
BASE_R = {"normal": 56.0, "large": 66.0}
BASE_LABEL = {"normal": 46.0, "large": 54.0}
MIN_LABEL = 30.0
MAX_COL = 270.0

SKELETON = -0.08       # the empty markers start before frame 0, so frame 0 always shows them
ENTER = 0.5
POP = 0.42             # circle pop (out_back)
ARROW = 0.34           # longest arrow draw, ending when the next step lights up
RING = 0.6             # pulse ring after a pop
LABEL_AT = 0.06
LABEL_IN = 0.34


def _times(p: dict) -> list[float]:
    n = len(p["items"])
    times = p.get("times")
    if times is None:
        return [p["delay"] + i * p["stagger"] for i in range(n)]
    if len(times) != n:
        raise VisualError(f'steps.params.times requires one time per step ({n} steps, got {len(times)})')
    for i in range(1, n):
        if times[i] < times[i - 1]:
            raise VisualError(f'steps.params.times must increase; step {i + 1} appears at {times[i]:g} s, before step {i} at {times[i - 1]:g} s)')
    return [float(v) for v in times]


def validate(params: dict) -> None:
    """One time per item, in order: refused when the item is planned, not only when it renders."""
    _times(params)


def warnings(params: dict, box: E.Box) -> list[str]:
    L = _layout(json.dumps(params, sort_keys=True), box)
    if L["vertical"] and L["R"] < BASE_R[params["size"]] * 0.8:
        return [f'vertical steps were reduced because the region is only {box.h:.0f} pixels high; use a taller region or fewer steps']
    return []


def _arrow_window(times: list[float], i: int) -> tuple[float, float]:
    """(start, duration) of the arrow that leads into step i (i >= 1)."""
    end = times[i]
    start = max(times[i - 1] + 0.12, end - ARROW)
    return min(start, end), max(0.0, end - start)


def _title_block(title: str, width: float, size: float) -> tuple[float, float, float]:
    """(size, cap, width) of the title line."""
    if not title:
        return 0.0, 0.0, 0.0
    plain = " ".join(w for w, _ in E.parse_accent(title))
    ts = E.fit_text(plain, TITLE_FONT, width, size, 26.0)
    return ts, E.measure("H", TITLE_FONT, ts).cap, E.measure(plain, TITLE_FONT, ts).width


def _fit_labels(items: list[str], size: float, width: float, max_lines: int) -> tuple[float, list[list[str]]]:
    """Largest common label size (<= size) where every label fits `width` in at most max_lines."""
    s = size
    while True:
        wraps = [E.wrap_text(it, LABEL_FONT, s, width, max_lines) for it in items]
        widest = max(E.measure(ln, LABEL_FONT, s).width for lines in wraps for ln in lines)
        if widest <= width + 0.5 or s <= MIN_LABEL:
            break
        s = max(MIN_LABEL, s * 0.95)
    if widest > width + 0.5:          # one long word: shrink below the floor rather than overflow
        s = min(E.fit_text(ln, LABEL_FONT, width, s, 20.0) for lines in wraps for ln in lines)
        wraps = [E.wrap_text(it, LABEL_FONT, s, width, max_lines) for it in items]
    return s, wraps


@lru_cache(maxsize=64)
def _layout(params_json: str, box: E.Box) -> dict:
    p = json.loads(params_json)
    items = p["items"]
    n = len(items)
    card = p["card"]
    title = p["title"].strip()
    pad_x = 36.0 if card else 4.0
    pad_y = 38.0 if card else 4.0
    R = BASE_R[p["size"]]
    lab = BASE_LABEL[p["size"]]
    rtl = any(E.has_arabic(it) for it in items) or E.has_arabic(title)
    steps = []
    if p["direction"] == "horizontal":
        col = min(MAX_COL, (box.w - 2 * pad_x) / n)
        R = min(R, col * 0.30)
        ts, tcap, tw = _title_block(title, box.w - 2 * pad_x, lab * 1.12)
        title_h = tcap + lab * 0.62 if title else 0.0
        lab_size, wraps = _fit_labels(items, lab, col - 14.0, 2)
        cap = E.measure("H", LABEL_FONT, lab_size).cap
        line_h = lab_size * 1.14
        lines = max(len(w) for w in wraps)
        gap = R * 0.40
        # a label block leaves room for descenders (p, g, ç)
        lab_h = cap + (lines - 1) * line_h + lab_size * 0.22
        content_h = title_h + 2 * R + gap + lab_h
        needed = content_h + 2 * pad_y
        if needed > box.h:                       # a short region: shrink circles and labels together
            k = max(0.55, (box.h - 2 * pad_y) / content_h)
            R *= k
            gap *= k
            lab_size, wraps = _fit_labels(items, max(MIN_LABEL * 0.9, lab_size * k), col - 14.0, 2)
            cap = E.measure("H", LABEL_FONT, lab_size).cap
            line_h = lab_size * 1.14
            lab_h = cap + (lines - 1) * line_h + lab_size * 0.22
            ts, tcap, tw = _title_block(title, box.w - 2 * pad_x, min(ts, lab_size * 1.12))
            title_h = tcap + lab_size * 0.62 if title else 0.0
            content_h = title_h + 2 * R + gap + lab_h
        content_w = col * n
        card_w = min(box.w, max(content_w, tw) + 2 * pad_x)
        card_h = content_h + 2 * pad_y
        cx0 = (box.w - card_w) / 2.0
        cy0 = (box.h - card_h) / 2.0
        top = cy0 + pad_y
        left = (box.w - content_w) / 2.0
        cy = top + title_h + R
        for i in range(n):
            col_idx = (n - 1 - i) if rtl else i
            x = left + col * (col_idx + 0.5)
            steps.append({"cx": x, "cy": cy, "lines": wraps[i], "anchor": "ms",
                          "lx": x, "ly": cy + R + gap + cap})
        g = R * 0.30
        if rtl:
            arrows = [((steps[i - 1]["cx"] - R - g, cy), (steps[i]["cx"] + R + g, cy)) for i in range(1, n)]
        else:
            arrows = [((steps[i - 1]["cx"] + R + g, cy), (steps[i]["cx"] - R - g, cy)) for i in range(1, n)]
        title_pos = (box.w / 2.0, top + tcap, "ms")
    else:
        ts, tcap, tw = _title_block(title, box.w - 2 * pad_x, lab * 1.12)
        title_h = tcap + lab * 0.70 if title else 0.0
        ratio = 1.2                              # arrow length in circle radii
        content_h = title_h + n * 2 * R + (n - 1) * ratio * R
        if content_h > box.h - 2 * pad_y:
            # a short region: tighter card padding, then shorter arrows, then smaller circles (never below 45%)
            pad_y = min(pad_y, 18.0)
            avail = box.h - 2 * pad_y - title_h
            k = avail / ((n * 2 + (n - 1) * ratio) * R)
            if k < 0.6:
                ratio = 0.6
                k = avail / ((n * 2 + (n - 1) * ratio) * R)
            R *= max(0.45, min(1.0, k))
        arrow_len = ratio * R
        content_h = title_h + n * 2 * R + (n - 1) * arrow_len
        gap = max(R * 0.45, 20.0)
        lab_size = min(lab * 1.12, max(MIN_LABEL, R * 1.05))
        lab_w = box.w - 2 * pad_x - 2 * R - gap
        lab_size, wraps = _fit_labels(items, lab_size, lab_w, 1)
        cap = E.measure("H", LABEL_FONT, lab_size).cap
        widest = max(E.measure(w[0], LABEL_FONT, lab_size).width for w in wraps)
        content_w = 2 * R + gap + widest
        card_w = min(box.w, max(content_w, tw) + 2 * pad_x, box.w)
        card_w = max(card_w, min(box.w, 420.0 if card else 0.0))
        card_h = content_h + 2 * pad_y
        cx0 = (box.w - card_w) / 2.0
        cy0 = (box.h - card_h) / 2.0
        top = cy0 + pad_y
        left = cx0 + pad_x
        if not title or tw <= content_w:
            left = (box.w - content_w) / 2.0
        x = left + R
        for i in range(n):
            cy = top + title_h + R + i * (2 * R + arrow_len)
            steps.append({"cx": x, "cy": cy, "lines": wraps[i], "anchor": "ls",
                          "lx": x + R + gap, "ly": cy + cap / 2.0})
        g = R * 0.16
        arrows = [((x, steps[i - 1]["cy"] + R + g), (x, steps[i]["cy"] - R - g)) for i in range(1, n)]
        title_pos = (cx0 + card_w / 2.0, top + tcap, "ms")
    return {
        "steps": steps, "arrows": arrows, "R": R, "lab_size": lab_size, "line_h": lab_size * 1.14,
        "card_box": (cx0, cy0, card_w, card_h), "radius": min(40.0, card_h * 0.24), "card": card,
        "title": title, "title_size": ts, "title_pos": title_pos, "times": _times(p), "icon": p["icon"],
        "vertical": p["direction"] == "vertical",
    }


def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    cv = E.Canvas(box)
    t = frame_index / fps
    t_in = t + 1.0 / fps
    L = _layout(json.dumps(params, sort_keys=True), box)
    card = L["card"]
    R = L["R"]
    times = L["times"]
    ink = "text" if card else "white"
    outline = {} if card else {"stroke": L["lab_size"] * 0.07, "stroke_color": "black", "shadow": E.TEXT_SHADOW}
    circle_shadow = E.Shadow(blur=12.0, dy=5.0, opacity=0.35 if card else 0.55)
    arrow_shadow = None if card else E.Shadow(blur=6.0, dy=2.0, opacity=0.5)
    arrow_color = "muted" if card else (255, 255, 255, 235)

    alpha = E.eased(t_in, 0.0, 0.18, "out_quad")
    scale = 0.92 + 0.08 * E.out_back(E.progress(t_in, 0.0, ENTER), 1.4)
    rise = 26.0 * (1.0 - E.eased(t_in, 0.0, 0.45))
    with cv.group(opacity=alpha, scale=scale, dy=rise):
        if card:
            x, y, w, h = L["card_box"]
            cv.card(x, y, w, h, radius=L["radius"])
        if L["title"]:
            tx, ty, anchor = L["title_pos"]
            cv.rich_text(tx, ty, L["title"], TITLE_FONT, L["title_size"], color=ink, anchor=anchor, **outline)
        # arrows first: circles sit on top of their ends
        for i, (a, b) in enumerate(L["arrows"], start=1):
            start, dur = _arrow_window(times, i)
            prog = E.eased(t, start, dur, "in_out_cubic") if dur > 0 else (1.0 if t >= start else 0.0)
            if prog > 0:
                cv.arrow(a[0], a[1], b[0], b[1], width=max(4.0, R * 0.13), color=arrow_color, progress=prog,
                         head=max(14.0, R * 0.34), shadow=arrow_shadow)
        for i, st in enumerate(L["steps"]):
            ti = times[i]
            cx, cy = st["cx"], st["cy"]
            pop_p = E.progress(t_in, ti, POP)
            # placeholder: the faint step that is waiting
            sk = E.out_back(E.progress(t_in, SKELETON + 0.06 * i, 0.36), 1.6)
            if sk > 0.01 and pop_p < 1.0:
                with cv.group(scale=max(0.01, sk), pivot=(cx, cy)):
                    if card:
                        cv.circle(cx, cy, R, fill="track")
                    else:
                        cv.circle(cx, cy, R, fill=(0, 0, 0, 120), stroke=(255, 255, 255, 200),
                                  stroke_width=max(2.0, R * 0.06))
                    if L["icon"] == "number":
                        cv.text(cx, cy, str(i + 1), NUM_FONT, R * 0.95, color=ink, opacity=0.45, anchor="mc")
            if pop_p <= 0:
                continue
            # pulse ring
            e = E.eased(t, ti, RING, "out_cubic")
            if 0 < e < 1:
                cv.ring(cx, cy, R * (1.0 + 0.5 * e), max(2.0, R * 0.08 * (1.0 - e) + 1.0), color="accent",
                        opacity=0.7 * (1.0 - e))
            with cv.group(scale=max(0.01, E.out_back(pop_p, 2.0)), pivot=(cx, cy)):
                cv.circle(cx, cy, R, fill="accent", shadow=circle_shadow)
                if L["icon"] == "number":
                    cv.text(cx, cy, str(i + 1), NUM_FONT, R * 0.95, color="on_accent", anchor="mc")
            if L["icon"] == "check":
                draw = E.eased(t, ti + 0.08, 0.26, "in_out_cubic")
                if draw > 0:
                    cv.check(cx, cy + R * 0.03, R * 0.98, width=R * 0.19, color="on_accent", progress=draw)
            la = E.eased(t, ti + LABEL_AT, LABEL_IN, "out_cubic")
            if cv.visible(la):
                off = 14.0 * (1.0 - la)
                for k, ln in enumerate(st["lines"]):
                    if L["vertical"]:
                        cv.text(st["lx"] - off, st["ly"], ln, LABEL_FONT, L["lab_size"], color=ink, opacity=la,
                                anchor="ls", **outline)
                    else:
                        cv.text(st["lx"], st["ly"] + k * L["line_h"] + off, ln, LABEL_FONT, L["lab_size"],
                                color=ink, opacity=la, anchor="ms", **outline)
    return cv


def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    return [{"frame": E.frame_of(ti, fps), "name": "pop", "why": f'passo {i + 1}'}
            for i, ti in enumerate(_times(params))]


def suggested_duration(params: dict) -> float:
    """Last step, its label and about 1.5 s to read the flow."""
    return round(max(TEMPLATE["default_duration"], _times(params)[-1] + max(POP, LABEL_AT + LABEL_IN) + 1.5), 2)


def settle(params: dict, fps: int) -> float:
    times = _times(params)
    n = len(times)
    return max(ENTER, SKELETON + 0.06 * (n - 1) + 0.36,
               times[-1] + max(POP, RING, LABEL_AT + LABEL_IN, 0.08 + 0.26)) + 0.05
