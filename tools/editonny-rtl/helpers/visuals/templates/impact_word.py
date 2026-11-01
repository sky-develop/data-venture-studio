"""impact_word: one to four big words that slam onto the screen one after the other (kinetic typography).

Each word starts big and faint, falls onto the screen accelerating, lands with a squash, and on the
landing the screen flashes, the block shakes and short impact lines shoot out. Two looks: "outline"
(white letters with a thick black outline, like the `impacto` captions) and "banner" (each word on a solid,
slightly tilted chip, white or in the accent color, like the `tarja` hook). In "banner" the chip and its word
slam in as one piece.

Full-screen flash: `flash: "screen"` draws on `E.Canvas(box, full=True)` on EVERY frame. The
flash, the glow and the impact lines are effects, not content: `ink()` gives the area of the resting words
(plus the entrance scale), so face-aware placing and the caption rules only see the words.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache

from PIL import Image

from visuals import engine as E
from visuals.engine import VisualError

TEMPLATE = {'id': 'impact_word', 'label': 'Impact Word', 'description': 'One or two large words strike the screen, settle with a snap, and optionally trigger a flash or shake.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'notes': 'Validate the parameters, keep text readable, and inspect the exported entrance and exit.', 'category': 'graphics', 'version': 1, 'params': {'text': {'type': 'text', 'default': '*ATTENTION*', 'max_len': 32, 'required_text': True, 'label': 'Text'}, 'style': {'type': 'choice', 'choices': ['outline', 'banner'], 'default': 'outline', 'label': 'Style'}, 'font': {'type': 'choice', 'choices': ['condensed', 'rounded'], 'default': 'condensed', 'label': 'Font'}, 'size': {'type': 'choice', 'choices': ['normal', 'large'], 'default': 'normal', 'label': 'Size'}, 'flash': {'type': 'choice', 'choices': ['screen', 'local', 'none'], 'default': 'screen', 'label': 'Flash'}, 'flash_color': {'type': 'choice', 'choices': ['white', 'accent'], 'default': 'white', 'label': 'Flash Color'}, 'shake': {'type': 'bool', 'default': True, 'label': 'Shake'}, 'burst': {'type': 'bool', 'default': True, 'label': 'Burst'}, 'stagger': {'type': 'number', 'default': 0.24, 'min': 0, 'max': 1.5, 'label': 'Stagger (0–1.5)'}, 'upper': {'type': 'bool', 'default': True, 'label': 'Upper'}, 'sound': {'type': 'choice', 'choices': ['metal', 'bass', 'hit'], 'default': 'metal', 'label': 'Sound'}}, 'default_duration': 2.0, 'default_region': 'bottom', 'sfx': [{'at': 0.13, 'name': 'metal_hit', 'why': 'word lands'}], 'bleed': 300, 'example': {'text': 'ZERO *INVENTORY*'}}

FONTS = {"condensed": "display_condensed", "rounded": "sans_black"}
MAX_SIZE = {("condensed", "normal"): 250.0, ("condensed", "large"): 330.0,
            ("rounded", "normal"): 165.0, ("rounded", "large"): 220.0}
SOUNDS = {"metal": "metal_hit", "bass": "bass_drop", "hit": "synth_hit"}
MAX_WORDS = 4

T_HIT = 0.15          # seconds from a word's start to its landing
T_FADE = 0.12         # the word fades in (in_quad) while it falls: frame 0 is faint, not yet "ink"
START_SCALE = 1.8     # the word starts this big (the bleed holds it)
SQUASH = 0.075        # scale dip on the landing
T_SETTLE = 0.40       # the landing wobble ends exactly here
FLASH_T = 0.15
FLASH_TAU = 0.0185    # flash decay at 30 fps: 0.60, 0.08, 0.01 (only the landing frame passes alpha 24)
SHAKE_T = 0.30
BURST_T = 0.30
N_RAYS = 12
RAY_SPREAD = math.radians(30)   # the lines fan out this much above and below the horizontal
SIDE = 24.0           # free space kept at the box sides
# "banner" chips, as fractions of the font size
CHIP_X, CHIP_Y, CHIP_GAP = 0.20, 0.17, 0.12
TILTS = (-3.0, 2.2, -1.6, 2.8)


# ------------------------------------------------------------------ text and layout

def _lines_of(text: str, upper: bool, font_key: str) -> list[list[tuple[str, bool]]]:
    """'ZERO | *ESTOQUE*' -> [[('ZERO', False)], [('ESTOQUE', True)]] (words the font cannot draw vanish)."""
    lines: list[list[tuple[str, bool]]] = []
    cur: list[tuple[str, bool]] = []
    for word, acc in E.parse_accent(str(text).replace("|", " | ")):
        if word == "|":
            if cur:
                lines.append(cur)
            cur = []
            continue
        w = E.clean_text(word, font_key).strip()
        if not w:
            continue
        cur.append((w.upper() if upper else w, acc))
    if cur:
        lines.append(cur)
    if not lines:
        raise VisualError('impact_word.params.text requires at least one word with supported letters or digits')
    count = sum(len(ln) for ln in lines)
    if count > MAX_WORDS:
        raise VisualError(f'impact_word.params.text has {count} words; maximum {MAX_WORDS}. Keep only the key words')
    if len(lines) > 2:
        raise VisualError(f'impact_word.params.text has {len(lines)} lines; maximum two, separated by one |')
    return lines


def _word_count(params: dict) -> int:
    return sum(len(ln) for ln in _lines_of(params["text"], params["upper"], FONTS[params["font"]]))


def validate(params: dict) -> None:
    """At most 4 words in 2 lines, with letters the font draws (refused when the item is planned)."""
    _lines_of(params["text"], params["upper"], FONTS[params["font"]])


def _hit_times(count: int, stagger: float, fps: int) -> list[float]:
    """Landing of each word in `t_in` time ((frame + 1) / fps), snapped to a frame so the flash peaks on it."""
    return [E.frame_of(k * stagger + T_HIT, fps) / float(fps) for k in range(count)]


@lru_cache(maxsize=64)
def _layout(params_json: str, box: E.Box) -> dict:
    p = json.loads(params_json)
    fk = FONTS[p["font"]]
    banner = p["style"] == "banner"
    lines = _lines_of(p["text"], p["upper"], fk)
    max_size = MAX_SIZE[(p["font"], p["size"])]
    avail = box.w - 2 * SIDE
    space_unit = E.font(fk, 100.0).getlength(" ") / 100.0
    # gap between two words and the extra width around a line, as fractions of the size
    gap_unit = (2 * CHIP_X + CHIP_GAP) if banner else space_unit
    ends_unit = (2 * CHIP_X) if banner else 0.16          # chips, or the outline and the shake

    def unit_width(line) -> float:
        return (sum(E.measure(w, fk, 100.0).width for w, _ in line) / 100.0
                + gap_unit * (len(line) - 1) + ends_unit)

    def fit(line) -> float:
        return min(max_size, avail / unit_width(line))

    if len(lines) == 1 and len(lines[0]) >= 2:
        words = lines[0]
        one = fit(words)
        best = None
        if one < 0.80 * max_size:
            for cut in range(1, len(words)):
                s = min(fit(words[:cut]), fit(words[cut:]))
                if best is None or s > best[0]:
                    best = (s, [words[:cut], words[cut:]])
        if best is not None and best[0] > one * 1.12:
            lines = best[1]
    n = len(lines)
    cap_unit = E.measure("H", fk, 100.0).cap / 100.0
    line_gap_unit = (2 * CHIP_Y + 0.14) if banner else 0.20
    pad_y_unit = CHIP_Y if banner else 0.10
    block_unit = n * cap_unit + (n - 1) * line_gap_unit + 2 * pad_y_unit
    size = min(min(fit(ln) for ln in lines), (box.h - 40.0) / block_unit)
    size = max(40.0, size)

    def width_at(line, s: float) -> float:
        return (sum(E.measure(w, fk, s).width for w, _ in line) + gap_unit * s * (len(line) - 1)
                + ends_unit * s)

    # the widths are checked at the final size (FreeType hinting is not perfectly linear)
    while size > 40.0 and any(width_at(ln, size) > avail + 0.5 for ln in lines):
        size *= 0.985
    cap = E.measure("H", fk, size).cap
    line_gap = line_gap_unit * size
    block_h = n * cap + (n - 1) * line_gap
    top = (box.h - block_h) / 2.0
    gap = gap_unit * size
    dark_value = box.theme.dark
    has_accent = any(a for ln in lines for _, a in ln)
    count = sum(len(ln) for ln in lines)
    px, py = CHIP_X * size, CHIP_Y * size
    words = []
    k = 0
    for i, line in enumerate(lines):
        widths = [E.measure(w, fk, size).width for w, _ in line]
        total = sum(widths) + gap * (len(line) - 1)
        x = (box.w - total) / 2.0
        base = top + cap + i * (cap + line_gap)
        for (text, acc), wd in zip(line, widths):
            w = {"text": text, "x": x, "w": wd, "k": k, "base": base, "cx": x + wd / 2.0, "cy": base - cap / 2.0}
            if banner:
                on_accent = acc if has_accent else (count == 1 or k % 2 == 1)
                w.update(chip=(x - px, base - cap - py, wd + 2 * px, cap + 2 * py),
                         chip_fill="accent" if on_accent else "white",
                         color="on_accent" if on_accent else "ink", stroke_color=None,
                         tilt=TILTS[k % len(TILTS)], ink=(x - px, base - cap - py, x + wd + px, base + py))
            else:
                w.update(color="accent" if acc else ("white" if dark_value else "ink"),
                         stroke_color="black" if dark_value else "white",
                         ink=(x, base - cap, x + wd, base))
            words.append(w)
            x += wd + gap
            k += 1
    hits = _hit_times(k, p["stagger"], box.fps)
    # per landing: the area of the words already on screen (impact lines and the local flash go around it)
    landings = []
    for j, h in enumerate(sorted(set(hits))):
        landed = [w for w in words if hits[w["k"]] <= h + 1e-9]
        x0 = min(w["ink"][0] for w in landed)
        y0 = min(w["ink"][1] for w in landed)
        x1 = max(w["ink"][2] for w in landed)
        y1 = max(w["ink"][3] for w in landed)
        r = E.rng("impact_word", "rays", j)
        ccx, ccy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        rx, ry = (x1 - x0) / 2.0 + 36.0, (y1 - y0) / 2.0 + 28.0
        rays = []
        for m in range(N_RAYS):
            # emphasis lines on the two sides (the placing only moves the item up or down, so lines above and
            # below the words would make it look taller than it is)
            side = 0.0 if m % 2 == 0 else math.pi
            ang = side + RAY_SPREAD * ((m // 2) / (N_RAYS // 2 - 1) * 2.0 - 1.0) + r.uniform(-0.08, 0.08)
            if m % 2:
                ang = side - (ang - side)
            ca, sa = math.cos(ang), math.sin(ang)
            sx, sy = ccx + rx * ca, ccy + ry * sa          # on the ellipse, pointing away from the center
            length = r.uniform(0.55, 1.0) * min(95.0, 0.36 * size + 20.0)
            rays.append({"p0": (sx + ca * 6.0, sy + sa * 6.0),
                         "p1": (sx + ca * (length + 24.0), sy + sa * (length + 24.0)),
                         "width": r.uniform(7.0, 11.0), "delay": r.uniform(0.0, 0.04)})
        landings.append({"hit": h, "first": j == 0, "area": (x0, y0, x1, y1), "rays": rays})
    return {"font": fk, "size": size, "cap": cap, "words": words, "hits": hits, "landings": landings,
            "stroke": size * 0.08, "banner": banner,
            "shadow": E.Shadow(blur=max(6.0, size * 0.05), dy=size * 0.045, opacity=0.55),
            "chip_shadow": E.Shadow(blur=22.0, dy=12.0, opacity=0.35), "chip_radius": size * 0.07}


# ------------------------------------------------------------------ motion curves

def _scale(tk: float) -> float:
    """tk = seconds since the landing (negative before it)."""
    if tk < 0:
        u = E.clamp01(1.0 + tk / T_HIT)
        return E.lerp(START_SCALE, 1.0, E.in_quad(u))
    if tk >= T_SETTLE:
        return 1.0
    env = math.exp(-tk * 12.0) * (1.0 - tk / T_SETTLE)
    return 1.0 - SQUASH * env * math.cos(2 * math.pi * 5.0 * tk)


def _opacity(tk: float) -> float:
    return E.eased(tk, -T_HIT, T_FADE, "in_quad")


def ink(params: dict, box: E.Box) -> tuple[float, float, float, float]:
    """What counts as drawn (box-local design px): the words at rest, their outline and shadow, and a little of
    the landing squash. The flash, the glow and the impact lines are effects and stay out."""
    L = _layout(json.dumps(params, sort_keys=True), box)
    pad = L["stroke"] + L["shadow"].dy + 6.0
    x0 = min(w["ink"][0] for w in L["words"]) - pad
    y0 = min(w["ink"][1] for w in L["words"]) - pad
    x1 = max(w["ink"][2] for w in L["words"]) + pad
    y1 = max(w["ink"][3] for w in L["words"]) + pad
    return x0, y0, x1, y1


def _flash(landing: dict, t: float) -> float:
    tk = t - landing["hit"]
    if not 0.0 <= tk < FLASH_T:
        return 0.0
    peak = 0.60 if landing["first"] else 0.38
    return peak * math.exp(-tk / FLASH_TAU) * (1.0 - tk / FLASH_T)


# ------------------------------------------------------------------ drawing

def _word_outline(cv: E.Canvas, L: dict, w: dict, t: float) -> None:
    tk = t - L["hits"][w["k"]]
    op = _opacity(tk)
    if not cv.visible(op):
        return
    fk, size = L["font"], L["size"]
    pivot = (w["cx"], w["cy"])
    if tk < 0:
        # motion trail: where the word was a moment ago, faint
        sg = _scale(tk - 0.04)
        og = _opacity(tk - 0.04) * 0.16 * op
        if cv.visible(og) and sg > _scale(tk) + 0.01:
            with cv.group(scale=sg, pivot=pivot):
                cv.text(w["x"], w["base"], w["text"], fk, size, color=w["color"], opacity=og)
    with cv.group(scale=_scale(tk), pivot=pivot, opacity=op):
        cv.text(w["x"], w["base"], w["text"], fk, size, color=w["color"], stroke=L["stroke"],
                stroke_color=w["stroke_color"], shadow=L["shadow"])


SPRITE_PAD = 110.0     # design px around a chip sprite: the shadow and the tilt


@lru_cache(maxsize=32)
def _chip_sprite(params_json: str, box: E.Box, k: int) -> tuple:
    """Word k on its chip, with the shadow, tilted, drawn ONCE at the render scale.

    A tilted group costs a full-layer rotation per frame (about 20 ms at 1080x1920); the chip never changes
    shape, so every frame only scales and pastes this sprite. Returns (premultiplied image, absolute render px
    of its top-left, chip center inside it)."""
    L = _layout(params_json, box)
    w = L["words"][k]
    x, y, cw, ch = w["chip"]
    sb = E.with_box(box, x=box.x + x, y=box.y + y, w=cw, h=ch, bleed=SPRITE_PAD)
    sc = E.Canvas(sb)
    with sc.group(rotate=w["tilt"], pivot=(cw / 2.0, ch / 2.0)):
        sc.rect(0.0, 0.0, cw, ch, radius=L["chip_radius"], fill=w["chip_fill"], shadow=L["chip_shadow"])
        sc.text(w["x"] - x, w["base"] - y, w["text"], L["font"], L["size"], color=w["color"])
    cx, cy = sc.px(cw / 2.0, ch / 2.0)
    return sc.image.convert("RGBa"), sc.origin_x, sc.origin_y, cx, cy


def _word_chip(cv: E.Canvas, L: dict, w: dict, t: float, params_json: str) -> None:
    tk = t - L["hits"][w["k"]]
    op = _opacity(tk)
    if not cv.visible(op):
        return
    img, ox, oy, cx, cy = _chip_sprite(params_json, cv.box, w["k"])
    s = _scale(tk)
    left, top = float(ox), float(oy)
    if abs(s - 1.0) > 1e-4:
        size = (max(1, int(round(img.width * s))), max(1, int(round(img.height * s))))
        sx, sy = size[0] / img.width, size[1] / img.height
        img = img.resize(size, Image.BICUBIC)
        left, top = ox + cx - cx * sx, oy + cy - cy * sy      # the chip center stays in place
    sprite = img.convert("RGBA")
    k = cv.box.scale
    cv.paste_image(sprite, left / k - cv.box.x, top / k - cv.box.y, w=sprite.width / k, h=sprite.height / k,
                   opacity=op)


def _rays(cv: E.Canvas, landing: dict, t: float) -> None:
    for ray in landing["rays"]:
        u = E.progress(t, landing["hit"] + ray["delay"], BURST_T)
        if u <= 0 or u >= 1:
            continue
        head = E.out_cubic(min(1.0, u * 1.5))
        tail = E.in_cubic(u)
        if (head - tail) * math.dist(ray["p0"], ray["p1"]) < 10.0:       # no leftover dots
            continue
        cv.polyline([ray["p0"], ray["p1"]], ray["width"], color="accent", start=tail, progress=head)


def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    key = json.dumps(params, sort_keys=True)
    L = _layout(key, box)
    cv = E.Canvas(box, full=params["flash"] == "screen")
    t = (frame_index + 1) / fps          # entrances run one frame early: frame 0 already shows the first word

    if params["flash"] != "none":
        color = "white" if params["flash_color"] == "white" else "accent"
        for landing in L["landings"]:
            fl = _flash(landing, t)
            if fl <= 0.002:
                continue
            if params["flash"] == "screen":
                cv.fill(color, fl)
            else:
                # a flat glow behind the words
                x0, y0, x1, y1 = landing["area"]
                m = cv.rect_mask(x0 - 60, y0 - 12, x1 - x0 + 120, y1 - y0 + 24, radius=(y1 - y0 + 24) / 2.0)
                if m is not None:
                    cv.fill_mask(E.blur_mask(m, cv.len(55), cv.W, cv.H), color, min(1.0, fl * 1.2))

    dx = dy = 0.0
    if params["shake"]:
        amp = 16.0 if L["size"] < 200 else 20.0
        for j, landing in enumerate(L["landings"]):
            sx, sy = E.shake(t, landing["hit"], SHAKE_T, amplitude=amp * (1.0 if j == 0 else 0.7),
                             frequency=20.0, seed=("impact_word", j))
            dx, dy = dx + sx, dy + sy

    with cv.group(dx=dx, dy=dy):
        if params["burst"]:
            for landing in L["landings"]:
                _rays(cv, landing, t)
        for w in L["words"]:
            if L["banner"]:
                _word_chip(cv, L, w, t, key)
            else:
                _word_outline(cv, L, w, t)
    return cv


def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    name = SOUNDS[params["sound"]]
    hits = sorted(set(_hit_times(_word_count(params), params["stagger"], fps)))
    # the landing is drawn on the frame where (frame + 1) / fps == hit
    return [{"frame": max(0, int(round(h * fps)) - 1), "name": name, "volume": 1.0 if j == 0 else 0.8,
             "why": "word lands"} for j, h in enumerate(hits)]


def suggested_duration(params: dict) -> float:
    """The last landing and about 1.4 s to read."""
    last = (_word_count(params) - 1) * params["stagger"] + T_HIT
    return round(max(TEMPLATE["default_duration"], last + 1.4), 2)


def settle(params: dict, fps: int) -> float:
    last = max(_hit_times(_word_count(params), params["stagger"], fps))
    return last - 1.0 / fps + max(T_SETTLE, SHAKE_T, BURST_T + 0.04, FLASH_T) + 0.02
