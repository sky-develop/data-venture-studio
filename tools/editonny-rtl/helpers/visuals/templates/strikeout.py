'Strikeout list animation.\n\nBuild the card and its item markers first using delay/stagger or explicit times. Cross each spoken item at its measured time using cross_delay/cross_stagger or cross_times, then reduce its opacity. A red line or X communicates removal. Frame zero already contains the card, title, and empty markers. Never imply a factual rejection or completed action absent from the actual speech.'

from __future__ import annotations

import json
import math
from functools import lru_cache

from visuals import engine as E
from visuals.engine import VisualError

TEMPLATE = {'id': 'strikeout', 'label': 'Strikeout', 'description': 'List items appear in a card and are crossed out sequentially with red lines or crosses.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'notes': 'Validate the parameters, keep text readable, and inspect the exported entrance and exit.', 'category': 'graphics', 'version': 1, 'params': {'items': {'type': 'list', 'item': {'type': 'text', 'max_len': 32, 'required_text': True, 'label': 'Item'}, 'min_items': 1, 'max_items': 6, 'default': ['Hold inventory', 'Appear in videos', 'Invest heavily'], 'label': 'Items'}, 'title': {'type': 'text', 'default': 'You do *not* need:', 'max_len': 36, 'label': 'Title'}, 'mode': {'type': 'choice', 'choices': ['line', 'x'], 'default': 'line', 'label': 'Mode'}, 'delay': {'type': 'number', 'default': 0.2, 'min': 0, 'max': 30, 'label': 'Delay (0–30)'}, 'stagger': {'type': 'number', 'default': 0.3, 'min': 0.1, 'max': 5, 'label': 'Stagger (0.1–5)'}, 'times': {'type': 'list', 'item': {'type': 'number', 'min': 0, 'max': 60, 'label': 'Item (0–60)'}, 'nullable': True, 'default': None, 'min_items': 1, 'max_items': 6, 'label': 'Times'}, 'cross_delay': {'type': 'number', 'default': 0.55, 'min': 0, 'max': 30, 'label': 'Cross Delay (0–30)'}, 'cross_stagger': {'type': 'number', 'default': 0.4, 'min': 0.1, 'max': 5, 'label': 'Cross Stagger (0.1–5)'}, 'cross_times': {'type': 'list', 'item': {'type': 'number', 'min': 0, 'max': 60, 'label': 'Item (0–60)'}, 'nullable': True, 'default': None, 'min_items': 1, 'max_items': 6, 'label': 'Cross Times'}, 'size': {'type': 'choice', 'choices': ['normal', 'large'], 'default': 'normal', 'label': 'Size'}, 'card': {'type': 'bool', 'default': True, 'label': 'Card'}}, 'default_duration': 3.5, 'default_region': 'bottom', 'sfx': [{'at': 0.0, 'name': 'pop'}, {'at': 1.47, 'name': 'tick', 'volume': 1.2}, {'at': 1.87, 'name': 'tick', 'volume': 1.2}, {'at': 2.27, 'name': 'tick', 'volume': 1.2}], 'example': {'title': 'You do *not* need:', 'items': ['Hold inventory', 'Appear in videos', 'Invest heavily', 'Experience']}}

FONT = "text_bold"
TITLE_FONT = "sans_black"
BASE_SIZE = {"normal": 56.0, "large": 68.0}
MIN_SIZE = 34.0

SKELETON = -0.08       # the empty markers start before frame 0, so frame 0 always shows them
ENTER = 0.5            # card entrance
ROW_IN = 0.32          # item slide and fade
STRIKE = 0.30          # the strike line draws (per item, all its lines)
X_POP = 0.38           # the red marker pops
X_DRAW = 0.24          # the X strokes draw
DIM_AT = 0.12          # the text starts fading this long after the cross starts
DIM = 0.30
DIM_TO = 0.5           # opacity of a crossed text
SOUND_AT = {"line": 0.12, "x": 0.04}    # the sound lands when the stroke is visibly under way


def _times(p: dict) -> tuple[list[float], list[float]]:
    """(appear times, cross times), validated."""
    n = len(p["items"])
    times = p.get("times")
    if times is None:
        shown = [p["delay"] + i * p["stagger"] for i in range(n)]
    else:
        if len(times) != n:
            raise VisualError(f'strikeout.params.times requires one time per item ({n} items, received {len(times)})')
        for i in range(1, n):
            if times[i] < times[i - 1]:
                raise VisualError(f'strikeout.params.times must increase; item {i + 1} appears at {times[i]:g} s, before item {i} at {times[i - 1]:g} s)')
        shown = [float(v) for v in times]
    crosses = p.get("cross_times")
    if crosses is None:
        first = shown[-1] + p["cross_delay"]
        crossed = [first + i * p["cross_stagger"] for i in range(n)]
    else:
        if len(crosses) != n:
            raise VisualError(f'strikeout.params.cross_times requires one time per item ({n} items, received {len(crosses)})')
        for i, (a, c) in enumerate(zip(shown, crosses)):
            if c < a:
                raise VisualError(f"strikeout.params.cross_times: item {i + 1} appears at {a:g} s but would be crossed out earlier, at {c:g} seconds. Each strike must follow its item's appearance")
        crossed = [float(v) for v in crosses]
    return shown, crossed


def validate(params: dict) -> None:
    """One time per item, in order: refused when the item is planned, not only when it renders."""
    _times(params)


@lru_cache(maxsize=64)
def _layout(params_json: str, box: E.Box) -> dict:
    p = json.loads(params_json)
    items = p["items"]
    card = p["card"]
    mode = p["mode"]
    title = p["title"].strip()
    title_plain = " ".join(w for w, _ in E.parse_accent(title))
    pad_x = 54.0 if card else 6.0
    pad_y = 48.0 if card else 6.0

    def build(size: float) -> tuple[dict, bool]:
        marker = size * (1.04 if mode == "x" else 0.30)
        gap = size * (0.44 if mode == "x" else 0.50)
        text_w = box.w - 2 * pad_x - marker - gap - size * 0.12     # the strike runs a little past the text
        m = E.measure("H", FONT, size)
        line_h = size * 1.17
        row_gap = size * 0.46
        slot = size * 1.04            # row height of a one-line item (same rhythm in both modes)
        rows, fits, y = [], True, 0.0
        title_size = title_w = 0.0
        if title:
            title_size = min(size * 0.94, E.fit_text(title_plain, TITLE_FONT, box.w - 2 * pad_x, size * 0.94, 26.0))
            title_w = E.measure(title_plain, TITLE_FONT, title_size).width
            y = E.measure("H", TITLE_FONT, title_size).cap + size * 0.74
        widest = 0.0
        for k, text in enumerate(items):
            lines = E.wrap_text(text, FONT, size, text_w, 2)
            sizes = []
            for ln in lines:
                if E.measure(ln, FONT, size).width > text_w + 0.5:
                    fits = False
                    sizes.append(E.fit_text(ln, FONT, text_w, size, 20.0))
                else:
                    sizes.append(size)
            widths = [E.measure(ln, FONT, s).width for ln, s in zip(lines, sizes)]
            widest = max(widest, max(widths))
            base0 = y + slot / 2.0 + m.cap / 2.0
            bottom = max(y + slot, base0 + (len(lines) - 1) * line_h)
            rows.append({"lines": lines, "sizes": sizes, "widths": widths, "cy": y + slot / 2.0, "base0": base0})
            y = bottom + (row_gap if k < len(items) - 1 else 0.0)
        card_h = y + 2 * pad_y
        content_w = max(marker + gap + widest + size * 0.12, title_w)
        card_w = min(box.w, max(content_w + 2 * pad_x, 380.0 if card else 0.0))
        L = {"size": size, "marker": marker, "gap": gap, "line_h": line_h, "rows": rows, "x_height": m.x_height,
             "title_size": title_size, "title_base": E.measure("H", TITLE_FONT, title_size).cap if title else 0.0,
             "card_w": card_w, "card_h": card_h}
        return L, fits and card_h <= box.h

    size = BASE_SIZE[p["size"]]
    L, ok = build(size)
    while not ok and size > MIN_SIZE:
        size = max(MIN_SIZE, size * 0.95)
        L, ok = build(size)
    card_x = (box.w - L["card_w"]) / 2.0
    card_y = (box.h - L["card_h"]) / 2.0
    L.update({"card_box": (card_x, card_y, L["card_w"], L["card_h"]), "x0": card_x + pad_x, "y0": card_y + pad_y,
              "radius": min(40.0, L["card_h"] * 0.2), "title": title, "card": card, "mode": mode})
    L["shown"], L["crossed"] = _times(p)
    # strike geometry per item: one stroke per line, a slight upward slant and bow like a marker
    for k, row in enumerate(L["rows"]):
        strokes = []
        for j, (w, sz) in enumerate(zip(row["widths"], row["sizes"])):
            xh = E.measure("x", FONT, sz).x_height
            y = row["base0"] + j * L["line_h"] - xh * 0.52
            x_a = -sz * 0.04
            x_b = w + sz * 0.10
            slant = min(sz * 0.07, (x_b - x_a) * 0.04) * (1 if (k + j) % 2 == 0 else 0.6)
            pts = E.bezier_points((x_a, y + slant), ((x_a + x_b) / 2.0, y + sz * 0.025), (x_b, y - slant), n=16)
            strokes.append({"points": pts, "width": sz * 0.16, "length": E.path_length(pts)})
        row["strokes"] = strokes
    return L


def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    cv = E.Canvas(box)
    t = frame_index / fps
    t_in = t + 1.0 / fps
    L = _layout(json.dumps(params, sort_keys=True), box)
    card, mode = L["card"], L["mode"]
    size, d = L["size"], L["marker"]

    alpha = E.eased(t_in, 0.0, 0.18, "out_quad")
    scale = 0.9 + 0.1 * E.out_back(E.progress(t_in, 0.0, ENTER), 1.4)
    rise = 32.0 * (1.0 - E.eased(t_in, 0.0, 0.45))
    outline = {} if card else {"stroke": size * 0.07, "stroke_color": "black", "shadow": E.TEXT_SHADOW}
    ink = "text" if card else "white"          # no card: white letters with a black outline in both themes
    empty = "track" if card else (255, 255, 255, 215)
    strike_shadow = E.Shadow(blur=6.0, dy=2.0, opacity=0.35 if card else 0.55)

    with cv.group(opacity=alpha, scale=scale, dy=rise):
        cx0, cy0, cw, ch = L["card_box"]
        if card:
            cv.card(cx0, cy0, cw, ch, radius=L["radius"])
        x0, y0 = L["x0"], L["y0"]
        if L["title"]:
            cv.rich_text(x0, y0 + L["title_base"], L["title"], TITLE_FONT, L["title_size"], color=ink, **outline)
        mx = x0 + d / 2.0
        tx = x0 + d + L["gap"]
        for i, row in enumerate(L["rows"]):
            ts, tc = L["shown"][i], L["crossed"][i]
            cy = y0 + row["cy"]
            sk = E.out_back(E.progress(t_in, SKELETON + 0.05 * i, 0.34), 1.8)
            if mode == "x":
                pop_p = E.progress(t, tc, X_POP)
                if sk > 0.01 and pop_p < 1.0:
                    with cv.group(scale=max(0.01, sk), pivot=(mx, cy)):
                        w = d * 0.085
                        if not card:
                            cv.ring(mx, cy, d / 2 - w / 2, w * 2.8, color=(0, 0, 0, 120))
                        cv.ring(mx, cy, d / 2 - w / 2, w, color=empty)
                if pop_p > 0:
                    with cv.group(scale=max(0.01, E.out_back(pop_p, 2.2)), pivot=(mx, cy)):
                        cv.circle(mx, cy, d / 2, fill="danger",
                                  shadow=None if card else E.Shadow(blur=10.0, dy=4.0, opacity=0.5))
                    draw = E.eased(t, tc + 0.05, X_DRAW, "in_out_cubic")
                    if draw > 0:
                        cv.cross(mx, cy, d * 0.36, width=d * 0.12, color="white", progress=draw)
            else:
                if sk > 0.01:
                    dot = d / 2.0 * max(0.01, sk)
                    turn = E.eased(t, tc, STRIKE * 0.6)
                    if not card:
                        cv.circle(mx, cy, dot + 2.5, fill=(0, 0, 0, 120))
                    if turn < 1:
                        cv.circle(mx, cy, dot, fill=empty if card else (255, 255, 255, 230))
                    if turn > 0:
                        cv.circle(mx, cy, dot, fill="danger", opacity=turn)
            # the text: slides in, then fades to half once crossed
            a = E.eased(t, ts, ROW_IN, "out_cubic")
            if cv.visible(a):
                dim = 1.0 - (1.0 - DIM_TO) * E.eased(t, tc + DIM_AT, DIM, "out_quad")
                dx = -min(L["gap"] * 0.8, 18.0) * (1.0 - a)
                dy = 10.0 * (1.0 - a)
                for k, (ln, sz) in enumerate(zip(row["lines"], row["sizes"])):
                    cv.text(tx + dx, y0 + row["base0"] + k * L["line_h"] + dy, ln, FONT, sz, color=ink,
                            opacity=a * dim, **outline)
            if mode == "line" and t >= tc:
                p = E.eased(t, tc, STRIKE, "in_out_cubic")
                total = sum(s["length"] for s in row["strokes"]) or 1.0
                done = 0.0
                for s in row["strokes"]:
                    part = E.clamp01((p * total - done) / max(1e-6, s["length"]))
                    done += s["length"]
                    if part <= 0:
                        break
                    pts = [(tx + u, y0 + v) for u, v in s["points"]]
                    n = len(pts)
                    widths = [s["width"] * (0.78 + 0.22 * math.sin(math.pi * min(1.0, k / (n - 1) * 1.15)))
                              for k in range(n)]
                    cv.polyline(pts, s["width"], color="danger", progress=part, widths=widths, shadow=strike_shadow)
    return cv


def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    _, crossed = _times(params)
    mode = params["mode"]
    cues = [{"at": 0.0, "name": "pop", "why": 'card entrance'}]
    for i, tc in enumerate(crossed):
        if mode == "x":
            cues.append({"at": tc + SOUND_AT["x"], "name": "error", "volume": 0.7, "why": f'cross on item {i + 1}'})
        else:
            cues.append({"at": tc + SOUND_AT["line"], "name": "tick", "volume": 1.2, "why": f'strike through item {i + 1}'})
    return [{"frame": E.frame_of(c.pop("at"), fps), **c} for c in cues]


def _end(params: dict) -> float:
    shown, crossed = _times(params)
    n = len(params["items"])
    last_cross = max(crossed)
    return max(ENTER, SKELETON + 0.05 * (n - 1) + 0.34, shown[-1] + ROW_IN,
               last_cross + max(STRIKE, X_POP, 0.05 + X_DRAW, DIM_AT + DIM))


def suggested_duration(params: dict) -> float:
    """Every item crossed and about 1.2 s to take it in."""
    return round(max(TEMPLATE["default_duration"], _end(params) + 1.2), 2)


def settle(params: dict, fps: int) -> float:
    return _end(params) + 0.05
