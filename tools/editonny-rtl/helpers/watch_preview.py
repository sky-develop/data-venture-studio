"Report new user actions from the local preview.\n\nRead edit/preview_marks.json, preview_trims.json, preview_approval.json, and preview_style.json. Track saved_at in preview_cache/watch_seen.json so repeated execution does not repeat events. Each flushed English line includes the user's save time for comparison with the last render. Warn when approval coexists with newer marks or changed media.\n\nLoop mode updates preview_cache/watcher.json so the interface can show whether an agent is listening. Output is UTF-8 and tolerates characters unsupported by the console.\n\nUsage: uv run python helpers/watch_preview.py --edit-dir EDIT --once\nOmit --once to watch, and use --interval to control polling."

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import fmt_time, load_json, load_style_catalog, save_json  # noqa: E402

SOURCES = (
    ("approval", "preview_approval.json"),
    ("marks", "preview_marks.json"),
    ("trims", "preview_trims.json"),
    ("style", "preview_style.json"),
)
PHASE_NAME = {"cut": "cut", "final": 'final video'}
PHASE_IN = {"cut": "in cut", "final": 'in the final video'}
SEEN_LIMIT = 50
HEARTBEAT_S = 3.0
DEFAULT_AVERAGE = {"cut": "cut.mp4", "final": "final.mp4"}
AVERAGE_KEY = {"cut": "cut_video", "final": "final_video"}
DENOISE_LABEL = {"off": "off", "light": "light", "strong": "strong"}   # preview_style.json "denoise"
# preview_style.json music_mood accepts off, file with music_file, or a library mood ID.
# preview_style.json "greenScreenPosition" and "greenScreenBackgroundInserts" (with "edit_type": "greenScreen")
TV_POSITION_LABEL = {"left": "left corner", "right": "right corner", "center": "center"}


def utf8_console() -> None:
    """Windows pipes (Monitor, Codex) default to the ANSI code page: an emoji in a note must not crash."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass


def saved_clock(saved_at: str) -> str:
    try:
        return datetime.fromisoformat(str(saved_at)).strftime("%H:%M:%S")
    except ValueError:
        return str(saved_at)


def quote(text: str) -> str:
    """One-line, double-quoted note."""
    clean = " ".join(str(text or "").split())
    return '"' + clean.replace('"', "'") + '"'


def format_approval(rec: dict) -> str:
    phase = PHASE_NAME.get(rec.get("phase"), str(rec.get("phase")))
    line = f'[preview] {phase} APPROVED'
    note = (rec.get("note") or "").strip()
    if note:
        line += f' · note: {quote(note)}'
    return line


def format_marks(rec: dict) -> str:
    marks = rec.get("marks") or []
    where = PHASE_IN.get(rec.get("phase"), f"in {rec.get('phase')}")
    if not marks:
        return f'[preview] marks {where} cleared; zero marks remain'
    parts = []
    for m in marks:
        note = (m.get("note") or "").strip()
        if m.get("kind") == "motion":
            piece = f"VISUAL HERE at {fmt_time(float(m.get('in', 0)))}"
            parts.append(f'{piece} {quote(note)}' if note else f'{piece} (the agent picks the template from the speech)')
            continue
        piece = f"{fmt_time(float(m.get('in', 0)))} to {fmt_time(float(m.get('out', 0)))}"
        parts.append(f'{piece} {quote(note)}' if note else f'{piece} (no note)')
    label = 'mark' if len(marks) == 1 else "annotations"
    return f'[preview] {len(marks)} {label} {where}: ' + "; ".join(parts)


def format_trims(rec: dict) -> str:
    """Requests to adjust an interval edge in EDL seconds."""
    trims = rec.get("trims") or []
    where = PHASE_IN.get(rec.get("phase"), f"in {rec.get('phase')}")
    if not trims:
        return f'[preview] interval adjustments {where} cleared (0 adjustments)'
    parts = []
    for tr in trims:
        rid = str(tr.get("id", "?"))
        bits = []
        for key, edge in (("start_delta", 'at the beginning'), ("end_delta", 'at the end')):
            delta = float(tr.get(key) or 0.0)
            if not delta:
                continue
            verb = "extend" if (delta < 0 if key == "start_delta" else delta > 0) else "shrink"
            bits.append(f'{verb} {abs(delta):.2f}s {edge}')
        quote_txt = (tr.get("quote") or "").strip()
        piece_value = f'{rid}: ' + " and ".join(bits or ['unchanged'])
        parts.append(f'{piece_value} {quote(quote_txt)}' if quote_txt else piece_value)
    label = "interval adjustment" if len(trims) == 1 else "interval adjustments"
    return (f'[preview] {len(trims)} {label} {where}: ' + "; ".join(parts)
            + " · apply to edl.json (add start_delta to start and end_delta to end), then render again")


def format_style(rec: dict, catalog: dict) -> str:
    elements = rec.get("elements") or {}
    order = [e.get("id") for e in (catalog.get("elements") or []) if e.get("id")]
    order += [k for k in elements if k not in order]
    on = [k for k in order if elements.get(k) is True]
    off = [k for k in order if k in elements and elements.get(k) is False]
    edit_type = rec.get("edit_type") or "-"
    if edit_type == "greenScreen":
        pos = rec.get("greenScreenPosition") or "right"
        moved = rec.get("greenScreenBackgroundInserts")
        if isinstance(moved, int) and not isinstance(moved, bool) and moved > 0:
            what = ("the upper or lower image insert will" if moved == 1 else
                    f'{moved} upper and lower media inserts move')
            what += ' to the background without a split screen'
        else:
            what = 'insert images and videos become the background without a split screen'
        edit_type += f' ({TV_POSITION_LABEL.get(pos, pos)}: greenScreen.position "{pos}"; {what})'
    fields = [
        f'[preview] style saved: {edit_type}',
        f"captions {rec.get('caption_style') or '-'}",
        f"hook {rec.get('hook_style') or '-'}",
        f"color {rec.get('accent') or '-'}",
        "elements: " + (", ".join(on) if on else "none"),
        "excluded: " + (", ".join(off) if off else "none"),
    ]
    if isinstance(elements.get("motion"), bool):
        if elements["motion"] and rec.get("motionCount"):
            n = {"few": 3, "some": 5}.get(rec["motionCount"], 3)
            theme_value = rec.get("motionTheme") or "paper"
            fields.append(f'''animations enabled, {rec['motionCount']} (motion.count {n}), visual {theme_value} (motion.theme "{theme_value}")''')
        else:
            fields.append(f"animations: {('enabled' if elements['motion'] else 'disabled')}")
    if isinstance(rec.get("denoise"), str) and rec["denoise"] in DENOISE_LABEL:
        fields.append(f"noise: {DENOISE_LABEL[rec['denoise']]}")
    mood = rec.get("music_mood")
    if isinstance(mood, str) and mood:
        if mood == "off":
            fields.append('music: off')
        elif mood == "file":
            fields.append(f"music: local file ({rec.get('music_file') or '?'})")
        else:
            fields.append(f'music: {mood}')
    if (rec.get("hook_text") or "").strip():
        fields.append(f"hook text: {quote(rec['hook_text'])}")
    if (rec.get("cta_text") or "").strip():
        fields.append(f"call to action: {quote(rec['cta_text'])}")
    if (rec.get("note") or "").strip():
        fields.append(f"note: {quote(rec['note'])}")
    return " · ".join(fields)


def read_record(path: Path) -> dict | None:
    for attempt in range(3):
        try:
            data = load_json(path)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            time.sleep(0.05)
        except OSError:
            return None
    return None


def load_seen(path: Path) -> dict[str, list[str]]:
    try:
        data = load_json(path, default={}) or {}
    except (json.JSONDecodeError, OSError):
        data = {}
    seen: dict[str, list[str]] = {}
    for kind, _ in SOURCES:
        value = data.get(kind)
        if isinstance(value, str):
            value = [value]
        seen[kind] = [v for v in (value or []) if isinstance(v, str)]
    return seen


def collect_new(edit_dir: Path, seen: dict[str, list[str]]) -> list[tuple[str, str, dict]]:
    events = []
    for kind, filename in SOURCES:
        path = edit_dir / filename
        if not path.is_file():
            continue
        rec = read_record(path)
        if not rec:
            continue
        saved_at = rec.get("saved_at")
        if not isinstance(saved_at, str) or not saved_at:
            continue
        if saved_at in seen.get(kind, []):
            continue
        events.append((saved_at, kind, rec))
    events.sort(key=lambda e: e[0])
    return events


def average_mtime(edit_dir: Path, phase: str) -> float | None:
    """mtime of the video the person was reviewing for this phase."""
    rel = DEFAULT_AVERAGE.get(phase)
    try:
        state = load_json(edit_dir / "state.json") or {}
        if isinstance(state, dict) and isinstance(state.get(AVERAGE_KEY.get(phase, "")), str):
            rel = state[AVERAGE_KEY[phase]]
    except (json.JSONDecodeError, OSError):
        pass
    try:
        return (edit_dir / rel).stat().st_mtime if rel else None
    except OSError:
        return None


def iso_epoch(saved_at: str) -> float | None:
    try:
        return datetime.fromisoformat(str(saved_at)).timestamp()
    except ValueError:
        return None


def pending_marks_note(edit_dir: Path, approval: dict) -> str | None:
    """Marks for the same video saved after its render and before the approval: the agent must ask."""
    phase = approval.get("phase")
    marks = read_record(edit_dir / "preview_marks.json")
    if not marks or marks.get("phase") != phase or not marks.get("marks"):
        return None
    marks_t, approval_t = iso_epoch(marks.get("saved_at", "")), iso_epoch(approval.get("saved_at", ""))
    render_t = average_mtime(edit_dir, str(phase))
    if marks_t is None or approval_t is None or marks_t > approval_t + 0.001:
        return None
    if render_t is not None and marks_t < render_t:
        return None  # those marks were for an older version, already handled
    n = len(marks["marks"])
    where = PHASE_IN.get(phase, f'in {phase}')
    label = 'mark' if n == 1 else "annotations"
    return (f'[preview] attention: approval includes {n} {label} {where} saved after the latest render. Resolve the requested adjustments before proceeding')


def stale_approval_note(edit_dir: Path, approval: dict) -> str | None:
    """The approval names the file version the person watched; a newer render needs a new approval."""
    rel, mtime, size = approval.get("file"), approval.get("file_mtime"), approval.get("file_size")
    try:
        from common import content_hash
        from preview_server import preview_dependencies_hash
        if isinstance(rel, str) and mtime is not None:
            path = (edit_dir / rel).resolve()
            path.relative_to(edit_dir.resolve())
            st = path.stat()
            if (abs(st.st_mtime - float(mtime)) <= 0.01 and size is not None and int(size) == st.st_size
                    and content_hash(path) == approval.get('file_sha256')
                    and preview_dependencies_hash(edit_dir) == approval.get('dependencies_hash')):
                return None
    except (OSError, ValueError, TypeError):
        pass
    name = PHASE_NAME.get(approval.get("phase"), str(approval.get("phase")))
    return (f'[preview] attention: approval refers to an earlier version of {name} ({rel} that has since changed. Review the current export before treating it as approved')


def emit(line: str) -> None:
    try:
        print(line, flush=True)
    except (UnicodeError, OSError):
        try:
            sys.stdout.buffer.write((line + "\n").encode("utf-8", "replace"))
            sys.stdout.flush()
        except (AttributeError, OSError):
            pass


def report(edit_dir: Path, seen_path: Path, seen: dict[str, list[str]], catalog: dict) -> int:
    events = collect_new(edit_dir, seen)
    if not events:
        return 0
    for saved_at, kind, rec in events:
        # mark it seen first: one line that fails to print must never block every later event
        seen[kind] = (seen.get(kind, []) + [saved_at])[-SEEN_LIMIT:]
        try:
            if kind == "approval":
                line = format_approval(rec)
            elif kind == "marks":
                line = format_marks(rec)
            elif kind == "trims":
                line = format_trims(rec)
            else:
                line = format_style(rec, catalog)
        except (TypeError, ValueError, AttributeError) as exc:
            line = f'[preview] {kind}: could not read the record ({exc}); open preview_{kind}.json'
        emit(f'{line}; saved at {saved_clock(saved_at)}')
        if kind == "approval":
            for note in (stale_approval_note(edit_dir, rec), pending_marks_note(edit_dir, rec)):
                if note:
                    emit(note)
    try:
        save_json(seen_path, seen)
    except OSError as exc:
        print(f'Notice: could not write {seen_path}: {exc}', file=sys.stderr, flush=True)
    return len(events)


def heartbeat(edit_dir: Path) -> None:
    try:
        save_json(edit_dir / "preview_cache" / "watcher.json", {"mode": "loop", "pid": os.getpid(), "updated_at": time.time()})
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Print one line per preview action: approval, marks, or style selection.',
    )
    parser.add_argument("--edit-dir", required=True, help='session edit folder', dest='edit_dir')
    parser.add_argument("--once", action="store_true", help="check once and exit", dest='once')
    parser.add_argument("--interval", type=float, default=1.0, help='seconds between checks (default 1.0)', dest='interval')
    args = parser.parse_args()
    utf8_console()

    edit_dir = Path(args.edit_dir).expanduser().resolve()
    if not edit_dir.is_dir():
        print(f'Error: edit folder does not exist: {edit_dir}', file=sys.stderr)
        return 2
    if args.interval <= 0:
        print('Error: --interval must be positive.', file=sys.stderr)
        return 2
    seen_path = edit_dir / "preview_cache" / "watch_seen.json"
    seen = load_seen(seen_path)
    catalog = load_style_catalog()

    if args.once:
        if report(edit_dir, seen_path, seen, catalog) == 0:
            print("[preview] nothing new", file=sys.stderr, flush=True)
        return 0

    print('Waiting for preview actions. Press Ctrl+C to stop.', file=sys.stderr, flush=True)
    last_beat = 0.0
    try:
        while True:
            if not catalog:
                catalog = load_style_catalog()
            if time.time() - last_beat >= HEARTBEAT_S:
                heartbeat(edit_dir)
                last_beat = time.time()
            report(edit_dir, seen_path, seen, catalog)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        try:
            (edit_dir / "preview_cache" / "watcher.json").unlink()
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
