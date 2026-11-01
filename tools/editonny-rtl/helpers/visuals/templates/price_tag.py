"""price_tag: a price tag that pops in with a spring and settles slightly tilted, with the cents raised
like a shop price, an optional old price crossed out above it and a shine that sweeps the tag once.
Two looks: "tag" (hang tag with a hole) and "badge" (round scalloped seal).

`_add_shine` is a soft diagonal light band on an image and `_transform` scales and rotates the small cached
tag sprite in one resampling step (a rotated group would redraw and resample its layer on every frame).
"""

from __future__ import annotations

import json
import math
from functools import lru_cache

import numpy as np
from PIL import Image

from visuals import engine as E

TEMPLATE = {'id': 'price_tag', 'label': 'Price Tag', 'description': 'An accent-colored price tag bounces into place with a slight tilt. Optional former price, decimal digits, and supporting text remain readable.', 'use_when': 'Use when this visual directly supports the recorded phrase. Align its timing to measured words and verify the sample.', 'category': 'graphics', 'version': 1, 'params': {'price': {'type': 'number', 'default': 37, 'min': 0, 'max': 1000000000.0, 'label': 'Price (0–1000000000.0)'}, 'old_price': {'type': 'number', 'default': None, 'nullable': True, 'min': 0, 'max': 1000000000.0, 'label': 'Old Price (0–1000000000.0)'}, 'prefix': {'type': 'text', 'default': '$', 'max_len': 4, 'label': 'Prefix'}, 'decimals': {'type': 'integer', 'default': None, 'nullable': True, 'min': 0, 'max': 2, 'label': 'Decimals (0–2)'}, 'label': {'type': 'text', 'default': 'only', 'max_len': 20, 'label': 'Label'}, 'note': {'type': 'text', 'default': '', 'max_len': 26, 'label': 'Note'}, 'old_label': {'type': 'text', 'default': 'was', 'max_len': 8, 'label': 'Old Label'}, 'mode': {'type': 'choice', 'choices': ['tag', 'badge'], 'default': 'tag', 'label': 'Mode'}, 'tilt': {'type': 'number', 'default': -4, 'min': -12, 'max': 12, 'label': 'Tilt (-12–12)'}, 'shine': {'type': 'bool', 'default': True, 'label': 'Shine'}, 'tag_delay': {'type': 'number', 'default': 0.62, 'min': 0.45, 'max': 5, 'label': 'Tag Delay (0.45–5)'}}, 'default_duration': 3.0, 'default_region': 'bottom', 'sfx': [{'at': 0.0, 'name': 'cash'}], 'example': {'price': 37.9, 'old_price': 97, 'note': 'one-time payment'}, 'notes': 'Validate the parameters, keep text readable, and inspect the exported entrance and exit.'}

SPRING = (0.5, 2.4)                        # damping, frequency of the pop (about 16% overshoot)
SPRING_T = E.spring_settle(*SPRING)        # seconds until it rests
STRIKE_AT, STRIKE_DUR = 0.30, 0.28         # the old price gets crossed
SHINE_AT, SHINE_DUR = 0.42, 0.62           # after the pop
POP_SWING = -9.0                           # extra degrees at the start of the pop


def _tag_start(p: dict) -> float:
    """The tag pops at the item start, or `tag_delay` after the old price (which appears at the start)."""
    return p["tag_delay"] if p["old_price"] is not None else 0.0


def _decimals(v: float, fixed) -> int:
    if fixed is not None:
        return int(fixed)
    return 2 if abs(v - round(v)) > 1e-9 else 0


def _price_parts(p: dict) -> tuple[str, str, str]:
    dec = _decimals(p["price"], p["decimals"])
    s = E.format_number(p["price"], dec)
    whole, _, cents = s.partition(",")
    return p["prefix"].strip(), whole, ("," + cents) if cents else ""


# ------------------------------------------------------------------ layout (once per item)

@lru_cache(maxsize=64)
def _layout(params_json: str, box: E.Box) -> dict:
    p = json.loads(params_json)
    prefix, whole, cents = _price_parts(p)
    label = p["label"].strip()
    note = p["note"].strip()
    badge_value = p["mode"] == "badge"
    theme = box.theme

    def price_row(P: float) -> dict:
        pre_s, cen_s = P * 0.30, P * 0.42
        pre_w = E.measure(prefix, "sans_black", pre_s).width if prefix else 0.0
        gap_p = P * 0.08 if prefix else 0.0
        num_w = E.measure(whole, "sans_black", P).width
        gap_c = P * 0.03 if cents else 0.0
        cen_w = E.measure(cents, "sans_black", cen_s).width if cents else 0.0
        return {"P": P, "pre_s": pre_s, "cen_s": cen_s, "pre_w": pre_w, "gap_p": gap_p, "num_w": num_w,
                "gap_c": gap_c, "cen_w": cen_w, "w": pre_w + gap_p + num_w + gap_c + cen_w,
                "cap": E.measure("0", "sans_black", P).cap}

    # old price pill (above the tag)
    old = None
    if p["old_price"] is not None:
        dec_o = _decimals(p["old_price"], p["decimals"])
        o_label = p["old_label"].strip()
        o_price = (prefix + " " if prefix else "") + E.format_number(p["old_price"], dec_o)
        o_size = E.fit_text(f'{o_label} {o_price}', "text_bold", box.w - 80.0, 44.0, 28.0)
        lab_w = E.measure(o_label, "text_bold", o_size).width if o_label else 0.0
        gap = o_size * 0.28 if o_label else 0.0
        pr_w = E.measure(o_price, "text_bold", o_size).width
        cap = E.measure("H", "text_bold", o_size).cap
        pad_x, pad_y = 30.0, 22.0
        old = {"size": o_size, "label": o_label, "price": o_price, "lab_w": lab_w, "gap": gap, "pr_w": pr_w,
               "cap": cap, "w": lab_w + gap + pr_w + 2 * pad_x, "h": cap + 2 * pad_y, "pad_x": pad_x}
    pill_block = (old["h"] + 30.0) if old else 0.0

    lab_size, note_size = 32.0, 34.0
    lab_cap = E.measure("H", "text_bold", lab_size).cap if label else 0.0
    note_cap = E.measure("H", "text_semibold", note_size).cap if note else 0.0
    lab_w = E.measure(label.upper(), "text_bold", lab_size, tracking=2.5).width if label else 0.0
    note_w = E.measure(note, "text_semibold", note_size).width if note else 0.0
    gap1 = 20.0 if label else 0.0
    gap2 = 24.0 if note else 0.0

    avail_h = box.h - pill_block - 12.0
    if badge_value:
        # round seal: the content sits in the middle 70% of the circle
        P = 150.0
        row = price_row(P)
        for _ in range(3):
            stack_h = lab_cap + gap1 + row["cap"] + gap2 + note_cap
            need = max(row["w"] / 1.28, lab_w / 1.2, note_w / 1.2)     # inside the inner ring
            R = max(need, stack_h / 1.25 + 10.0, 150.0)
            R_max = min(box.w * 0.5 - 30.0, avail_h / 2.0)
            if R <= R_max:
                break
            k = R_max / R
            P = max(60.0, P * k)
            row = price_row(P)
            if label:
                lab_size = max(24.0, lab_size * k)
            if note:
                note_size = max(24.0, note_size * k)
            lab_cap = E.measure("H", "text_bold", lab_size).cap if label else 0.0
            note_cap = E.measure("H", "text_semibold", note_size).cap if note else 0.0
            lab_w = E.measure(label.upper(), "text_bold", lab_size, tracking=2.5).width if label else 0.0
            note_w = E.measure(note, "text_semibold", note_size).width if note else 0.0
        R = min(R, R_max)
        tag_w = tag_h = 2 * R
        stack_h = lab_cap + gap1 + row["cap"] + gap2 + note_cap
        block_h = pill_block + tag_h
        top = (box.h - block_h) / 2.0
        tag_x = (box.w - tag_w) / 2.0
        tag_y = top + pill_block
        content_cx = box.w / 2.0
        content_top = tag_y + R - stack_h / 2.0
        shape = {"kind": "badge", "cx": content_cx, "cy": tag_y + R, "R": R}
    else:
        pad_t, pad_b = (34.0, 36.0) if (label or note) else (44.0, 44.0)
        pad_l, pad_r = 36.0, 52.0
        P = 180.0
        row = price_row(P)
        # fit the tilted tag (its rotated bounding box) in the box
        ca, sa = math.cos(math.radians(abs(p["tilt"]))), math.sin(math.radians(abs(p["tilt"])))
        for _ in range(6):
            tag_h = pad_t + lab_cap + gap1 + row["cap"] + gap2 + note_cap + pad_b
            point_w = tag_h * 0.30
            inner = max(row["w"], lab_w, note_w)
            tag_w = max(point_w + pad_l + inner + pad_r, 420.0)
            rot_w, rot_h = tag_w * ca + tag_h * sa, tag_w * sa + tag_h * ca
            k = min((box.w - 24.0) / rot_w, avail_h / rot_h, 1.0)
            if k >= 0.995:
                break
            k = max(k, 0.5) * 0.99
            P = max(60.0, P * k)
            row = price_row(P)
            if label:
                lab_size = max(24.0, lab_size * k)
                lab_cap = E.measure("H", "text_bold", lab_size).cap
                lab_w = E.measure(label.upper(), "text_bold", lab_size, tracking=2.5).width
            if note:
                note_size = max(24.0, note_size * k)
                note_cap = E.measure("H", "text_semibold", note_size).cap
                note_w = E.measure(note, "text_semibold", note_size).width
        inner = tag_w - point_w - pad_l - pad_r
        rot_h = tag_w * sa + tag_h * ca
        block_h = pill_block + rot_h
        top = (box.h - block_h) / 2.0
        tag_x = (box.w - tag_w) / 2.0
        tag_y = top + pill_block + rot_h / 2.0 - tag_h / 2.0
        body_x = tag_x + point_w
        content_cx = body_x + pad_l + inner / 2.0
        content_top = tag_y + pad_t
        r = 16.0
        pts = [(body_x, tag_y), (tag_x + tag_w, tag_y), (tag_x + tag_w, tag_y + tag_h), (body_x, tag_y + tag_h),
               (tag_x, tag_y + tag_h / 2.0)]
        shape = {"kind": "tag", "pts": pts, "radius": r,
                 "hole": (tag_x + point_w * 0.62, tag_y + tag_h / 2.0), "hole_r": max(9.0, tag_h * 0.055)}

    y = content_top
    lab_base = y + lab_cap
    price_top = lab_base + gap1 if label else y
    price_base = price_top + row["cap"]
    note_base = price_base + gap2 + note_cap
    tag_cx, tag_cy = tag_x + tag_w / 2.0, tag_y + tag_h / 2.0
    out = {
        "prefix": prefix, "whole": whole, "cents": cents, "label": label.upper(), "note": note,
        "lab_size": lab_size, "note_size": note_size, "lab_base": lab_base, "note_base": note_base,
        "row": row, "price_x": content_cx - row["w"] / 2.0, "price_top": price_top, "price_base": price_base,
        "content_cx": content_cx, "tag": (tag_x, tag_y, tag_w, tag_h), "pivot": (tag_cx, tag_cy),
        "shape": shape, "old": old, "on_accent": theme.on_accent,
    }
    if old:
        ox = tag_cx - old["w"] / 2.0 if badge_value else min(max(tag_x + 10.0, tag_cx - old["w"] / 2.0),
                                                      box.w - old["w"])
        oy = top
        out["old_box"] = (ox, oy, old["w"], old["h"])
        tx = ox + old["pad_x"]
        out["old_lab_x"] = tx
        out["old_pr_x"] = tx + old["lab_w"] + old["gap"]
        out["old_base"] = oy + (old["h"] + old["cap"]) / 2.0
    return out


# ------------------------------------------------------------------ drawing
#
# The tag is drawn flat once, on a small canvas of its own (vector, scale 1, no tilt). Each frame of the pop
# scales and rotates that sprite in one resampling step and casts its shadow; the resting result (scale 1,
# final tilt, shadow) is cached, so the shine frames only add the light band. The engine's rotated group
# resamples its whole layer instead (about 40 ms a frame for this item).

TAG_SHADOW = E.Shadow(blur=26.0, dy=16.0, opacity=0.40)


def _draw_shape(cv: E.Canvas, L: dict) -> None:
    sh = L["shape"]
    ink = L["on_accent"]
    if sh["kind"] == "tag":
        cv.fill_mask(cv.polygon_mask(sh["pts"], radius=sh["radius"]), "accent")
        # a stitched line inside the edge
        tx, ty, tw, th = L["tag"]
        inset = 14.0
        body_x = sh["pts"][0][0]
        ip = [(body_x + inset * 0.45, ty + inset), (tx + tw - inset, ty + inset), (tx + tw - inset, ty + th - inset),
              (body_x + inset * 0.45, ty + th - inset), (tx + inset * 2.1, ty + th / 2.0)]
        ip.append(ip[0])
        total = E.path_length(ip)
        dash, gap = 16.0, 11.0
        k = 0.0
        while k < total - 2.0:
            cv.polyline(E.trim_points(ip, k / total, min(total, k + dash) / total), width=3.0,
                        color=(ink[0], ink[1], ink[2], 60))
            k += dash + gap
        hx, hy = sh["hole"]
        cv.circle(hx, hy, sh["hole_r"] + 7.0, fill=(ink[0], ink[1], ink[2], 40))
    else:
        cx, cy, R = sh["cx"], sh["cy"], sh["R"]
        bumps = 20
        parts = [cv.circle_mask(cx, cy, R * 0.90)]
        for i in range(bumps):
            a = 2 * math.pi * i / bumps
            parts.append(cv.circle_mask(cx + math.cos(a) * R * 0.86, cy + math.sin(a) * R * 0.86, R * 0.14))
        cv.fill_mask(E.union_masks(parts), "accent")
        cv.ring(cx, cy, R * 0.74, 3.0, color=(ink[0], ink[1], ink[2], 60))


def _draw_text(cv: E.Canvas, L: dict) -> None:
    row = L["row"]
    ink = "on_accent"
    if L["label"]:
        cv.text(L["content_cx"], L["lab_base"], L["label"], "text_bold", L["lab_size"], color=ink, opacity=0.78,
                anchor="ms", tracking=2.5)
    x = L["price_x"]
    top = L["price_top"]
    if L["prefix"]:
        cv.text(x, top, L["prefix"], "sans_black", row["pre_s"], color=ink, anchor="lt")
        x += row["pre_w"] + row["gap_p"]
    cv.text(x, L["price_base"], L["whole"], "sans_black", row["P"], color=ink)
    x += row["num_w"] + row["gap_c"]
    if L["cents"]:
        cv.text(x, top, L["cents"], "sans_black", row["cen_s"], color=ink, anchor="lt")
    if L["note"]:
        cv.text(L["content_cx"], L["note_base"], L["note"], "text_semibold", L["note_size"], color=ink,
                opacity=0.86, anchor="ms")


@lru_cache(maxsize=8)
def _flat_sprite(params_json: str, box: E.Box) -> tuple[Image.Image, int, int]:
    """The flat tag (no tilt, no shadow, scale 1): (RGBA image, x, y) in render px of the output canvas."""
    L = _layout(params_json, box)
    tx, ty, tw, th = L["tag"]
    m = 6.0
    sub = E.Canvas(E.with_box(box, x=box.x + tx - m, y=box.y + ty - m, w=tw + 2 * m, h=th + 2 * m, bleed=0.0))
    with sub.group(dx=-(tx - m), dy=-(ty - m)):          # main-box coordinates inside the sprite
        _draw_shape(sub, L)
        _draw_text(sub, L)
        if L["shape"]["kind"] == "tag":
            hx, hy = L["shape"]["hole"]
            sub.erase_circle(hx, hy, L["shape"]["hole_r"])
    return sub.image, sub.origin_x, sub.origin_y


def _transform(sprite: Image.Image, ax: int, ay: int, pivot: tuple[float, float], scale: float, angle: float,
               resample) -> tuple[Image.Image, int, int]:
    """Scale and rotate (clockwise degrees) a sprite around a render-px point of the output canvas.
    Returns the new image and its render-px position."""
    px, py = pivot
    if scale < 0.85:          # shrink with a proper filter first (no aliasing on the small first frames)
        w = max(1, int(round(sprite.width * scale)))
        h = max(1, int(round(sprite.height * scale)))
        sx, sy = w / sprite.width, h / sprite.height
        sprite = sprite.convert("RGBa").resize((w, h), Image.LANCZOS).convert("RGBA")
        ax, ay = px + (ax - px) * sx, py + (ay - py) * sy
        scale = scale / ((sx + sy) / 2.0)
    th = math.radians(angle)
    cs, sn = math.cos(th), math.sin(th)
    corners = [(ax, ay), (ax + sprite.width, ay), (ax, ay + sprite.height), (ax + sprite.width, ay + sprite.height)]
    rx = [px + scale * ((x - px) * cs - (y - py) * sn) for x, y in corners]
    ry = [py + scale * ((x - px) * sn + (y - py) * cs) for x, y in corners]
    bx0, by0 = int(math.floor(min(rx))) - 1, int(math.floor(min(ry))) - 1
    bx1, by1 = int(math.ceil(max(rx))) + 1, int(math.ceil(max(ry))) + 1
    if abs(angle) < 1e-6 and abs(scale - 1.0) < 1e-9 and float(ax).is_integer() and float(ay).is_integer():
        return sprite, int(ax), int(ay)
    # output pixel (X, Y) -> sprite pixel: undo the rotation and the scale around the pivot
    k = 1.0 / scale
    coeffs = (cs * k, sn * k, px + (cs * (bx0 - px) + sn * (by0 - py)) * k - ax,
              -sn * k, cs * k, py + (-sn * (bx0 - px) + cs * (by0 - py)) * k - ay)
    out = sprite.convert("RGBa").transform((bx1 - bx0, by1 - by0), Image.AFFINE, coeffs, resample=resample)
    return out.convert("RGBA"), bx0, by0


def _shadow(cv_like: E.Canvas, img: Image.Image, x: int, y: int):
    """Blurred alpha of a placed image, moved down (a Mask in the canvas' render px)."""
    alpha = np.asarray(img.getchannel("A"), np.float32) / 255.0
    sh = E.blur_mask(E.Mask(alpha, x - cv_like.origin_x, y - cv_like.origin_y), cv_like.len(TAG_SHADOW.blur),
                     cv_like.W, cv_like.H)
    sh.y += int(round(cv_like.len(TAG_SHADOW.dy)))
    return sh


@lru_cache(maxsize=8)
def _resting(params_json: str, box: E.Box):
    """The tag at rest (scale 1, final tilt, high quality resampling) and its shadow mask."""
    L = _layout(params_json, box)
    p = json.loads(params_json)
    sprite, ax, ay = _flat_sprite(params_json, box)
    pv = ((box.x + L["pivot"][0]) * box.scale, (box.y + L["pivot"][1]) * box.scale)
    img, x, y = _transform(sprite, ax, ay, pv, 1.0, p["tilt"], Image.BICUBIC)
    return img, x, y, _shadow(E.Canvas(box), img, x, y)


def _add_shine(img: Image.Image, progress: float, width_px: float, opacity: float, accent) -> Image.Image:
    """A soft light band tilted 20 degrees crossing the tag, straight alpha. It lights the accent surface only:
    the printed text and the hole stay as they are."""
    arr = np.asarray(img)
    h, w = arr.shape[:2]
    xs = np.arange(w, dtype=np.float32)[None, :] + 0.5
    ys = np.arange(h, dtype=np.float32)[:, None] + 0.5
    half = max(1.0, width_px / 2.0)
    u = xs + (ys - h / 2.0) * math.tan(math.radians(20))
    c = -half * 2 + (w + half * 4) * E.in_out_sine(progress)
    band = np.clip(1.0 - np.abs(u - c) / half, 0.0, 1.0) ** 1.5
    out = arr.astype(np.float32)
    a_dst = out[..., 3] / 255.0
    dist = np.abs(out[..., :3] - np.array(accent[:3], np.float32)).max(axis=2)
    surface = np.clip(1.0 - dist / 90.0, 0.0, 1.0)     # 1 on the tag color, 0 on the text
    a_src = band * opacity * a_dst * surface
    a_out = a_src + a_dst * (1.0 - a_src)
    safe = np.where(a_out > 0, a_out, 1.0)
    for c_i in range(3):
        out[..., c_i] = (255.0 * a_src + out[..., c_i] * a_dst * (1.0 - a_src)) / safe
    out[..., 3] = a_out * 255.0
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGBA")


def render(frame_index: int, fps: int, params: dict, box: E.Box) -> E.Canvas:
    cv = E.Canvas(box)
    t = frame_index / fps
    key = json.dumps(params, sort_keys=True)
    L = _layout(key, box)
    t_in = t + 1.0 / fps
    d = 0.0
    T = _tag_start(params)

    # the tag: spring pop and swing into its tilt
    local = t_in - T
    if local > 0:
        sp = 1.0 if local >= SPRING_T else E.spring(local, *SPRING)
        alpha = E.clamp01(local / 0.08)
        scale = 0.5 + 0.5 * sp                      # peaks near 1.08: a small bounce
        angle = params["tilt"] + POP_SWING * (1.0 - sp)
        if sp == 1.0:
            img, x, y, shadow = _resting(key, box)
        else:
            sprite, ax, ay = _flat_sprite(key, box)
            pv = ((box.x + L["pivot"][0]) * box.scale, (box.y + L["pivot"][1]) * box.scale)
            img, x, y = _transform(sprite, ax, ay, pv, scale, angle, Image.BILINEAR)
            shadow = _shadow(cv, img, x, y)
        shine_p = E.progress(t, T + SHINE_AT, SHINE_DUR)
        if params["shine"] and 0.0 < shine_p < 1.0:
            img = _add_shine(img, shine_p, cv.len(L["tag"][3] * 0.55), 0.42, box.theme.accent)
        cv.fill_mask(shadow, (0, 0, 0), TAG_SHADOW.opacity * alpha)
        if L["shape"]["kind"] == "tag":         # the hole shows the video, not the shadow
            hx, hy = L["shape"]["hole"]
            pvx, pvy = L["pivot"]
            ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            dx, dy = hx - pvx, hy - pvy
            cv.erase_circle(pvx + scale * (dx * ca - dy * sa), pvy + scale * (dx * sa + dy * ca),
                            L["shape"]["hole_r"] * scale)
        s = box.scale
        cv.paste_image(img, x / s - box.x, y / s - box.y, opacity=alpha)

    # the old price: slides in, gets crossed, then steps back a little
    old = L["old"]
    if old:
        q = t_in - d
        if q > 0:
            a = E.eased(q, 0.0, 0.18, "out_quad") * (1.0 - 0.22 * E.eased(t, T, 0.3))
            s = 0.9 + 0.1 * E.out_back(E.progress(q, 0.0, 0.4), 1.6)
            ox, oy, ow, oh = L["old_box"]
            with cv.group(opacity=a, scale=s, dy=18.0 * (1.0 - E.eased(q, 0.0, 0.35)),
                          pivot=(ox + ow / 2.0, oy + oh / 2.0)):
                cv.card(ox, oy, ow, oh, radius=oh / 2.0)
                base = L["old_base"]
                if old["label"]:
                    cv.text(L["old_lab_x"], base, old["label"], "text_bold", old["size"], color="muted")
                cv.text(L["old_pr_x"], base, old["price"], "text_bold", old["size"], color="text")
                k = E.eased(t, d + STRIKE_AT, STRIKE_DUR, "in_out_cubic")
                if k > 0:
                    x0 = L["old_pr_x"] - 8.0
                    x1 = L["old_pr_x"] + old["pr_w"] + 8.0
                    mid = base - old["cap"] * 0.42
                    cv.line(x0, mid + 7.0, x1, mid - 7.0, width=7.0, color="danger", progress=k)
    return cv


def sfx(params: dict, fps: int, frames: int) -> list[dict]:
    d = 0.0
    cues = []
    if params["old_price"] is not None:
        cues.append({"at": d, "name": "pop", "volume": 0.9, "why": 'former price'})
        cues.append({"at": d + STRIKE_AT, "name": "tick", "why": 'former-price strikeout'})
    cues.append({"at": _tag_start(params), "name": "cash", "why": 'new price'})
    return [{"frame": E.frame_of(c.pop("at"), fps), **c} for c in cues]


def suggested_duration(params: dict) -> float:
    """The pop, the shine and about 1.6 s to read the price."""
    return round(max(TEMPLATE["default_duration"], settle(params, 30) + 1.6), 2)


def settle(params: dict, fps: int) -> float:
    T = _tag_start(params)
    end = T + max(SPRING_T, SHINE_AT + SHINE_DUR)
    if params["old_price"] is not None:
        end = max(end, STRIKE_AT + STRIKE_DUR, T + 0.3)
    return end + 0.05
