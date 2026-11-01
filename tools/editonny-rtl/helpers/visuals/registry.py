"""Template registry: finds the templates in helpers/visuals/templates/*.py, checks their TEMPLATE
description and turns the params written by the agent into complete, validated values.

A broken template never breaks the others: its problem goes to `errors()` (PT-BR) and it is left out.
"""

from __future__ import annotations

import difflib
import hashlib
import importlib.util
import json
import math
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from . import engine as E
from .engine import VisualError

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
PARAM_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PARAM_TYPES = ("number", "integer", "text", "bool", "choice", "color", "box", "list", "object")
REQUIRED_KEYS = ("id", "label", "description", "params", "default_duration", "default_region", "sfx")
OPTIONAL_KEYS = ("version", "use_when", "notes", "example", "exit", "bleed", "category", "movable")
# Optional functions of a template module: sfx, suggested_duration, settle, validate(params) (raise VisualError
# for rules between params), warnings(params, box) -> [English texts], ink(params, box) -> (x0, y0, x1, y1)
# box-local design px of what counts as drawn (a full-screen flash is not).
EXIT_MODES = ("fade", "none")
# Params every template gets for free (a template must not declare them).
COMMON_PARAMS: dict[str, dict] = {
    "theme": {"type": "choice", "choices": list(E.THEME_NAMES), "default": "dark",
             "label": 'Visual theme: dark, light, paper, bluePaper, greenPaper, orangePaper, graphitePaper, pop, or neon'},
    "color": {"type": "color", "default": None, "nullable": True,
            "label": "Accent color for this item; defaults to the video's accent"},
}
MAX_DURATION = 60.0


@dataclass
class Template:
    id: str
    label: str
    description: str
    params: dict[str, dict]
    default_duration: float
    default_region: Any
    sfx: list[dict]
    module: ModuleType
    path: Path
    source_hash: str
    version: int = 1
    exit: str = "fade"
    bleed: float = E.DEFAULT_BLEED
    use_when: str = ""
    notes: str = ""
    example: dict = field(default_factory=dict)
    category: str = ""
    movable: bool = True          # False: the place means something (a target, the top bar); never slid off a face

    @property
    def render(self) -> Callable:
        return self.module.render

    def suggested_duration(self, params: dict) -> float:
        fn = getattr(self.module, "suggested_duration", None)
        if callable(fn):
            try:
                v = float(fn(params))
                if math.isfinite(v) and v > 0:
                    return min(MAX_DURATION, v)
            except Exception:  # noqa: BLE001
                pass
        return self.default_duration

    def settle(self, params: dict, fps: int) -> float | None:
        fn = getattr(self.module, "settle", None)
        if not callable(fn):
            return None
        v = fn(params, fps)
        return None if v is None else float(v)

    def all_params(self) -> dict[str, dict]:
        return {**self.params, **COMMON_PARAMS}

    def describe(self) -> dict:
        return {"id": self.id, "label": self.label, "description": self.description, "use_when": self.use_when,
                "notes": self.notes, "default_duration": self.default_duration,
                "default_region": self.default_region, "exit": self.exit,
                "params": {k: {kk: vv for kk, vv in v.items()} for k, v in self.all_params().items()},
                "sfx": self.sfx, "example": self.example, "category": self.category,
                "movable": self.movable}


# ------------------------------------------------------------------ schema checks

def _check_spec(tid: str, name: str, spec: Any, where: str) -> list[str]:
    probs = []
    if not isinstance(spec, dict):
        return [f"{tid}: {where}{name} must be an object containing 'type'"]
    typ = spec.get("type")
    if typ not in PARAM_TYPES:
        return [f"{tid}: {where}{name}.type is invalid ({typ!r}); use {', '.join(PARAM_TYPES)}"]
    if not where and not isinstance(spec.get("label"), str):
        probs.append(f"{tid}: params.{name} requires an English 'label'")
    if typ == "choice" and (not isinstance(spec.get("choices"), list) or not spec["choices"]):
        probs.append(f"{tid}: {where}{name} is a choice and requires 'choices'")
    if typ == "list":
        if "item" not in spec:
            probs.append(f"{tid}: {where}{name} is a list and requires 'item'")
        else:
            probs += _check_spec(tid, name + ".item", spec["item"], where or "params.")
    if typ == "object":
        fields = spec.get("fields")
        if not isinstance(fields, dict) or not fields:
            probs.append(f"{tid}: {where}{name} is an object and requires 'fields'")
        else:
            for k, v in fields.items():
                probs += _check_spec(tid, f'{name}.{k}', v, where or "params.")
    if "default" in spec and spec["default"] is not None:
        try:
            coerce(spec, spec["default"], f'{tid}.params.{name}')
        except VisualError as e:
            probs.append(f'{tid}: default value for {name} fails its own validation ({e})')
    return probs


def check_description(tid: str, desc: Any, path: Path) -> list[str]:
    if not isinstance(desc, dict):
        return [f'{path.name}: missing TEMPLATE = {{...}}']
    probs = []
    for key in REQUIRED_KEYS:
        if key not in desc:
            probs.append(f"{path.name}: TEMPLATE lacks '{key}'")
    unknown = sorted(set(desc) - set(REQUIRED_KEYS) - set(OPTIONAL_KEYS))
    if unknown:
        probs.append(f"{path.name}: TEMPLATE contains unknown keys: {', '.join(unknown)}")
    if probs:
        return probs
    if desc["id"] != tid or not ID_RE.match(str(desc["id"])):
        probs.append(f"{path.name}: TEMPLATE['id'] must match its filename ({tid}) and use a-z, 0-9, and _")
    for key in ("label", "description"):
        if not isinstance(desc[key], str) or not desc[key].strip():
            probs.append(f"{tid}: '{key}' must be text")
        elif "\u2014" in desc[key]:
            probs.append(f"{tid}: '{key}' must not contain an em dash")
    params = desc["params"]
    if not isinstance(params, dict):
        probs.append(f"{tid}: 'params' must be an object")
    else:
        for name, spec in params.items():
            if not PARAM_RE.match(str(name)):
                probs.append(f'{tid}: invalid parameter name: {name!r}')
            if name in COMMON_PARAMS:
                probs.append(f"{tid}: '{name}' is a shared parameter; do not redeclare it")
            probs += _check_spec(tid, name, spec, "")
    dur = desc["default_duration"]
    if isinstance(dur, bool) or not isinstance(dur, (int, float)) or not 0.2 <= float(dur) <= MAX_DURATION:
        probs.append(f'{tid}: default_duration must be seconds between 0.2 and {MAX_DURATION:g}')
    try:
        E.region_box(desc["default_region"])
    except VisualError as e:
        probs.append(f'{tid}: invalid default_region ({e})')
    probs += check_sfx_list(tid, desc["sfx"])
    if desc.get("exit", "fade") not in EXIT_MODES:
        probs.append(f"{tid}: exit must be {' or '.join(EXIT_MODES)}")
    bleed = desc.get("bleed", E.DEFAULT_BLEED)
    if isinstance(bleed, bool) or not isinstance(bleed, (int, float)) or not 0 <= bleed <= 400:
        probs.append(f'{tid}: bleed must be between 0 and 400')
    for key in ("use_when", "notes", "category"):
        if key in desc and not isinstance(desc[key], str):
            probs.append(f"{tid}: '{key}' must be text")
    if "movable" in desc and not isinstance(desc["movable"], bool):
        probs.append(f"{tid}: 'movable' must be true or false")
    if "example" in desc and not isinstance(desc["example"], dict):
        probs.append(f"{tid}: 'example' must be a parameter object")
    return probs


def check_sfx_list(tid: str, cues: Any) -> list[str]:
    if not isinstance(cues, list):
        return [f"{tid}: 'sfx' must be a list, which may be empty"]
    probs = []
    for i, cue in enumerate(cues):
        if not isinstance(cue, dict) or not isinstance(cue.get("name"), str) or not cue["name"].strip():
            probs.append(f"{tid}: sfx[{i}] requires 'name'")
            continue
        at = cue.get("at", 0.0)
        if isinstance(at, bool) or not isinstance(at, (int, float)) or at < 0:
            probs.append(f'{tid}: sfx[{i}].at must be nonnegative seconds from the item beginning')
        vol = cue.get("volume", 1.0)
        if isinstance(vol, bool) or not isinstance(vol, (int, float)) or not 0 <= vol <= 2:
            probs.append(f'{tid}: sfx[{i}].volume must be between 0.0 and 2.0')
        length = cue.get("length")
        if length is not None and (isinstance(length, bool) or not isinstance(length, (int, float)) or length <= 0):
            probs.append(f'{tid}: sfx[{i}].length must be positive seconds')
        extra = set(cue) - {"at", "name", "volume", "why", "length"}
        if extra:
            probs.append(f"{tid}: sfx[{i}] contains unknown keys: {', '.join(sorted(extra))}")
    return probs


# ------------------------------------------------------------------ discovery

_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[dict[str, Template], list[str]]] = {}


def _load_module(path: Path, tid: str) -> ModuleType:
    name = f'motion_template_{tid}_{hashlib.sha1(str(path).encode()).hexdigest()[:8]}'
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f'Could not open {path}')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


def discover(dirs: list[Path] | None = None, refresh: bool = False) -> tuple[dict[str, Template], list[str]]:
    """{id: Template} from every `<dir>/*.py` (files starting with '_' are skipped) and the list of problems."""
    folders = [Path(d).resolve() for d in (dirs if dirs is not None else [TEMPLATES_DIR])]
    key_parts = []
    for folder in folders:
        files = sorted(folder.glob("*.py")) if folder.is_dir() else []
        key_parts.append((str(folder), tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)))
    key = tuple(key_parts)
    with _LOCK:
        if not refresh and key in _CACHE:
            return _CACHE[key]
        found: dict[str, Template] = {}
        errors: list[str] = []
        for folder in folders:
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                tid = path.stem
                try:
                    mod = _load_module(path, tid)
                except Exception as e:  # noqa: BLE001
                    errors.append(f'{path.name}: failed to load ({e.__class__.__name__}: {e})')
                    continue
                desc = getattr(mod, "TEMPLATE", None)
                probs = check_description(tid, desc, path)
                if not callable(getattr(mod, "render", None)):
                    probs.append(f'{path.name}: missing render(frame_index, fps, params, box)')
                if tid in found:
                    probs.append(f"{path.name}: template already exists: '{tid}' in {found[tid].path}")
                if probs:
                    errors.extend(probs)
                    continue
                found[tid] = Template(
                    id=tid, label=desc["label"].strip(), description=desc["description"].strip(),
                    params=desc["params"], default_duration=float(desc["default_duration"]),
                    default_region=desc["default_region"], sfx=list(desc["sfx"]), module=mod, path=path,
                    source_hash=hashlib.sha1(path.read_bytes()).hexdigest()[:16],
                    version=int(desc.get("version", 1)), exit=desc.get("exit", "fade"),
                    bleed=float(desc.get("bleed", E.DEFAULT_BLEED)), use_when=desc.get("use_when", ""),
                    notes=desc.get("notes", ""), example=dict(desc.get("example", {})),
                    category=desc.get("category", ""), movable=desc.get("movable", True))
        _CACHE[key] = (found, errors)
        return found, errors


def templates(dirs: list[Path] | None = None) -> dict[str, Template]:
    return discover(dirs)[0]


def errors(dirs: list[Path] | None = None) -> list[str]:
    return discover(dirs)[1]


def get(tid: str, dirs: list[Path] | None = None) -> Template:
    found, errs = discover(dirs)
    if tid in found:
        return found[tid]
    broken = [e for e in errs if e.startswith(f'{tid}.py') or e.startswith(f'{tid}:')]
    if broken:
        raise VisualError(f"template '{tid}' is invalid: {broken[0]}")
    near = difflib.get_close_matches(str(tid), list(found), n=1, cutoff=0.6)
    hint = f" Did you mean '{near[0]}'?" if near else ""
    raise VisualError(f"visual template '{tid}' does not exist{hint} Modelos: {', '.join(sorted(found)) or 'none'}")


# ------------------------------------------------------------------ params

def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
    elif isinstance(value, str):
        try:
            v = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return v if math.isfinite(v) else None


def _show(value) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    if isinstance(value, str) and "," in value and _num(value.replace(".", "").replace(",", ".")) is not None:
        text += '; use a decimal point without thousands separators, for example 1904152.87'
    return text[:120]


def _fmt(v: float) -> str:
    """A number the way it is written in JSON (point for decimals, no thousands separator)."""
    return str(int(v)) if float(v).is_integer() else f'{v:.6g}'


def coerce(spec: dict, value: Any, where: str) -> Any:
    """One value checked against its spec (numeric text is accepted for numbers)."""
    typ = spec["type"]
    if value is None:
        if spec.get("nullable"):
            return None
        raise VisualError(f'{where} cannot be empty')
    if typ in ("number", "integer"):
        v = _num(value)
        if v is None or (typ == "integer" and abs(v - round(v)) > 1e-9):
            kind = 'an integer' if typ == "integer" else 'a number'
            raise VisualError(f'{where} must be {kind} (got {_show(value)})')
        lo, hi = spec.get("min"), spec.get("max")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            if lo is not None and hi is not None:
                rule = f'a value between {_fmt(lo)} and {_fmt(hi)}'
            elif lo is not None:
                rule = f'{_fmt(lo)} or more'
            else:
                rule = f'{_fmt(hi)} or less'
            raise VisualError(f'{where} requires {rule} (got {_fmt(v)})')
        return int(round(v)) if typ == "integer" else v
    if typ == "text":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str):
            raise VisualError(f'{where} must be text (received {_show(value)})')
        text = " ".join(value.replace("\n", " ").split()) if not spec.get("multiline") else value.strip()
        mx = spec.get("max_len")
        if mx is not None and len(text) > mx:
            raise VisualError(f'{where} has {len(text)} characters; maximum {mx}. Shorten the text')
        if spec.get("required_text") and not text:
            raise VisualError(f'{where} cannot remain empty')
        return text
    if typ == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() in ("true",)
        raise VisualError(f'{where} must be true or false (received {_show(value)})')
    if typ == "choice":
        choices = spec["choices"]
        if value in choices:
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            for c in choices:
                if isinstance(c, str) and c.lower() == low:
                    return c
        raise VisualError(f"{where} must be {', '.join(map(str, choices))} (got {_show(value)})")
    if typ == "color":
        if isinstance(value, str) and value.strip().lower() in E.COLOR_NAMES:
            return value.strip().lower()
        try:
            r, g, b, a = E.rgba(value)
        except (VisualError, ValueError):
            raise VisualError(f"{where} must be #RRGGBB or a supported color name ({', '.join(sorted(E.COLOR_NAMES))}) (got {_show(value)})") from None
        return f'#{r:02X}{g:02X}{b:02X}' + (f'{a:02X}' if a != 255 else "")
    if typ == "box":
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise VisualError(f'{where} must be [x, y, width, height] in 1080x1920 design pixels (received {_show(value)})')
        nums = [_num(v) for v in value]
        if any(n is None for n in nums):
            raise VisualError(f'{where} requires four numbers [x, y, width, height] (received {_show(value)})')
        if nums[2] <= 0 or nums[3] <= 0:
            raise VisualError(f'{where}: width and height must exceed zero (received {_show(value)})')
        return [float(n) for n in nums]
    if typ == "list":
        if isinstance(value, tuple):
            value = list(value)
        if not isinstance(value, list):
            raise VisualError(f'{where} must be a list (received {_show(value)})')
        lo, hi = spec.get("min_items"), spec.get("max_items")
        if lo is not None and len(value) < lo:
            raise VisualError(f'{where} requires at least {lo} items (got {len(value)})')
        if hi is not None and len(value) > hi:
            raise VisualError(f'{where} accepts at most {hi} items (got {len(value)})')
        return [coerce(spec["item"], v, f'{where}[{i}]') for i, v in enumerate(value)]
    if typ == "object":
        if not isinstance(value, dict):
            raise VisualError(f'{where} must be an object (received {_show(value)})')
        return resolve_fields(spec["fields"], value, where)
    raise VisualError(f'{where}: unknown type {typ!r}')


def resolve_fields(fields: dict[str, dict], given: dict, where: str) -> dict:
    unknown = [k for k in given if k not in fields]
    if unknown:
        k = unknown[0]
        near = difflib.get_close_matches(str(k), list(fields), n=1, cutoff=0.6)
        hint = f" Did you mean '{near[0]}'?" if near else ""
        raise VisualError(f"{where}.{k} does not exist.{hint} Campos: {', '.join(fields)}")
    out = {}
    for name, spec in fields.items():
        if name in given:
            out[name] = coerce(spec, given[name], f'{where}.{name}')
        elif "default" in spec:
            default = spec["default"]
            out[name] = json.loads(json.dumps(default)) if default is not None else None
        else:
            raise VisualError(f"{where}.{name} is required ({spec.get('label', name)})")
    return out


def resolve_params(tpl: Template, params: dict | None) -> dict:
    """Complete params (defaults filled, values checked). Raises VisualError with a PT-BR message."""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise VisualError(f"params for '{tpl.id}' must be an object, for example {json.dumps(tpl.example, ensure_ascii=False)})")
    resolved = resolve_fields(tpl.all_params(), params, f'{tpl.id}.params')
    check = getattr(tpl.module, "validate", None)
    if callable(check):
        # rules between params (one time per list item, at most 4 words...): refused before anything renders
        check(json.loads(json.dumps(resolved)))
    return resolved


# ------------------------------------------------------------------ contract check (used by the tests)

def check_template(tid: str, dirs: list[Path] | None = None, params: dict | None = None,
                   canvases=((1080, 1920), (540, 960))) -> list[str]:
    """Problems of one template when it runs (empty list = ok): the render contract, determinism, alpha,
    sfx names, the example params and the default text. Tests of every template should call this."""
    from . import render as R  # noqa: PLC0415

    try:
        tpl = get(tid, dirs)
    except VisualError as e:
        return [str(e)]
    probs: list[str] = []
    for key in ("label", "description", "use_when", "notes"):
        text = tpl.module.TEMPLATE.get(key, "")
        if "\u2014" in text:
            probs.append(f'{tid}: {key} contains an em dash')
    for name, spec in tpl.params.items():
        if "\u2014" in str(spec.get("label", "")):
            probs.append(f'{tid}: params.{name}.label contains an em dash')
    samples = [params if params is not None else tpl.example, {}]
    for sample in samples:
        try:
            resolved = resolve_params(tpl, sample)
        except VisualError as e:
            probs.append(f'{tid}: params {sample!r} recusados: {e}')
            continue
        dur = tpl.suggested_duration(resolved)
        for canvas in canvases:
            try:
                item = R.plan_item(tid, sample, fps=30, canvas=canvas, duration=dur, dirs=dirs)
            except VisualError as e:
                probs.append(f'{tid}: plan_item failed at {canvas}: {e}')
                continue
            n = item.frames
            picks = sorted({0, 1, n // 4, n // 2, (3 * n) // 4, n - 1})
            try:
                first = {i: R.render_frame(item, i) for i in picks}
                again = {i: R.render_frame(item, i) for i in reversed(picks)}
            except Exception as e:  # noqa: BLE001
                probs.append(f'{tid}: render failed at {canvas} ({e.__class__.__name__}: {e})')
                continue
            for i in picks:
                if first[i].image.tobytes() != again[i].image.tobytes():
                    probs.append(f'{tid}: frame {i} differs between identical calls; rendering must be deterministic')
                    break
            if first[0].image.getchannel("A").getbbox() is None:
                probs.append(f'{tid}: frame zero is empty at {canvas} (the entrance sound lands there: begin one frame earlier, t + 1/fps)')
            mid = first[picks[len(picks) // 2]]
            if mid.image.getchannel("A").getbbox() is None:
                probs.append(f'{tid}: middle frame is empty at {canvas}')
            full = first[picks[len(picks) // 2]].image
            if full.mode != "RGBA":
                probs.append(f'{tid}: render must return RGBA')
        try:
            item = R.plan_item(tid, sample, fps=30, duration=dur, dirs=dirs)
            for cue in R.sfx_cues(item, check_names=True):
                if not 0 <= cue["frame"] < item.frames:
                    probs.append(f'{tid}: sound effect outside the item ({cue})')
        except VisualError as e:
            probs.append(f'{tid}: {e}')
    return probs
