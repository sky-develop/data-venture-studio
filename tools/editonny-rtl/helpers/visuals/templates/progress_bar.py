"""progress_bar: a progress bar (or ring) filling up to a percentage, with the label and the percentage
counting, moving stripes and a bright tip while it fills, and a short glow when it lands.
Optional segments turn it into a step bar ("step 3 of 5").

`_stripes` draws moving diagonal stripes clipped to a mask. The card, label and track are drawn once
(`E.static_sprite`) and pasted after the entrance.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache

import numpy as np

from visuals import engine as E

TEMPLATE = {'id': 'progress_bar', 'label': 'Progress Bar', 'description': 'A progress bar or ring fills to the requested percentage while its value counts. Optional segments represent stages.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'category': 'graphics', 'version': 1, 'params': {'value': {'type': 'number', 'default': 75, 'min': 0, 'max': 100, 'label': 'Value (0–100)'}, 'start_value': {'type': 'number', 'default': 0, 'min': 0, 'max': 100, 'label': 'Start Value (0–100)'}, 'label': {'type': 'text', 'default': 'Completed', 'max_len': 30, 'label': 'Label'}, 'note': {'type': 'text', 'default': '', 'max_len': 36, 'label': 'Note'}, 'decimals': {'type': 'integer', 'default': None, 'nullable': True, 'min': 0, 'max': 1, 'label': 'Decimals (0–1)'}, 'mode': {'type': 'choice', 'choices': ['bar', 'ring'], 'default': 'bar', 'label': 'Mode'}, 'segments': {'type': 'integer', 'default': None, 'nullable': True, 'min': 2, 'max': 10, 'label': 'Segments (2–10)'}, 'show_value': {'type': 'bool', 'default': True, 'label': 'Show Value'}, 'duration': {'type': 'number', 'default': 1.4, 'min': 0.3, 'max': 10, 'label': 'Duration (0.3–10)'}, 'delay': {'type': 'number', 'default': 0.15, 'min': 0, 'max': 5, 'label': 'Delay (0–5)'}, 'ease': {'type': 'choice', 'choices': ['smooth', 'linear'], 'default': 'smooth', 'label': 'Ease'}, 'card': {'type': 'bool', 'default': True, 'label': 'Card'}}, 'default_duration': 3.0, 'default_region': 'bottom', 'sfx': [{'at': 0.0, 'name': 'pop'}, {'at': 0.15, 'name': 'count', 'volume': 1.3}], 'example': {'value': 87, 'label': 'Places filled', 'note': 'few places remain in this class'}, 'notes': 'Validate the parameters, keep text readable, and inspect the exported entrance and exit.'}

LAND = 0.4
FIT = 0.97           # text fitted to a full width keeps 3% spare: the half-size preview rounds glyphs wider


def _decimals(p: dict) -> int:
    if p["decimals"] is not None:
        return int(p["decimals"])
    return max(E.auto_decimals(p["value"], 1), E.auto_decimals(p["start_value"], 1))


def _stripes(cv: E.Canvas, m, t: float, period: float, opacity: float) -> None:
    """Diagonal light stripes moving to the right, clipped to the mask."""
    if m is None or opacity <= 0.003:
        return
    h, w = m.a.shape
    per = cv.len(period)
    xs = np.arange(w, dtype=np.float32)[None, :] + m.x + 0.5
    ys = np.arange(h, dtype=np.float32)[:, None] + m.y + 0.5
    u = (xs + ys - cv.len(t * period * 1.6)) / per
    tri = np.abs((u % 1.0) - 0.5) * 2.0                      # 0..1 triangle wave
    edge = 1.0 / max(1.0, per)                               # about one pixel of soft edge
    band = np.clip((tri - 0.5) / (2 * edge) + 0.5, 0.0, 1.0)
    cv.fill_mask(E.Mask(m.a * band, m.x, m.y), "white", opacity)


# ------------------------------------------------------------------ layout (once per item)

@lru_cache(maxsize=64)
def _layout(params_json: str, box: E.Box) -> dict:
    p = json.loads(params_json)
    theme = box.theme
    card = p["card"]
    dec = _decimals(p)
    label = p["label"].strip()
    note = p["note"].strip()
    show = p["show_value"]
    widest = E.format_number(100 if p["value"] >= 99.5 or p["start_value"] >= 99.5 else 88.8, dec)
    accent_ok = not card or E.contrast(theme.accent, theme.card) >= 2.4
    out = {"dec": dec, "label": label, "note": note, "card": card, "show": show,
           "value_color": "accent" if accent_ok else "text", "mode": p["mode"], "segments": p["segments"]}
    note_size = 32.0
    if p["mode"] == "bar":
        pad = 44.0 if card else 8.0
        card_w = box.w
        inner = card_w - 2 * pad
        v_size = 66.0
        pct = 0.56
        num_w = E.measure(widest, "sans_black", v_size, tabular=True).width
        val_w = (num_w + v_size * 0.03 + E.measure("%", "sans_black", v_size * pct).width) if show else 0.0
        l_size = E.fit_text(label, "text_bold", inner - val_w - 30.0, 44.0, 28.0) if label else 0.0
        l_cap = E.measure("H", "text_bold", l_size).cap if label else 0.0
        v_cap = E.measure("0", "sans_black", v_size).cap if show else 0.0
        head = max(l_cap, v_cap)
        bar_h = 38.0
        gap = 28.0 if head else 0.0
        if note:
            note_size = E.fit_text(note, "text_semibold", inner * FIT, 32.0, 24.0)
        n_cap = E.measure("H", "text_semibold", note_size).cap if note else 0.0
        n_gap = 26.0 if note else 0.0
        card_h = 2 * pad + head + gap + bar_h + n_gap + n_cap + (4.0 if note else 0.0)
        top = (box.h - card_h) / 2.0
        x0 = (box.w - card_w) / 2.0
        head_base = top + pad + head
        out.update(card_box=(x0, top, card_w, card_h), radius=min(40.0, card_h * 0.2), l_size=l_size,
                   v_size=v_size, pct=pct, num_w=num_w, head_base=head_base, left=x0 + pad, right=x0 + pad + inner,
                   bar=(x0 + pad, head_base + gap, inner, bar_h), note_size=note_size,
                   note_base=head_base + gap + bar_h + n_gap + n_cap)
    else:
        pad = 44.0 if card else 8.0
        R = 150.0
        ring_w = 30.0
        v_size = 92.0
        pct = 0.5
        num_w = E.measure(widest, "sans_black", v_size, tabular=True).width
        full_w = num_w + v_size * 0.03 + E.measure("%", "sans_black", v_size * pct).width
        inner_room = (R - ring_w / 2.0) * 2 * 0.8
        if full_w > inner_room:
            v_size *= inner_room / full_w
            num_w = E.measure(widest, "sans_black", v_size, tabular=True).width
        l_size = E.fit_text(label, "text_bold", (box.w - 2 * pad) * FIT, 42.0, 28.0) if label else 0.0
        l_cap = E.measure("H", "text_bold", l_size).cap if label else 0.0
        if note:
            note_size = E.fit_text(note, "text_semibold", (box.w - 2 * pad) * FIT, 32.0, 24.0)
        n_cap = E.measure("H", "text_semibold", note_size).cap if note else 0.0
        l_gap = 34.0 if label else 0.0
        n_gap = 18.0 if note else 0.0
        content_w = max(2 * R + ring_w, E.measure(label, "text_bold", l_size).width if label else 0.0,
                        E.measure(note, "text_semibold", note_size).width if note else 0.0)
        card_w = min(box.w, content_w + 2 * pad + 20.0)
        card_h = 2 * pad + 2 * R + ring_w + l_gap + l_cap + n_gap + n_cap + (4.0 if note else 0.0)
        if card_h > box.h:          # a short region: shrink the ring
            k = max(0.5, 1.0 - (card_h - box.h) / (2 * R + ring_w))
            R, ring_w, v_size = R * k, ring_w * k, v_size * k
            num_w = E.measure(widest, "sans_black", v_size, tabular=True).width
            card_h = 2 * pad + 2 * R + ring_w + l_gap + l_cap + n_gap + n_cap + (4.0 if note else 0.0)
        top = (box.h - card_h) / 2.0
        x0 = (box.w - card_w) / 2.0
        cy = top + pad + ring_w / 2.0 + R
        v_cap = E.measure("0", "sans_black", v_size).cap
        label_base = cy + R + ring_w / 2.0 + l_gap + l_cap
        out.update(card_box=(x0, top, card_w, card_h), radius=min(44.0, card_w * 0.1), l_size=l_size,
                   v_size=v_size, pct=pct, num_w=num_w, ring=(box.w / 2.0, cy, R, ring_w),
                   value_base=cy + v_cap / 2.0, label_base=label_base, note_size=note_size,
                   note_base=label_base + n_gap + n_cap, cx=box.w / 2.0)
    return out


# ------------------------------------------------------------------ drawing

def _fill(params: dict, t: float) -> tuple[float, float]:
    """(percentage shown now, eased progress 0..1)."""
    fn = "linear" if params["ease"] == "linear" else "out_quart"
    k = E.eased(t, params["delay"], params["duration"], fn)
    return params["start_value"] + (params["value"] - params["start_value"]) * k, k


def _t_end(params: dict) -> float:
    return params["delay"] + params["duration"]


def _value(cv: E.Canvas, L: dict, x: float, base: float, v: float, anchor: str, scale: float, outline: dict) -> None:
    size = L["v_size"]
    num = E.format_number(v, L["dec"])
    pct_s = size * L["pct"]
    num_w = E.measure(num, "sans_black", size, tabular=True).width
    pct_w = E.measure("%", "sans_black", pct_s).width
    total = num_w + size * 0.03 + pct_w
    x0 = x - total if anchor == "r" else x - total / 2.0
    with cv.group(scale=scale, pivot=(x0 + total / 2.0, base - size * 0.36)):
        cv.text(x0, base, num, "sans_black", size, color=L["value_color"], tabular=True, **outline)
        cv.text(x0 + num_w + size * 0.03, base, "%", "sans_black", pct_s, color="text", opacity=0.9, **outline)


STATIC_AFTER = 0.5     # seconds: from here on the card, label, note and tracks never change


def _segments(L: dict) -> list[tuple[float, float]]:
    """(x, width) of each piece of the bar."""
    bx, by, bw, bh = L["bar"]
    n = L["segments"] or 1
    gap = 10.0 if L["segments"] else 0.0
    sw = (bw - gap * (n - 1)) / n
    return [(bx + i * (sw + gap), sw) for i in range(n)]


def _arcs(L: dict) -> list[tuple[float, float]]:
    """(start degrees, sweep degrees) of each piece of the ring."""
    rcx, rcy, R, rw = L["ring"]
    segs = L["segments"]
    n = segs or 1
    # round caps stick out rw/2 at each end: leave room for them so the gaps stay open
    cap_deg = math.degrees(rw / 2.0 / R) if segs else 0.0
    gap_deg = 8.0 if segs else 0.0
    span = 360.0 / n
    return [(-90.0 + i * span + gap_deg / 2.0 + cap_deg, span - gap_deg - 2 * cap_deg) for i in range(n)]


def _draw_static(cv: E.Canvas, L: dict, note_in: float) -> None:
    """Everything that does not move once the card is in: card, label, note and the empty track."""
    card = L["card"]
    outline = {} if card else {"stroke": 5.0, "stroke_color": "black", "shadow": E.TEXT_SHADOW}
    soft = {} if card else {"stroke": 4.0, "stroke_color": "black"}
    track = "track" if card else (0, 0, 0, 120)
    if card:
        cx0, cy0, cw, ch = L["card_box"]
        cv.card(cx0, cy0, cw, ch, radius=L["radius"])
    if L["mode"] == "bar":
        if L["label"]:
            cv.text(L["left"], L["head_base"], L["label"], "text_bold", L["l_size"], color="text", **outline)
        bx, by, bw, bh = L["bar"]
        for sx, sw in _segments(L):
            cv.pill(sx, by, sw, bh, fill=track)
        if L["note"]:
            cv.text(L["left"], L["note_base"], L["note"], "text_semibold", L["note_size"],
                    color="muted" if card else "text", opacity=note_in, **soft)
        return
    rcx, rcy, R, rw = L["ring"]
    if L["segments"]:
        for a0, sweep in _arcs(L):
            cv.polyline(E.ellipse_points(rcx, rcy, R, R, a0, sweep), rw, color=track)
    else:
        cv.ring(rcx, rcy, R, rw, color=track)
    if L["label"]:
        cv.text(L["cx"], L["label_base"], L["label"], "text_bold", L["l_size"], color="text", anchor="ms", **outline)
    if L["note"]:
        cv.text(L["cx"], L["note_base"], L["note"], "text_semibold", L["note_size"],
                color="muted" if card else "text", opacity=note_in, anchor="ms", **soft)


def _static_sprite(params_json: str, box: E.Box):
    """The static part drawn once (card, label, note and the empty track)."""
    return E.static_sprite(("progress_bar", params_json), box,
                           lambda c: _draw_static(c, _layout(params_json, box), 1.0))


def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    cv = E.Canvas(box)
    t = frame_index / fps
    key = json.dumps(params, sort_keys=True)
    L = _layout(key, box)
    cx0, cy0, cw, ch = L["card_box"]
    card = L["card"]
    t_in = t + 1.0 / fps
    alpha = E.eased(t_in, 0.0, 0.18, "out_quad")
    scale = 0.92 + 0.08 * E.out_back(E.progress(t_in, 0.0, 0.5), 1.4)
    rise = 30.0 * (1.0 - E.eased(t_in, 0.0, 0.45))
    outline = {} if card else {"stroke": 5.0, "stroke_color": "black", "shadow": E.TEXT_SHADOW}
    v, k = _fill(params, t)
    fraction_value = v / 100.0
    t_end = _t_end(params)
    moving = params["value"] != params["start_value"] and 0.0 < k < 1.0
    land = E.pulse(t, t_end - 0.02, LAND) if params["value"] != params["start_value"] else 0.0
    stripe_op = 0.16 * (1.0 - E.eased(t, t_end - 0.25, 0.3))
    segs = L["segments"]

    with cv.group(opacity=alpha, scale=scale, dy=rise, pivot=(cx0 + cw / 2.0, cy0 + ch / 2.0)):
        if t_in >= STATIC_AFTER:            # the entrance is over: the group is the identity
            cv.paste_sprite(_static_sprite(key, box))
        else:
            _draw_static(cv, L, E.eased(t, 0.1, 0.35))

        if L["mode"] == "bar":
            if L["show"]:
                _value(cv, L, L["right"], L["head_base"], v, "r", 1.0 + 0.07 * land, outline)
            bx, by, bw, bh = L["bar"]
            fills = []
            pieces = _segments(L)
            for i, (sx, sw) in enumerate(pieces):
                f = E.clamp01(fraction_value * len(pieces) - i)
                if f <= 0:
                    break
                fw = sw * f
                m = cv.rect_mask(sx, by, max(fw, bh), bh, radius=bh / 2.0)
                if fw < bh:
                    m = E.clip_mask(m, x1=cv.px(sx + fw, 0.0)[0])
                fills.append(m)
            tip_x = bx + bw * fraction_value if not segs else None

            def draw_fill() -> None:
                grad = E.Gradient(box.theme.accent, E.mix_color(box.theme.accent, (255, 255, 255), 0.28),
                                  start=(bx, 0.0), end=(bx + bw, 0.0))
                for m in fills:
                    if m is None:
                        continue
                    cv.fill_mask(m, grad)
                    cv.fill_mask(_gloss(m), "white", 0.16)
                    if moving:
                        _stripes(cv, m, t, 26.0, stripe_op)

            if fills:
                if land > 0.02:
                    cv.glow(draw_fill, radius=20.0, opacity=0.65 * land)
                else:
                    draw_fill()
            if moving and tip_x is not None and fraction_value > 0:
                tx = max(bx + bh / 2.0, tip_x - bh / 2.0)
                cv.glow(lambda: cv.circle(tx, by + bh / 2.0, bh * 0.2, fill="white"), radius=14.0, opacity=0.9,
                        color="white")
        else:
            rcx, rcy, R, rw = L["ring"]
            arcs = []
            n = len(_arcs(L))
            for i, (a0, sweep) in enumerate(_arcs(L)):
                f = E.clamp01(fraction_value * n - i)
                if f > 0:
                    arcs.append((a0, sweep * f))

            def draw_arcs() -> None:
                for a0, sw in arcs:
                    if sw >= 359.99:
                        cv.ring(rcx, rcy, R, rw, color="accent")
                    else:
                        cv.polyline(E.ellipse_points(rcx, rcy, R, R, a0, sw), rw, color="accent")

            if arcs:
                if land > 0.02:
                    cv.glow(draw_arcs, radius=22.0, opacity=0.65 * land)
                else:
                    draw_arcs()
            if moving and not segs and fraction_value > 0:
                ang = math.radians(-90.0 + 360.0 * fraction_value)
                tx, ty = rcx + R * math.cos(ang), rcy + R * math.sin(ang)
                cv.glow(lambda: cv.circle(tx, ty, rw * 0.2, fill="white"), radius=14.0, opacity=0.9, color="white")
            if L["show"]:
                _value(cv, L, rcx, L["value_base"], v, "m", 1.0 + 0.07 * land, outline)
    return cv


def _gloss(m):
    """The upper 45% of a bar mask (a soft highlight on top of the fill)."""
    h = m.a.shape[0]
    rows = np.arange(h, dtype=np.float32)
    k = np.clip((h * 0.45 - rows) / max(1.0, h * 0.25), 0.0, 1.0)
    return E.Mask(m.a * k[:, None], m.x, m.y)


def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    cues = [{"at": 0.0, "name": "pop", "why": "input"}]
    start, dur = params["delay"], params["duration"]
    if params["value"] != params["start_value"]:
        steps = 1 if params["ease"] != "linear" else max(1, int(dur / 0.6 + 0.5))
        for k in range(steps):
            cues.append({"at": start + k * 0.6, "name": "count", "volume": 1.3, "why": "bar filling"})
        if params["value"] >= 100:
            cues.append({"at": start + dur, "name": "success", "why": "100%"})
    return [{"frame": E.frame_of(c.pop("at"), fps), **c} for c in cues]


def suggested_duration(params: dict) -> float:
    """Fill, landing glow and about 1.5 s to read."""
    return round(max(TEMPLATE["default_duration"], _t_end(params) + LAND + 1.5), 2)


def settle(params: dict, fps: int) -> float:
    return _t_end(params) + LAND + 0.05
