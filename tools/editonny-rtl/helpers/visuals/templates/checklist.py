"""checklist: a list on a card where each item slides in and gets a check mark, one after the other.

Frame 0 already shows the card, the title and the empty markers (the skeleton of the list), so the
entrance sound lands on something even when the first item comes later. Each item then slides in and its
marker fills with the accent and draws a check. After the last check the card border glows once.
Timing: `delay` + `stagger`, or `times` (one second per item, counted from the item start).
"""

from __future__ import annotations

import json
from functools import lru_cache

from visuals import engine as E
from visuals.engine import VisualError

TEMPLATE = {'id': 'checklist', 'label': 'Checklist', 'description': 'Items slide into a card and receive accent-colored checkmarks one at a time. An optional title and final shine complete the list.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'notes': 'Validate the parameters, keep text readable, and inspect the exported entrance and exit.', 'category': 'graphics', 'version': 1, 'params': {'items': {'type': 'list', 'item': {'type': 'text', 'max_len': 32, 'required_text': True, 'label': 'Item'}, 'min_items': 1, 'max_items': 6, 'default': ['Instant access', 'WhatsApp support', '7-day guarantee'], 'label': 'Items'}, 'title': {'type': 'text', 'default': '', 'max_len': 36, 'label': 'Title'}, 'delay': {'type': 'number', 'default': 0.25, 'min': 0, 'max': 30, 'label': 'Delay (0–30)'}, 'stagger': {'type': 'number', 'default': 0.45, 'min': 0.1, 'max': 5, 'label': 'Stagger (0.1–5)'}, 'times': {'type': 'list', 'item': {'type': 'number', 'min': 0, 'max': 60, 'label': 'Item (0–60)'}, 'nullable': True, 'default': None, 'min_items': 1, 'max_items': 6, 'label': 'Times'}, 'mode': {'type': 'choice', 'choices': ['circle', 'box'], 'default': 'circle', 'label': 'Mode'}, 'size': {'type': 'choice', 'choices': ['normal', 'large'], 'default': 'normal', 'label': 'Size'}, 'card': {'type': 'bool', 'default': True, 'label': 'Card'}}, 'default_duration': 3.0, 'default_region': 'bottom', 'sfx': [{'at': 0.0, 'name': 'pop'}, {'at': 0.35, 'name': 'tick'}, {'at': 0.8, 'name': 'tick'}, {'at': 1.25, 'name': 'tick'}, {'at': 1.75, 'name': 'success', 'volume': 0.8}], 'example': {'title': 'What you *receive*:', 'items': ['Instant access', 'WhatsApp support', 'Short, focused lessons', '7-day guarantee']}}

FONT = "text_bold"
TITLE_FONT = "sans_black"
BASE_SIZE = {"normal": 56.0, "large": 68.0}
MIN_SIZE = 34.0

SKELETON = -0.08       # the empty markers start before frame 0, so frame 0 always shows them
ENTER = 0.5            # card entrance (seconds)
ROW_IN = 0.32          # item slide and fade
CHECK_AT = 0.10        # the marker fills this long after the item starts entering
CHECK_POP = 0.40       # marker pop (out_back)
CHECK_DRAW = 0.26      # the check stroke draws
GLOW_AFTER = 0.30      # card border glow starts this long after the last check
GLOW = 0.55


def _times(p: dict) -> list[float]:
    """Entry second of every item (validated against the item count)."""
    n = len(p["items"])
    times = p.get("times")
    if times is None:
        return [p["delay"] + i * p["stagger"] for i in range(n)]
    if len(times) != n:
        raise VisualError(f'checklist.params.times requires one time per item ({n} items, got {len(times)})')
    for i in range(1, n):
        if times[i] < times[i - 1]:
            raise VisualError(f'checklist.params.times must increase; item {i + 1} appears at {times[i]:g} s, before item {i} at {times[i - 1]:g} s)')
    return [float(v) for v in times]


def validate(params: dict) -> None:
    """One time per item, in order: refused when the item is planned, not only when it renders."""
    _times(params)


@lru_cache(maxsize=64)
def _layout(params_json: str, box: E.Box) -> dict:
    p = json.loads(params_json)
    items = p["items"]
    card = p["card"]
    title = p["title"].strip()
    title_plain = " ".join(w for w, _ in E.parse_accent(title))
    pad_x = 54.0 if card else 6.0
    pad_y = 48.0 if card else 6.0

    def build(size: float) -> tuple[dict, bool]:
        marker = size * 1.04
        gap = size * 0.44
        text_w = box.w - 2 * pad_x - marker - gap
        m = E.measure("H", FONT, size)
        line_h = size * 1.17
        row_gap = size * 0.46
        rows, fits, y = [], True, 0.0
        title_size = title_h = title_w = 0.0
        if title:
            title_size = min(size * 0.94, E.fit_text(title_plain, TITLE_FONT, box.w - 2 * pad_x, size * 0.94, 26.0))
            title_cap = E.measure("H", TITLE_FONT, title_size).cap
            title_w = E.measure(title_plain, TITLE_FONT, title_size).width
            title_h = title_cap + size * 0.74
            y = title_h
        widest = 0.0
        for k, text in enumerate(items):
            lines = E.wrap_text(text, FONT, size, text_w, 2)
            sizes = []
            for ln in lines:
                w = E.measure(ln, FONT, size).width
                if w > text_w + 0.5:
                    fits = False
                    sizes.append(E.fit_text(ln, FONT, text_w, size, 20.0))
                else:
                    sizes.append(size)
            widths = [E.measure(ln, FONT, s).width for ln, s in zip(lines, sizes)]
            widest = max(widest, max(widths))
            base0 = y + marker / 2.0 + m.cap / 2.0
            bottom = max(y + marker, base0 + (len(lines) - 1) * line_h)
            rows.append({"lines": lines, "sizes": sizes, "widths": widths, "top": y,
                         "cy": y + marker / 2.0, "base0": base0})
            y = bottom + (row_gap if k < len(items) - 1 else 0.0)
        content_h = y
        card_h = content_h + 2 * pad_y
        content_w = max(marker + gap + widest, title_w)
        card_w = min(box.w, max(content_w + 2 * pad_x, 380.0 if card else 0.0))
        L = {"size": size, "marker": marker, "gap": gap, "line_h": line_h, "rows": rows,
             "title_size": title_size, "title_base": E.measure("H", TITLE_FONT, title_size).cap if title else 0.0,
             "card_w": card_w, "card_h": card_h, "pad_x": pad_x, "pad_y": pad_y}
        return L, fits and card_h <= box.h

    size = BASE_SIZE[p["size"]]
    L, ok = build(size)
    while not ok and size > MIN_SIZE:
        size = max(MIN_SIZE, size * 0.95)
        L, ok = build(size)
    card_x = (box.w - L["card_w"]) / 2.0
    card_y = (box.h - L["card_h"]) / 2.0
    L["card_box"] = (card_x, card_y, L["card_w"], L["card_h"])
    L["x0"] = card_x + pad_x
    L["y0"] = card_y + pad_y
    L["radius"] = min(40.0, L["card_h"] * 0.2)
    theme = box.theme
    # a light accent on a white card gets a darker rim so the filled marker keeps its edge
    L["rim"] = card and E.contrast(theme.accent, theme.card) < 1.6
    L["times"] = _times(p)
    L["title"] = title
    L["card"] = card
    L["mode"] = p["mode"]
    return L


def _marker(cv: E.Canvas, mode: str, cx: float, cy: float, d: float, filled: bool, color,
            halo: bool = False, rim: bool = False) -> None:
    """Empty or filled marker. `halo` (no card): a dark edge so it reads on any video."""
    w = d * 0.085
    if mode == "box":
        r = d * 0.26
        if filled:
            cv.rect(cx - d / 2, cy - d / 2, d, d, radius=r, fill=color,
                    shadow=E.Shadow(blur=10.0, dy=4.0, opacity=0.5) if halo else None,
                    stroke=(0, 0, 0, 40) if rim else None, stroke_width=2.0 if rim else 0.0)
            return
        if halo:
            e = w * 0.9
            cv.rect(cx - d / 2 - e, cy - d / 2 - e, d + 2 * e, d + 2 * e, radius=r + e, fill=None,
                    stroke=(0, 0, 0, 120), stroke_width=w + 2 * e)
        cv.rect(cx - d / 2, cy - d / 2, d, d, radius=r, fill=None, stroke=color, stroke_width=w)
        return
    if filled:
        cv.circle(cx, cy, d / 2, fill=color, shadow=E.Shadow(blur=10.0, dy=4.0, opacity=0.5) if halo else None,
                  stroke=(0, 0, 0, 40) if rim else None, stroke_width=2.0 if rim else 0.0)
        return
    if halo:
        cv.ring(cx, cy, d / 2 - w / 2, w * 2.8, color=(0, 0, 0, 120))
    cv.ring(cx, cy, d / 2 - w / 2, w, color=color)


def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    cv = E.Canvas(box)
    t = frame_index / fps
    t_in = t + 1.0 / fps
    L = _layout(json.dumps(params, sort_keys=True), box)
    card = L["card"]
    size, d = L["size"], L["marker"]
    times = L["times"]

    alpha = E.eased(t_in, 0.0, 0.18, "out_quad")
    scale = 0.9 + 0.1 * E.out_back(E.progress(t_in, 0.0, ENTER), 1.4)
    rise = 32.0 * (1.0 - E.eased(t_in, 0.0, 0.45))
    outline = {} if card else {"stroke": size * 0.07, "stroke_color": "black", "shadow": E.TEXT_SHADOW}
    ink = "text" if card else "white"          # no card: white letters with a black outline in both themes
    halo = not card
    empty_color = "track" if card else (255, 255, 255, 215)

    with cv.group(opacity=alpha, scale=scale, dy=rise):
        cx0, cy0, cw, ch = L["card_box"]
        if card:
            cv.card(cx0, cy0, cw, ch, radius=L["radius"])
            glow = E.pulse(t, times[-1] + CHECK_AT + CHECK_POP * 0.5 + GLOW_AFTER, GLOW)
            if glow > 0.01:
                cv.rect(cx0, cy0, cw, ch, radius=L["radius"], fill=None, stroke="accent", stroke_width=3.0,
                        opacity=0.9 * glow)
        x0, y0 = L["x0"], L["y0"]
        rtl = any(E.has_arabic(ln) for row in L["rows"] for ln in row["lines"]) or E.has_arabic(L["title"])
        x_right = cx0 + cw - L["pad_x"]
        if L["title"]:
            cv.rich_text(x_right if rtl else x0, y0 + L["title_base"], L["title"], TITLE_FONT, L["title_size"],
                         color=ink, anchor="rs" if rtl else "ls", **outline)
        mx = (x_right - d / 2.0) if rtl else (x0 + d / 2.0)
        tx = (x_right - d - L["gap"]) if rtl else (x0 + d + L["gap"])
        for i, row in enumerate(L["rows"]):
            ti = times[i]
            cy = y0 + row["cy"]
            # skeleton: the empty marker pops in with the card
            sk = E.out_back(E.progress(t_in, SKELETON + 0.05 * i, 0.34), 1.8)
            fill_p = E.progress(t, ti + CHECK_AT, CHECK_POP)
            if sk > 0.01 and fill_p < 1.0:
                with cv.group(scale=max(0.01, sk), pivot=(mx, cy)):
                    _marker(cv, L["mode"], mx, cy, d, False, empty_color, halo=halo)
            if fill_p > 0:
                pop = max(0.01, E.out_back(fill_p, 2.2))
                with cv.group(scale=pop, pivot=(mx, cy)):
                    _marker(cv, L["mode"], mx, cy, d, True, "accent", halo=halo, rim=L["rim"])
                draw = E.eased(t, ti + CHECK_AT + 0.05, CHECK_DRAW, "in_out_cubic")
                if draw > 0:
                    cv.check(mx, cy + d * 0.02, d * 0.54, width=d * 0.12, color="on_accent", progress=draw)
            # the text slides in from the marker
            a = E.eased(t, ti, ROW_IN, "out_cubic")
            if cv.visible(a):
                dx = (1 if rtl else -1) * min(L["gap"] * 0.8, 18.0) * (1.0 - a)
                dy = 10.0 * (1.0 - a)
                for k, (ln, sz) in enumerate(zip(row["lines"], row["sizes"])):
                    cv.text(tx + dx, y0 + row["base0"] + k * L["line_h"] + dy, ln, FONT, sz, color=ink,
                            opacity=a, anchor="rs" if rtl else "ls", **outline)
    return cv


def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    times = _times(params)
    cues = [{"at": 0.0, "name": "pop", "why": 'card entrance'}]
    for i, ti in enumerate(times):
        cues.append({"at": ti + CHECK_AT, "name": "tick", "why": f'check item {i + 1}'})
    cues.append({"at": times[-1] + CHECK_AT + CHECK_POP * 0.5 + GLOW_AFTER, "name": "success", "volume": 0.8,
                 "why": "list complete"})
    return [{"frame": E.frame_of(c.pop("at"), fps), **c} for c in cues]


def suggested_duration(params: dict) -> float:
    """Last check, the glow and about 1.4 s to read the whole list."""
    last = _times(params)[-1]
    return round(max(TEMPLATE["default_duration"], last + CHECK_AT + CHECK_POP + 1.4 + 0.15 * len(params["items"])), 2)


def settle(params: dict, fps: int) -> float:
    last = _times(params)[-1]
    n = len(params["items"])
    return max(ENTER, SKELETON + 0.05 * (n - 1) + 0.34,
               last + CHECK_AT + CHECK_POP * 0.5 + GLOW_AFTER + GLOW,
               last + CHECK_AT + CHECK_POP, last + CHECK_AT + 0.05 + CHECK_DRAW, last + ROW_IN) + 0.05
