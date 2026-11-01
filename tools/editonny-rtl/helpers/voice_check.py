'Measure voice clipping and room echo from the waveform; noise_check.py consumes these results.\n\nInspect original audio at its sample rate and per channel. Clipping indicators include excessive samples crowded near the measured speech ceiling, repeated nonzero plateaus, decoded over-range samples, and dense 10 ms windows near the ceiling. Report definite, possible, or absent clipping with times to listen. Strong limiting can resemble clipping, so do not overstate an uncertain result.\n\nEstimate room echo from pause tails and cepstral repetition. Fit decay between 5 and 25 dB below speech, stopping near the noise floor, and summarize reliable phrase endings with enough headroom. Analyze repeated energy 35–500 ms later in one-second speech windows. Use the measured tails and repetition strengths to describe light, strong, absent, or unmeasurable echo.\n\nThese are waveform measurements, not a listening verdict. Listen to the reported intervals before selecting treatment; clipping is not fully reversible and aggressive dereverberation can damage speech.\n\nUsage: uv run python helpers/voice_check.py VIDEO_OR_AUDIO --json'

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import probe_video, require_binary  # noqa: E402

WIN_S = 0.010
NEAR_PEAK_DB = 1.0
MOMENT_GAP_S = 0.05
PLATEAU_RUN = 4               # Consecutive identical samples.
PLATEAU_MIN = 0.1             # Stay away from zero; digital silence is also constant.
LOUD_BELOW_DB = 20.0          # Speech windows: within 20 dB of strong speech.
HIST_LO_DB, HIST_HI_DB, HIST_STEP = -40.0, 6.0, 0.25
RMS_LO_DB, RMS_HI_DB = -100.0, 10.0
TOP_SHARE = 0.001             # Ceiling exceeded by only 0.1% of speech samples.
SAT_PILEUP = 1.5              # Empirical clipping score: clean voice 0.35, strong limiter 0.7, clipping 2.3 to 6.
MAYBE_PILEUP = 1.0
PLATEAU_EVENTS = 20
MAYBE_PLATEAUS = 3
OVER_EVENTS = 10              # AAC can exceed 0 dBFS at isolated points even with clean loud speech.
MAYBE_OVER = 4
ECHO_SR = 16000
ECHO_HOP_S = 0.005
ECHO_PAUSE_S = 0.25
ECHO_MIN_RANGE_DB = 15.0
ECHO_MIN_OFFSETS = 3
ECHO_LIGHT_S = 0.40
ECHO_STRONG_S = 0.60
CEPS_N = 16384                # One second at 16 kHz.
CEPS_HOP = 4096
CEPS_RANGE_S = (0.035, 0.5)   # Below 35 ms, voice pitch and harmonics dominate.
CEPS_BASE_S = 0.02
CEPS_REPEAT_Z = 60.0
CEPS_STRONG_Z = 250.0


def utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass


def fmt_t(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f'{int(m)}:{s:04.1f}'


# ------------------------------------------------------------------ clipping


def measure_clipping(path: Path, info: dict) -> dict:
    sr = int(info.get("sample_rate") or 48000)
    ch = max(1, int(info.get("audio_channels") or 1))
    w = max(1, int(round(sr * WIN_S)))
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vn", "-map", "0:a:0", "-f", "f32le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    nb = int((HIST_HI_DB - HIST_LO_DB) / HIST_STEP)
    nr = int(RMS_HI_DB - RMS_LO_DB)
    # Bin samples by amplitude (0.25 dB) and 10 ms window level (1 dB); count only speech
    # windows, without decoding the file twice.
    hist = np.zeros((nr, nb), dtype=np.int64)
    per_rms = np.zeros(nr, dtype=np.int64)
    win_max: list[np.ndarray] = []
    plateaus = over = 0
    over_windows: list[int] = []
    frame_bytes = w * ch * 4
    rest = b""
    done = 0
    while True:
        chunk = proc.stdout.read(frame_bytes * 1000)
        if not chunk:
            break
        data = rest + chunk
        usable = len(data) - len(data) % frame_bytes
        rest = data[usable:]
        if not usable:
            continue
        x = np.frombuffer(data[:usable], dtype=np.float32).reshape(-1, ch)
        per = np.abs(x).astype(np.float64).reshape(-1, w * ch)
        win_max.append(per.max(axis=1))
        rb = np.clip((20 * np.log10(np.sqrt((per ** 2).mean(axis=1)) + 1e-12) - RMS_LO_DB).astype(np.int64), 0, nr - 1)
        per_rms += np.bincount(rb, minlength=nr) * w * ch
        rows, cols = np.nonzero(per > 10 ** (HIST_LO_DB / 20))
        if rows.size:
            idx = np.clip(((20 * np.log10(per[rows, cols]) - HIST_LO_DB) / HIST_STEP).astype(np.int64), 0, nb - 1)
            np.add.at(hist, (rb[rows], idx), 1)
        above = per > 1.0
        if above.any():
            over += int(above.sum())
            over_windows += list(np.flatnonzero(above.any(axis=1)) + done)
        for c in range(ch):
            col = x[:, c]
            eq = (np.diff(col) == 0) & (np.abs(col[1:]) >= PLATEAU_MIN)
            if eq.any():
                d = np.diff(np.concatenate([[0], eq.astype(np.int8), [0]]))
                starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
                plateaus += int(((ends - starts) >= PLATEAU_RUN - 1).sum())
        done += per.shape[0]
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    proc.wait()
    if proc.returncode != 0 or not win_max:
        return {"error": f'could not read audio: {err.strip()[-200:]}'}
    wmax = np.concatenate(win_max)
    peak = float(wmax.max())
    if peak <= 1e-6:
        return {"error": 'silent audio'}
    peak_db = 20 * math.log10(peak)
    # Speech windows lie within LOUD_BELOW_DB of the 95th-percentile window level.
    cum = np.cumsum(per_rms)
    loud_bin = int(np.searchsorted(cum, 0.95 * cum[-1]))
    first = max(0, loud_bin - int(LOUD_BELOW_DB))
    h = hist[first:].sum(axis=0)
    speech_samples = int(per_rms[first:].sum())
    # Ceiling exceeded by only 0.1% of speech samples.
    from_top = np.cumsum(h[::-1])
    k_top = nb - 1 - int(np.searchsorted(from_top, TOP_SHARE * speech_samples))
    ceil_db = HIST_LO_DB + (k_top + 1) * HIST_STEP
    step = int(round(1.0 / HIST_STEP))
    near = int(h[max(0, k_top + 1 - step):].sum())
    band = int(h[max(0, k_top + 1 - 4 * step): max(0, k_top + 1 - 3 * step)].sum())
    pileup = near / max(1, band)
    fraction = near / max(1, speech_samples)
    hot = np.flatnonzero(20 * np.log10(wmax + 1e-12) >= ceil_db - NEAR_PEAK_DB)
    moments: list[list[int]] = []
    gap = int(round(MOMENT_GAP_S / WIN_S))
    for i in hot:
        if moments and i - moments[-1][1] <= gap:
            moments[-1][1] = int(i)
        else:
            moments.append([int(i), int(i)])
    over_moments = 0
    last = -10 ** 9
    for i in over_windows:
        if i - last > gap:
            over_moments += 1
        last = i
    if plateaus >= PLATEAU_EVENTS or over_moments >= OVER_EVENTS or pileup >= SAT_PILEUP:
        level = "clipped"
    elif plateaus >= MAYBE_PLATEAUS or over_moments >= MAYBE_OVER or pileup >= MAYBE_PILEUP:
        level = "possible"
    else:
        level = "ok"
    return {"level": level, "peak_dbfs": round(peak_db, 2), "ceiling_dbfs": round(ceil_db, 2),
            "pileup": round(pileup, 2), "near_ceiling_fraction": round(fraction, 5), "moments": len(moments),
            "moment_times": [round(a * WIN_S, 2) for a, _ in moments][:12], "plateaus": plateaus,
            "over_full_scale": over, "over_moments": over_moments,
            "speech_s": round(speech_samples / ch / sr, 2)}


# ------------------------------------------------------------------ echo


def echo_envelope(path: Path) -> np.ndarray:
    """Speech-band level (200 Hz to 5 kHz) in 5 ms windows, mono at 16 kHz."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vn", "-map", "0:a:0", "-af",
           f'aresample={ECHO_SR}:async=1:first_pts=0,highpass=f=200,lowpass=f=5000', "-ac", "1", "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    w = int(ECHO_SR * ECHO_HOP_S)
    n = len(x) // w
    if n == 0:
        return np.zeros(0)
    return 10 * np.log10((x[: n * w].reshape(n, w) ** 2).mean(axis=1) + 1e-12)


def repetition(path: Path) -> dict | None:
    'Peak of the mean speech cepstrum between 35 and 500 ms, indicating delayed repeated sound.'
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vn", "-map", "0:a:0", "-af",
                          f'aresample={ECHO_SR}', "-ac", "1", "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    starts = list(range(0, len(x) - CEPS_N, CEPS_HOP))
    if len(starts) < 4:
        return None
    rms_db = np.array([10 * math.log10(float(np.mean(x[i:i + CEPS_N] ** 2)) + 1e-12) for i in starts])
    loud = float(np.percentile(rms_db, 90))
    win = np.hanning(CEPS_N)
    acc = np.zeros(CEPS_N // 2)
    used = 0
    for i, level in zip(starts, rms_db):
        if level < loud - 15:
            continue
        spec = np.abs(np.fft.rfft(x[i:i + CEPS_N] * win)) ** 2
        acc += np.fft.irfft(np.log(spec + 1e-10))[: CEPS_N // 2]
        used += 1
    if used < 3:
        return None
    c = acc / used
    lo, hi = int(CEPS_RANGE_S[0] * ECHO_SR), int(CEPS_RANGE_S[1] * ECHO_SR)
    half = int(CEPS_BASE_S * ECHO_SR) // 2
    seg = c[lo - half: hi + half]
    base = np.array([np.median(seg[k - half: k + half + 1]) for k in range(half, len(seg) - half)])
    r = c[lo:hi] - base
    mad = float(np.median(np.abs(r))) + 1e-12
    z = r / mad
    k = int(np.argmax(z))
    return {"z": round(float(z[k]), 1), "delay_s": round((lo + k) / ECHO_SR, 3), "windows": used}


def measure_echo(path: Path) -> dict:
    rep_ = repetition(path)
    res = measure_tail(path)
    if rep_ is not None:
        res["repetition"] = rep_
        if rep_["z"] >= CEPS_REPEAT_Z:
            strong = rep_["z"] >= CEPS_STRONG_Z
            if res["level"] in ("unmeasured", "none") or (strong and res["level"] == "light"):
                res["level"] = "strong" if strong else "light"
            res["basis"] = "repetition" if "rt60_s" not in res or res["rt60_s"] < ECHO_LIGHT_S else "tail_and_repetition"
    return res


def measure_tail(path: Path) -> dict:
    env = echo_envelope(path)
    if env.size < int(2.0 / ECHO_HOP_S):
        return {"level": "unmeasured", "why": 'audio too short'}
    floor = float(np.percentile(env, 10))
    loud = float(np.percentile(env, 95))
    speech_th = max(floor + 15.0, loud - 25.0)
    pause_n = int(round(ECHO_PAUSE_S / ECHO_HOP_S))
    look = int(round(0.6 / ECHO_HOP_S))
    is_speech = env >= speech_th
    rows = []
    i = 0
    n = len(env)
    while i < n - pause_n:
        # Last speech frame before at least ECHO_PAUSE_S of silence.
        if is_speech[i] and not is_speech[i + 1: i + 1 + pause_n].any():
            lo = max(0, i - 10)
            top = lo + int(np.argmax(env[lo: i + 1]))
            l0 = float(env[top])
            stop_db = max(l0 - 25.0, floor + 5.0)
            if l0 - stop_db >= ECHO_MIN_RANGE_DB:
                seg = env[top: min(n, top + look)]
                below5 = np.flatnonzero(seg <= l0 - 5.0)
                below_end = np.flatnonzero(seg <= stop_db)
                if below5.size and below_end.size and below_end[0] > below5[0] + 1:
                    a, b = int(below5[0]), int(below_end[0])
                    t = np.arange(a, b + 1) * ECHO_HOP_S
                    slope = float(np.polyfit(t, seg[a: b + 1], 1)[0])
                    if slope < -1.0:
                        rows.append({"at": round((top) * ECHO_HOP_S, 2), "rt60": round(min(5.0, 60.0 / -slope), 3),
                                     "decay_db": round(l0 - stop_db, 1)})
            i += pause_n
            continue
        i += 1
    if len(rows) < ECHO_MIN_OFFSETS:
        why = ('noise level too close to voice level' if loud - floor < ECHO_MIN_RANGE_DB + 10
               else f'only {len(rows)} phrase endings with measurable pauses')
        return {"level": "unmeasured", "why": why, "offsets": len(rows), "floor_db": round(floor, 1)}
    rt = float(np.median([r["rt60"] for r in rows]))
    level = "strong" if rt >= ECHO_STRONG_S else "light" if rt >= ECHO_LIGHT_S else "none"
    worst = sorted(rows, key=lambda r: -r["rt60"])[:5]
    return {"level": level, "rt60_s": round(rt, 3), "offsets": len(rows),
            "worst_times": [r["at"] for r in worst], "floor_db": round(floor, 1)}


# ------------------------------------------------------------------ combined checks


def measure_voice(path: Path) -> dict:
    try:
        info = probe_video(path)
    except Exception as exc:  # noqa: BLE001
        return {"error": f'Could not read: {exc}'}
    if not info.get("has_audio"):
        return {"error": 'no audio'}
    return {"clipping": measure_clipping(path, info), "echo": measure_echo(path)}


def clip_text(c: dict) -> str:
    if c.get("error"):
        return f"clipping: {c['error']}"
    when = ", ".join(fmt_t(t) for t in c.get("moment_times", [])[:6])
    extra = []
    if c["pileup"] >= MAYBE_PILEUP:
        extra.append(f"{c['near_ceiling_fraction']:.1%} of speech crowded near the ceiling")
    if c["plateaus"]:
        extra.append(f"{c['plateaus']} plateaus")
    if c["over_moments"]:
        extra.append(f"exceeded 0 dBFS at {c['over_moments']} points")
    more = f" ({', '.join(extra)})" if extra else ""
    if c["level"] == "clipped":
        return (f"CLIPPED voice at {c['moments']} moments ({when}...){more}. Clipping cannot be fully restored. Consider recording again with lower gain or greater microphone distance")
    if c["level"] == "possible":
        return f'possible clipping{more}: listen at {when} before concluding that the source clipped'
    return f"no clipping detected; peak {c['peak_dbfs']:.1f} dBFS)"


def echo_text(e: dict) -> str:
    if e.get("error"):
        return ""
    rt = str(round(e["rt60_s"], 2)) if "rt60_s" in e else None
    if e["level"] == "unmeasured":
        return f"echo not measured ({e['why']})"
    if e["level"] == "none":
        return f'no room echo detected; voice decays within {rt} s during pauses'
    what = "strong room echo" if e["level"] == "strong" else "light room echo"
    parts = []
    if rt and e["rt60_s"] >= ECHO_LIGHT_S:
        when = ", ".join(fmt_t(t) for t in e.get("worst_times", [])[:4])
        parts.append(f'voice takes about {rt} seconds to decay during pauses; listen at {when})')
    rp = e.get("repetition") or {}
    if rp.get("z", 0) >= CEPS_REPEAT_Z:
        parts.append(f"sound repeats after about {round(rp['delay_s'] * 1000)} ms")
    return (f"{what}: {'; '.join(parts)}. Apply light treatment and compare before and after at equal perceived loudness (references/review.md, Voice)")


def line_for(name: str, r: dict) -> str:
    if r.get("error"):
        return f"  {name}: {r['error']}"
    parts = [clip_text(r["clipping"]), echo_text(r["echo"])]
    return f'  {name} · ' + " · ".join(p for p in parts if p)


def main() -> int:
    ap = argparse.ArgumentParser(description='Measure voice clipping and room echo in each video.')
    ap.add_argument('paths', nargs="+", type=Path, help='video or audio files')
    ap.add_argument("--json", action="store_true", help="print JSON", dest='json')
    args = ap.parse_args()
    utf8_console()
    require_binary("ffmpeg")
    require_binary("ffprobe")
    out = {}
    for p in args.paths:
        p = p.expanduser().absolute()
        if not p.is_file():
            print(f'error: not found: {p}', file=sys.stderr)
            return 2
        key = p.stem if p.stem not in out and not any(q != p and q.stem == p.stem for q in args.paths) else p.name
        out[key] = measure_voice(p)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print('Voice clipping and room-echo waveform measurements:')
    for name, r in out.items():
        print(line_for(name, r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
