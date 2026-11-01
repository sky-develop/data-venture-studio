"""notification: a generic phone notification card that slides down from the top of the screen.

Title and text come from the person; the app icon is a generic glyph (speech bubble, bell, check, heart or a
letter) on the accent color, never the logo or the look of a real app. The card springs down from above the
screen, holds, and slides back up at the end (the template draws its own exit, so `exit` is "none").

A notification shows only what really happened in the person's material (a message, a sale, a pix): `notes_pt`
says so; the template cannot check it.
"""

from __future__ import annotations

import json
from functools import lru_cache

from visuals import engine as E
from visuals.engine import VisualError

TEMPLATE = {'id': 'notification', 'label': 'Notification', 'description': 'A generic notification card drops from the top, settles for reading, and exits upward or fades. No real application branding is required.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'notes': 'Validate the parameters, keep text readable, and inspect the exported entrance and exit.', 'category': 'graphics', 'version': 1, 'params': {'app_name': {'type': 'text', 'default': 'Messages', 'max_len': 24, 'label': 'App Name'}, 'title': {'type': 'text', 'default': 'New message', 'max_len': 48, 'required_text': True, 'label': 'Title'}, 'text': {'type': 'text', 'default': 'Hi! I saw your video and would like to learn more.', 'max_len': 120, 'label': 'Text'}, 'time_label': {'type': 'text', 'default': 'now', 'max_len': 12, 'label': 'Time Label'}, 'icon': {'type': 'choice', 'choices': ['message', 'bell', 'check', 'heart', 'letter'], 'default': 'message', 'label': 'Icon'}, 'exit_mode': {'type': 'choice', 'choices': ['rise', 'fade'], 'default': 'rise', 'label': 'Exit Mode'}, 'stack': {'type': 'bool', 'default': False, 'label': 'Stack'}}, 'default_duration': 3.5, 'default_region': 'top_band', 'sfx': [{'at': 0.0, 'name': 'notification', 'why': 'notification entrance'}], 'exit': 'none', 'bleed': 260, 'movable': False, 'example': {'app_name': 'Messages', 'title': 'Juliana', 'text': 'I started yesterday and understand the steps. May I ask a question?'}}

LAND = 0.80           # the spring is forced to rest exactly here
DAMPING, FREQ = 0.68, 1.9
EXIT_T = 0.36
EXIT_DIP = 0.18       # share of the exit spent on a small dip before the flick up
PAD = 32.0
ICON = 100.0
PEEK = 44.0          # design px of the card already on screen on frame 0


# ------------------------------------------------------------------ layout

def validate(params: dict) -> None:
    """The title needs letters the font draws (refused when the item is planned)."""
    if not E.clean_text(params["title"], "text_bold").strip():
        raise VisualError('notification.params.title requires letters or digits supported by the font')


def _truncate(text: str, font_key: str, size: float, max_width: float) -> str:
    if E.measure(text, font_key, size).width <= max_width:
        return text
    words = text
    while words and E.measure(words.rstrip() + "...", font_key, size).width > max_width:
        words = words[:-1]
    return words.rstrip(" ,.;:") + "..."


@lru_cache(maxsize=64)
def _layout(params_json: str, box: E.Box) -> dict:
    p = json.loads(params_json)
    W = box.w
    content_x = PAD + ICON + 26.0
    content_w = W - content_x - PAD
    app = E.clean_text(p["app_name"], "text_semibold").strip()
    time_label = E.clean_text(p["time_label"], "text_semibold").strip()
    title = E.clean_text(p["title"], "text_bold").strip()
    body = E.clean_text(p["text"], "text_semibold").strip()
    if not title:
        raise VisualError('notification.params.title requires letters or digits supported by the font')

    head_size = 30.0
    head_cap = E.measure("H", "text_semibold", head_size).cap
    time_w = E.measure(time_label, "text_semibold", head_size).width if time_label else 0.0
    app_text = _truncate(app.upper(), "text_semibold", head_size, content_w - time_w - 24.0) if app else ""

    title_size = E.fit_text(title, "text_bold", content_w, 44.0, 34.0)
    title = _truncate(title, "text_bold", title_size, content_w)
    title_cap = E.measure("H", "text_bold", title_size).cap

    body_size = 40.0
    lines: list[str] = []
    if body:
        lines = E.wrap_text(body, "text_semibold", body_size, content_w, 3)
        while body_size > 32.0 and (len(lines) > 3 or any(
                E.measure(ln, "text_semibold", body_size).width > content_w + 0.5 for ln in lines)):
            body_size = max(32.0, body_size * 0.95)
            lines = E.wrap_text(body, "text_semibold", body_size, content_w, 3)
        lines = [_truncate(ln, "text_semibold", body_size, content_w) for ln in lines[:3]]
    body_cap = E.measure("H", "text_semibold", body_size).cap
    pitch = body_size * 1.26

    head_base = PAD + head_cap + 2.0
    title_base = head_base + 18.0 + title_cap
    body_base = title_base + 14.0 + body_cap
    bottom = (body_base + pitch * (len(lines) - 1) + body_size * 0.28) if lines else title_base + title_size * 0.28
    card_h = max(ICON + 2 * PAD, bottom + PAD)
    extra = (card_h - (bottom + PAD)) / 2.0          # a short text sits in the middle of the icon's height
    head_base, title_base, body_base = head_base + extra, title_base + extra, body_base + extra
    top = min(12.0, max(0.0, box.h - card_h))

    # slide distance: from above the screen (only the bottom PEEK px show on frame 0) when the canvas reaches
    # it, else a short slide with a fade
    canvas_top = box.y - box.bleed
    off_screen = canvas_top <= -20.0
    if off_screen:
        a0 = _arrive(1.0 / box.fps)
        travel = max(150.0, (box.y + top + card_h - PEEK) / max(0.2, 1.0 - a0))
        travel = min(travel, box.bleed + box.y + top - 10.0 + card_h)
    else:
        travel = 150.0
    dark_value = box.theme.dark
    acc = box.theme.accent
    return {
        "W": W, "top": top, "card_h": card_h, "content_x": content_x, "content_w": content_w,
        "app": app_text, "time": time_label, "time_w": time_w, "head_size": head_size, "head_base": head_base,
        "title": title, "title_size": title_size, "title_base": title_base,
        "lines": lines, "body_size": body_size, "body_base": body_base, "pitch": pitch,
        "travel": travel, "off_screen": off_screen, "letter": (app[:1] or title[:1]).upper(),
        "grad": (E.mix_color(acc, (255, 255, 255), 0.18), E.mix_color(acc, (0, 0, 0), 0.22)),
        "fill": (38, 38, 42, 236) if dark_value else (250, 250, 250, 244),
        "stack_fill": (30, 30, 34, 200) if dark_value else (236, 236, 236, 220),
    }


# ------------------------------------------------------------------ icon glyphs (generic)

def _icon(cv: E.Canvas, L: dict, x: float, y: float, kind: str) -> None:
    s = ICON
    light, dark = L["grad"]
    cv.rect(x, y, s, s, radius=s * 0.24, fill=E.Gradient(light, dark, (x, y), (x + s, y + s)),
            shadow=E.Shadow(blur=10, dy=4, opacity=0.25))
    cx, cy = x + s / 2.0, y + s / 2.0
    ink = "on_accent"
    if kind == "message":
        bw, bh = s * 0.58, s * 0.44
        bx, by = cx - bw / 2.0, cy - bh / 2.0 - s * 0.04
        cv.rect(bx, by, bw, bh, radius=bh * 0.42, fill=ink)
        cv.polygon([(bx + bw * 0.22, by + bh - 2.0), (bx + bw * 0.46, by + bh - 2.0),
                    (bx + bw * 0.14, by + bh + s * 0.13)], fill=ink)
        for k in (-1, 0, 1):
            cv.circle(cx + k * s * 0.12, by + bh / 2.0, s * 0.036, fill=light)
    elif kind == "bell":
        cv.circle(cx, cy - s * 0.30, s * 0.05, fill=ink)
        body = [(cx - s * 0.13, cy - s * 0.20), (cx + s * 0.13, cy - s * 0.20),
                (cx + s * 0.22, cy + s * 0.14), (cx - s * 0.22, cy + s * 0.14)]
        cv.polygon(body, fill=ink, radius=s * 0.04)
        cv.circle(cx, cy - s * 0.13, s * 0.14, fill=ink)
        cv.pill(cx - s * 0.29, cy + s * 0.10, s * 0.58, s * 0.09, fill=ink)
        cv.circle(cx, cy + s * 0.25, s * 0.065, fill=ink)
    elif kind == "check":
        cv.check(cx, cy + s * 0.02, s * 0.52, width=s * 0.11, color=ink)
    elif kind == "heart":
        cv.polygon(E.heart_points(cx, cy + s * 0.02, s * 0.56, n=48, closed=False), fill=ink)
    else:
        cv.text(cx, cy, L["letter"], "sans_black", s * 0.50, color=ink, anchor="mc")


# ------------------------------------------------------------------ motion

def _arrive(t_in: float) -> float:
    """0 -> 1 with a light overshoot; exactly 1 from LAND on (no endless sub-pixel wobble)."""
    if t_in >= LAND:
        return 1.0
    v = E.spring(t_in, DAMPING, FREQ)
    blend = E.eased(t_in, LAND - 0.25, 0.25, "in_out_sine")
    return v + (1.0 - v) * blend


def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    key = json.dumps(params, sort_keys=True)
    L = _layout(key, box)
    cv = E.Canvas(box)
    t = frame_index / fps
    t_in = t + 1.0 / fps
    end = box.frames / fps

    a = _arrive(t_in)
    dy = -L["travel"] * (1.0 - a)
    opacity = E.eased(t_in, 0.0, 0.10 if L["off_screen"] else 0.25, "out_quad")
    scale = 0.96 + 0.04 * a

    # exit, drawn by the template: up and out, or a fade in place
    t_exit = end - EXIT_T
    if t >= t_exit - 1e-9:
        # the last frame is fully gone (+ 1 frame so the exit ends on the last frame)
        p = E.progress(t + 1.0 / fps, t_exit, EXIT_T)
        if params["exit_mode"] == "rise":
            dy += 12.0 * E.pulse(p, 0.0, EXIT_DIP * 2.0)
            dy -= (L["travel"] + PEEK + 60.0) * E.in_quad(E.remap(p, EXIT_DIP, 1.0))
            if not L["off_screen"]:
                opacity *= 1.0 - E.eased(p, 0.3, 0.7, "in_quad")
        else:
            opacity *= 1.0 - E.out_quad(p)
            scale *= 1.0 - 0.04 * p
        if p >= 1.0:
            return cv

    if t_in >= LAND and t < t_exit - 1e-9:
        # resting: every frame is the same picture (settle() cannot be used with a template-drawn exit)
        cv.image.alpha_composite(_rest_image(key, box))
        return cv
    _draw_card(cv, L, params, dy, scale, opacity)
    return cv


@lru_cache(maxsize=8)
def _rest_image(params_json: str, box: E.Box):
    cv = E.Canvas(box)
    _draw_card(cv, _layout(params_json, box), json.loads(params_json), 0.0, 1.0, 1.0)
    return cv.image


def _draw_card(cv: E.Canvas, L: dict, params: dict, dy: float, scale: float, opacity: float) -> None:
    x, y, w, h = 0.0, L["top"], L["W"], L["card_h"]
    with cv.group(dy=dy, scale=scale, pivot=(w / 2.0, y), opacity=opacity):
        if params["stack"]:
            cv.rect(w * 0.05, y + 22.0, w * 0.90, h, radius=40.0, fill=L["stack_fill"],
                    shadow=E.Shadow(blur=20, dy=10, opacity=0.25), stroke="card_border", stroke_width=2.0)
        cv.rect(x, y, w, h, radius=44.0, fill=L["fill"], shadow=E.Shadow(blur=34, dy=16, opacity=0.38),
                stroke="card_border", stroke_width=2.0)
        _icon(cv, L, PAD, y + PAD, params["icon"])
        cx = L["content_x"]
        if L["app"]:
            cv.text(cx, y + L["head_base"], L["app"], "text_semibold", L["head_size"], color="muted", tracking=1.5)
        if L["time"]:
            cv.text(w - PAD, y + L["head_base"], L["time"], "text_semibold", L["head_size"], color="muted",
                    anchor="rs")
        cv.text(cx, y + L["title_base"], L["title"], "text_bold", L["title_size"], color="text")
        for i, line in enumerate(L["lines"]):
            cv.text(cx, y + L["body_base"] + i * L["pitch"], line, "text_semibold", L["body_size"], color="text",
                    opacity=0.9)

def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    return [{"frame": 0, "name": "notification", "volume": 1.0, "why": 'notification entrance'}]


def suggested_duration(params: dict) -> float:
    """Arrival, reading time by the size of the text, and the exit."""
    words = len((params["title"] + " " + params["text"]).split())
    return round(min(8.0, max(TEMPLATE["default_duration"], LAND + 0.8 + words * 0.22 + EXIT_T)), 2)
