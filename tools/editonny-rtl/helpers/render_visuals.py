"""Renders one animated template (helpers/visuals) to check it: transparent .mov, PNG frames, an .mp4 over a
background and a contact sheet. The compose uses the same renderer.

Usage:
    uv run python helpers/render_visuals.py --list [--json]
    uv run python helpers/render_visuals.py --describe counter
    uv run python helpers/render_visuals.py --template counter --params '{"value": 1904152.87, "prefix": "R$"}' \\
        --out DIR [--format mov,sheet,mp4,png,clip] [--duration 3] [--fps 30] [--size 1080x1920] \\
        [--color '#FFC400'] [--region center | --region 140,640,800,640] [--background frame.png] [--frames 8]
    --params-file F  reads the params from a JSON file (easier with R$ and quotes).

The last stdout line is a JSON object with the files, the frames and the sound cues.
Exit 2 for things to fix (unknown template, bad params), 1 for ffmpeg or disk trouble.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from visuals import engine as E  # noqa: E402
from visuals import registry as Rg  # noqa: E402
from visuals import render as R  # noqa: E402
from visuals.engine import VisualError  # noqa: E402

FORMATS = ("mov", "sheet", "mp4", "png", "clip")


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def parse_size(text: str) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in str(text).lower().split("x"))
    except ValueError:
        raise VisualError(f'invalid --size: {text!r} (for example 1080x1920)') from None
    if w < 16 or h < 16 or w > 4096 or h > 4096:
        raise VisualError(f'--size outside the allowed range: {text!r}')
    return w, h


def parse_region(text: str | None):
    if text is None:
        return None
    t = text.strip()
    if "," in t:
        parts = t.replace("[", "").replace("]", "").split(",")
        try:
            return [float(v) for v in parts]
        except ValueError:
            raise VisualError(f'invalid --region: {text!r} (use a name or x,y,width,height)') from None
    return t


def load_params(args) -> dict:
    if args.params and args.params_file:
        raise VisualError('use either --params or --params-file, not both')
    raw = None
    if args.params_file:
        p = Path(args.params_file)
        if not p.exists():
            raise VisualError(f'parameter file not found: {p}')
        raw = p.read_text(encoding="utf-8")
    elif args.params:
        raw = args.params
    if raw is None:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise VisualError(f'params is not valid JSON ({e}). Tip: use --params-file to read JSON from a file') from None
    if not isinstance(data, dict):
        raise VisualError('params must be a JSON object, for example {"value": 37}')
    return data


def cmd_list(as_json: bool) -> int:
    found, errs = Rg.discover()
    if as_json:
        print(json.dumps({"templates": [t.describe() for t in found.values()], "errors": errs}, ensure_ascii=False))
        return 0
    if not found:
        print('no visual template found')
    for t in sorted(found.values(), key=lambda x: x.id):
        print(f'{t.id:18s} {t.label} · {t.default_duration:g} s · {t.description}')
    for e in errs:
        print(f'notice: {e}', file=sys.stderr)
    return 0


def _limits(spec: dict) -> list[str]:
    extra = []
    if "choices" in spec:
        extra.append('options: ' + ", ".join(map(str, spec["choices"])))
    if "min" in spec or "max" in spec:
        lo = E.format_number(spec["min"], E.auto_decimals(spec["min"])) if "min" in spec else "-"
        hi = E.format_number(spec["max"], E.auto_decimals(spec["max"])) if "max" in spec else "-"
        extra.append(f'from {lo} to {hi}')
    if "max_len" in spec:
        extra.append(f"up to {spec['max_len']} letters")
    if "min_items" in spec or "max_items" in spec:
        extra.append(f"{spec.get('min_items', 0)} to {spec.get('max_items', '?')} items")
    if spec.get("nullable"):
        extra.append("may be null")
    return extra


def cmd_describe(tid: str) -> int:
    t = Rg.get(tid)
    print(f'{t.id}: {t.label}')
    print(t.description)
    if t.use_when:
        print(f'When to use: {t.use_when}')
    if t.notes:
        print(f'Attention: {t.notes}')
    region = t.default_region if isinstance(t.default_region, str) else list(t.default_region)
    print(f'Default duration: {t.default_duration:g} seconds · default region: {region} · exit: {t.exit}')
    print('Parameters:')
    for name, spec in t.all_params().items():
        extra = _limits(spec)
        default = json.dumps(spec["default"], ensure_ascii=False) if "default" in spec else 'required'
        print(f"  {name} ({spec['type']}, default {default}){(' · ' + '; '.join(extra) if extra else '')}: {spec.get('label', '')}")
        if spec.get("type") == "list" and isinstance(spec.get("item"), dict):
            item = spec["item"]
            if item.get("type") == "object":
                for fname, fspec in (item.get("fields") or {}).items():
                    fextra = _limits(fspec)
                    fdefault = (json.dumps(fspec["default"], ensure_ascii=False) if "default" in fspec
                                else 'required')
                    print(f"    each item.{fname} ({fspec['type']}, default {fdefault}){(' · ' + '; '.join(fextra) if fextra else '')}: {fspec.get('label', '')}")
            else:
                iextra = _limits(item)
                if iextra:
                    print(f"    each item ({item['type']}): {'; '.join(iextra)}")
    print("Sounds: " + (", ".join(f"{c['name']} at {c.get('at', 0):g} s" for c in t.sfx) or "none"))
    if t.example:
        print("Example parameters: " + json.dumps(t.example, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(description='Render a visual template such as a counter, list, or price tag for inspection.')
    ap.add_argument("--list", action="store_true", help='list available templates', dest='list')
    ap.add_argument("--json", action="store_true", help='with --list: emit JSON', dest='json')
    ap.add_argument("--describe", metavar="ID", help="describe a template's parameters", dest='describe')
    ap.add_argument("--template", "-t", help="template ID (see --list)", dest='template')
    ap.add_argument("--params", help='JSON parameters, for example \'{"value": 37}\'', dest='params')
    ap.add_argument("--params-file", help='JSON file containing parameters', dest='params_file')
    ap.add_argument("--out", "-o", help='output folder', dest='out')
    ap.add_argument("--format", default="mov,sheet",
                    help=f"comma-separated outputs to generate: {', '.join(FORMATS)} (default mov,sheet)", dest='format_value')
    ap.add_argument("--duration", type=float, help='duration in seconds (default: template duration)', dest='duration_value')
    ap.add_argument("--fps", type=int, default=30, dest='fps')
    ap.add_argument("--size", default="1080x1920", help="canvas size, for example 1080x1920 or 540x960", dest='size_value')
    ap.add_argument("--color", default=E.DEFAULT_ACCENT, help='video accent color as #RRGGBB', dest='color_value')
    ap.add_argument("--region", help="region name (top, center, bottom, top_band, screen) or x,y,width,height on 1080x1920", dest='region_value')
    ap.add_argument("--background", help='background image for the contact sheet and MP4, such as a cut frame', dest='background_value')
    ap.add_argument("--frames", type=int, default=8, help='frames in the contact sheet (default 8)', dest='frames_value')
    ap.add_argument("--name", help='output filename stem (default: template ID)', dest='name_value')
    args = ap.parse_args(argv)

    try:
        if args.list:
            return cmd_list(args.json)
        if args.describe:
            return cmd_describe(args.describe)
        if not args.template:
            raise VisualError('select a template with --template; use --list to inspect choices')
        if not args.out:
            raise VisualError('provide the output folder with --out')
        formats = [f.strip() for f in args.format_value.split(",") if f.strip()]
        bad = [f for f in formats if f not in FORMATS]
        if bad or not formats:
            raise VisualError(f"invalid --format: {', '.join(bad) or '(empty)'} (use {', '.join(FORMATS)})")
        E.rgba(args.color_value)
        if args.frames_value < 1 or args.frames_value > 48:
            raise VisualError('--frames must be between 1 and 48')
        if args.name_value is not None and (not args.name_value.strip() or any(c in args.name_value for c in '/\\:*?"<>|')):
            raise VisualError('--name must be a plain filename without path separators')
        params = load_params(args)
        t0 = time.time()
        item = R.plan_item(args.template, params, fps=args.fps, canvas=parse_size(args.size_value),
                           region=parse_region(args.region_value), duration=args.duration_value, accent=args.color_value)
        out = Path(args.out).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        base = args.name_value or item.template.id
        result: dict = {"template": item.template.id, "frames": item.frames, "fps": item.fps,
                        "duration": round(item.frames / item.fps, 4), "canvas": list(item.canvas),
                        "region": [round(v, 2) for v in item.region], "params": item.params,
                        "sfx": R.sfx_cues(item, check_names=True), "files": {}}
        print(f'{item.template.label}: {item.frames} frames ({E.format_number(item.frames / item.fps, 2)} s) at {item.canvas[0]}x{item.canvas[1]}')
        if "mov" in formats:
            result["files"]["mov"] = str(R.write_mov(item, out / f'{base}.mov'))
        if "clip" in formats:
            clip = R.render_clip(item, out, cache=False)
            result["files"]["clip"] = clip["file"]
            result["clip"] = {k: clip[k] for k in ("x", "y", "w", "h", "ink")}
        if "png" in formats:
            pngs = R.write_pngs(item, out / f'{base}_frames')
            result["files"]["png"] = str(out / f'{base}_frames')
            result["png_count"] = len(pngs)
        if "mp4" in formats:
            result["files"]["mp4"] = str(R.write_mp4(item, out / f'{base}.mp4', args.background_value))
        if "sheet" in formats:
            result["files"]["sheet"] = str(R.contact_sheet(item, out / f'{base}_sheet.png', args.frames_value, args.background_value))
        result["seconds"] = round(time.time() - t0, 2)
        for kind, path in result["files"].items():
            print(f'ok    {kind}: {path}')
        sounds = ", ".join(f"{c['name']} on frame {c['frame']}" for c in result["sfx"]) or "none"
        print(f'suggested sounds: {sounds}')
        for w in item.warnings:
            print(f'notice: {w}')
        print(f"ready in {result['seconds']:.1f} s")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except VisualError as e:
        print(f'error: {e}', file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except OSError as e:
        print(f'error: {e}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
