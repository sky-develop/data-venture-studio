"""comment_cta: a generic comment bar where a keyword is typed letter by letter, then sent with a tap.

The look is a neutral social app (no logo, name or icon of a real platform): a round avatar with a generic
silhouette, a pill-shaped field with the placeholder, a blinking cursor, and a send button with a paper-plane
glyph (or the word "Send"). After the tap the comment shows up above the bar and the call-to-action text
moves up to make room, like a real comment list.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache

from visuals import engine as E
from visuals.engine import VisualError

TEMPLATE = {'id': 'comment_cta', 'label': 'Comment Cta', 'description': 'A generic comment field types a keyword, activates the send button, and displays the submitted comment. No real service branding is used.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'notes': 'Validate the parameters, keep text readable, and inspect the exported entrance and exit.', 'category': 'graphics', 'version': 1, 'params': {'keyword': {'type': 'text', 'default': 'IDEA', 'max_len': 20, 'required_text': True, 'label': 'Keyword'}, 'prompt_text': {'type': 'text', 'default': 'Comment the word', 'max_len': 48, 'label': 'Prompt Text'}, 'placeholder': {'type': 'text', 'default': 'Add a comment...', 'max_len': 32, 'label': 'Placeholder'}, 'user_name': {'type': 'text', 'default': 'you', 'max_len': 20, 'label': 'User Name'}, 'upper': {'type': 'bool', 'default': True, 'label': 'Upper'}, 'delay': {'type': 'number', 'default': 0.7, 'min': 0.3, 'max': 5, 'label': 'Delay (0.3–5)'}, 'typing_speed': {'type': 'number', 'default': 7.0, 'min': 2, 'max': 30, 'label': 'Typing Speed (2–30)'}, 'button': {'type': 'choice', 'choices': ['icon', 'text'], 'default': 'icon', 'label': 'Button'}, 'show_sent': {'type': 'bool', 'default': True, 'label': 'Show Sent'}, 'size': {'type': 'choice', 'choices': ['normal', 'large'], 'default': 'normal', 'label': 'Size'}}, 'default_duration': 3.8, 'default_region': 'bottom', 'sfx': [{'at': 0.0, 'name': 'pop', 'volume': 0.8, 'why': 'bar appears'}, {'at': 0.7, 'name': 'keyboard', 'volume': 0.9, 'why': 'typing'}, {'at': 1.76, 'name': 'mouse_click', 'why': 'send tap'}, {'at': 1.84, 'name': 'pop', 'volume': 0.7, 'why': 'comment submitted'}], 'example': {'keyword': 'IDEA', 'prompt_text': 'Comment *{keyword}* to receive the link'}}

ENTER = 0.40          # entrance of the bar
TAP_AFTER = 0.45      # pause between the last letter and the tap
SENT_AFTER = 0.08     # the comment starts this long after the tap
SENT_T = 0.42         # comment slide
PROMPT_MOVE = 0.30    # the prompt moves up first, so the comment never covers it
HOLD = 1.6            # seconds to read after the comment lands
BLINK = 1.0           # cursor period
MAX_TYPING = 1.6      # seconds
RIPPLE = 0.36         # the tap ripple grows to 1.36x the button


# ------------------------------------------------------------------ text and timing

def _keyword(p: dict) -> str:
    kw = E.clean_text(p["keyword"], "text_bold").strip()
    if not kw:
        raise VisualError('comment_cta.params.keyword requires letters or digits supported by the font')
    return kw.upper() if p["upper"] else kw


@lru_cache(maxsize=128)
def _timeline(params_json: str) -> dict:
    """Seconds (item time) of every beat; typing has a small human rhythm, fixed by the keyword."""
    p = json.loads(params_json)
    kw = _keyword(p)
    r = E.rng("comment_cta", kw)
    # long keywords are typed faster so the typing never takes much more than MAX_TYPING
    base = 1.0 / max(p["typing_speed"], (len(kw) - 1) / MAX_TYPING if len(kw) > 1 else 1.0)
    times = []
    t = p["delay"]
    for i, ch in enumerate(kw):
        times.append(t)
        step = base * r.uniform(0.7, 1.3)
        if ch == " " or (i + 1 < len(kw) and kw[i + 1] == " "):
            step *= 1.35
        t += step
    t_end = times[-1]
    t_tap = t_end + TAP_AFTER
    return {"keyword": kw, "chars": times, "focus": min(0.45, max(0.25, p["delay"] - 0.35)), "type": p["delay"],
            "type_end": t_end, "tap": t_tap, "sent": t_tap + SENT_AFTER}


def _prompt(p: dict, kw: str) -> str:
    return p["prompt_text"].replace("{keyword}", kw).strip()


def _prompt_lines(text: str, max_width: float, k: float) -> tuple[list[str], float]:
    """The prompt on one line at a good size, or on two balanced lines (the *accent* marks are kept per word)."""
    words = E.parse_accent(text)
    if not words:
        return [], 0.0
    plain = " ".join(w for w, _ in words)
    one = E.fit_text(plain, "sans_extrabold", max_width, 56.0 * k, 20.0)
    marked = [("*" + w.replace("*", "\\*") + "*") if a else w.replace("*", "\\*") for w, a in words]
    if one >= 46.0 * k or len(words) < 2:
        return [" ".join(marked)], max(30.0, one)
    size = 52.0 * k
    lines = E.wrap_text(plain, "sans_extrabold", size, max_width, 2)
    widest = max(E.measure(ln, "sans_extrabold", size).width for ln in lines)
    size = max(30.0, min(size, size * max_width / widest))
    out, i = [], 0
    for ln in lines:
        n = len(ln.split())
        out.append(" ".join(marked[i:i + n]))
        i += n
    return out, size


def validate(params: dict) -> None:
    """The keyword needs letters the font draws (refused when the item is planned)."""
    _keyword(params)


@lru_cache(maxsize=64)
def _layout(params_json: str, box: E.Box) -> dict:
    p = json.loads(params_json)
    T = _timeline(params_json)
    kw = T["keyword"]
    k = 1.18 if p["size"] == "large" else 1.0
    theme = box.theme
    W = box.w
    bar_h = 116.0 * k
    av_r = 44.0 * k
    gap = 18.0 * k
    field_x = 2 * av_r + gap
    field_w = W - field_x
    btn_r = 38.0 * k
    pad = 34.0 * k
    btn_cx = W - 14.0 * k - btn_r
    text_w_max = field_w - pad - (2 * btn_r + 30.0 * k)
    send_label = "Send"
    if p["button"] == "text":
        send_size = 38.0 * k
        send_w = E.measure(send_label, "text_bold", send_size).width
        btn_cx = W - 26.0 * k - send_w / 2.0
        text_w_max = field_w - pad - (send_w + 52.0 * k)
    else:
        send_size = 0.0
        send_w = 0.0
    kw_size = E.fit_text(kw, "text_bold", text_w_max - 14.0 * k, 50.0 * k, 26.0)
    ph_size = E.fit_text(p["placeholder"], "text_semibold", text_w_max, 40.0 * k, 24.0) if p["placeholder"] else 0.0
    kw_cap = E.measure("H", "text_bold", kw_size).cap

    prompt_lines, pr_size = _prompt_lines(_prompt(p, kw), W - 20.0, k)
    pr_cap = E.measure("H", "sans_extrabold", pr_size).cap if prompt_lines else 0.0
    pr_pitch = pr_size * 1.22
    pr_h = pr_cap + pr_pitch * (len(prompt_lines) - 1) if prompt_lines else 0.0
    pr_gap = 34.0 * k

    # the published comment (content-sized bubble)
    row_h = 126.0 * k
    cav_r = 34.0 * k
    name_size, time_size, ckw_size = 31.0 * k, 27.0 * k, 50.0 * k
    name = E.clean_text(p["user_name"], "text_bold").strip() or "you"
    name_w = E.measure(name, "text_bold", name_size).width
    time_w = E.measure("now", "text_semibold", time_size).width
    heart = 34.0 * k
    ckw_size = E.fit_text(kw, "text_bold", field_w - 2 * pad - heart - 30.0 * k, ckw_size, 24.0)
    ckw_w = E.measure(kw, "text_bold", ckw_size).width
    bubble_w = min(field_w, max(name_w + 16.0 * k + time_w, ckw_w) + 2 * pad + heart + 34.0 * k)
    bubble_w = max(bubble_w, min(field_w, 360.0 * k))

    sent = p["show_sent"]
    stack_h = (pr_h + pr_gap if prompt_lines else 0.0) + (row_h + gap if sent else 0.0) + bar_h
    top = (box.h - stack_h) / 2.0
    bar_y = top + stack_h - bar_h
    row_y = bar_y - gap - row_h
    prompt_base_end = (row_y if sent else bar_y) - pr_gap
    prompt_base_start = bar_y - pr_gap

    acc = theme.accent
    card = theme.card
    on_card_accent = "accent" if E.contrast(acc, card[:3]) >= 2.2 else "text"
    dark = E.mix_color(acc, (0, 0, 0), 0.35)
    light = E.mix_color(acc, (255, 255, 255), 0.25)
    return {
        "k": k, "kw": kw, "bar_y": bar_y, "bar_h": bar_h, "av_r": av_r, "field_x": field_x, "field_w": field_w,
        "btn_r": btn_r, "btn_cx": btn_cx, "pad": pad,
        "kw_size": kw_size, "kw_cap": kw_cap, "ph_size": ph_size, "send_size": send_size, "send_w": send_w,
        "send_label": send_label, "prompt": prompt_lines, "pr_size": pr_size, "pr_pitch": pr_pitch,
        "prompt_start": prompt_base_start, "prompt_end": prompt_base_end,
        "row_y": row_y, "row_h": row_h, "cav_r": cav_r, "bubble_w": bubble_w, "name": name,
        "name_size": name_size, "name_w": name_w, "time_size": time_size, "ckw_size": ckw_size, "heart": heart,
        "accent_text": on_card_accent, "grad": (light, dark),
    }


# ------------------------------------------------------------------ small drawings (mask helpers)

def _avatar(cv: E.Canvas, L: dict, cx: float, cy: float, r: float) -> None:
    """Round avatar with a generic person silhouette."""
    light, dark = L["grad"]
    cv.circle(cx, cy, r, fill=E.Gradient(light, dark, (cx - r, cy - r), (cx + r, cy + r)),
              stroke="card_border", stroke_width=2.0)
    white = (255, 255, 255, 235)
    cv.circle(cx, cy - 0.17 * r, 0.33 * r, fill=white)
    body = E.intersect_masks(cv.circle_mask(cx, cy + 0.98 * r, 0.64 * r), cv.circle_mask(cx, cy, r - 1.0))
    cv.fill_mask(body, white)


PLANE = [(2.01, 21.0), (23.0, 12.0), (2.01, 3.0), (2.0, 10.0), (17.0, 12.0), (2.0, 14.0)]


def _plane(cv: E.Canvas, cx: float, cy: float, size: float, color, opacity: float = 1.0) -> None:
    """Generic 'send' glyph (paper plane), tilted up."""
    s = size / 21.0
    a = math.radians(-22.0)
    ca, sa = math.cos(a), math.sin(a)
    pts = []
    for x, y in PLANE:
        u, v = (x - 12.2) * s, (y - 12.0) * s
        pts.append((cx + u * ca - v * sa + size * 0.04, cy + u * sa + v * ca))
    cv.polygon(pts, fill=color, opacity=opacity)


# ------------------------------------------------------------------ render

def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    key = json.dumps(params, sort_keys=True)
    L = _layout(key, box)
    T = _timeline(key)
    cv = E.Canvas(box)
    t = frame_index / fps
    t_in = t + 1.0 / fps
    k = L["k"]

    # --- the call-to-action text above (moves up when the comment arrives)
    if L["prompt"]:
        pa = E.eased(t_in, 0.08, 0.30, "out_quad")
        if cv.visible(pa):
            move = E.eased(t, T["sent"], PROMPT_MOVE, "out_cubic") if params["show_sent"] else 0.0
            base = E.lerp(L["prompt_start"], L["prompt_end"], move) + 26.0 * (1.0 - E.eased(t_in, 0.08, 0.45))
            last = len(L["prompt"]) - 1
            for i, line in enumerate(L["prompt"]):
                cv.rich_text(cv.cx, base - (last - i) * L["pr_pitch"], line, "sans_extrabold", L["pr_size"],
                             color="white", opacity=pa, anchor="ms", stroke=L["pr_size"] * 0.09,
                             stroke_color="black", shadow=E.TEXT_SHADOW)

    # --- the published comment
    if params["show_sent"]:
        ca = E.eased(t, T["sent"] + 0.12, 0.20, "out_quad")
        if cv.visible(ca):
            rise = 24.0 * k * (1.0 - E.eased(t, T["sent"] + 0.06, SENT_T - 0.06, "out_cubic"))
            y0 = L["row_y"] + rise
            with cv.group(opacity=ca):
                rcx = L["av_r"]
                rcy = y0 + L["row_h"] / 2.0
                _avatar(cv, L, rcx, rcy, L["cav_r"])
                bx = L["field_x"]
                cv.card(bx, y0, L["bubble_w"], L["row_h"], radius=30.0 * k)
                name_base = y0 + L["pad"] * 0.62 + E.measure("H", "text_bold", L["name_size"]).cap
                cv.text(bx + L["pad"], name_base, L["name"], "text_bold", L["name_size"], color="muted")
                cv.text(bx + L["pad"] + L["name_w"] + 14.0 * k, name_base, "now", "text_semibold",
                        L["time_size"], color="muted", opacity=0.7)
                kw_base = y0 + L["row_h"] - L["pad"] * 0.62
                cv.text(bx + L["pad"], kw_base, L["kw"], "text_bold", L["ckw_size"], color="text")
                hx = bx + L["bubble_w"] - L["pad"] - L["heart"] / 2.0
                cv.polyline(E.heart_points(hx, rcy, L["heart"]), 3.2 * k, color="muted")

    # --- the bar
    ba = E.eased(t_in, 0.0, 0.22, "out_quad")
    rise = 40.0 * k * (1.0 - E.eased(t_in, 0.0, ENTER, "out_cubic"))
    with cv.group(opacity=ba, dy=rise):
        cy = L["bar_y"] + L["bar_h"] / 2.0
        _avatar(cv, L, L["av_r"], cy, L["av_r"])
        cv.card(L["field_x"], L["bar_y"], L["field_w"], L["bar_h"], radius=L["bar_h"] / 2.0)
        tx = L["field_x"] + L["pad"]

        n = sum(1 for c in T["chars"] if t >= c)
        cleared = params["show_sent"] and t >= T["sent"]
        typed = L["kw"][:n]
        # placeholder: until the first letter, and back after the comment is sent
        ph = 0.0
        if L["ph_size"]:
            if n == 0:
                ph = 1.0
            elif cleared:
                ph = E.eased(t, T["sent"] + 0.06, 0.16, "out_quad")
        if cv.visible(ph):
            cv.text(tx, cy, params["placeholder"], "text_semibold", L["ph_size"], color="muted", opacity=0.62 * ph,
                    anchor="lc")
        text_end = tx - 9.0 * k
        if typed:
            ka = 1.0 - E.eased(t, T["sent"], 0.10, "out_quad") if cleared else 1.0
            if cv.visible(ka):
                x0, _, x1, _ = cv.text(tx, cy, typed, "text_bold", L["kw_size"], color="text", opacity=ka,
                                       anchor="lc")
                text_end = x1
        # cursor: blinks while waiting, stays on while typing, gone after the tap
        if T["focus"] <= t < T["tap"]:
            typing = T["type"] <= t <= T["type_end"] + 0.12
            ref = T["focus"] if t < T["type"] else T["type_end"] + 0.12
            on = typing or ((t - ref) % BLINK) < BLINK * 0.55
            if on:
                ch = L["kw_cap"] * 1.45
                cv.rect(text_end + 3.0 * k, cy - ch / 2.0, 4.0 * k, ch, radius=2.0 * k, fill=L["accent_text"])

        # send button: gray until the first letter, then the accent; gray again once the field is cleared
        act = E.eased(t, T["chars"][0], 0.14, "out_quad") if n > 0 else 0.0
        if cleared:
            act *= 1.0 - E.eased(t, T["sent"], 0.14, "out_quad")
        pop = E.pulse(t, T["chars"][0], 0.26) * 0.12
        press = E.pulse(t, T["tap"] - 0.05, 0.22) * 0.16
        bs = 1.0 + pop - press
        bcx = L["btn_cx"]
        with cv.group(scale=bs, pivot=(bcx, cy)):
            if params["button"] == "text":
                col = L["accent_text"]
                cv.text(bcx, cy, L["send_label"], "text_bold", L["send_size"], color="muted", opacity=0.45 * (1 - act),
                        anchor="mc")
                if act > 0.001:
                    cv.text(bcx, cy, L["send_label"], "text_bold", L["send_size"], color=col, opacity=act,
                            anchor="mc")
            else:
                r = L["btn_r"]
                cv.circle(bcx, cy, r, fill="track", opacity=1.0 - act)
                if act > 0.001:
                    cv.circle(bcx, cy, r, fill="accent", opacity=act)
                _plane(cv, bcx, cy, r * 1.02, "muted", opacity=0.55 * (1.0 - act))
                if act > 0.001:
                    _plane(cv, bcx, cy, r * 1.02, "on_accent", opacity=act)

        # the finger tap: a soft touch dot, then a ripple
        tap = T["tap"]
        # the touch reads as a light mark on the dark bar and a dark one on the light bar
        touch, touch_k = ("white", 1.0) if box.theme.dark else ("ink", 0.55)
        if tap - 0.2 <= t <= tap + 0.45:
            dot = E.eased(t, tap - 0.2, 0.12, "out_quad") * (1.0 - E.eased(t, tap + 0.02, 0.2, "out_quad"))
            if cv.visible(dot):
                rr = L["btn_r"] * (1.25 - 0.25 * E.eased(t, tap - 0.2, 0.18))
                cv.circle(bcx, cy, rr, fill=touch, opacity=0.38 * touch_k * dot)
            rp = E.progress(t, tap, 0.4)
            if 0 < rp < 1:
                # the ripple stays inside the box (the right rail of the app starts right after it)
                cv.ring(bcx, cy, L["btn_r"] * (1.0 + RIPPLE * E.out_cubic(rp)), max(1.0, 7.0 * k * (1 - rp)),
                        color=touch, opacity=0.7 * touch_k * (1 - rp))
    return cv


def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    T = _timeline(json.dumps(params, sort_keys=True))
    cues = [{"at": 0.0, "name": "pop", "volume": 0.8, "why": "bar appears"},
            {"at": T["type"], "name": "keyboard", "volume": 0.9, "why": "typing",
             "length": round(T["type_end"] - T["type"] + 0.2, 3)},
            {"at": T["tap"], "name": "mouse_click", "volume": 1.0, "why": "send tap"}]
    if params["show_sent"]:
        cues.append({"at": T["sent"], "name": "pop", "volume": 0.7, "why": 'comment submitted'})
    return [{"frame": E.frame_of(c.pop("at"), fps), **c} for c in cues]


def suggested_duration(params: dict) -> float:
    """Typing, the tap, the comment and about 1.6 s to read."""
    T = _timeline(json.dumps(params, sort_keys=True))
    return round(max(2.5, T["sent"] + SENT_T + HOLD), 2)


def settle(params: dict, fps: int) -> float:
    """After the comment lands (or the tap ripple ends) nothing moves: the cursor is gone after the tap."""
    T = _timeline(json.dumps(params, sort_keys=True))
    end = T["sent"] + SENT_T if params["show_sent"] else T["tap"] + 0.45
    return end + 1.5 / fps
