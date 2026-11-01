'Measure source noise and voice defects before choosing treatment.\n\nFor each source, estimate the noise floor from speech-region pauses trimmed by 60 ms and compare it with speech RMS. Missing or stale region measurements are rebuilt. With under 0.1 seconds of pauses, use the quietest 50 ms windows with a 3 dB correction. Preserve the same channel-folding rule as the cut renderer.\n\nA speech/noise difference of at least 30 dB is clean; 20–30 dB suggests light denoising; below 20 dB suggests stronger treatment. These are measurements to review, not permission to damage the voice.\n\nPrint English findings and write edit/verify/noise_check.json. Also call voice_check.py for clipping and room echo unless --without-voice is specified. Listen at the reported times; clipped speech cannot be fully recovered and reverberation needs conservative treatment.\n\nUsage: uv run python helpers/noise_check.py VIDEO_OR_FOLDER --edit-dir EDIT --json'

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import now_iso, require_binary, save_json  # noqa: E402
import denoise  # noqa: E402
import voice_check  # noqa: E402

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mkv", ".webm"}
ORDER = {"clean": 0, "light": 1, "strong": 2}


def utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass


def collect(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out += sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXT
                          and not f.name.startswith("."))
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(f'not found: {p}')
    return list(dict.fromkeys(out))


def line_for(r: dict) -> str:
    name = r.get("stem", "?")
    if r.get("error"):
        return f"  {name}: {r['error']}"
    extra = ' (few pauses: background estimated from the quietest intervals)' if r.get("noise_from") != "pauses" else ""
    return (f"  {name}; background {r['noise_db']:.1f} dB · voice {r['speech_db']:.1f} dB · difference {r['snr_db']:.0f} dB · {r['advice']}{extra}")


def recommendation(results: list[dict]) -> dict:
    measured = [r for r in results if r.get("level")]
    if not measured:
        return {"suggest": None, "text": 'Could not measure noise: no audio or no speech.'}
    worst = max(measured, key=lambda r: (ORDER[r["level"]], -r["snr_db"]))
    if worst["level"] == "clean":
        return {"suggest": "off", "worst": worst["stem"],
                "text": 'Recommendation: no video requires noise removal; leave it off.'}
    label = "light" if worst["level"] == "light" else "strong"
    return {"suggest": worst["suggest"], "worst": worst["stem"],
            "text": (f"Recommendation: offer {label} background-noise removal (worst source: {worst['stem']}, difference {worst['snr_db']:.0f} dB between voice and background).")}


def main() -> int:
    ap = argparse.ArgumentParser(description='Measure background noise in each video and recommend voice cleanup.')
    ap.add_argument('paths', nargs="+", type=Path, help='video folder or video files')
    ap.add_argument("--edit-dir", type=Path, default=None,
                    help='edit folder (default: <video folder>/edit); reads regions/<name>.json', dest='edit_dir')
    ap.add_argument("--json", action="store_true", help="print structured JSON instead of human-readable lines", dest='json')
    ap.add_argument("--without-voice", action="store_true", help='skip voice clipping and echo measurements', dest='without_voice')
    args = ap.parse_args()
    utf8_console()
    require_binary("ffmpeg")
    require_binary("ffprobe")
    paths = [p.expanduser().absolute() for p in args.paths]
    try:
        videos = collect(paths)
    except FileNotFoundError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    if not videos:
        print('error: no video found (.mov, .mp4, .m4v, .mkv, .webm)', file=sys.stderr)
        return 2
    first = paths[0] if paths[0].is_dir() else videos[0].parent
    edit_dir = (args.edit_dir.expanduser().absolute() if args.edit_dir else first / "edit")
    results = [denoise.measure_source(v, edit_dir if edit_dir.is_dir() else None) for v in videos]
    if not args.without_voice:
        for v, r in zip(videos, results):
            try:
                r["voice"] = voice_check.measure_voice(v)
            except Exception as exc:  # noqa: BLE001
                r["voice"] = {"error": f'could not measure voice ({exc.__class__.__name__})'}
    rec = recommendation(results)
    doc = {"created_at": now_iso(), "edit_dir": str(edit_dir), "videos": results, "recommendation": rec,
           "thresholds_db": {"clean": 30, "light": 20}}
    if edit_dir.is_dir():
        try:
            save_json(edit_dir / "verify" / "noise_check.json", doc)
        except OSError:
            pass
    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return 0
    print('Background noise (background = pause level; voice = speech level):')
    for r in results:
        print(line_for(r))
    print(rec["text"])
    if not args.without_voice:
        print('Voice (waveform clipping and room-echo measurements; listen at the reported times):')
        for r in results:
            print(voice_check.line_for(r.get("stem", "?"), r.get("voice") or {"error": 'not measured'}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
