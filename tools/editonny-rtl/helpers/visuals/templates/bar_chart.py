"""bar_chart: 2 to 6 bars growing one after the other, the value counting over each bar and its name
under it. Two looks: "columns" (standing bars) and "bars" (lying bars, better
for longer names). The highlighted bar gets the accent color and a short glow when it lands.

A bar is a rounded rect cut at the axis (`E.clip_mask`), so it is rounded on the free end and flat on the
axis. The card, title, axis and names are drawn once (`E.static_sprite`) and pasted after the entrance.
"""

from __future__ import annotations

import json
from functools import lru_cache

from visuals import engine as E

TEMPLATE = {'id': 'bar_chart', 'label': 'Bar Chart', 'description': 'Two to six bars grow sequentially in a card, with counting values and labels. The selected bar uses the accent color and shines on arrival.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'category': 'graphics', 'version': 1, 'params': {'bars': {'type': 'list', 'min_items': 2, 'max_items': 6, 'item': {'type': 'object', 'fields': {'label': {'type': 'text', 'default': '', 'max_len': 22, 'label': 'Label'}, 'value': {'type': 'number', 'min': 0, 'max': 10000000000000.0, 'label': 'Value (0–10000000000000.0)'}, 'highlight': {'type': 'bool', 'default': False, 'label': 'Highlight'}}, 'label': 'Item'}, 'default': [{'label': 'Jan', 'value': 120}, {'label': 'Feb', 'value': 260}, {'label': 'Mar', 'value': 410}, {'label': 'Apr', 'value': 680}], 'label': 'Bars'}, 'title': {'type': 'text', 'default': '', 'max_len': 34, 'label': 'Title'}, 'prefix': {'type': 'text', 'default': '', 'max_len': 4, 'label': 'Prefix'}, 'suffix': {'type': 'text', 'default': '', 'max_len': 8, 'label': 'Suffix'}, 'decimals': {'type': 'integer', 'default': None, 'nullable': True, 'min': 0, 'max': 2, 'label': 'Decimals (0–2)'}, 'mode': {'type': 'choice', 'choices': ['columns', 'bars'], 'default': 'columns', 'label': 'Mode'}, 'highlight': {'type': 'choice', 'choices': ['largest', 'last', 'all', 'none'], 'default': 'largest', 'label': 'Highlight'}, 'show_values': {'type': 'bool', 'default': True, 'label': 'Show Values'}, 'duration': {'type': 'number', 'default': 0.9, 'min': 0.2, 'max': 5, 'label': 'Duration (0.2–5)'}, 'stagger': {'type': 'number', 'default': 0.18, 'min': 0, 'max': 2, 'label': 'Stagger (0–2)'}, 'delay': {'type': 'number', 'default': 0.15, 'min': 0, 'max': 5, 'label': 'Delay (0–5)'}, 'card': {'type': 'bool', 'default': True, 'label': 'Card'}}, 'default_duration': 3.5, 'default_region': 'bottom', 'sfx': [{'at': 0.0, 'name': 'pop'}, {'at': 0.15, 'name': 'tick', 'volume': 0.8}], 'example': {'title': 'Weekly hours in the store', 'suffix': 'h', 'highlight': 'last', 'bars': [{'label': 'Before', 'value': 20}, {'label': 'After', 'value': 4}]}, 'notes': 'Validate the parameters, keep text readable, and inspect the exported entrance and exit.'}

LAND = 0.4           # seconds of the glow on a highlighted bar after it lands
FIT = 0.97           # text fitted to a full width keeps 3% spare: the half-size preview rounds glyphs wider
ROOM = 40.0          # design px of the region left free (the entrance rise and the card shadow)
OVERSHOOT = 1.0      # out_back overshoot of the growth (about 5% of the height)


# ------------------------------------------------------------------ helpers

def _decimals(p: dict) -> int:
    if p["decimals"] is not None:
        return int(p["decimals"])
    d = max(E.auto_decimals(b["value"]) for b in p["bars"])
    money = E.looks_like_money(p["prefix"], p["suffix"])
    return 2 if money and d else d


def _join(num: str, prefix: str, suffix: str) -> str:
    """'R$' + '12' -> 'R$ 12'; '+' + '12' -> '+12'; '12' + '%' -> '12%'; '12' + 'mil' -> '12 mil'."""
    pre = (prefix + (" " if len(prefix) > 1 else "")) if prefix else ""
    suf = ((" " if len(suffix) > 1 else "") + suffix) if suffix else ""
    return pre + num + suf


def _value_text(v: float, dec: int, prefix: str, suffix: str, compact: bool, final: float | None = None) -> str:
    """The value as shown; `final` (the bar's end value) keeps the compact form and its unit while it counts."""
    if compact and abs(final if final is not None else v) >= 1000:
        num = E.format_compact(v, 1, like=final)
    elif compact:
        num = E.format_number(v, min(dec, E.auto_decimals(final if final is not None else v, 1)))
    else:
        num = E.format_number(v, dec)
    return _join(num, prefix, suffix)


def _rest_color(theme: E.Theme, card: bool):
    if not card:                  # straight on the video: a solid light bar with a shadow
        return (236, 236, 236, 225)
    return (255, 255, 255, 72) if theme.dark else (17, 17, 17, 52)


def _fill_bar(cv: E.Canvas, m, color, card: bool) -> None:
    """Fill a bar mask; without a card the bar gets a soft drop shadow to read on any video."""
    if m is None:
        return
    if not card:
        sh = E.blur_mask(m, cv.len(9.0), cv.W, cv.H)
        sh.y += int(round(cv.len(5.0)))
        cv.fill_mask(sh, (0, 0, 0), 0.45)
    cv.fill_mask(m, color)


def _highlighted(p: dict) -> list[bool]:
    bars = p["bars"]
    chosen = [bool(b.get("highlight")) for b in bars]
    if any(chosen):
        return chosen
    mode = p["highlight"]
    if mode == "all":
        return [True] * len(bars)
    if mode == "none":
        return [False] * len(bars)
    if mode == "last":
        return [i == len(bars) - 1 for i in range(len(bars))]
    top = max(b["value"] for b in bars)
    first = next(i for i, b in enumerate(bars) if b["value"] == top)
    return [i == first for i in range(len(bars))]


COUNT_SAMPLES = 33


def _count_steps() -> list[tuple[float, float]]:
    """(bar length, count) pairs over the growth of a bar (the length overshoots a little, the count does not)."""
    return [(E.out_back(g, OVERSHOOT), E.out_cubic(g))
            for g in (k / (COUNT_SAMPLES - 1) for k in range(COUNT_SAMPLES))]


def _widest(texts: list[str]) -> str:
    return max(texts, key=lambda s: E.measure(s, "sans_black", 100, tabular=True).width)


def _fit_values(texts_of, width: float, max_size: float, min_size: float) -> tuple[float, bool]:
    """Size for the widest value text; switch to the compact form ('12,5 mil') when it does not fit."""
    for compact in (False, True):
        texts = texts_of(compact)
        widest = max(texts, key=lambda s: E.measure(s, "sans_black", 100, tabular=True).width)
        size = E.fit_text(widest, "sans_black", width, max_size, min_size, tabular=True)
        if E.measure(widest, "sans_black", size, tabular=True).width <= width + 0.5 or compact:
            return size, compact
    return min_size, True


# ------------------------------------------------------------------ layout (once per item)

COL_VALUE_MIN = 24.0
COL_LABEL_MIN = 24.0


def _columns_fit(p: dict, box: E.Box, pad: float, texts_of) -> bool:
    """Standing bars need every value on one line over its bar and every name word inside its slot."""
    n = len(p["bars"])
    slot = min(200.0, (box.w - 2 * pad) / n)
    if p["show_values"]:
        size, _compact = _fit_values(texts_of, slot * 0.98, 46.0, COL_VALUE_MIN)
        widest = max(E.measure(s, "sans_black", size, tabular=True).width for s in texts_of(_compact))
        if widest > slot * 0.98 + 0.5:
            return False
    for b in p["bars"]:
        for word in b["label"].split():
            if E.measure(word, "text_semibold", COL_LABEL_MIN).width > slot * 0.96:
                return False
        lines = E.wrap_text(b["label"], "text_semibold", COL_LABEL_MIN, slot * 0.96, 2)
        if len(lines) > 2 or any(E.measure(ln, "text_semibold", COL_LABEL_MIN).width > slot * 0.96 for ln in lines):
            return False
    return True

@lru_cache(maxsize=64)
def _layout(params_json: str, box: E.Box) -> dict:
    p = json.loads(params_json)
    bars = p["bars"]
    n = len(bars)
    dec = _decimals(p)
    prefix, suffix = p["prefix"].strip(), p["suffix"].strip()
    card = p["card"]
    theme = box.theme
    pad = 48.0 if card else 10.0
    title = p["title"].strip()
    vmax = max(b["value"] for b in bars)
    fracs = [(b["value"] / vmax) if vmax > 0 else 0.0 for b in bars]
    hi = _highlighted(p)
    accent_ok = not card or E.contrast(theme.accent, theme.card) >= 2.4
    out = {"n": n, "dec": dec, "prefix": prefix, "suffix": suffix, "card": card, "fracs": fracs, "hi": hi,
           "hi_text": "accent" if accent_ok else "text", "mode": p["mode"], "title": title,
           "show_values": p["show_values"]}

    def counted(b: dict, compact: bool) -> list[str]:
        """Every text the bar shows while it counts (a count can be wider than its final value: 0,9 tri)."""
        return [_value_text(b["value"] * c, dec, prefix, suffix, compact, b["value"]) for _, c in _count_steps()]

    def texts_of(compact: bool) -> list[str]:
        return [_widest(counted(b, compact)) for b in bars]

    title_size = E.fit_text(title, "text_bold", (box.w - 2 * pad) * FIT, 42.0, 30.0) if title else 0.0
    title_cap = E.measure("H", "text_bold", title_size).cap if title else 0.0
    title_w = E.measure(title, "text_bold", title_size).width if title else 0.0
    title_gap = 48.0 if title else 0.0
    out.update(title_size=title_size)

    mode = p["mode"]
    if mode == "columns" and not _columns_fit(p, box, pad, texts_of):
        mode = "bars"          # names or values too long for standing bars: lay them down
    out["mode"] = mode
    if mode == "columns":
        slot = min(200.0, (box.w - 2 * pad) / n)
        card_w = min(box.w, max(n * slot + 2 * pad, title_w + 2 * pad, 0.55 * box.w))
        slot = min(200.0, (card_w - 2 * pad) / n)
        col_w = min(slot * 0.58, 118.0)
        val_size, compact = _fit_values(texts_of, slot * 0.98, 46.0, COL_VALUE_MIN)
        val_cap = E.measure("0", "sans_black", val_size).cap if p["show_values"] else 0.0
        val_gap = 16.0 if p["show_values"] else 0.0
        # names: up to two balanced lines inside the slot, the same size for every bar
        lab_size = 34.0
        names = [b["label"].strip() for b in bars]
        widest_name = max(names, key=lambda s: E.measure(s, "text_semibold", 100).width)
        one_line = E.fit_text(widest_name, "text_semibold", slot * 0.96, 34.0, 12.0) if widest_name else 34.0
        if one_line >= 29.0:            # a slightly smaller name on one line reads better than two lines
            lab_size = one_line
        for _ in range(12):
            wrapped = [E.wrap_text(s, "text_semibold", lab_size, slot * 0.96, 2) if s else [] for s in names]
            too_wide = any(E.measure(ln, "text_semibold", lab_size).width > slot * 0.96
                           for lines in wrapped for ln in lines)
            if not too_wide or lab_size <= COL_LABEL_MIN:
                break
            lab_size = max(COL_LABEL_MIN, lab_size * 0.93)
        lab_cap = E.measure("H", "text_semibold", lab_size).cap
        lines = max((len(w) for w in wrapped), default=0)
        pitch = lab_size * 1.18
        lab_block = (20.0 + lab_cap + pitch * (lines - 1)) if lines else 0.0
        fixed = 2 * pad + title_cap + title_gap + val_cap + val_gap + lab_block + 4.0
        plot_h = max(120.0, min(400.0, box.h - ROOM - fixed))
        card_h = fixed + plot_h
        top = (box.h - card_h) / 2.0
        x0 = (box.w - card_w) / 2.0
        base = top + pad + title_cap + title_gap + val_cap + val_gap + plot_h
        inner_x = x0 + (card_w - n * slot) / 2.0
        cols = [inner_x + slot * (i + 0.5) for i in range(n)]
        out.update(slot=slot, col_w=col_w, val_size=val_size, compact=compact, lab_size=lab_size,
                   lab_cap=lab_cap, pitch=pitch, wrapped=wrapped, plot_h=plot_h, base=base, cols=cols,
                   card_box=(x0, top, card_w, card_h), title_base=top + pad + title_cap,
                   title_x=x0 + pad if n > 2 or title_w > card_w * 0.7 else x0 + card_w / 2.0,
                   title_anchor="ls" if n > 2 or title_w > card_w * 0.7 else "ms",
                   axis=(inner_x + slot * 0.08, inner_x + n * slot - slot * 0.08),
                   col_radius=min(18.0, col_w * 0.24))
    else:
        card_w = box.w
        inner_w = card_w - 2 * pad
        x0 = (box.w - card_w) / 2.0
        names = [b["label"].strip() for b in bars]
        show = p["show_values"]
        # stacked rows (name and value on a line, the bar under them at full width) when they fit the box;
        # otherwise one line per bar: name | bar | value
        lab_size, val_size = 36.0, 38.0
        bar_h = 26.0 if n <= 4 else 22.0
        lab_cap = E.measure("H", "text_semibold", lab_size).cap
        row_h = lab_cap + 14.0 + bar_h
        row_gap = 26.0 if n <= 4 else 18.0
        stacked_h = 2 * pad + title_cap + title_gap + n * row_h + (n - 1) * row_gap
        texts = texts_of(False)
        compact = False
        if stacked_h <= box.h - ROOM:
            for k, name in enumerate(names):
                vw = E.measure(texts[k], "sans_black", val_size, tabular=True).width if show else 0.0
                room = (inner_w - vw - 24.0) * FIT
                if name and E.measure(name, "text_semibold", lab_size).width > room:
                    lab_size = min(lab_size, E.fit_text(name, "text_semibold", room, lab_size, 26.0))
            lab_cap = E.measure("H", "text_semibold", lab_size).cap
            row_h = lab_cap + 14.0 + bar_h
            rows_h = n * row_h + (n - 1) * row_gap
            card_h = 2 * pad + title_cap + title_gap + rows_h
            top = (box.h - card_h) / 2.0
            first = top + pad + title_cap + title_gap
            tops = [first + i * (row_h + row_gap) for i in range(n)]
            pad_top = pad
            out.update(layout="stacked", val_size=val_size, compact=False, lab_size=lab_size, names=names,
                       bar_h=bar_h, axis_x=x0 + pad, bar_max=inner_w, lab_base=[y + lab_cap for y in tops],
                       centers=[y + lab_cap + 14.0 + bar_h / 2.0 for y in tops], value_x=x0 + pad + inner_w)
        else:
            widest = max(names, key=lambda s: E.measure(s, "text_semibold", 100).width) if any(names) else ""
            lab_size = E.fit_text(widest, "text_semibold", inner_w * 0.34, 34.0, 26.0) if widest else 34.0
            lab_w = E.measure(widest, "text_semibold", lab_size).width if widest else 0.0
            axis_x = x0 + pad + (lab_w + 24.0 if widest else 0.0)
            area = x0 + pad + inner_w - axis_x
            fracs = out["fracs"]
            if show:
                val_size, compact = _fit_values(texts_of, area * 0.42, 34.0, 26.0)
                # the longest bar that still leaves room for every value after its own bar, on every frame of
                # the growth (the bar overshoots a little and the counting text can be wider than the final one)
                limits = [area]
                for b, f in zip(bars, fracs):
                    if f <= 0:
                        continue
                    for (grow, _), txt in zip(_count_steps(), counted(b, compact)):
                        if grow > 0:
                            w = E.measure(txt, "sans_black", val_size, tabular=True).width
                            limits.append((area - 16.0 - w) / (f * grow))
                bar_max = min(limits)
            else:
                bar_max = area
            bar_h = 30.0
            row_h = max(bar_h, lab_size * 1.1)
            # squeeze the gaps (never the text) when the rows need more height than the region has
            pad_y, t_gap = pad, title_gap
            over = 2 * pad_y + title_cap + t_gap + n * row_h + (n - 1) * row_gap - (box.h - ROOM)
            if over > 0 and n > 1:
                cut = min(over, (row_gap - 12.0) * (n - 1))
                row_gap -= cut / (n - 1)
                over -= cut
            if over > 0 and title:
                cut = min(over, t_gap - 28.0)
                t_gap -= cut
                over -= cut
            if over > 0:
                pad_y -= min(over / 2.0, pad_y - 24.0)
            rows_h = n * row_h + (n - 1) * row_gap
            card_h = 2 * pad_y + title_cap + t_gap + rows_h
            top = (box.h - card_h) / 2.0
            first = top + pad_y + title_cap + t_gap
            out.update(layout="inline", val_size=val_size, compact=compact, lab_size=lab_size, names=names,
                       bar_h=bar_h, axis_x=axis_x, bar_max=max(60.0, bar_max),
                       centers=[first + row_h / 2.0 + i * (row_h + row_gap) for i in range(n)],
                       rows=(first - row_gap / 2.0, first + rows_h + row_gap / 2.0))
            pad_top = pad_y
        out.update(card_box=(x0, top, card_w, card_h), title_base=top + pad_top + title_cap, title_x=x0 + pad,
                   title_anchor="ls", label_x=x0 + pad)
    out["radius"] = min(44.0, out["card_box"][3] * 0.12)
    return out


# ------------------------------------------------------------------ drawing

def _growth(params: dict, i: int, t: float) -> tuple[float, float]:
    """(eased length with a small overshoot, count progress 0..1) of bar i at time t."""
    start = params["delay"] + i * params["stagger"]
    g = E.progress(t, start, params["duration"])
    return E.out_back(g, OVERSHOOT), E.out_cubic(g)


def _land(params: dict, i: int, t: float) -> float:
    return E.pulse(t, params["delay"] + i * params["stagger"] + params["duration"] - 0.05, LAND)


STATIC_AFTER = 0.5     # seconds: from here on the card, title, axis, tracks and names never change


def _draw_static(cv: E.Canvas, L: dict, names_in: float) -> None:
    """Everything that does not move once the card is in: card, title, axis, tracks and names."""
    card = L["card"]
    outline = {} if card else {"stroke": 5.0, "stroke_color": "black", "shadow": E.TEXT_SHADOW}
    name_kw = {} if card else {"stroke": 4.0, "stroke_color": "black"}
    name_color = "muted" if card else "text"
    if card:
        cx0, cy0, cw, ch = L["card_box"]
        cv.card(cx0, cy0, cw, ch, radius=L["radius"])
    if L["title"]:
        cv.text(L["title_x"], L["title_base"], L["title"], "text_bold", L["title_size"],
                color="text", anchor=L["title_anchor"], **outline)
    axis_color = "track" if card else (255, 255, 255, 120)
    if L["mode"] == "columns":
        base = L["base"]
        ax0, ax1 = L["axis"]
        cv.line(ax0, base + 1.5, ax1, base + 1.5, width=3.0, color=axis_color)
        if names_in > 0:
            for i, lines in enumerate(L["wrapped"]):
                y = base + 20.0 + L["lab_cap"]
                for k, ln in enumerate(lines):
                    cv.text(L["cols"][i], y + k * L["pitch"], ln, "text_semibold", L["lab_size"],
                            color=name_color, opacity=names_in, anchor="ms", **name_kw)
        return
    ax = L["axis_x"]
    bh = L["bar_h"]
    stacked = L["layout"] == "stacked"
    if not stacked:
        y0, y1 = L["rows"]
        cv.line(ax - 1.5, y0 + 6.0, ax - 1.5, y1 - 6.0, width=3.0, color=axis_color)
    for i, name in enumerate(L["names"]):
        cy = L["centers"][i]
        if names_in > 0 and name:
            if stacked:
                cv.text(L["label_x"], L["lab_base"][i], name, "text_semibold", L["lab_size"],
                        color=name_color, opacity=names_in, anchor="ls", **name_kw)
            else:
                cv.text(L["label_x"], cy, name, "text_semibold", L["lab_size"],
                        color=name_color, opacity=names_in, anchor="lc", **name_kw)
        if stacked:          # the track shows where the bar runs
            cv.pill(ax, cy - bh / 2.0, L["bar_max"], bh, fill="track" if card else (0, 0, 0, 110), opacity=names_in)


def _static_sprite(params_json: str, box: E.Box):
    """The static part drawn once (card, title, axis, tracks and names)."""
    return E.static_sprite(("bar_chart", params_json), box,
                           lambda c: _draw_static(c, _layout(params_json, box), 1.0))


def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    cv = E.Canvas(box)
    t = frame_index / fps
    key = json.dumps(params, sort_keys=True)
    L = _layout(key, box)
    bars = params["bars"]
    cx0, cy0, cw, ch = L["card_box"]

    t_in = t + 1.0 / fps
    alpha = E.eased(t_in, 0.0, 0.18, "out_quad")
    scale = 0.92 + 0.08 * E.out_back(E.progress(t_in, 0.0, 0.5), 1.4)
    rise = 30.0 * (1.0 - E.eased(t_in, 0.0, 0.45))
    rest = _rest_color(box.theme, L["card"])
    outline = {} if L["card"] else {"stroke": 5.0, "stroke_color": "black", "shadow": E.TEXT_SHADOW}

    with cv.group(opacity=alpha, scale=scale, dy=rise, pivot=(cx0 + cw / 2.0, cy0 + ch / 2.0)):
        if t_in >= STATIC_AFTER:            # the entrance is over: the group is the identity
            cv.paste_sprite(_static_sprite(key, box))
        else:
            _draw_static(cv, L, E.eased(t, 0.06, 0.3))

        if L["mode"] == "columns":
            base = L["base"]
            base_px = cv.px(0.0, base)[1]
            for i, b in enumerate(bars):
                grow, count = _growth(params, i, t)
                cx = L["cols"][i]
                h = L["plot_h"] * L["fracs"][i] * grow
                color = "accent" if L["hi"][i] else rest
                land = _land(params, i, t) if L["hi"][i] else 0.0
                r = L["col_radius"]
                if h > 0.05:
                    def column(cx=cx, h=h, color=color, r=r) -> None:
                        m = cv.rect_mask(cx - L["col_w"] / 2.0, base - h, L["col_w"], h + r + 2.0, radius=r)
                        _fill_bar(cv, E.clip_mask(m, y1=base_px), color, L["card"])
                    if land > 0.02:
                        cv.glow(column, radius=22.0, opacity=0.6 * land)
                    else:
                        column()
                if L["show_values"] and count > 0:
                    txt = _value_text(b["value"] * count, L["dec"], L["prefix"], L["suffix"], L["compact"], b["value"])
                    va = E.clamp01(count * 3.0)
                    with cv.group(scale=1.0 + 0.08 * land, pivot=(cx, base - h - 16.0)):
                        cv.text(cx, base - h - 16.0, txt, "sans_black", L["val_size"],
                                color=L["hi_text"] if L["hi"][i] else "text", opacity=va * (1.0 if L["hi"][i] else 0.86),
                                anchor="ms", tabular=True, **outline)
        else:
            ax = L["axis_x"]
            stacked = L["layout"] == "stacked"
            ax_px = cv.px(ax, 0.0)[0]
            bh = L["bar_h"]
            for i, b in enumerate(bars):
                grow, count = _growth(params, i, t)
                cy = L["centers"][i]
                w = L["bar_max"] * L["fracs"][i] * grow
                if stacked:
                    w = min(w, L["bar_max"])          # the track is the limit
                color = "accent" if L["hi"][i] else rest
                land = _land(params, i, t) if L["hi"][i] else 0.0
                r = bh / 2.0
                if w > 0.05:
                    def bar(cy=cy, w=w, color=color, r=r) -> None:
                        if stacked:
                            m = cv.rect_mask(ax, cy - bh / 2.0, max(bh, w), bh, radius=r)
                        else:
                            m = E.clip_mask(cv.rect_mask(ax - r - 2.0, cy - bh / 2.0, w + r + 2.0, bh, radius=r),
                                            x0=ax_px)
                        _fill_bar(cv, m, color, L["card"])
                    if land > 0.02:
                        cv.glow(bar, radius=20.0, opacity=0.6 * land)
                    else:
                        bar()
                if L["show_values"] and count > 0:
                    txt = _value_text(b["value"] * count, L["dec"], L["prefix"], L["suffix"], L["compact"], b["value"])
                    if stacked:
                        vx, vy, anchor = L["value_x"], L["lab_base"][i], "rs"
                    else:
                        vx, vy, anchor = ax + w + 16.0, cy, "lc"
                    with cv.group(scale=1.0 + 0.08 * land, pivot=(vx, vy)):
                        cv.text(vx, vy, txt, "sans_black", L["val_size"],
                                color=L["hi_text"] if L["hi"][i] else "text",
                                opacity=E.clamp01(count * 3.0) * (1.0 if L["hi"][i] else 0.86),
                                anchor=anchor, tabular=True, **outline)
    return cv


def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    cues = [{"at": 0.0, "name": "pop", "why": "input"}]
    for i in range(len(params["bars"])):
        cues.append({"at": params["delay"] + i * params["stagger"], "name": "tick", "volume": 0.8,
                     "why": f'bar {i + 1}'})
    return [{"frame": E.frame_of(c.pop("at"), fps), **c} for c in cues]


def _end(params: dict) -> float:
    return params["delay"] + (len(params["bars"]) - 1) * params["stagger"] + params["duration"]


def suggested_duration(params: dict) -> float:
    """All bars grown, the glow, and about 1.8 s to read."""
    return round(max(TEMPLATE["default_duration"], _end(params) + LAND + 1.8), 2)


def settle(params: dict, fps: int) -> float:
    return _end(params) + LAND + 0.05
