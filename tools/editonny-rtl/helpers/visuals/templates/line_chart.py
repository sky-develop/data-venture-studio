"""line_chart: a line that draws itself from left to right through the values, with a soft area under it,
a dot that pops on the last point, and the value at the tip counting in the header.

The curve is a monotone cubic (Fritsch-Carlson): smooth, and it never goes above or below the data between
two points. The area under the line is filled column by column (faster than a polygon with many sides).
`_area_mask` is the area under a curve up to a baseline. The card, grid and names are drawn once
(`E.static_sprite`) and pasted after the entrance.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache

import numpy as np

from visuals import engine as E

TEMPLATE = {'id': 'line_chart', 'label': 'Line Chart', 'description': 'A line draws through values from left to right, with optional area fill and points. The heading number follows the current endpoint.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'category': 'graphics', 'version': 1, 'params': {'values': {'type': 'list', 'item': {'type': 'number', 'min': -10000000000000.0, 'max': 10000000000000.0, 'label': 'Item (-10000000000000.0–10000000000000.0)'}, 'min_items': 2, 'max_items': 24, 'default': [12, 15, 14, 21, 26, 24, 33, 41], 'label': 'Values'}, 'labels': {'type': 'list', 'item': {'type': 'text', 'max_len': 10, 'label': 'Item'}, 'max_items': 24, 'default': [], 'label': 'Labels'}, 'title': {'type': 'text', 'default': '', 'max_len': 34, 'label': 'Title'}, 'prefix': {'type': 'text', 'default': '', 'max_len': 4, 'label': 'Prefix'}, 'suffix': {'type': 'text', 'default': '', 'max_len': 10, 'label': 'Suffix'}, 'decimals': {'type': 'integer', 'default': None, 'nullable': True, 'min': 0, 'max': 2, 'label': 'Decimals (0–2)'}, 'label': {'type': 'text', 'default': '', 'max_len': 24, 'label': 'Label'}, 'badge': {'type': 'text', 'default': '', 'max_len': 12, 'label': 'Badge'}, 'zero': {'type': 'bool', 'default': True, 'label': 'Zero'}, 'dots': {'type': 'bool', 'default': True, 'label': 'Dots'}, 'area': {'type': 'bool', 'default': True, 'label': 'Area'}, 'duration': {'type': 'number', 'default': 1.6, 'min': 0.3, 'max': 8, 'label': 'Duration (0.3–8)'}, 'delay': {'type': 'number', 'default': 0.2, 'min': 0, 'max': 5, 'label': 'Delay (0–5)'}, 'card': {'type': 'bool', 'default': True, 'label': 'Card'}}, 'default_duration': 4.0, 'default_region': 'bottom', 'sfx': [{'at': 0.0, 'name': 'pop'}, {'at': 0.2, 'name': 'count', 'volume': 1.3}, {'at': 1.8, 'name': 'pop'}], 'example': {'title': 'Profile followers', 'values': [800, 950, 1400, 1300, 2600, 3900, 5200], 'labels': ['Jan', 'Jul'], 'label': 'in 6 months', 'badge': '+550%'}, 'notes': 'Validate the parameters, keep text readable, and inspect the exported entrance and exit.'}

EASE = "in_out_cubic"
DOT_POP = 0.3
HALO = 0.7
FIT = 0.97           # text fitted to a full width keeps 3% spare: the half-size preview rounds glyphs wider
ROOM = 40.0          # design px of the region left free (the entrance rise and the card shadow)


# ------------------------------------------------------------------ helpers

def _decimals(p: dict) -> int:
    if p["decimals"] is not None:
        return int(p["decimals"])
    d = max(E.auto_decimals(v) for v in p["values"])
    money = E.looks_like_money(p["prefix"], p["suffix"])
    return 2 if money and d else d


def _affix_gap(affix: str, size: float) -> float:
    """Space between the number and a prefix/suffix: none for one symbol (%, x, +), a word space otherwise."""
    return size * (0.02 if len(affix) <= 1 else 0.14) if affix else 0.0


def _slopes(ys: list[float]) -> list[float]:
    """Fritsch-Carlson tangents for x spacing 1: the curve keeps the shape of the data (no overshoot)."""
    n = len(ys)
    d = [ys[i + 1] - ys[i] for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        m[i] = 0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2.0
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        if a < 0:
            m[i], a = 0.0, 0.0
        if b < 0:
            m[i + 1], b = 0.0, 0.0
        s = a * a + b * b
        if s > 9.0:
            tau = 3.0 / math.sqrt(s)
            m[i], m[i + 1] = tau * a * d[i], tau * b * d[i]
    return m


def _hermite(ys: list[float], m: list[float], u: float) -> float:
    """Value of the curve at u (0 .. n-1, in points)."""
    n = len(ys)
    u = min(max(u, 0.0), n - 1.0)
    i = min(int(u), n - 2)
    t = u - i
    t2, t3 = t * t, t * t * t
    return ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * m[i] + (-2 * t3 + 3 * t2) * ys[i + 1]
            + (t3 - t2) * m[i + 1])


def _area_mask(cv: E.Canvas, pts: list[tuple[float, float]], base: float):
    """Coverage of the region between a curve (x increasing, design px) and the horizontal line y = base."""
    if len(pts) < 2:
        return None
    P = [cv.px(x, y) for x, y in pts]
    X = np.array([p[0] for p in P], np.float64)
    Y = np.array([p[1] for p in P], np.float64)
    B = cv.px(0.0, base)[1]
    x0, x1 = float(X[0]), float(X[-1])
    if x1 - x0 < 0.05:
        return None
    ix0, ix1 = max(0, int(math.floor(x0))), min(cv.W, int(math.ceil(x1)))
    iy0, iy1 = max(0, int(math.floor(Y.min()))), min(cv.H, int(math.ceil(B)))
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    cols = np.arange(ix0, ix1, dtype=np.float64)
    ycurve = np.interp(cols + 0.5, X, Y)
    rows = np.arange(iy0, iy1, dtype=np.float64)
    a = np.clip(rows[:, None] + 0.5 - ycurve[None, :] + 0.5, 0.0, 1.0)      # under the curve
    a *= np.clip(B - rows, 0.0, 1.0)[:, None]                                # above the base line
    a *= (np.clip(cols + 1.0 - x0, 0.0, 1.0) * np.clip(x1 - cols, 0.0, 1.0))[None, :]
    return E.Mask(a.astype(np.float32), ix0, iy0)


def _time_at(progress: float, delay: float, duration: float) -> float:
    """Seconds when the eased line progress reaches `progress` (bisection, deterministic)."""
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if E.ease(EASE, mid) < progress:
            lo = mid
        else:
            hi = mid
    return delay + duration * hi


# ------------------------------------------------------------------ layout (once per item)

@lru_cache(maxsize=64)
def _layout(params_json: str, box: E.Box) -> dict:
    p = json.loads(params_json)
    vals = [float(v) for v in p["values"]]
    n = len(vals)
    labels = [s.strip() for s in p["labels"]]
    if labels and len(labels) not in (2, n):
        raise E.VisualError(f'line_chart.params.labels has {len(labels)} names and values has {n} values: supply first/last labels, one label per value, or no labels')
    dec = _decimals(p)
    prefix, suffix = p["prefix"].strip(), p["suffix"].strip()
    card = p["card"]
    theme = box.theme
    pad = 46.0 if card else 12.0
    card_w = box.w
    inner = card_w - 2 * pad
    x_card = (box.w - card_w) / 2.0

    # header: title / big value + label ... badge
    title = p["title"].strip()
    t_size = E.fit_text(title, "text_bold", inner * FIT, 38.0, 28.0) if title else 0.0
    t_cap = E.measure("H", "text_bold", t_size).cap if title else 0.0
    t_gap = 26.0 if title else 0.0
    widest = max((E.format_number(v, dec) for v in (min(vals), max(vals), vals[0], vals[-1])),
                 key=lambda s: E.measure(s, "sans_black", 100, tabular=True).width)
    pre, suf = prefix, suffix
    badge = p["badge"].strip()
    b_size = 36.0
    b_w = (E.measure(badge, "sans_black", b_size).width + 2 * 22.0) if badge else 0.0
    b_h = 64.0
    label = p["label"].strip()

    def value_w(size: float) -> float:
        aff = size * 0.52
        return (E.measure(pre, "sans_black", aff).width + _affix_gap(pre, size)
                + E.measure(widest, "sans_black", size, tabular=True).width
                + _affix_gap(suf, size) + E.measure(suf, "sans_black", aff).width)

    v_size = 92.0
    room = inner - (b_w + 24.0 if badge else 0.0)
    if value_w(v_size) > room * 0.72:
        v_size = max(48.0, v_size * room * 0.72 / value_w(v_size))
    v_cap = E.measure("0", "sans_black", v_size).cap
    l_size = 36.0
    label_below = False
    if label:
        left = room - value_w(v_size) - 22.0
        l_size = E.fit_text(label, "text_semibold", max(10.0, left * FIT), 36.0, 10.0)
        if l_size < 28.0:
            label_below = True
            l_size = E.fit_text(label, "text_semibold", room * FIT, 34.0, 26.0)
    l_cap = E.measure("H", "text_semibold", l_size).cap if label else 0.0
    head_h = t_cap + t_gap + v_cap + ((18.0 + l_cap) if label_below else 0.0)

    # x labels
    xl_size = 32.0
    xl_cap = E.measure("H", "text_semibold", xl_size).cap if labels else 0.0
    xl_block = (20.0 + xl_cap) if labels else 0.0

    plot_gap = 40.0
    fixed = 2 * pad + head_h + plot_gap + xl_block + 16.0
    plot_h = max(110.0, min(360.0, box.h - ROOM - fixed))
    card_h = fixed + plot_h
    top = (box.h - card_h) / 2.0
    plot_top = top + pad + head_h + plot_gap + 16.0      # 16: room for the end dot above the highest point
    base = plot_top + plot_h
    x0 = x_card + pad + 14.0
    x1 = x_card + card_w - pad - 18.0

    lo, hi = min(vals), max(vals)
    if p["zero"]:
        lo = min(0.0, lo)
        hi = max(0.0, hi)
    else:
        span = hi - lo
        lo -= span * 0.12 if span > 0 else 1.0
    if hi - lo <= 1e-12:
        lo, hi = lo - 1.0, hi + 1.0

    def ymap(v: float) -> float:
        return base - (v - lo) / (hi - lo) * plot_h

    slopes = _slopes(vals)
    xs = [x0 + (x1 - x0) * i / (n - 1) for i in range(n)]
    per = max(3, min(48, int((x1 - x0) / (n - 1) / 9.0)))     # a curve sample about every 9 px
    dense = []
    for k in range((n - 1) * per + 1):
        u = k / per
        dense.append((x0 + (x1 - x0) * u / (n - 1), ymap(_hermite(vals, slopes, u))))
    dots = p["dots"] and n <= 12
    dot_times = [_time_at(i / (n - 1), p["delay"], p["duration"]) for i in range(n)]

    # x labels: 2 = first and last; n = every point (thinned when they would touch)
    xlabels = []
    if labels:
        idx = [0, n - 1] if len(labels) == 2 else list(range(n))
        names = labels if len(labels) == n else [labels[0]] + [""] * (n - 2) + [labels[1]]
        if len(idx) > 2:
            widest_l = max(E.measure(s, "text_semibold", xl_size).width for s in names) + 18.0
            step = max(1, math.ceil(widest_l / ((x1 - x0) / (n - 1))))
            idx = [i for i in idx if i % step == 0 or i == n - 1]
            if len(idx) >= 2 and idx[-1] - idx[-2] < step:
                idx.pop(-2)
        for i in idx:
            if not names[i]:
                continue
            anchor = "ls" if i == 0 else "rs" if i == n - 1 else "ms"
            x = x0 - 14.0 if i == 0 else x1 + 18.0 if i == n - 1 else xs[i]
            xlabels.append((x, names[i], anchor))

    accent_ok = not card or E.contrast(theme.accent, theme.card) >= 2.4
    head_base = top + pad + t_cap + t_gap + v_cap
    return {
        "n": n, "vals": vals, "slopes": slopes, "dec": dec, "pre": pre, "suf": suf, "card": card,
        "card_box": (x_card, top, card_w, card_h), "radius": min(44.0, card_h * 0.12),
        "title": title, "t_size": t_size, "title_base": top + pad + t_cap, "x_left": x_card + pad,
        "v_size": v_size, "value_w": value_w(v_size), "head_base": head_base, "value_color": "accent" if accent_ok else "text",
        "label": label, "l_size": l_size, "label_below": label_below,
        "label_base": head_base + 18.0 + l_cap if label_below else head_base,
        "badge": badge, "b_size": b_size, "b_w": b_w, "b_h": b_h,
        "badge_box": (x_card + card_w - pad - b_w, head_base - v_cap / 2.0 - b_h / 2.0, b_w, b_h),
        "plot_top": plot_top, "base": base, "plot_h": plot_h, "lo": lo, "hi": hi, "x0": x0, "x1": x1, "xs": xs,
        "ys": [ymap(v) for v in vals], "dense": dense, "dots": dots, "dot_times": dot_times,
        "xlabels": xlabels, "xl_size": xl_size, "xl_base": base + 20.0 + xl_cap,
        "grid": [plot_top + plot_h * k / 3.0 for k in range(4)], "zero_line": ymap(0.0) if lo < 0 < hi else None,
    }


# ------------------------------------------------------------------ drawing

def _t_end(params: dict) -> float:
    return params["delay"] + params["duration"]


STATIC_AFTER = 0.5     # seconds: from here on the card, grid, names, title and label never change


def _draw_static(cv: E.Canvas, L: dict, ui_in: float) -> None:
    """Everything that does not move once the card is in."""
    card = L["card"]
    stroke = {} if card else {"stroke": 5.0, "stroke_color": "black", "shadow": E.TEXT_SHADOW}
    soft = {} if card else {"stroke": 4.0, "stroke_color": "black"}
    x0, x1 = L["x0"], L["x1"]
    if card:
        cx0, cy0, cw, ch = L["card_box"]
        cv.card(cx0, cy0, cw, ch, radius=L["radius"])
    grid_c = (255, 255, 255, 26) if (cv.theme.dark or not card) else (0, 0, 0, 20)
    strong_c = "track" if card else (255, 255, 255, 110)
    for k, gy in enumerate(L["grid"]):
        strong = k == len(L["grid"]) - 1
        cv.line(x0 - 14.0, gy, x1 + 18.0, gy, width=2.5 if strong else 2.0, color=strong_c if strong else grid_c,
                opacity=ui_in)
    if L["zero_line"] is not None:
        cv.line(x0 - 14.0, L["zero_line"], x1 + 18.0, L["zero_line"], width=2.5, color=strong_c, opacity=ui_in)
    for x, name, anchor in L["xlabels"]:
        cv.text(x, L["xl_base"], name, "text_semibold", L["xl_size"], color="muted" if card else "text",
                opacity=ui_in, anchor=anchor, **soft)
    if L["title"]:
        cv.text(L["x_left"], L["title_base"], L["title"], "text_bold", L["t_size"], color="text", **stroke)
    if L["label"]:
        lx = L["x_left"] if L["label_below"] else L["x_left"] + L["value_w"] + 22.0
        cv.text(lx, L["label_base"], L["label"], "text_semibold", L["l_size"],
                color="muted" if card else "text", opacity=ui_in, **soft)


def _static_sprite(params_json: str, box: E.Box):
    """The static part drawn once (card, grid, names, title and label)."""
    return E.static_sprite(("line_chart", params_json), box,
                           lambda c: _draw_static(c, _layout(params_json, box), 1.0))


def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    cv = E.Canvas(box)
    t = frame_index / fps
    key = json.dumps(params, sort_keys=True)
    L = _layout(key, box)
    cx0, cy0, cw, ch = L["card_box"]
    theme = box.theme
    t_end = _t_end(params)

    t_in = t + 1.0 / fps
    alpha = E.eased(t_in, 0.0, 0.18, "out_quad")
    scale = 0.92 + 0.08 * E.out_back(E.progress(t_in, 0.0, 0.5), 1.4)
    rise = 30.0 * (1.0 - E.eased(t_in, 0.0, 0.45))
    card = L["card"]
    stroke = {} if card else {"stroke": 5.0, "stroke_color": "black", "shadow": E.TEXT_SHADOW}
    ui_in = E.eased(t, 0.05, 0.3)
    p = E.eased(t, params["delay"], params["duration"], EASE)
    x0, x1 = L["x0"], L["x1"]
    tip_x = x0 + (x1 - x0) * p
    u_tip = p * (L["n"] - 1)
    if p >= 1.0:
        tip_v = L["vals"][-1]
    elif p <= 0.0:
        tip_v = L["vals"][0]
    else:
        tip_v = _hermite(L["vals"], L["slopes"], u_tip)
    base = L["base"]

    with cv.group(opacity=alpha, scale=scale, dy=rise, pivot=(cx0 + cw / 2.0, cy0 + ch / 2.0)):
        if t_in >= STATIC_AFTER:            # the entrance is over: the group is the identity
            cv.paste_sprite(_static_sprite(key, box))
        else:
            _draw_static(cv, L, ui_in)

        # header (the title and the label are in the static part)
        size = L["v_size"]
        aff = size * 0.52
        num = E.format_number(tip_v, L["dec"])
        x = L["x_left"]
        hb = L["head_base"]
        if L["pre"]:
            x = cv.text(x, hb, L["pre"], "sans_black", aff, color="text", opacity=0.92, **stroke)[2]
            x += _affix_gap(L["pre"], size)
        x = cv.text(x, hb, num, "sans_black", size, color=L["value_color"], tabular=True, **stroke)[2]
        if L["suf"]:
            x += _affix_gap(L["suf"], size)
            cv.text(x, hb, L["suf"], "sans_black", aff, color="text", opacity=0.92, **stroke)

        # area and line
        if p > 0:
            if p >= 1.0:
                pts = list(L["dense"])
                tip = pts[-1]
            else:
                pts = [q for q in L["dense"] if q[0] < tip_x]
                tip = (tip_x, _y_of(L, tip_v))
                pts.append(tip)
            if len(pts) == 1:
                pts = [pts[0], pts[0]]
            if params["area"]:
                acc = theme.accent
                grad = E.Gradient((acc[0], acc[1], acc[2], 105 if card else 90), (acc[0], acc[1], acc[2], 0),
                                  start=(0.0, L["plot_top"]), end=(0.0, base))
                cv.fill_mask(_area_mask(cv, pts, base), grad)
            # on the card the area already separates the line; on the video a shadow does
            cv.polyline(pts, width=9.0, color="accent",
                        shadow=None if card else E.Shadow(blur=10.0, dy=5.0, opacity=0.55))

            # point markers
            if L["dots"]:
                for i in range(L["n"] - 1):
                    q = E.progress(t, L["dot_times"][i], DOT_POP)
                    if q <= 0:
                        continue
                    s = E.out_back(q, 2.2)
                    cv.circle(L["xs"][i], L["ys"][i], 11.0 * s, fill="accent")
                    cv.circle(L["xs"][i], L["ys"][i], 5.5 * s, fill="card" if card else (20, 20, 22, 255))

            if p < 1.0:
                cv.circle(tip[0], tip[1], 11.0, fill="accent")
                cv.circle(tip[0], tip[1], 5.0, fill="white")

        # end: dot, halo, badge
        q_end = E.progress(t, t_end - 0.02, 0.35)
        if q_end > 0:
            ex, ey = L["xs"][-1], L["ys"][-1]
            h = E.progress(t, t_end, HALO)
            if 0 < h < 1:
                cv.ring(ex, ey, 16.0 + 34.0 * E.out_cubic(h), 5.0 * (1.0 - h) + 1.0, color="accent",
                        opacity=0.8 * (1.0 - h))
            s = E.out_back(q_end, 2.4)

            def end_dot() -> None:
                cv.circle(ex, ey, 17.0 * s, fill="accent")
                cv.circle(ex, ey, 7.0 * s, fill="white")

            glow = E.pulse(t, t_end - 0.02, 0.5)
            if glow > 0.02:
                cv.glow(end_dot, radius=18.0, opacity=0.7 * glow)
            else:
                end_dot()

        if L["badge"]:
            qb = E.progress(t, t_end + 0.1, 0.35)
            if qb > 0:
                bx, by, bw, bh = L["badge_box"]
                with cv.group(scale=0.4 + 0.6 * E.out_back(qb, 2.0), opacity=E.clamp01(qb * 3.0),
                              pivot=(bx + bw / 2.0, by + bh / 2.0)):
                    cv.pill(bx, by, bw, bh, fill="accent", shadow=E.Shadow(blur=14.0, dy=6.0, opacity=0.35))
                    cv.text(bx + bw / 2.0, by + bh / 2.0, L["badge"], "sans_black", L["b_size"],
                            color="on_accent", anchor="mc")
    return cv


def _y_of(L: dict, v: float) -> float:
    """Design y of a value on the plot scale."""
    return L["base"] - (v - L["lo"]) / (L["hi"] - L["lo"]) * L["plot_h"]


def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    start, dur = params["delay"], params["duration"]
    cues = [{"at": 0.0, "name": "pop", "why": "input"}]
    if params["values"][0] != params["values"][-1]:
        for k in range(max(1, int(dur / 0.6 + 0.25))):
            cues.append({"at": start + k * 0.6, "name": "count", "volume": 1.3, "why": 'counting number'})
    cues.append({"at": start + dur, "name": "pop", "volume": 0.9, "why": "last point"})
    return [{"frame": E.frame_of(c.pop("at"), fps), **c} for c in cues]


def suggested_duration(params: dict) -> float:
    """The line, the dot and badge, and about 1.8 s to read."""
    return round(max(TEMPLATE["default_duration"], _t_end(params) + HALO + 1.8), 2)


def settle(params: dict, fps: int) -> float:
    return _t_end(params) + HALO + 0.05
