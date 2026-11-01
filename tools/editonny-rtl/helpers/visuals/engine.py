"""Visual design engine: drawing, easing and timing for the animated templates.

Everything is drawn with Pillow + numpy. Shapes get analytic anti-aliasing from signed distance
fields (exact coverage per pixel, sub-pixel positions, no supersampling); text is FreeType through
Pillow with float sizes and sub-pixel origins, using only the fonts in assets/fonts. Output is
straight-alpha RGBA, deterministic (no clock, no global random state).

Coordinates: templates draw in DESIGN pixels, box-local (origin = top-left of the box, y down).
Design space is the output canvas divided by `k = min(W, H) / 1080`, so 1080x1920 for every vertical
render (full or 540x960 preview). The Canvas multiplies by the render scale itself.

Main pieces:
    Box, Theme, Canvas (rect, circle, ring, line, polyline, polygon, arrow, check, cross, text,
    rich_text, image, group), fonts (font, measure, fit_text, wrap_text), easing (EASINGS, ease, spring),
    timing (seconds, frame_of, progress, eased, stagger, keyframes, pulse, shake, typewriter),
    paths (ellipse_points, bezier_points, trim_points, path_length, wobble), numbers (format_number,
    format_money, format_percent, format_compact, auto_decimals), regions (REGIONS, region_box, safe_area).
"""

from __future__ import annotations

import hashlib
import math
import random
import re
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HELPERS = Path(__file__).resolve().parent.parent
if str(HELPERS) not in sys.path:
    sys.path.insert(0, str(HELPERS))
from common import (  # noqa: E402
    FONTS_DIR,
    SAFE_ZONE_VERTICAL,
    has_arabic,
    hex_to_rgb,
    load_font_for_text,
    load_json,
    shape_arabic,
)

ENGINE_VERSION = 2            # part of every cache key: bump when drawing changes
REF = 1080.0                  # design units: the short side of the canvas is 1080
DEFAULT_ACCENT = "#FFC400"
DEFAULT_BLEED = 80.0          # design px drawn around the box (shadows, overshoot)

RGBA = tuple  # (r, g, b, a) ints 0-255


class VisualError(Exception):
    """A problem with a PT-BR message. `code` 2 = something to fix in the request, 1 = ffmpeg or disk trouble."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


# ============================================================== colors and theme

def _clamp8(v: float) -> int:
    return int(max(0, min(255, round(v))))


def rgba(value, alpha: float | None = None) -> RGBA:
    """'#RRGGBB' | '#RRGGBBAA' | (r, g, b) | (r, g, b, a) -> (r, g, b, a). `alpha` (0-1) replaces the alpha."""
    if isinstance(value, str):
        h = value.strip().lstrip("#")
        try:
            if len(h) == 8:
                r, g, b = hex_to_rgb(h[:6])
                a = int(h[6:], 16)
            else:
                r, g, b = hex_to_rgb(h)
                a = 255
        except ValueError:
            raise VisualError(f'invalid color: {value!r} (use #RRGGBB)') from None
    elif isinstance(value, (tuple, list)) and len(value) in (3, 4):
        r, g, b = (_clamp8(c) for c in value[:3])
        a = _clamp8(value[3]) if len(value) == 4 else 255
    else:
        raise VisualError(f'invalid color: {value!r} (use #RRGGBB)')
    if alpha is not None:
        a = _clamp8(255 * alpha)
    return (r, g, b, a)


def luminance(rgb) -> float:
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2])


def readable_on(bg) -> RGBA:
    """Text color (near-black or white) with the best contrast on a solid color."""
    lum = luminance(bg)
    return (17, 17, 17, 255) if (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05) else (255, 255, 255, 255)


def mix_color(a, b, p: float) -> RGBA:
    a, b = rgba(a), rgba(b)
    return tuple(_clamp8(x + (y - x) * p) for x, y in zip(a, b))  # type: ignore[return-value]


def contrast(a, b) -> float:
    """WCAG contrast ratio of two solid colors (1 to 21). Below about 2.4 an accent does not read on a card."""
    la, lb = luminance(rgba(a) if isinstance(a, str) else a), luminance(rgba(b) if isinstance(b, str) else b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


@dataclass(frozen=True)
class Theme:
    """Palette of one render. `dark` (default) is the glass card of the "glass" hook; `light` a white card."""
    name: str
    accent: RGBA
    on_accent: RGBA
    card: RGBA
    card_border: RGBA
    text: RGBA
    muted: RGBA
    ink: RGBA
    track: RGBA
    danger: RGBA
    success: RGBA
    shadow: RGBA
    white: RGBA = (255, 255, 255, 255)
    black: RGBA = (0, 0, 0, 255)
    fonts: tuple[tuple[str, str], ...] = ()   # font key -> key the theme draws it with
    clay: RGBA | None = None                  # paper: the solid clay base under each card
    style: str = "glass"                      # Card drawing modes: glass, paper, pop, neon.
    dark: bool = True                         # dark card (white text): templates pick their greys by it

    def get(self, name: str) -> RGBA:
        return getattr(self, name)


# Looks of the animations. "dark" and "light" are the original glass and white cards. The "paper" family is
# an off-white paper card on a solid colored clay base, dark ink, and Montserrat in several
# colors, so it is not only lilac: (clay base, ink, default accent, track, hand font). "pop" is a cream card
# with a thick black outline and a hard offset shadow (cut-out look); "neon" a dark card with a glowing
# border in the accent color.
PAPER = {
    "paper": ((199, 183, 239), (53, 44, 64), "#665080", (230, 221, 248), "hand"),       # lilac
    "bluePaper": ((169, 199, 242), (31, 42, 68), "#2457C5", (219, 231, 250), "marker"),
    "greenPaper": ((168, 220, 192), (30, 58, 47), "#1F8A5B", (217, 240, 227), "marker"),
    "orangePaper": ((246, 194, 155), (61, 42, 30), "#E0591F", (251, 227, 209), "hand"),
    "graphitePaper": ((201, 204, 209), (29, 31, 35), "#D6452B", (228, 230, 233), "marker"),
}
THEME_NAMES = ("dark", "light", *PAPER, "pop", "neon")
PAPER_ACCENT = PAPER["paper"][2]
PAPER_FONTS = (("text_bold", "sans_extrabold"), ("text_semibold", "sans_bold"), ("marker", "hand"),
                   ("display_condensed", "sans_black"))
POP_ACCENT, NEON_ACCENT = "#E8412C", "#22D3EE"
COLOR_NAMES = {  # names a template (or a "color" param) may use instead of #RRGGBB
    "accent": "accent", "on_accent": "on_accent", "card": "card", "card_border": "card_border",
    "text": "text", "muted": "muted", "ink": "ink", "track": "track", "danger": "danger",
    "red": "danger", "success": "success", "green": "success",
    "shadow": "shadow", "white": "white", "black": "black",
}


def make_theme(accent=DEFAULT_ACCENT, name: str = "dark") -> Theme:
    def own(default: str) -> RGBA:      # the video's accent, or the look's own when the video keeps the default
        return rgba(default if rgba(accent) == rgba(DEFAULT_ACCENT) else accent)

    if name in PAPER:
        clay, ink, acc_default, track, hand = PAPER[name]
        acc = own(acc_default)
        fonts = tuple((k, hand if k == "marker" else v) for k, v in PAPER_FONTS)
        return Theme(name=name, accent=acc, on_accent=readable_on(acc), card=(255, 253, 253, 250),
                     card_border=(*clay, 255), text=(*ink, 255), muted=(*ink, 190), ink=(*ink, 255),
                     track=(*track, 255), danger=(229, 62, 45, 255), success=(46, 158, 91, 255),
                     shadow=(*ink, 255), fonts=fonts, clay=(*clay, 255), style="paper", dark=False)
    if name == "pop":
        acc = own(POP_ACCENT)
        return Theme(name="pop", accent=acc, on_accent=readable_on(acc), card=(255, 248, 231, 255),
                     card_border=(17, 17, 17, 255), text=(17, 17, 17, 255), muted=(17, 17, 17, 175),
                     ink=(17, 17, 17, 255), track=(17, 17, 17, 40), danger=(235, 45, 45, 255),
                     success=(22, 163, 74, 255), shadow=(17, 17, 17, 255), fonts=PAPER_FONTS[:2] +
                     (("display_condensed", "sans_black"),), style="pop", dark=False)
    if name == "neon":
        acc = own(NEON_ACCENT)
        return Theme(name="neon", accent=acc, on_accent=readable_on(acc), card=(10, 10, 18, 238),
                     card_border=acc, text=(255, 255, 255, 255), muted=(255, 255, 255, 190),
                     ink=(17, 17, 17, 255), track=(255, 255, 255, 38), danger=(255, 69, 58, 255),
                     success=(52, 211, 104, 255), shadow=(0, 0, 0, 255), style="neon", dark=True)
    acc = rgba(accent)
    if name == "light":
        return Theme(name="light", accent=acc, on_accent=readable_on(acc), card=(255, 255, 255, 246),
                     card_border=(0, 0, 0, 22), text=(17, 17, 17, 255), muted=(17, 17, 17, 165),
                     ink=(17, 17, 17, 255), track=(17, 17, 17, 34), danger=(235, 45, 45, 255),
                     success=(22, 163, 74, 255), shadow=(0, 0, 0, 255), dark=False)
    if name != "dark":
        raise VisualError(f"theme '{name}' does not exist; use {', '.join(THEME_NAMES)}")
    return Theme(name="dark", accent=acc, on_accent=readable_on(acc), card=(18, 18, 20, 226),
                 card_border=(255, 255, 255, 30), text=(255, 255, 255, 255), muted=(255, 255, 255, 196),
                 ink=(17, 17, 17, 255), track=(255, 255, 255, 44), danger=(255, 69, 58, 255),
                 success=(52, 211, 104, 255), shadow=(0, 0, 0, 255))


# ============================================================== regions and the box

# Named regions on the vertical design canvas (1080x1920): x, y, w, h. They sit inside the platform-safe
# band (SAFE_ZONE_VERTICAL: top 210, bottom 1550) and clear of the right rail (140 px on each side
# keeps the content centered and off the like/comment column).
REGIONS: dict[str, tuple[float, float, float, float]] = {
    "top": (140.0, 250.0, 800.0, 520.0),
    "center": (140.0, 640.0, 800.0, 640.0),
    "bottom": (140.0, 1000.0, 800.0, 520.0),
    "top_band": (90.0, 230.0, 900.0, 300.0),
    "screen": (90.0, 230.0, 900.0, 1300.0),
}
REGION_LABELS = {"top": "parte de cima", "center": "screen center", "bottom": "parte de baixo",
                 "top_band": 'upper notification band', "screen": 'complete safe area'}
# English names accepted everywhere a region is given (edit_data.json "motion" uses them)
REGION_ALIASES = {name: name for name in REGIONS}


def design_size(canvas: Sequence[int]) -> tuple[float, float]:
    k = min(canvas[0], canvas[1]) / REF
    return canvas[0] / k, canvas[1] / k


def canvas_shape(canvas: Sequence[int]) -> str:
    w, h = canvas[0], canvas[1]
    if h > w * 1.05:
        return "vertical"
    if w > h * 1.05:
        return "horizontal"
    return "square"


def safe_area(canvas: Sequence[int] = (1080, 1920)) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) in design px where content is safe from the platform UI."""
    dw, dh = design_size(canvas)
    if canvas_shape(canvas) == "vertical":
        top = SAFE_ZONE_VERTICAL["top"] * dh / 1920.0
        bottom = SAFE_ZONE_VERTICAL["bottom"] * dh / 1920.0
        return 90.0, top, dw - 90.0, bottom
    return 0.05 * dw, 0.06 * dh, 0.95 * dw, 0.94 * dh


def region_box(spec, canvas: Sequence[int] = (1080, 1920)) -> tuple[float, float, float, float]:
    """A named region or [x, y, w, h] (design px of a vertical 1080x1920 canvas) -> (x, y, w, h) on `canvas`.

    Named regions keep their place relative to the safe area on horizontal and square canvases."""
    dw, dh = design_size(canvas)
    if isinstance(spec, dict) and set(spec) == {"x", "y", "w", "h"}:
        spec = [spec["x"], spec["y"], spec["w"], spec["h"]]
    if isinstance(spec, str):
        spec = REGION_ALIASES.get(spec.strip().lower(), spec.strip().lower())
        if spec not in REGIONS:
            raise VisualError(f"region '{spec}' does not exist; use {', '.join(REGIONS)} or [x, y, width, height])")
        x, y, w, h = REGIONS[spec]
        if canvas_shape(canvas) == "vertical":
            return x, y * dh / 1920.0, w, h * dh / 1920.0
        sx0, sy0, sx1, sy1 = safe_area(canvas)
        v0, v1 = REGIONS["screen"][1], REGIONS["screen"][1] + REGIONS["screen"][3]
        fy0 = (y - v0) / (v1 - v0)
        fy1 = (y + h - v0) / (v1 - v0)
        ww = min(w, sx1 - sx0)
        return (dw - ww) / 2.0, sy0 + fy0 * (sy1 - sy0), ww, (fy1 - fy0) * (sy1 - sy0)
    try:
        x, y, w, h = (float(v) for v in spec)
    except (TypeError, ValueError):
        raise VisualError(f'invalid region: {spec!r} (use a name or [x, y, width, height])') from None
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w < 20 or h < 20:
        raise VisualError(f'invalid region: {spec!r}; width and height must be at least 20')
    if x + w <= 0 or y + h <= 0 or x >= dw or y >= dh:
        raise VisualError(f'region {spec!r} lies outside the canvas ({dw:.0f}x{dh:.0f})')
    return x, y, w, h


@dataclass(frozen=True)
class Box:
    """Where and how one template instance is drawn. Geometry in design px of the output canvas."""
    x: float
    y: float
    w: float
    h: float
    scale: float = 1.0                    # render px per design px
    canvas: tuple[int, int] = (1080, 1920)  # render canvas in px
    fps: int = 30
    frames: int = 90                      # length of the item on screen
    theme: Theme = field(default_factory=make_theme)
    bleed: float = DEFAULT_BLEED

    @property
    def cx(self) -> float:                # box-local center
        return self.w / 2.0

    @property
    def cy(self) -> float:
        return self.h / 2.0

    @property
    def duration(self) -> float:
        return self.frames / float(self.fps)

    @property
    def accent(self) -> RGBA:
        return self.theme.accent

    @property
    def design(self) -> tuple[float, float]:
        return design_size(self.canvas)

    @property
    def safe(self) -> tuple[float, float, float, float]:
        """Safe area in BOX-LOCAL design px (x0, y0, x1, y1)."""
        x0, y0, x1, y1 = safe_area(self.canvas)
        return x0 - self.x, y0 - self.y, x1 - self.x, y1 - self.y

    def to_canvas(self, u: float, v: float) -> tuple[float, float]:
        """Box-local design point -> design point on the whole canvas."""
        return self.x + u, self.y + v

    def from_canvas(self, x: float, y: float) -> tuple[float, float]:
        """Canvas design point (e.g. a target in 1080x1920) -> box-local design point."""
        return x - self.x, y - self.y


def make_box(region, canvas: Sequence[int] = (1080, 1920), fps: int = 30, frames: int = 90,
             accent=DEFAULT_ACCENT, theme: str = "dark", bleed: float = DEFAULT_BLEED) -> Box:
    canvas = (int(canvas[0]), int(canvas[1]))
    x, y, w, h = region_box(region, canvas)
    return Box(x=x, y=y, w=w, h=h, scale=min(canvas) / REF, canvas=canvas, fps=int(fps), frames=int(frames),
               theme=make_theme(accent, theme), bleed=float(bleed))


# ============================================================== easing

def clamp01(x: float) -> float:
    return 0.0 if x <= 0.0 else 1.0 if x >= 1.0 else float(x)


def lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p


def remap(x: float, a0: float, a1: float, b0: float = 0.0, b1: float = 1.0, clamp: bool = True) -> float:
    p = (x - a0) / (a1 - a0) if a1 != a0 else (1.0 if x >= a1 else 0.0)
    if clamp:
        p = clamp01(p)
    return b0 + (b1 - b0) * p


def linear(t: float) -> float:
    return clamp01(t)


def in_quad(t: float) -> float:
    t = clamp01(t)
    return t * t


def out_quad(t: float) -> float:
    t = clamp01(t)
    return 1 - (1 - t) * (1 - t)


def in_out_quad(t: float) -> float:
    t = clamp01(t)
    return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2


def in_cubic(t: float) -> float:
    return clamp01(t) ** 3


def out_cubic(t: float) -> float:
    return 1 - (1 - clamp01(t)) ** 3


def in_out_cubic(t: float) -> float:
    t = clamp01(t)
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def out_quart(t: float) -> float:
    return 1 - (1 - clamp01(t)) ** 4


def out_quint(t: float) -> float:
    return 1 - (1 - clamp01(t)) ** 5


def out_expo(t: float) -> float:
    t = clamp01(t)
    if t >= 1.0:
        return 1.0
    # normalized so out_expo(1) == 1 exactly
    return (1 - 2 ** (-10 * t)) / (1 - 2 ** -10)


def in_out_sine(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * clamp01(t))


def out_sine(t: float) -> float:
    return math.sin(math.pi / 2 * clamp01(t))


def out_back(t: float, overshoot: float = 1.70158) -> float:
    t = clamp01(t) - 1.0
    return 1 + (overshoot + 1) * t ** 3 + overshoot * t ** 2


def in_back(t: float, overshoot: float = 1.70158) -> float:
    t = clamp01(t)
    return (overshoot + 1) * t ** 3 - overshoot * t ** 2


def out_elastic(t: float) -> float:
    t = clamp01(t)
    if t in (0.0, 1.0):
        return t
    return 2 ** (-10 * t) * math.sin((t * 10 - 0.75) * (2 * math.pi / 3)) + 1


def out_bounce(t: float) -> float:
    t = clamp01(t)
    n1, d1 = 7.5625, 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


def spring(t: float, damping: float = 0.5, frequency: float = 2.2) -> float:
    """Damped spring from 0 to 1. `t` in SECONDS since the start, `frequency` in Hz, `damping` 0-1
    (0.3 bouncy, 0.5 lively, 0.8 soft, 1 no overshoot). spring(0) == 0; it settles on 1."""
    if t <= 0:
        return 0.0
    w0 = 2 * math.pi * frequency
    z = min(max(damping, 0.01), 1.0)
    if z >= 0.999:
        return 1 - math.exp(-w0 * t) * (1 + w0 * t)
    wd = w0 * math.sqrt(1 - z * z)
    return 1 - math.exp(-z * w0 * t) * (math.cos(wd * t) + z * w0 / wd * math.sin(wd * t))


def spring_settle(damping: float = 0.5, frequency: float = 2.2, tolerance: float = 0.002) -> float:
    """Seconds until the spring stays within `tolerance` of 1 (envelope bound)."""
    w0 = 2 * math.pi * frequency
    z = min(max(damping, 0.01), 1.0)
    if z >= 0.999:
        t = 0.0
        while abs(1 - spring(t, damping, frequency)) > tolerance and t < 30:
            t += 0.005
        return t
    return math.log(1.0 / (tolerance * math.sqrt(1 - z * z))) / (z * w0)


EASINGS: dict[str, Callable[[float], float]] = {
    "linear": linear, "in_quad": in_quad, "out_quad": out_quad, "in_out_quad": in_out_quad,
    "in_cubic": in_cubic, "out_cubic": out_cubic, "in_out_cubic": in_out_cubic, "out_quart": out_quart,
    "out_quint": out_quint, "out_expo": out_expo, "in_out_sine": in_out_sine, "out_sine": out_sine,
    "out_back": out_back, "in_back": in_back, "out_elastic": out_elastic, "out_bounce": out_bounce,
}


def ease(name_or_fn, t: float) -> float:
    fn = EASINGS[name_or_fn] if isinstance(name_or_fn, str) else name_or_fn
    return fn(t)


# ============================================================== timing

def seconds(frame_index: int, fps: float) -> float:
    return frame_index / float(fps)


def frame_of(t: float, fps: float) -> int:
    """Seconds -> frame with the same rounding compose_final uses (Plan.fr)."""
    return int(round(float(t) * fps + 1e-6))


def progress(t: float, start: float, duration: float) -> float:
    """0 before `start`, 1 after `start + duration`, linear in between."""
    if duration <= 0:
        return 1.0 if t >= start else 0.0
    return clamp01((t - start) / duration)


def eased(t: float, start: float, duration: float, fn="out_cubic") -> float:
    return ease(fn, progress(t, start, duration))


def stagger(index: int, step: float, start: float = 0.0) -> float:
    """Start time of the index-th element of a list that enters one by one."""
    return start + index * step


def keyframes(t: float, keys: Sequence[tuple[float, float]], fn="in_out_cubic") -> float:
    """Value at time t from [(time, value), ...] (sorted), eased between neighbours; held outside."""
    if not keys:
        return 0.0
    if t <= keys[0][0]:
        return float(keys[0][1])
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * ease(fn, (t - t0) / (t1 - t0) if t1 > t0 else 1.0)
    return float(keys[-1][1])


def pulse(t: float, start: float, duration: float) -> float:
    """0 -> 1 -> 0 (half sine) inside [start, start + duration]."""
    p = progress(t, start, duration)
    return math.sin(math.pi * p) if 0 < p < 1 else 0.0


def stable_seed(*parts) -> int:
    return int.from_bytes(hashlib.sha1(repr(parts).encode("utf-8")).digest()[:8], "big")


def rng(*parts) -> random.Random:
    """Deterministic random generator for one purpose (never the global one)."""
    return random.Random(stable_seed(*parts))


def shake(t: float, start: float, duration: float, amplitude: float = 14.0, frequency: float = 18.0,
          seed=0) -> tuple[float, float]:
    """Decaying pseudo-random shake (dx, dy) in design px, deterministic by `seed`."""
    p = progress(t, start, duration)
    if p <= 0 or p >= 1:
        return 0.0, 0.0
    r = rng("shake", seed)
    ph = [r.uniform(0, 2 * math.pi) for _ in range(4)]
    env = (1 - p) ** 2
    tt = (t - start) * 2 * math.pi * frequency
    dx = math.sin(tt + ph[0]) * 0.7 + math.sin(tt * 1.7 + ph[1]) * 0.3
    dy = math.sin(tt * 1.3 + ph[2]) * 0.7 + math.sin(tt * 2.1 + ph[3]) * 0.3
    return amplitude * env * dx, amplitude * env * dy


def typewriter(text: str, t: float, start: float, chars_per_second: float = 14.0) -> str:
    n = int(math.floor(max(0.0, t - start) * chars_per_second + 1e-6))
    return text[:max(0, min(len(text), n))]


# ============================================================== paths

Point = tuple[float, float]


def path_length(points: Sequence[Point]) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def trim_points(points: Sequence[Point], start: float = 0.0, end: float = 1.0) -> list[Point]:
    """The part of a polyline between two fractions of its length (for drawing it by progress)."""
    pts = [tuple(map(float, p)) for p in points]
    if len(pts) < 2:
        return list(pts)
    start, end = clamp01(start), clamp01(end)
    if end <= start:
        return []
    total = path_length(pts)
    if total <= 0:
        return [pts[0], pts[0]]
    a, b = start * total, end * total
    out: list[Point] = []
    run = 0.0
    for p, q in zip(pts, pts[1:]):
        seg = math.dist(p, q)
        s0, s1 = run, run + seg
        run = s1
        if s1 < a or seg <= 0:
            continue
        if s0 > b:
            break
        u0 = max(0.0, (a - s0) / seg)
        u1 = min(1.0, (b - s0) / seg)
        p0 = (p[0] + (q[0] - p[0]) * u0, p[1] + (q[1] - p[1]) * u0)
        p1 = (p[0] + (q[0] - p[0]) * u1, p[1] + (q[1] - p[1]) * u1)
        if not out:
            out.append(p0)
        out.append(p1)
    if len(out) == 1:
        out.append(out[0])
    return out


def point_at(points: Sequence[Point], fraction: float) -> tuple[Point, float]:
    """(point, direction angle in radians) at a fraction of the polyline length."""
    pts = trim_points(points, 0.0, max(1e-6, clamp01(fraction)))
    if len(pts) < 2:
        return (tuple(points[0]) if points else (0.0, 0.0)), 0.0
    (x0, y0), (x1, y1) = pts[-2], pts[-1]
    if (x0, y0) == (x1, y1) and len(points) >= 2:
        (x0, y0), (x1, y1) = points[0], points[1]
    return pts[-1], math.atan2(y1 - y0, x1 - x0)


def bezier_points(p0: Point, p1: Point, p2: Point, p3: Point | None = None, n: int = 32) -> list[Point]:
    """Quadratic (3 points) or cubic (4 points) Bezier sampled into n+1 points."""
    out = []
    for i in range(n + 1):
        u = i / n
        if p3 is None:
            x = (1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * p1[0] + u * u * p2[0]
            y = (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * p1[1] + u * u * p2[1]
        else:
            x = ((1 - u) ** 3 * p0[0] + 3 * (1 - u) ** 2 * u * p1[0] + 3 * (1 - u) * u * u * p2[0] + u ** 3 * p3[0])
            y = ((1 - u) ** 3 * p0[1] + 3 * (1 - u) ** 2 * u * p1[1] + 3 * (1 - u) * u * u * p2[1] + u ** 3 * p3[1])
        out.append((x, y))
    return out


def ellipse_points(cx: float, cy: float, rx: float, ry: float, start_deg: float = -90.0, sweep_deg: float = 360.0,
                   n: int | None = None, wobble: float = 0.0, seed=0) -> list[Point]:
    """Points on an ellipse (clockwise on screen), optionally hand-drawn: `wobble` = radius noise as a
    fraction, and a sweep over 360 overlaps the start like a marker circle."""
    if n is None:
        n = max(24, int(abs(sweep_deg) / 6))
    r = rng("ellipse", seed)
    harm = [(r.uniform(0.6, 1.0), r.uniform(0, 2 * math.pi), k) for k in (2, 3, 5)]
    out = []
    for i in range(n + 1):
        u = i / n
        a = math.radians(start_deg + sweep_deg * u)
        k = 1.0
        if wobble:
            k += wobble * sum(amp * math.sin(kk * a + ph) for amp, ph, kk in harm) / 2.2
            k += wobble * 0.6 * u      # the end drifts outwards, like a pen that overshoots
        out.append((cx + rx * k * math.cos(a), cy + ry * k * math.sin(a)))
    return out


def wobble_points(points: Sequence[Point], amount: float, seed=0, step: float = 18.0) -> list[Point]:
    """Resample a polyline every `step` px and nudge it sideways (hand-drawn look), deterministic."""
    total = path_length(points)
    if total <= 0 or amount <= 0:
        return [tuple(p) for p in points]
    n = max(2, int(total / step))
    r = rng("wobble", seed)
    ph1, ph2 = r.uniform(0, 6.28), r.uniform(0, 6.28)
    out = []
    for i in range(n + 1):
        (x, y), ang = point_at(points, i / n)
        off = amount * (0.65 * math.sin(i * 0.9 + ph1) + 0.35 * math.sin(i * 2.3 + ph2))
        out.append((x - math.sin(ang) * off, y + math.cos(ang) * off))
    return out


# ============================================================== numbers (English defaults)

def auto_decimals(value: float, max_decimals: int = 2) -> int:
    """0 for whole numbers, else up to max_decimals (1.5 -> 1, 1904152.87 -> 2)."""
    try:
        d = Decimal(repr(float(value))).normalize()
    except (InvalidOperation, ValueError):
        return 0
    exp = d.as_tuple().exponent
    return 0 if not isinstance(exp, int) or exp >= 0 else min(max_decimals, -exp)


def format_number(value: float, decimals: int = 0, thousands: str = ",", decimal: str = ".") -> str:
    """1904152.87 -> '1,904,152.87' (half-up rounding, no '-0')."""
    decimals = max(0, int(decimals))
    try:
        d = Decimal(repr(float(value)))
    except (InvalidOperation, ValueError):
        raise VisualError(f'invalid number: {value!r}') from None
    if not d.is_finite():
        raise VisualError(f'invalid number: {value!r}')
    q = d.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    neg = q < 0
    q = abs(q)
    whole, _, fraction_value = f'{q:f}'.partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    out = thousands.join(groups)
    if decimals:
        out += decimal + fraction_value.ljust(decimals, "0")[:decimals]
    if neg and any(c not in "0.," for c in out):
        out = "-" + out
    return out


MONEY_SYMBOLS = ("R$", "US$", "$", "€", "£")


def looks_like_money(*texts: str) -> bool:
    """True when a prefix or suffix is a currency (R$, US$, $, €, £): counters then show cents when they have them."""
    return any(sym in str(t) for t in texts for sym in MONEY_SYMBOLS)


def format_money(value: float, decimals: int = 2, prefix: str = "$") -> str:
    s = format_number(value, decimals)
    if s.startswith("-"):
        return "-" + prefix + s[1:]
    return prefix + s


def format_percent(value: float, decimals: int = 0) -> str:
    return format_number(value, decimals) + "%"


_COMPACT_UNITS = ((1.0, ""), (1e3, "K"), (1e6, "M"), (1e9, "B"), (1e12, "T"))


def _compact_unit(v: float, decimals: int) -> int:
    k = max(i for i, (limit, _) in enumerate(_COMPACT_UNITS) if i == 0 or abs(v) >= limit)
    while k + 1 < len(_COMPACT_UNITS):
        d = auto_decimals(v, decimals) if k == 0 else decimals
        shown = format_number(abs(v) / _COMPACT_UNITS[k][0], d)
        if len(shown.split(".")[0].replace(",", "")) < 4:      # rounding did not reach 1000 of this unit
            break
        k += 1
    return k


def format_compact(value: float, decimals: int = 1, like: float | None = None) -> str:
    """12500 -> '12,5 mil', 1904152 -> '1,9 mi', 2.4e9 -> '2,4 bi', 999999.99 -> '1 mi' (never '1.000 mil').
    `like` = use the unit of that other number (a count keeps the unit of its final value: '0,4 mi' -> '1,9 mi')."""
    v = float(value)
    k = _compact_unit(float(like) if like is not None else v, decimals)
    if k == 0:
        return format_number(v, auto_decimals(v, decimals))
    limit, word = _COMPACT_UNITS[k]
    s = format_number(v / limit, decimals)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return f'{s} {word}'


# ============================================================== fonts and text metrics

_tls = threading.local()
_FONT_MANIFEST: dict | None = None


def font_keys() -> list[str]:
    return sorted(_manifest())


def _manifest() -> dict:
    global _FONT_MANIFEST
    if _FONT_MANIFEST is None:
        _FONT_MANIFEST = load_json(FONTS_DIR / "fonts.json", default={}) or {}
    return _FONT_MANIFEST


@contextmanager
def theme_fonts(theme: "Theme"):
    """While drawing or measuring an item, font keys resolve through its theme (papelaria swaps them)."""
    prev = getattr(_tls, "font_map", None)
    _tls.font_map = dict(theme.fonts) if theme.fonts else None
    try:
        yield
    finally:
        _tls.font_map = prev


def font(key: str, size: float, text: str = "") -> ImageFont.FreeTypeFont:
    """Bundled font (assets/fonts/fonts.json key) or Arabic system fallback at a float pixel size, cached per thread."""
    fmap = getattr(_tls, "font_map", None)
    base_key = key.removesuffix(":ar")
    if fmap:
        base_key = fmap.get(base_key, base_key)
    is_ar = key.endswith(":ar") or has_arabic(text)
    eff_key = f"{base_key}:ar" if is_ar else base_key
    cache = getattr(_tls, "fonts", None)
    if cache is None:
        cache = _tls.fonts = {}
    px = max(4.0, round(float(size) * 8) / 8)
    f = cache.get((eff_key, px))
    if f is None:
        if is_ar:
            f = load_font_for_text(eff_key, px, text or "ع")
        else:
            entry = _manifest().get(base_key)
            if not entry:
                raise VisualError(f"font '{base_key}' does not exist; use {', '.join(font_keys())})")
            f = ImageFont.truetype(str(FONTS_DIR / entry["file"]), size=px)
            if entry.get("variation") is not None:
                try:
                    f.set_variation_by_axes([entry["variation"]])
                except Exception:  # noqa: BLE001  (a static build of the font: keep its default weight)
                    pass
        if len(cache) > 512:
            cache.clear()
        cache[(eff_key, px)] = f
    return f


@dataclass(frozen=True)
class TextMetrics:
    width: float        # advance width (design px)
    cap: float          # cap height
    ascent: float
    descent: float
    x_height: float


def _digit_cell(f: ImageFont.FreeTypeFont) -> float:
    return max(f.getlength(d) for d in "0123456789")


def _advance(f: ImageFont.FreeTypeFont, text: str, tabular: bool, tracking: float) -> float:
    if not tabular and not tracking:
        return f.getlength(text)
    cell = _digit_cell(f) if tabular else 0.0
    w = sum(cell if (tabular and ch.isdigit()) else f.getlength(ch) for ch in text)
    return w + tracking * max(0, len(text) - 1)


def measure(text: str, font_key: str = "sans_black", size: float = 64.0, tabular: bool = False,
            tracking: float = 0.0) -> TextMetrics:
    """Metrics in design px (size is in design px too) of the text as the Canvas draws it."""
    text = clean_text(text, font_key)
    is_ar = has_arabic(text)
    f = font(font_key, size, text)
    s = shape_arabic(text) if is_ar else text
    trk = 0.0 if is_ar else tracking * 1.0
    cap = -f.getbbox("H", anchor="ls")[1]
    xh = -f.getbbox("x", anchor="ls")[1]
    asc, desc = f.getmetrics()
    return TextMetrics(width=_advance(f, s, tabular and not is_ar, trk), cap=float(cap), ascent=float(asc),
                       descent=float(desc), x_height=float(xh))


def clean_text(text: str, font_key: str) -> str:
    """Characters the font cannot draw (emoji...) become a look-alike or go away (text_render rules)."""
    try:
        import text_render as tr  # noqa: PLC0415
        fmap = getattr(_tls, "font_map", None)
        return tr.sanitize_text(str(text), fmap.get(font_key, font_key) if fmap else font_key)[0]
    except Exception:  # noqa: BLE001
        return str(text)


def fit_text(text: str, font_key: str, max_width: float, max_size: float, min_size: float = 12.0,
             tabular: bool = False, tracking: float = 0.0) -> float:
    """Largest size (<= max_size, >= min_size) whose advance fits max_width."""
    w = measure(text, font_key, max_size, tabular, tracking).width
    if w <= max_width or w <= 0:
        return float(max_size)
    size = max_size * max_width / w
    for _ in range(4):
        w = measure(text, font_key, size, tabular, tracking).width
        if w <= max_width:
            break
        size *= max_width / w * 0.995
    return float(max(min_size, size))


def wrap_text(text: str, font_key: str, size: float, max_width: float, max_lines: int = 2) -> list[str]:
    """Balanced lines (the widest line as short as possible), at most max_lines; words never split."""
    words = str(text).split()
    if not words:
        return []
    f = font(font_key, size, text)

    def width(ws):
        return f.getlength(shape_arabic(" ".join(ws)))

    if width(words) <= max_width or max_lines <= 1:
        return [" ".join(words)]
    best = None
    n = len(words)

    def splits(start, lines_left):
        if lines_left == 1:
            yield [words[start:]]
            return
        for cut in range(start + 1, n - lines_left + 2):
            for rest in splits(cut, lines_left - 1):
                yield [words[start:cut]] + rest

    for count in range(2, min(max_lines, n) + 1):
        for parts in splits(0, count):
            widest = max(width(p) for p in parts)
            score = (widest > max_width, widest, count)
            if best is None or score < best[0]:
                best = (score, parts)
        if best and not best[0][0]:
            break
    return [" ".join(p) for p in best[1]] if best else [" ".join(words)]


def parse_accent(text: str) -> list[tuple[str, bool]]:
    """'Compra. *Conecta.* Usa.' -> [('Compra.', False), ('Conecta.', True), ('Usa.', False)] (per word;
    a word touched by the markup is accented whole; '\\*' is a real asterisk)."""
    out = []
    on = False
    for raw in re.split(r"(\s+)", str(text)):
        if not raw or raw.isspace():
            continue
        word, touched = [], False
        i = 0
        while i < len(raw):
            ch = raw[i]
            if ch == "\\" and i + 1 < len(raw) and raw[i + 1] == "*":
                word.append("*")
                i += 2
                continue
            if ch == "*":
                on = not on
                touched = True
                i += 1
                continue
            if on:
                touched = True
            word.append(ch)
            i += 1
        if word:
            out.append(("".join(word), touched))
    return out


# ============================================================== rasterizing helpers (render px)

def _grid(x0: float, y0: float, x1: float, y1: float, W: int, H: int, pad: int = 2):
    ix0, iy0 = max(0, int(math.floor(x0)) - pad), max(0, int(math.floor(y0)) - pad)
    ix1, iy1 = min(W, int(math.ceil(x1)) + pad), min(H, int(math.ceil(y1)) + pad)
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    xs = np.arange(ix0, ix1, dtype=np.float32) + 0.5
    ys = np.arange(iy0, iy1, dtype=np.float32) + 0.5
    return ix0, iy0, xs[None, :], ys[:, None]


def _coverage(d: np.ndarray) -> np.ndarray:
    """Signed distance (px, negative inside) -> coverage 0..1 (1 px wide anti-aliased edge)."""
    return np.clip(0.5 - d, 0.0, 1.0)


def _rrect_sd(px, py, x0, y0, x1, y1, r):
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    hx, hy = (x1 - x0) / 2.0 - r, (y1 - y0) / 2.0 - r
    qx = np.abs(px - cx) - hx
    qy = np.abs(py - cy) - hy
    outside = np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2)
    inside = np.minimum(np.maximum(qx, qy), 0)
    return outside + inside - r


def _seg_dist(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    l2 = vx * vx + vy * vy
    wx, wy = px - ax, py - ay
    if l2 <= 1e-12:
        return np.sqrt(wx * wx + wy * wy)
    t = np.clip((wx * vx + wy * vy) / l2, 0.0, 1.0)
    dx, dy = wx - t * vx, wy - t * vy
    return np.sqrt(dx * dx + dy * dy)


def _inset_convex(pts: list[Point], r: float) -> list[Point] | None:
    """A convex polygon moved inwards by r (None when it would vanish)."""
    n = len(pts)
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n)) / 2.0
    if abs(area) < 1e-9:
        return None
    sign = 1.0 if area > 0 else -1.0
    lines = []
    for i in range(n):
        (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        ln = math.hypot(dx, dy)
        if ln < 1e-9:
            continue
        nx, ny = -dy / ln * sign, dx / ln * sign        # inward normal
        lines.append((ax + nx * r, ay + ny * r, dx, dy))
    out = []
    for (ax, ay, adx, ady), (bx, by, bdx, bdy) in zip(lines, lines[1:] + lines[:1]):
        den = adx * bdy - ady * bdx
        if abs(den) < 1e-12:
            continue
        t = ((bx - ax) * bdy - (by - ay) * bdx) / den
        out.append((ax + adx * t, ay + ady * t))
    if len(out) < 3:
        return None
    new_area = sum(out[i][0] * out[(i + 1) % len(out)][1] - out[(i + 1) % len(out)][0] * out[i][1]
                   for i in range(len(out))) / 2.0
    if new_area * area <= 0 or abs(new_area) < 1.0:
        return None
    return out


@dataclass
class Mask:
    """Coverage (float32 0..1) placed at integer render px (x, y)."""
    a: np.ndarray
    x: int
    y: int

    @property
    def bbox(self):
        return self.x, self.y, self.x + self.a.shape[1], self.y + self.a.shape[0]


def blur_mask(m: Mask, radius: float, W: int, H: int) -> Mask:
    """Gaussian-blurred copy of a mask (grown by 3 radii), computed at half resolution for big radii."""
    if radius < 0.5:
        return m
    pad = int(math.ceil(radius * 3)) + 2
    x0, y0 = max(0, m.x - pad), max(0, m.y - pad)
    x1, y1 = min(W, m.x + m.a.shape[1] + pad), min(H, m.y + m.a.shape[0] + pad)
    if x1 <= x0 or y1 <= y0:
        return Mask(np.zeros((1, 1), np.float32), 0, 0)
    buf = np.zeros((y1 - y0, x1 - x0), np.float32)
    # the mask may pass the canvas edge (a word scaled up near the border): copy only the part inside
    sx0, sy0 = max(m.x, x0), max(m.y, y0)
    sx1, sy1 = min(m.x + m.a.shape[1], x1), min(m.y + m.a.shape[0], y1)
    if sx1 <= sx0 or sy1 <= sy0:
        return Mask(np.zeros((1, 1), np.float32), 0, 0)
    buf[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = m.a[sy0 - m.y:sy1 - m.y, sx0 - m.x:sx1 - m.x]
    img = Image.fromarray(np.clip(buf * 255.0 + 0.5, 0, 255).astype(np.uint8), "L")
    if radius >= 6:
        small = img.resize((max(1, img.width // 2), max(1, img.height // 2)), Image.BILINEAR)
        small = small.filter(ImageFilter.GaussianBlur(radius / 2.0))
        img = small.resize(img.size, Image.BILINEAR)
    else:
        img = img.filter(ImageFilter.GaussianBlur(radius))
    return Mask(np.asarray(img, np.float32) / 255.0, x0, y0)


def clip_mask(m: Mask | None, x0: float | None = None, x1: float | None = None, y0: float | None = None,
              y1: float | None = None) -> Mask | None:
    """The part of a mask inside render-px limits (any of them may be None), with partial coverage on the cut
    lines. A bar rounded on its free end and flat on the axis is a rounded rect cut at the axis."""
    if m is None:
        return None
    a = m.a
    if x0 is not None or x1 is not None:
        cols = np.arange(a.shape[1], dtype=np.float32) + m.x
        k = np.ones_like(cols)
        if x0 is not None:
            k *= np.clip(cols + 1.0 - x0, 0.0, 1.0)
        if x1 is not None:
            k *= np.clip(x1 - cols, 0.0, 1.0)
        a = a * k[None, :]
    if y0 is not None or y1 is not None:
        rows = np.arange(a.shape[0], dtype=np.float32) + m.y
        k = np.ones_like(rows)
        if y0 is not None:
            k *= np.clip(rows + 1.0 - y0, 0.0, 1.0)
        if y1 is not None:
            k *= np.clip(y1 - rows, 0.0, 1.0)
        a = a * k[:, None]
    if not a.any():
        return None
    return Mask(a, m.x, m.y)


def union_masks(masks: Iterable[Mask | None]) -> Mask | None:
    """Union (the highest coverage) of several masks, e.g. a scalloped seal made of circles."""
    ms = [m for m in masks if m is not None]
    if not ms:
        return None
    x0 = min(m.x for m in ms)
    y0 = min(m.y for m in ms)
    x1 = max(m.x + m.a.shape[1] for m in ms)
    y1 = max(m.y + m.a.shape[0] for m in ms)
    out = np.zeros((y1 - y0, x1 - x0), np.float32)
    for m in ms:
        sub = out[m.y - y0:m.y - y0 + m.a.shape[0], m.x - x0:m.x - x0 + m.a.shape[1]]
        np.maximum(sub, m.a, out=sub)
    return Mask(out, x0, y0)


def intersect_masks(a: Mask | None, b: Mask | None) -> Mask | None:
    """Coverage inside both masks (clips a shape to another, e.g. a silhouette to its round avatar)."""
    if a is None or b is None:
        return None
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1 = min(a.x + a.a.shape[1], b.x + b.a.shape[1])
    y1 = min(a.y + a.a.shape[0], b.y + b.a.shape[0])
    if x1 <= x0 or y1 <= y0:
        return None
    return Mask(a.a[y0 - a.y:y1 - a.y, x0 - a.x:x1 - a.x] * b.a[y0 - b.y:y1 - b.y, x0 - b.x:x1 - b.x], x0, y0)


# Masks and blurred shadows of plain shapes, reused while a card stands still (a card's shadow blur is
# 10-20 ms at 1080x1920). Keyed by the exact render-px geometry; the arrays are read-only.
_MASK_CACHE: "dict[tuple, Mask]" = {}
_MASK_CACHE_LOCK = threading.Lock()
_MASK_CACHE_MAX = 96


def _cached_mask(key: tuple, make: Callable[[], "Mask | None"]) -> "Mask | None":
    with _MASK_CACHE_LOCK:
        hit = _MASK_CACHE.get(key)
        if hit is not None:
            _MASK_CACHE[key] = _MASK_CACHE.pop(key)          # most recent last
            return Mask(hit.a, hit.x, hit.y)
    m = make()
    if m is None:
        return None
    m.a.flags.writeable = False
    with _MASK_CACHE_LOCK:
        _MASK_CACHE[key] = m
        while len(_MASK_CACHE) > _MASK_CACHE_MAX:
            _MASK_CACHE.pop(next(iter(_MASK_CACHE)))
    return Mask(m.a, m.x, m.y)


def heart_points(cx: float, cy: float, size: float, n: int = 64, closed: bool = True) -> list[Point]:
    """Outline of a heart centered on (cx, cy), `size` wide (generic 'like' glyph)."""
    s = size / 34.0
    pts = []
    for i in range(n + 1 if closed else n):
        t = 2 * math.pi * i / n
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x * s, cy - (y + 2.5) * s))
    return pts


@dataclass(frozen=True)
class Shadow:
    blur: float = 26.0       # design px
    dy: float = 14.0
    dx: float = 0.0
    opacity: float = 0.42
    color: tuple = (0, 0, 0)


@dataclass(frozen=True)
class Gradient:
    """Linear gradient fill: color c0 at `start`, c1 at `end` (box-local design px), clamped outside."""
    c0: object
    c1: object
    start: tuple = (0.0, 0.0)
    end: tuple = (0.0, 100.0)


SOFT_SHADOW = Shadow()
TEXT_SHADOW = Shadow(blur=10.0, dy=5.0, opacity=0.55)


def _shadow_of(value) -> Shadow | None:
    if value is None or value is False:
        return None
    if value is True:
        return SOFT_SHADOW
    if isinstance(value, Shadow):
        return value
    if isinstance(value, dict):
        return Shadow(**value)
    raise VisualError(f'invalid shadow: {value!r}')


# ============================================================== canvas

@dataclass
class _Layer:
    image: Image.Image
    dirty: list[int] | None = None


@dataclass
class _Transform:
    a: float = 1.0      # uniform scale (design -> design)
    bx: float = 0.0
    by: float = 0.0

    def point(self, u: float, v: float) -> tuple[float, float]:
        return self.a * u + self.bx, self.a * v + self.by


class Canvas:
    """Transparent drawing surface for one frame of one box (the box plus `bleed` all around).

    Every coordinate and size is in box-local DESIGN px. Colors: '#RRGGBB', (r, g, b[, a]) or a theme
    name ('accent', 'text', 'muted', 'card', 'danger', 'success', 'on_accent', 'track', 'white', 'black',
    and English semantic color names).
    """

    def __init__(self, box: Box, full: bool = False):
        self.box = box
        s = box.scale
        self.s = s
        self.full = bool(full)
        if self.full:          # the whole output canvas (full-screen flash, drawing far from the box)
            self.origin_x = self.origin_y = 0
            self.W, self.H = int(box.canvas[0]), int(box.canvas[1])
        else:
            self.origin_x = int(math.floor((box.x - box.bleed) * s))
            self.origin_y = int(math.floor((box.y - box.bleed) * s))
            self.W = int(math.ceil((box.x + box.w + box.bleed) * s)) - self.origin_x
            self.H = int(math.ceil((box.y + box.h + box.bleed) * s)) - self.origin_y
        self.image = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        self._targets: list[Image.Image] = [self.image]
        self._dirty: list[list[int] | None] = [None]     # drawn area of each target (render px)
        self._tf = [_Transform()]

    # ---------------------------------------------------------- basics
    @property
    def w(self) -> float:
        return self.box.w

    @property
    def h(self) -> float:
        return self.box.h

    @property
    def cx(self) -> float:
        return self.box.w / 2.0

    @property
    def cy(self) -> float:
        return self.box.h / 2.0

    @property
    def theme(self) -> Theme:
        return self.box.theme

    @property
    def offset(self) -> tuple[int, int]:
        """Render px of this canvas' top-left on the output canvas."""
        return self.origin_x, self.origin_y

    def color(self, value, opacity: float = 1.0) -> RGBA:
        if isinstance(value, str) and not value.startswith("#"):
            key = COLOR_NAMES.get(value.strip().lower())
            if key is None:
                raise VisualError(f"color '{value}' does not exist; use #RRGGBB or {', '.join(sorted(set(COLOR_NAMES)))})")
            c = self.theme.get(key)
        else:
            c = rgba(value)
        return (c[0], c[1], c[2], _clamp8(c[3] * opacity))

    def px(self, u: float, v: float) -> tuple[float, float]:
        """Box-local design point (through the group transform) -> render px of this canvas."""
        x, y = self._tf[-1].point(u, v)
        s = self.s
        return (self.box.x + x) * s - self.origin_x, (self.box.y + y) * s - self.origin_y

    def len(self, d: float) -> float:
        """Design length -> render px (through the group scale)."""
        return d * self._tf[-1].a * self.s

    @property
    def _target(self) -> Image.Image:
        return self._targets[-1]

    def _mark(self, x0: int, y0: int, x1: int, y1: int) -> None:
        d = self._dirty[-1]
        if d is None:
            self._dirty[-1] = [x0, y0, x1, y1]
        else:
            d[0], d[1], d[2], d[3] = min(d[0], x0), min(d[1], y0), max(d[2], x1), max(d[3], y1)

    def _paste_layer(self, img: Image.Image, x: int, y: int) -> None:
        """alpha_composite an image at (x, y) of the current target, clipped, marking the drawn area."""
        tgt = self._target
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(tgt.width, x + img.width), min(tgt.height, y + img.height)
        if x1 <= x0 or y1 <= y0:
            return
        part = img if (x0, y0, x1, y1) == (x, y, x + img.width, y + img.height) else\
            img.crop((x0 - x, y0 - y, x1 - x, y1 - y))
        tgt.alpha_composite(part, (x0, y0))
        self._mark(x0, y0, x1, y1)

    def fill_mask(self, m: Mask | None, color, opacity: float = 1.0) -> None:
        """Composite a color (or a Gradient) through a coverage mask (straight alpha, real blending)."""
        if m is None or opacity <= 0:
            return
        tgt = self._target
        x0, y0 = max(0, m.x), max(0, m.y)
        x1, y1 = min(tgt.width, m.x + m.a.shape[1]), min(tgt.height, m.y + m.a.shape[0])
        if x1 <= x0 or y1 <= y0:
            return
        a = m.a[y0 - m.y:y1 - m.y, x0 - m.x:x1 - m.x]
        if isinstance(color, Gradient):
            c0, c1 = self.color(color.c0, opacity), self.color(color.c1, opacity)
            sx, sy = self.px(*color.start)
            ex, ey = self.px(*color.end)
            vx, vy = ex - sx, ey - sy
            l2 = max(1e-9, vx * vx + vy * vy)
            xs = np.arange(x0, x1, dtype=np.float32)[None, :] + 0.5
            ys = np.arange(y0, y1, dtype=np.float32)[:, None] + 0.5
            t = np.clip(((xs - sx) * vx + (ys - sy) * vy) / l2, 0.0, 1.0)
            lo, hi = np.array(c0, np.float32), np.array(c1, np.float32)
            rgba_px = lo[None, None, :] + (hi - lo)[None, None, :] * t[..., None]
            rgba_px[..., 3] *= a
            arr = np.clip(rgba_px + 0.5, 0, 255).astype(np.uint8)
            if not arr[..., 3].any():
                return
            tgt.alpha_composite(Image.fromarray(np.ascontiguousarray(arr), "RGBA"), (x0, y0))
            self._mark(x0, y0, x1, y1)
            return
        c = self.color(color, opacity)
        if c[3] <= 0:
            return
        if c[3] < 255:
            a = a * (c[3] / 255.0)
        a8 = np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)
        if not a8.any():
            return
        src = Image.new("RGBA", (x1 - x0, y1 - y0), (c[0], c[1], c[2], 255))
        src.putalpha(Image.fromarray(np.ascontiguousarray(a8), "L"))
        tgt.alpha_composite(src, (x0, y0))
        self._mark(x0, y0, x1, y1)

    def erase_mask(self, m: Mask | None, amount: float = 1.0) -> None:
        """Cut a hole: the drawing under the mask becomes transparent (amount 0-1)."""
        if m is None or amount <= 0:
            return
        tgt = self._target
        x0, y0 = max(0, m.x), max(0, m.y)
        x1, y1 = min(tgt.width, m.x + m.a.shape[1]), min(tgt.height, m.y + m.a.shape[0])
        if x1 <= x0 or y1 <= y0:
            return
        a = m.a[y0 - m.y:y1 - m.y, x0 - m.x:x1 - m.x] * min(1.0, amount)
        region = tgt.crop((x0, y0, x1, y1))
        al = np.asarray(region.getchannel("A"), np.float32) * (1.0 - a)
        region.putalpha(Image.fromarray(np.clip(al + 0.5, 0, 255).astype(np.uint8), "L"))
        tgt.paste(region, (x0, y0))
        self._mark(x0, y0, x1, y1)

    def erase_circle(self, cx: float, cy: float, r: float, amount: float = 1.0) -> None:
        self.erase_mask(self.circle_mask(cx, cy, r), amount)

    def erase_rect(self, x: float, y: float, w: float, h: float, radius: float = 0.0, amount: float = 1.0) -> None:
        self.erase_mask(self.rect_mask(x, y, w, h, radius), amount)

    def fill(self, color="white", opacity: float = 1.0) -> None:
        """Paint the whole canvas (a full-screen flash needs Canvas(box, full=True))."""
        self.fill_mask(Mask(np.ones((self.H, self.W), np.float32), 0, 0), color, opacity)

    def _cast_shadow(self, m: Mask | None, shadow: Shadow | None, opacity: float, key: tuple | None = None) -> None:
        """Soft shadow of a mask. `key` (the exact render-px geometry of a plain shape) reuses the blur."""
        if m is None or shadow is None or opacity <= 0:
            return
        radius = self.len(shadow.blur)
        if key is not None:
            b = _cached_mask(("blur", key, round(radius, 4), self.W, self.H), lambda: blur_mask(m, radius, self.W, self.H))
            if b is None:
                return
        else:
            b = blur_mask(m, radius, self.W, self.H)
        b = Mask(b.a, b.x + int(round(self.len(shadow.dx))), b.y + int(round(self.len(shadow.dy))))
        self.fill_mask(b, tuple(shadow.color), shadow.opacity * opacity)

    # ---------------------------------------------------------- shape masks (render px)
    def _rect_geometry(self, x: float, y: float, w: float, h: float, radius: float, inset: float) -> tuple | None:
        X0, Y0 = self.px(x, y)
        X1, Y1 = self.px(x + w, y + h)
        ins = self.len(inset)
        X0, Y0, X1, Y1 = X0 + ins, Y0 + ins, X1 - ins, Y1 - ins
        if X1 - X0 <= 0.05 or Y1 - Y0 <= 0.05:
            return None
        r = max(0.0, min(self.len(radius) - ins, (X1 - X0) / 2.0, (Y1 - Y0) / 2.0))
        return ("rect", X0, Y0, X1, Y1, r, self.W, self.H)

    @staticmethod
    def _rect_from(geo: tuple) -> Mask | None:
        _, X0, Y0, X1, Y1, r, W, H = geo
        g = _grid(X0, Y0, X1, Y1, W, H)
        if g is None:
            return None
        ix, iy, px, py = g
        return Mask(_coverage(_rrect_sd(px, py, X0, Y0, X1, Y1, r)).astype(np.float32), ix, iy)

    def rect_mask(self, x: float, y: float, w: float, h: float, radius: float = 0.0, inset: float = 0.0) -> Mask | None:
        geo = self._rect_geometry(x, y, w, h, radius, inset)
        if geo is None:
            return None
        return _cached_mask(geo, lambda: self._rect_from(geo))

    def circle_mask(self, cx: float, cy: float, r: float) -> Mask | None:
        X, Y = self.px(cx, cy)
        R = self.len(r)
        if R <= 0.05:
            return None

        def make() -> Mask | None:
            g = _grid(X - R, Y - R, X + R, Y + R, self.W, self.H)
            if g is None:
                return None
            ix, iy, px, py = g
            d = np.sqrt((px - X) ** 2 + (py - Y) ** 2) - R
            return Mask(_coverage(d).astype(np.float32), ix, iy)

        return _cached_mask(("circle", X, Y, R, self.W, self.H), make)

    def stroke_mask(self, points: Sequence[Point], width: float, widths: Sequence[float] | None = None) -> Mask | None:
        """Round-capped, round-joined stroke through points (union, so crossings never double the alpha)."""
        pts = [self.px(*p) for p in points]
        if len(pts) == 1:
            pts = pts * 2
        if len(pts) < 2:
            return None
        rads = [self.len(wd) / 2.0 for wd in widths] if widths else [self.len(width) / 2.0] * len(pts)
        if len(rads) != len(pts):
            raise VisualError('widths requires one value per point')
        rmax = max(rads)
        if rmax <= 0.02:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        g = _grid(min(xs) - rmax, min(ys) - rmax, max(xs) + rmax, max(ys) + rmax, self.W, self.H)
        if g is None:
            return None
        ix, iy, gx, gy = g
        hgt, wid = gy.shape[0], gx.shape[1]
        best = np.full((hgt, wid), np.inf, np.float32)
        for (ax, ay), (bx, by), ra, rb in zip(pts, pts[1:], rads, rads[1:]):
            r = max(ra, rb)
            sx0 = max(0, int(math.floor(min(ax, bx) - r)) - 1 - ix)
            sx1 = min(wid, int(math.ceil(max(ax, bx) + r)) + 2 - ix)
            sy0 = max(0, int(math.floor(min(ay, by) - r)) - 1 - iy)
            sy1 = min(hgt, int(math.ceil(max(ay, by) + r)) + 2 - iy)
            if sx1 <= sx0 or sy1 <= sy0:
                continue
            px = gx[:, sx0:sx1]
            py = gy[sy0:sy1, :]
            if abs(ra - rb) < 1e-6:
                d = _seg_dist(px, py, ax, ay, bx, by) - ra
            else:   # tapered: radius interpolated along the segment
                vx, vy = bx - ax, by - ay
                l2 = max(1e-12, vx * vx + vy * vy)
                t = np.clip(((px - ax) * vx + (py - ay) * vy) / l2, 0.0, 1.0)
                dx, dy = px - ax - t * vx, py - ay - t * vy
                d = np.sqrt(dx * dx + dy * dy) - (ra + (rb - ra) * t)
            sub = best[sy0:sy1, sx0:sx1]
            np.minimum(sub, d.astype(np.float32), out=sub)
        return Mask(_coverage(best).astype(np.float32), ix, iy)

    def polygon_mask(self, points: Sequence[Point], radius: float = 0.0) -> Mask | None:
        """Filled polygon; `radius` rounds the corners of a CONVEX polygon."""
        pts = [self.px(*p) for p in points]
        if len(pts) < 3:
            return None
        grow = 0.0
        if radius > 0:
            r = self.len(radius)
            inner = _inset_convex(pts, r)
            if inner is not None:
                pts, grow = inner, r
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        g = _grid(min(xs) - grow, min(ys) - grow, max(xs) + grow, max(ys) + grow, self.W, self.H)
        if g is None:
            return None
        ix, iy, px, py = g
        px = np.broadcast_to(px, (py.shape[0], px.shape[1]))
        py = np.broadcast_to(py, px.shape)
        d = np.full(px.shape, np.inf, np.float32)
        inside = np.zeros(px.shape, bool)
        for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
            np.minimum(d, _seg_dist(px, py, ax, ay, bx, by).astype(np.float32), out=d)
            cond = (ay > py) != (by > py)
            with np.errstate(divide="ignore", invalid="ignore"):
                xint = (bx - ax) * (py - ay) / (by - ay) + ax
            inside ^= cond & (px < xint)
        sd = np.where(inside, -d, d) - grow
        return Mask(_coverage(sd).astype(np.float32), ix, iy)

    # ---------------------------------------------------------- shapes
    def rect(self, x: float, y: float, w: float, h: float, radius: float = 0.0, fill="card", opacity: float = 1.0,
             stroke=None, stroke_width: float = 0.0, shadow=None) -> None:
        """Rounded rectangle; `stroke` draws an inner border of `stroke_width`. With `fill=None` the shadow is
        the border's own (an outline casts no filled shadow)."""
        geo = self._rect_geometry(x, y, w, h, radius, 0.0)
        m = self.rect_mask(x, y, w, h, radius)
        if m is None:
            return
        ring = None
        if stroke is not None and stroke_width > 0:
            inner = self.rect_mask(x, y, w, h, radius, inset=stroke_width)
            a = m.a.copy()
            if inner is not None:
                oy, ox = inner.y - m.y, inner.x - m.x
                a[oy:oy + inner.a.shape[0], ox:ox + inner.a.shape[1]] -= inner.a
            ring = Mask(np.clip(a, 0, 1), m.x, m.y)
        if fill is not None:
            self._cast_shadow(m, _shadow_of(shadow), opacity, key=geo)
            self.fill_mask(m, fill, opacity)
        elif ring is not None:
            self._cast_shadow(ring, _shadow_of(shadow), opacity, key=(geo, "ring", self.len(stroke_width)))
        if ring is not None:
            self.fill_mask(ring, stroke, opacity)

    def pill(self, x: float, y: float, w: float, h: float, **kw) -> None:
        self.rect(x, y, w, h, radius=h / 2.0, **kw)

    def card(self, x: float, y: float, w: float, h: float, radius: float = 40.0, opacity: float = 1.0,
             shadow=True, fill="card", border: bool = True) -> None:
        """The standard card of the templates: theme fill, hairline border, soft shadow. In papelaria the
        card sits on a solid lilac clay base 10 px lower, as in the VSL pieces."""
        th = self.theme
        if fill == "card" and th.style == "paper" and th.clay is not None:
            self.rect(x, y + 10.0, w, h, radius=radius, fill=th.clay, opacity=opacity,
                      shadow=Shadow(blur=26.0, dy=14.0, opacity=0.22, color=tuple(th.ink[:3])) if shadow else None)
            shadow = False
        elif fill == "card" and th.style == "pop":       # hard black shadow, thick black outline
            self.rect(x + 9.0, y + 9.0, w, h, radius=radius, fill=(17, 17, 17, 255), opacity=opacity)
            self.rect(x, y, w, h, radius=radius, fill=fill, opacity=opacity, stroke="card_border", stroke_width=4.5)
            return
        elif fill == "card" and th.style == "neon":      # glow in the accent color around a thin bright border
            self.rect(x, y, w, h, radius=radius, fill=fill, opacity=opacity,
                      shadow=Shadow(blur=34.0, dy=0.0, opacity=0.75, color=tuple(th.accent[:3])),
                      stroke="accent", stroke_width=3.0)
            return
        self.rect(x, y, w, h, radius=radius, fill=fill, opacity=opacity, shadow=shadow,
                  stroke="card_border" if border else None, stroke_width=2.0 if border else 0.0)

    def circle(self, cx: float, cy: float, r: float, fill="accent", opacity: float = 1.0, stroke=None,
               stroke_width: float = 0.0, shadow=None) -> None:
        m = self.circle_mask(cx, cy, r)
        if m is None:
            return
        X, Y = self.px(cx, cy)
        if fill is not None:
            self._cast_shadow(m, _shadow_of(shadow), opacity, key=("circle", X, Y, self.len(r), self.W, self.H))
            self.fill_mask(m, fill, opacity)
        if stroke is not None and stroke_width > 0:
            if fill is None and shadow:
                ring = self._ring_mask(cx, cy, r - stroke_width / 2.0, stroke_width)
                self._cast_shadow(ring, _shadow_of(shadow), opacity,
                                  key=("ring", X, Y, self.len(r), self.len(stroke_width), self.W, self.H))
            self.ring(cx, cy, r - stroke_width / 2.0, stroke_width, color=stroke, opacity=opacity)

    def _ring_mask(self, cx: float, cy: float, r: float, width: float) -> Mask | None:
        X, Y = self.px(cx, cy)
        R, half = self.len(r), self.len(width) / 2.0

        def make() -> Mask | None:
            g = _grid(X - R - half, Y - R - half, X + R + half, Y + R + half, self.W, self.H)
            if g is None:
                return None
            ix, iy, px, py = g
            d = np.abs(np.sqrt((px - X) ** 2 + (py - Y) ** 2) - R) - half
            return Mask(_coverage(d).astype(np.float32), ix, iy)

        return _cached_mask(("ringmask", X, Y, R, half, self.W, self.H), make)

    def ring(self, cx: float, cy: float, r: float, width: float, color="accent", opacity: float = 1.0,
             progress: float = 1.0, start_deg: float = -90.0) -> None:
        """Circle outline (centered on r); progress < 1 draws an arc clockwise from start_deg with round caps."""
        if progress <= 0:
            return
        if progress >= 1:
            self.fill_mask(self._ring_mask(cx, cy, r, width), color, opacity)
            return
        pts = ellipse_points(cx, cy, r, r, start_deg, 360.0 * clamp01(progress))
        self.polyline(pts, width, color=color, opacity=opacity)

    def line(self, x0: float, y0: float, x1: float, y1: float, width: float = 8.0, color="accent",
             opacity: float = 1.0, progress: float = 1.0, shadow=None) -> None:
        self.polyline([(x0, y0), (x1, y1)], width, color=color, opacity=opacity, progress=progress, shadow=shadow)

    def polyline(self, points: Sequence[Point], width: float = 8.0, color="accent", opacity: float = 1.0,
                 progress: float = 1.0, start: float = 0.0, widths: Sequence[float] | None = None,
                 shadow=None) -> None:
        """Stroke with round caps; draws the [start, progress] part of the path (by length)."""
        if progress <= start or opacity <= 0:
            return
        pts = list(points)
        ws = None
        if progress < 1 or start > 0:
            if widths:
                # keep one width per kept point: resample widths along the trimmed path
                total = path_length(pts)
                cum = [0.0]
                for p, q in zip(pts, pts[1:]):
                    cum.append(cum[-1] + math.dist(p, q))
                pts = trim_points(pts, start, progress)
                run = start * total
                ws = []
                for i, p in enumerate(pts):
                    if i:
                        run += math.dist(pts[i - 1], p)
                    ws.append(float(np.interp(run, cum, list(widths))))
            else:
                pts = trim_points(pts, start, progress)
        elif widths:
            ws = list(widths)
        m = self.stroke_mask(pts, width, ws)
        self._cast_shadow(m, _shadow_of(shadow), opacity)
        self.fill_mask(m, color, opacity)

    def polygon(self, points: Sequence[Point], fill="accent", opacity: float = 1.0, shadow=None,
                radius: float = 0.0) -> None:
        """Filled polygon (radius rounds the corners of a convex one)."""
        m = self.polygon_mask(points, radius)
        self._cast_shadow(m, _shadow_of(shadow), opacity)
        self.fill_mask(m, fill, opacity)

    def arrow(self, x0: float, y0: float, x1: float, y1: float, width: float = 10.0, color="accent",
              opacity: float = 1.0, progress: float = 1.0, head: float | None = None, curve: float = 0.0,
              filled: bool = False, shadow=None) -> None:
        """Arrow from (x0, y0) to (x1, y1), drawn by progress; `curve` bends it sideways (fraction of its length).
        The head grows in over the last 25% of the progress."""
        if progress <= 0:
            return
        L = math.dist((x0, y0), (x1, y1))
        if curve:
            mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            nx, ny = -(y1 - y0) / max(L, 1e-6), (x1 - x0) / max(L, 1e-6)
            pts = bezier_points((x0, y0), (mx + nx * curve * L, my + ny * curve * L), (x1, y1), n=40)
        else:
            pts = [(x0, y0), (x1, y1)]
        head = head if head is not None else max(width * 3.2, 26.0)
        self.polyline(pts, width, color=color, opacity=opacity, progress=progress, shadow=shadow)
        hp = remap(progress, 0.75, 1.0)
        if hp <= 0:
            return
        (tx, ty), ang = point_at(pts, progress)
        size = head * out_back(hp, 2.2)
        wing = math.radians(32)
        a1, a2 = ang + math.pi - wing, ang + math.pi + wing
        p1 = (tx + size * math.cos(a1), ty + size * math.sin(a1))
        p2 = (tx + size * math.cos(a2), ty + size * math.sin(a2))
        if filled:
            tip = (tx + width * 0.6 * math.cos(ang), ty + width * 0.6 * math.sin(ang))
            self.polygon([tip, p1, p2], fill=color, opacity=opacity)
        else:
            self.polyline([p1, (tx, ty), p2], width, color=color, opacity=opacity)

    def check(self, cx: float, cy: float, size: float, width: float | None = None, color="white",
              opacity: float = 1.0, progress: float = 1.0) -> None:
        """Check mark centered on (cx, cy), `size` = its width; drawn stroke by stroke with progress."""
        s = size
        pts = [(cx - 0.40 * s, cy + 0.02 * s), (cx - 0.12 * s, cy + 0.28 * s), (cx + 0.42 * s, cy - 0.30 * s)]
        self.polyline(pts, width if width is not None else s * 0.16, color=color, opacity=opacity,
                      progress=out_quad(progress) if progress < 1 else 1.0)

    def cross(self, cx: float, cy: float, size: float, width: float | None = None, color="white",
              opacity: float = 1.0, progress: float = 1.0) -> None:
        """X mark: first stroke in the first half of the progress, the second in the other half."""
        q = size / 2.0
        wd = width if width is not None else size * 0.16
        self.polyline([(cx - q, cy - q), (cx + q, cy + q)], wd, color=color, opacity=opacity,
                      progress=clamp01(progress * 2))
        if progress > 0.5:
            self.polyline([(cx + q, cy - q), (cx - q, cy + q)], wd, color=color, opacity=opacity,
                          progress=clamp01(progress * 2 - 1))

    def glow(self, draw: Callable[[], None], radius: float = 24.0, opacity: float = 0.6, color="accent") -> None:
        """Soft colored glow behind whatever `draw()` paints, then the sharp drawing on top."""
        with self._layer() as lay:
            draw()
        if lay.dirty is None:
            return
        x0, y0, x1, y1 = lay.dirty
        part = lay.image.crop((x0, y0, x1, y1))
        if opacity > 0:
            a = np.asarray(part.getchannel("A"), np.float32) / 255.0
            m = blur_mask(Mask(a, x0, y0), self.len(radius), self.W, self.H)
            self.fill_mask(Mask(np.clip(m.a * 1.6, 0, 1), m.x, m.y), color, opacity)
        self._paste_layer(part, x0, y0)

    # ---------------------------------------------------------- text
    def _text_mask(self, x: float, y: float, text: str, f: ImageFont.FreeTypeFont, stroke_px: float,
                   tabular: bool, tracking_px: float) -> Mask | None:
        """Mask of a text whose baseline-left origin is at render px (x, y)."""
        if not text:
            return None
        asc, desc = f.getmetrics()
        pad = int(math.ceil(stroke_px)) + 4
        adv = _advance(f, text, tabular, tracking_px)
        left = int(math.floor(x)) - pad - int(f.size * 0.25)
        top = int(math.floor(y - asc * 1.25)) - pad
        wpx = int(math.ceil(adv + f.size * 0.5)) + 2 * pad
        hpx = int(math.ceil((asc * 1.25) + desc * 1.4)) + 2 * pad
        if left >= self.W or top >= self.H or left + wpx <= 0 or top + hpx <= 0:
            return None
        img = Image.new("L", (wpx, hpx), 0)
        d = ImageDraw.Draw(img)
        ox, oy = x - left, y - top
        kw = {"stroke_width": stroke_px, "stroke_fill": 255} if stroke_px > 0 else {}
        if tabular or tracking_px:
            cell = _digit_cell(f) if tabular else 0.0
            for ch in text:
                wch = f.getlength(ch)
                if tabular and ch.isdigit():
                    d.text((ox + (cell - wch) / 2.0, oy), ch, font=f, fill=255, anchor="ls", **kw)
                    ox += cell
                else:
                    d.text((ox, oy), ch, font=f, fill=255, anchor="ls", **kw)
                    ox += wch
                ox += tracking_px
        else:
            d.text((ox, oy), text, font=f, fill=255, anchor="ls", **kw)
        return Mask(np.asarray(img, np.float32) / 255.0, left, top)

    def text(self, x: float, y: float, text: str, font_key: str = "sans_black", size: float = 64.0, color="text",
             opacity: float = 1.0, anchor: str = "ls", stroke: float = 0.0, stroke_color="black",
             shadow=None, tabular: bool = False, tracking: float = 0.0, upper: bool = False) -> tuple[float, float, float, float]:
        """Draw one line. anchor = horizontal (l, m, r) + vertical (a ascender, t cap top, c cap middle,
        s baseline, d descender). Returns the laid-out box (x0, y0, x1, y1) in box-local design px:
        advance width by cap top to baseline."""
        s = clean_text(text, font_key)
        if upper:
            s = s.upper()
        m = measure(s, font_key, size, tabular, tracking)
        h, v = (anchor + "s")[0], (anchor + "s")[1]
        dx = {"l": 0.0, "m": -m.width / 2.0, "r": -m.width}.get(h)
        dy = {"a": m.ascent, "t": m.cap, "c": m.cap / 2.0, "s": 0.0, "d": -m.descent}.get(v)
        if dx is None or dy is None:
            raise VisualError(f'invalid text anchor: {anchor!r}')
        bx, by = x + dx, y + dy
        if opacity > 0 and s:
            is_ar = has_arabic(s)
            s_draw = shape_arabic(s) if is_ar else s
            k = self._tf[-1].a * self.s
            f = font(font_key, size * k, s)
            X, Y = self.px(bx, by)
            sh = _shadow_of(shadow)
            stroke_px = stroke * k
            trk_px = 0.0 if is_ar else tracking * k
            tab = tabular and not is_ar
            body = self._text_mask(X, Y, s_draw, f, 0.0, tab, trk_px)
            outline = self._text_mask(X, Y, s_draw, f, stroke_px, tab, trk_px) if stroke_px > 0 else None
            self._cast_shadow(outline or body, sh, opacity)
            if outline is not None:
                self.fill_mask(outline, stroke_color, opacity)
            self.fill_mask(body, color, opacity)
        return bx, by - m.cap, bx + m.width, by

    def rich_text(self, x: float, y: float, text: str, font_key: str = "sans_black", size: float = 64.0,
                  color="text", accent_color="accent", opacity: float = 1.0, anchor: str = "ls",
                  stroke: float = 0.0, stroke_color="black", shadow=None, upper: bool = False,
                  highlight: bool = False) -> tuple[float, float, float, float]:
        """One line where *marked* words get the accent color, or (highlight=True) an accent box behind them
        with readable text on it."""
        parts = parse_accent(clean_text(text, font_key))
        if upper:
            parts = [(w.upper(), a) for w, a in parts]
        rtl = any(has_arabic(w) for w, _ in parts)
        f = font(font_key, size, text)
        space = f.getlength(" ")
        widths = [f.getlength(shape_arabic(w)) for w, _ in parts]
        total = sum(widths) + space * max(0, len(parts) - 1)
        m = measure("H", font_key, size)
        h, v = (anchor + "s")[0], (anchor + "s")[1]
        dx = {"l": 0.0, "m": -total / 2.0, "r": -total}.get(h)
        dy = {"a": m.ascent, "t": m.cap, "c": m.cap / 2.0, "s": 0.0, "d": -m.descent}.get(v)
        if dx is None or dy is None:
            raise VisualError(f'invalid text anchor: {anchor!r}')
        bx, by = x + dx, y + dy
        cur = (bx + total) if rtl else bx
        for (word, acc), wd in zip(parts, widths):
            draw_x = (cur - wd) if rtl else cur
            col = color
            if acc and highlight:
                padx, pady = size * 0.14, size * 0.16
                self.rect(draw_x - padx, by - m.cap - pady, wd + 2 * padx, m.cap + 2 * pady, radius=size * 0.12,
                          fill=accent_color, opacity=opacity)
                col = readable_on(self.color(accent_color))
            elif acc:
                col = accent_color
            self.text(draw_x, by, word, font_key, size, color=col, opacity=opacity, anchor="ls", stroke=stroke,
                      stroke_color=stroke_color, shadow=shadow)
            cur = (cur - wd - space) if rtl else (cur + wd + space)
        return bx, by - m.cap, bx + total, by

    def paragraph(self, x: float, y: float, text: str, font_key: str = "text_bold", size: float = 44.0,
                  max_width: float = 700.0, max_lines: int = 3, line_height: float = 1.22, align: str = "l",
                  color="text", opacity: float = 1.0, min_size: float | None = None, **kw) -> tuple[float, float, float, float]:
        """Wrapped text (balanced lines) with its first cap top at y. `align` l/m/r relative to x. The size
        shrinks (down to min_size, default 55%) until the text fits max_lines x max_width; a text that still does
        not fit is drawn at min_size (limit the text length in the params). Returns (x0, y0, x1, y1)."""
        size = float(size)
        floor = float(min_size) if min_size is not None else size * 0.55
        clean = clean_text(text, font_key)
        lines = wrap_text(clean, font_key, size, max_width, max_lines)
        while size > floor and (len(lines) > max_lines or any(
                measure(ln, font_key, size).width > max_width + 0.5 for ln in lines)):
            size = max(floor, size * 0.94)
            lines = wrap_text(clean, font_key, size, max_width, max_lines)
        m = measure("H", font_key, size)
        pitch = size * line_height
        x0, x1 = float("inf"), float("-inf")
        for i, ln in enumerate(lines[:max(1, max_lines)]):
            bx0, _, bx1, _ = self.text(x, y + m.cap + pitch * i, ln, font_key, size, color=color, opacity=opacity,
                                       anchor=align + "s", **kw)
            x0, x1 = min(x0, bx0), max(x1, bx1)
        n = min(len(lines), max(1, max_lines))
        return (x0 if n else x), y, (x1 if n else x), y + m.cap + pitch * max(0, n - 1)

    def paste_sprite(self, sprite: "Sprite | None", opacity: float = 1.0) -> None:
        """Paste a part drawn once (`static_sprite`) at its place. Outside a scaled group it is pixel-identical to
        drawing it again."""
        if sprite is None:
            return
        k = self.box.scale
        self.paste_image(sprite.image, sprite.x, sprite.y, w=sprite.image.width / k, h=sprite.image.height / k,
                         opacity=opacity)

    def paste_image(self, img: Image.Image, x: float, y: float, w: float | None = None, h: float | None = None,
                    opacity: float = 1.0) -> None:
        """Paste an RGBA image with its top-left at (x, y), resized to w x h design px (keeps ratio if one is None).
        (`cv.image` is the frame itself, a PIL image.)"""
        if opacity <= 0:
            return
        src = img.convert("RGBA")
        if w is None and h is None:
            w, h = src.width / self.box.scale, src.height / self.box.scale
        elif w is None:
            w = h * src.width / src.height
        elif h is None:
            h = w * src.height / src.width
        X0, Y0 = self.px(x, y)
        tw, th = max(1, int(round(self.len(w)))), max(1, int(round(self.len(h))))
        if (tw, th) != src.size:
            src = src.resize((tw, th), Image.LANCZOS)
        if opacity < 1:
            a = np.asarray(src.getchannel("A"), np.float32) * opacity
            src.putalpha(Image.fromarray(np.clip(a + 0.5, 0, 255).astype(np.uint8), "L"))
        self._paste_layer(src, int(round(X0)), int(round(Y0)))

    # ---------------------------------------------------------- groups
    @contextmanager
    def _layer(self):
        lay = _Layer(Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0)))
        self._targets.append(lay.image)
        self._dirty.append(None)
        try:
            yield lay
        finally:
            self._targets.pop()
            lay.dirty = self._dirty.pop()

    @contextmanager
    def group(self, opacity: float = 1.0, scale: float = 1.0, pivot: Point | None = None, dx: float = 0.0,
              dy: float = 0.0, rotate: float = 0.0):
        """Draw the block inside as one piece: moved by (dx, dy), scaled around `pivot` (default: the box
        center), rotated by `rotate` degrees (clockwise, as a picture) and faded as a whole."""
        px_, py_ = pivot if pivot is not None else (self.cx, self.cy)
        cur = self._tf[-1]
        # new(u) = cur(pivot + (u - pivot) * scale + d)
        nb = cur.point(px_ * (1 - scale) + dx, py_ * (1 - scale) + dy)
        self._tf.append(_Transform(cur.a * scale, nb[0], nb[1]))
        needs_layer = opacity < 0.999 or abs(rotate) > 1e-3
        try:
            if not needs_layer:
                yield self
                return
            with self._layer() as lay:
                yield self
            if opacity <= 0.001 or lay.dirty is None:
                return
            x0, y0, x1, y1 = lay.dirty
            part = lay.image.crop((x0, y0, x1, y1))
            if abs(rotate) > 1e-3:
                # rotate on a square around the pivot that holds the drawing in every angle
                cx, cy = self.px(px_, py_)
                reach = max(math.hypot(x - cx, y - cy) for x in (x0, x1) for y in (y0, y1))
                half = int(math.ceil(reach)) + 2
                sx0, sy0 = int(math.floor(cx)) - half, int(math.floor(cy)) - half
                square = Image.new("RGBA", (2 * half + 2, 2 * half + 2), (0, 0, 0, 0))
                square.paste(part, (x0 - sx0, y0 - sy0))
                part = square.convert("RGBa").rotate(-rotate, resample=Image.BICUBIC,
                                                     center=(cx - sx0, cy - sy0)).convert("RGBA")
                x0, y0 = sx0, sy0
            if opacity < 0.999:
                a = np.asarray(part.getchannel("A"), np.float32) * opacity
                part.putalpha(Image.fromarray(np.clip(a + 0.5, 0, 255).astype(np.uint8), "L"))
            self._paste_layer(part, x0, y0)
        finally:
            self._tf.pop()

    def visible(self, opacity: float) -> bool:
        return opacity > 0.001

    def result(self) -> "Canvas":
        return self


# ============================================================== parts drawn once

@dataclass(frozen=True)
class Sprite:
    """A drawn part: RGBA image at render px, its top-left in box-local design px."""
    image: Image.Image
    x: float
    y: float


_SPRITES: "dict[tuple, Sprite | None]" = {}
_SPRITES_LOCK = threading.Lock()
_SPRITES_MAX = 24


def static_sprite(key: tuple, box: Box, draw: Callable[[Canvas], None]) -> Sprite | None:
    """Draw `draw(canvas)` once for this box and keep it (the card, title and axes of a chart that never move
    after the entrance). `key` must name everything the drawing depends on besides the box, e.g.
    ("bar_chart", json.dumps(params, sort_keys=True)). Paste it with `cv.paste_sprite(sprite)`.
    None when nothing was drawn."""
    full_key = (key, box)
    with _SPRITES_LOCK:
        if full_key in _SPRITES:
            return _SPRITES[full_key]
    c = Canvas(box)
    draw(c)
    bb = c.image.getbbox()
    sprite = None
    if bb is not None:
        s = box.scale
        sprite = Sprite(c.image.crop(bb), (bb[0] + c.origin_x) / s - box.x, (bb[1] + c.origin_y) / s - box.y)
    with _SPRITES_LOCK:
        _SPRITES[full_key] = sprite
        while len(_SPRITES) > _SPRITES_MAX:
            _SPRITES.pop(next(iter(_SPRITES)))
    return sprite


# ============================================================== frame helpers for the renderer

def blank_frame(canvas: Sequence[int]) -> Image.Image:
    return Image.new("RGBA", (int(canvas[0]), int(canvas[1])), (0, 0, 0, 0))


def with_box(box: Box, **changes) -> Box:
    return replace(box, **changes)


def iter_frames(n: int) -> Iterable[int]:
    return range(max(0, int(n)))
