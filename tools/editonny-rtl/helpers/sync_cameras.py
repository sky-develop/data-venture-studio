'Synchronize simultaneous camera and microphone recordings using audio.\n\nMeasure each source against a primary recording and write synchronization.json for picture-source switching while retaining the selected voice source. Positive offset means the other source began recording earlier. Source time equals offset plus manual adjustment plus scale times primary time. Preserve an explicit manual adjustment on remeasurement.\n\nFirst compute 10 ms loudness envelopes at 8 kHz, removing noise floor and slow trends. Use overlap-normalized FFT cross-correlation for a coarse offset. Refine with GCC-PHAT at 16 kHz in distributed six-second speech windows near the coarse result. Reject silent or weak windows. With reliable windows spanning at least 20 seconds, fit drift; record a scale when drift reaches 2 ms and warn at 10 ms. Outliers over 15 ms can indicate stopped cameras, missing frames, or discontinuous recordings.\n\nReport weak correlation, indistinct peaks, insufficient overlap, drift, discontinuities, and absent audio. Exit 1 for weak results while still saving the measurements. Generate proof videos and five-frame comparison sheets when requested; verify a clap or slate closes on the same frame in both views.\n\nUsage: uv run python helpers/sync_cameras.py PRIMARY OTHER -o EDIT/synchronization.json\nUse labeled inputs for multiple cameras/microphones and the English proof/time controls listed by --help. Measurements are evidence to review, not an unconditional synchronization guarantee.'

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import file_signature, load_json, now_iso, probe_video, require_binary, save_json  # noqa: E402

VERSION = 1
HOP_S = 0.01
ENV_SR = 8000
FINE_SR = 16000
FINE_WIN_S = 6.0
FINE_MARGIN_S = 0.15
FINE_BAND_HZ = (150.0, 3800.0)
DETREND_S = 1.0
MIN_OVERLAP_S = 5.0
MIN_OVERLAP_SHARE = 0.2
GOOD_SHARPNESS = 6.0         # Reject a window when its GCC-PHAT peak relative to other positions is below this threshold.
DRIFT_MIN_SPAN_S = 20.0
DRIFT_APPLY_S = 0.002        # Include a scale factor in JSON only when the fitted line spans at least this much recording time.
DRIFT_WARN_S = 0.010
NONLINEAR_WARN_S = 0.015
OUTLIER_S = 0.004
CORR_GOOD, CORR_AVERAGE = 0.5, 0.3
ACCENT_GOOD, ACCENT_MIN = 6.0, 5.0
PEAK_EXCLUDE_S = 1.0


class SyncError(Exception):
    pass


def utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass


# ------------------------------------------------------------------ audio decoding


def audio_facts(path: Path) -> dict:
    try:
        info = probe_video(path)
    except Exception as exc:  # noqa: BLE001
        raise SyncError(f'could not read {path.name}: {exc}')
    if not info.get("has_audio"):
        raise SyncError(f'{path.name} has no audio and cannot be waveform-synchronized. Align visually to a clap and record the offset manually')
    return info


def _decode_cmd(path: Path, sr: int, channels: int, start: float | None = None, dur: float | None = None,
                pan: str | None = None) -> list[str]:
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f'{max(0.0, start):.6f}']
    if dur is not None:
        cmd += ["-t", f'{dur:.6f}']
    # first_pts=0 adds leading silence when delayed AAC audio starts after the video, making time zero
    # the beginning of the file, consistent with render_cut.py -ss.
    af = f'aresample={sr}:async=1:first_pts=0'
    if pan:
        af += f',{pan}'
    cmd += ["-i", str(path), "-vn", "-map", "0:a:0", "-af", af]
    if not pan:
        cmd += ["-ac", str(channels)]
    cmd += ["-f", "f32le", "-"]
    return cmd


def envelope(path: Path, info: dict) -> tuple[np.ndarray, str]:
    'Levels in 10 ms windows, indexed from source time zero, plus channel selection for refinement.'
    ch = 2 if int(info.get("audio_channels") or 1) >= 2 else 1
    w = int(ENV_SR * HOP_S)
    proc = subprocess.Popen(_decode_cmd(path, ENV_SR, ch), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    block = w * ch * 4 * 2000
    sums: list[np.ndarray] = []
    rest = b""
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(block)
        if not chunk:
            break
        data = rest + chunk
        usable = len(data) - len(data) % (w * ch * 4)
        rest = data[usable:]
        if not usable:
            continue
        x = np.frombuffer(data[:usable], dtype=np.float32).astype(np.float64).reshape(-1, w, ch)
        if ch == 2:
            left, right = x[:, :, 0], x[:, :, 1]
            mid = 0.5 * (left + right)
            sums.append(np.stack([(left ** 2).mean(1), (right ** 2).mean(1), (mid ** 2).mean(1)], axis=1))
        else:
            sums.append((x[:, :, 0] ** 2).mean(1)[:, None])
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    proc.wait()
    if proc.returncode != 0 or not sums:
        raise SyncError(f'could not read audio from {path.name}: {err.strip()[-400:]}')
    p = np.concatenate(sums, axis=0)
    how = "mix"
    if ch == 2:
        total = p.sum(axis=0) + 1e-20
        best = int(np.argmax(total[:2]))
        if 10 * math.log10(total[2] / total[best]) < -6:   # When L+R cancels through phase inversion, use the stronger channel.
            how = "left" if best == 0 else "right"
            p = p[:, best]
        else:
            p = p[:, 2]
    else:
        p = p[:, 0]
    return (10 * np.log10(p + 1e-12)).astype(np.float64), how


def pan_for(how: str) -> str | None:
    return {"left": "pan=mono|c0=c0", "right": "pan=mono|c0=c1"}.get(how)


def read_window(path: Path, how: str, start: float, dur: float, sr: int = FINE_SR) -> np.ndarray:
    pan = pan_for(how)
    proc = subprocess.run(_decode_cmd(path, sr, 1, start, dur, pan), capture_output=True)
    if proc.returncode != 0:
        raise SyncError(f"could not read {path.name} at {start:.2f} s: {proc.stderr.decode('utf-8', 'replace').strip()[-300:]}")
    x = np.frombuffer(proc.stdout, dtype=np.float32).astype(np.float64)
    need = int(round(dur * sr))
    if start < 0:   # Silence before the file begins.
        pad = int(round(-start * sr))
        x = np.concatenate([np.zeros(pad), x])
    if len(x) < need:
        x = np.concatenate([x, np.zeros(need - len(x))])
    return x[:need]


# ------------------------------------------------------------------ medida


def feature(db: np.ndarray) -> np.ndarray:
    'Cross-microphone envelope: clip the floor, remove a one-second trend, then normalize mean and variance.'
    floor = float(np.percentile(db, 10))
    loud = float(np.percentile(db, 95))
    e = np.clip(db, max(floor + 3.0, loud - 45.0), None)
    k = max(1, int(round(DETREND_S / HOP_S)))
    kernel = np.ones(k) / k
    trend = np.convolve(np.pad(e, (k // 2, k - 1 - k // 2), mode="edge"), kernel, mode="valid")
    f = e - trend
    sd = float(f.std())
    return (f - f.mean()) / sd if sd > 1e-9 else np.zeros_like(f)


def coarse_offset(fp: np.ndarray, fx: np.ndarray) -> dict:
    """Envelope offset in seconds: an event at time t in p appears at t + d in x."""
    n, m = len(fp), len(fx)
    size = 1 << int(math.ceil(math.log2(n + m)))
    spec = np.conj(np.fft.rfft(fp, size)) * np.fft.rfft(fx, size)
    raw = np.fft.irfft(spec, size)
    # lag L from -(n-1) to m-1: r[L] = sum p[t] x[t+L]
    lags = np.arange(-(n - 1), m)
    vals = np.concatenate([raw[size - (n - 1):], raw[:m]])
    overlap = np.minimum(n, m - lags) - np.maximum(0, -lags)
    overlap = np.minimum(overlap, np.minimum(n, m))
    need = max(int(MIN_OVERLAP_S / HOP_S), int(MIN_OVERLAP_SHARE * min(n, m)))
    ok = overlap >= need
    if not ok.any():
        raise SyncError('recordings are too short to compare: less than five seconds overlap')
    curve = np.full(len(vals), -np.inf)
    curve[ok] = vals[ok] / overlap[ok]
    best = int(np.argmax(curve))
    lag = int(lags[best])
    # Compare the peak against positions outside its one-second neighborhood.
    far = ok & (np.abs(lags - lag) > int(PEAK_EXCLUDE_S / HOP_S))
    others = curve[far]
    accent_value = float((curve[best] - others.mean()) / (others.std() + 1e-9)) if others.size > 10 else 0.0
    second = float(others.max()) if others.size else 0.0
    # Parabolic peak interpolation.
    fraction_value = 0.0
    if 0 < best < len(curve) - 1 and np.isfinite(curve[best - 1]) and np.isfinite(curve[best + 1]):
        y0, y1, y2 = curve[best - 1], curve[best], curve[best + 1]
        den = y0 - 2 * y1 + y2
        if abs(den) > 1e-12:
            fraction_value = float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
    # Pearson correlation over the shared interval.
    a0, a1 = max(0, -lag), min(n, m - lag)
    pa, xa = fp[a0:a1], fx[a0 + lag:a1 + lag]
    corr = float(np.corrcoef(pa, xa)[0, 1]) if len(pa) > 10 and pa.std() > 0 and xa.std() > 0 else 0.0
    return {"offset": (lag + fraction_value) * HOP_S, "correlation": corr, "accent": accent_value,
            'secondary_peak': round(second / curve[best], 3) if curve[best] > 0 else None,
            "overlap_s": float(overlap[best]) * HOP_S}


def gcc_phat(a: np.ndarray, b: np.ndarray, max_k: int, sr: int = FINE_SR) -> tuple[float, float]:
    'Return fractional-sample offset and sharpness for b[i+k] matching a[i], with k in [0,max_k].'
    size = 1 << int(math.ceil(math.log2(len(a) + len(b))))
    win_a = a * np.hanning(len(a))
    A = np.fft.rfft(win_a, size)
    B = np.fft.rfft(b, size)
    R = np.conj(A) * B
    R /= np.abs(R) + 1e-12
    freqs = np.fft.rfftfreq(size, 1.0 / sr)
    R[(freqs < FINE_BAND_HZ[0]) | (freqs > FINE_BAND_HZ[1])] = 0
    r = np.fft.irfft(R, size)[: max_k + 1]
    mag = np.abs(r)
    k = int(np.argmax(mag))
    rest = np.concatenate([mag[: max(0, k - 32)], mag[k + 33:]])
    sharp = float(mag[k] / (np.sqrt(np.mean(rest ** 2)) + 1e-12)) if rest.size else 0.0
    fraction_value = 0.0
    if 0 < k < len(mag) - 1:
        y0, y1, y2 = mag[k - 1], mag[k], mag[k + 1]
        den = y0 - 2 * y1 + y2
        if abs(den) > 1e-12:
            fraction_value = float(np.clip(0.5 * (y0 - y2) / den, -0.5, 0.5))
    return k + fraction_value, sharp


def window_centers(env_p: np.ndarray, t0: float, t1: float) -> list[float]:
    """Fine-window centers: one per overlap slice, where the main recording has the most speech."""
    span = t1 - t0
    if span < FINE_WIN_S + 0.5:
        return [0.5 * (t0 + t1)] if span > 1.0 else []
    k = int(min(9, max(3, span // 20)))
    k = max(1, min(k, int(span // FINE_WIN_S)))
    lin = 10 ** (env_p / 10)
    win = int(FINE_WIN_S / HOP_S)
    csum = np.concatenate([[0.0], np.cumsum(lin)])
    out = []
    for i in range(k):
        a = t0 + span * i / k + FINE_WIN_S / 2
        b = t0 + span * (i + 1) / k - FINE_WIN_S / 2
        if b < a:
            a = b = t0 + span * (i + 0.5) / k
        ia, ib = int(a / HOP_S), int(b / HOP_S)
        best, best_e = 0.5 * (a + b), -1.0
        for c in range(ia, ib + 1, 25):
            lo, hi = max(0, c - win // 2), min(len(lin), c + win // 2)
            if hi <= lo:
                continue
            e = (csum[hi] - csum[lo]) / (hi - lo)
            if e > best_e:
                best, best_e = c * HOP_S, e
        out.append(best)
    return out


def fine_offsets(p: dict, x: dict, coarse: float, dur_p: float, dur_x: float) -> list[dict]:
    t0 = max(0.0, -coarse) + 0.2
    t1 = min(dur_p, dur_x - coarse) - 0.2
    rows = []
    max_k = int(round(2 * FINE_MARGIN_S * FINE_SR))
    for tc in window_centers(p["env"], t0, t1):
        start_p = tc - FINE_WIN_S / 2
        a = read_window(p["path"], p["how"], start_p, FINE_WIN_S)
        start_x = start_p + coarse - FINE_MARGIN_S
        b = read_window(x["path"], x["how"], start_x, FINE_WIN_S + 2 * FINE_MARGIN_S)
        if np.sqrt(np.mean(a ** 2)) < 1e-5 or np.sqrt(np.mean(b ** 2)) < 1e-5:
            continue
        k, sharp = gcc_phat(a, b, max_k)
        d = start_x - start_p + k / FINE_SR
        rows.append({"at": round(tc, 3), "offset": d, "sharpness": round(sharp, 1)})
    return rows


def fit_line(rows: list[dict]) -> tuple[float, float, list[dict], float]:
    'Fit d=a+b*t, reject residual outliers, and return coefficients, retained windows, and maximum residual.'
    use = [r for r in rows if r["sharpness"] >= GOOD_SHARPNESS]
    if not use:
        return float("nan"), 0.0, [], 0.0
    for _ in range(3):
        t = np.array([r["at"] for r in use])
        d = np.array([r["offset"] for r in use])
        wts = np.array([r["sharpness"] for r in use])
        if len(use) >= 2 and t.max() - t.min() >= DRIFT_MIN_SPAN_S:
            A = np.stack([np.ones_like(t), t], axis=1) * np.sqrt(wts)[:, None]
            a, b = np.linalg.lstsq(A, d * np.sqrt(wts), rcond=None)[0]
        else:
            a, b = float(np.median(d)), 0.0
        res = d - (a + b * t)
        worst = int(np.argmax(np.abs(res)))
        if abs(res[worst]) <= OUTLIER_S or len(use) <= 3:
            break
        use = [r for i, r in enumerate(use) if i != worst]
    t = np.array([r["at"] for r in use])
    d = np.array([r["offset"] for r in use])
    all_t = np.array([r["at"] for r in rows if r["sharpness"] >= GOOD_SHARPNESS])
    all_d = np.array([r["offset"] for r in rows if r["sharpness"] >= GOOD_SHARPNESS])
    worst_all = float(np.max(np.abs(all_d - (a + b * all_t)))) if all_t.size else 0.0
    return float(a), float(b), use, worst_all


def measure_pair(p: dict, x: dict, fps: float) -> dict:
    coarse = coarse_offset(p["feat"], x["feat"])
    d0 = coarse["offset"]
    rows = fine_offsets(p, x, d0, p["dur"], x["dur"])
    a, b, used, worst = fit_line(rows)
    warnings_value: list[str] = []
    span_t = (max(r["at"] for r in used) - min(r["at"] for r in used)) if len(used) >= 2 else 0.0
    t_lo = max(0.0, -d0)
    t_hi = min(p["dur"], x["dur"] - d0)
    over = max(0.0, t_hi - t_lo)
    if not used:
        offset, scale_value = d0, 1.0
        warnings_value.append('no clear speech window in both sources for refinement; coarse timing remains, with about 10 ms uncertainty. Verify against a clap')
    else:
        offset, scale_value = a, 1.0 + b
        total = abs(b) * over
        if span_t < DRIFT_MIN_SPAN_S or total < DRIFT_APPLY_S:
            offset, scale_value = float(np.median([r["offset"] for r in used])), 1.0
        if abs(offset - d0) > 0.05:
            warnings_value.append(f'fine measurement ({offset:+.3f} s) differs substantially from the coarse estimate ({d0:+.3f} s): verify against a clap')
    drift_ms_min = (scale_value - 1.0) * 60_000.0
    drift_total = abs(scale_value - 1.0) * over
    if drift_total >= DRIFT_WARN_S:
        warnings_value.append(f'clock drift: source moves {drift_ms_min:+.1f} ms per minute relative to the primary ({drift_total * 1000:.0f} ms over {over / 60:.1f} minutes). Rendering compensates with the fitted scale; verify synchronization at both ends')
    if worst > NONLINEAR_WARN_S:
        far = max((r for r in rows if r["sharpness"] >= GOOD_SHARPNESS),
                  key=lambda r: abs(r["offset"] - (offset + (scale_value - 1.0) * r["at"])))
        warnings_value.append(f"source offset is nonlinear, deviating up to {worst * 1000:.0f} ms, near {far['at']:.1f} seconds from primary. The camera may have stopped, dropped frames, or split its recording. Inspect and synchronize pieces independently")
    corr, dest = coarse["correlation"], coarse["accent"]
    if corr >= CORR_GOOD and dest >= ACCENT_GOOD and used and 2 * len(used) >= len(rows):
        conf = "good"
    elif corr >= CORR_AVERAGE and dest >= ACCENT_MIN and used:
        conf = "medium"
    else:
        conf = "weak"
        warnings_value.append(f'weak correlation (envelope {corr:.2f}, prominence {dest:.1f}): sources sound dissimilar. Verify against a clap and enter the offset manually if needed')
    if coarse['secondary_peak'] is not None and coarse['secondary_peak'] > 0.8 and conf != "weak":
        warnings_value.append('another offset is nearly as strong, possibly due to repetitive sound or music. Verify against a clap')
    if over < 30:
        warnings_value.append(f'only {over:.0f} seconds overlap between recordings')
    step = 1.0 / fps if fps else 1 / 30
    return {
        "offset": round(offset, 5), "scale": round(scale_value, 9), "adjustment_s": 0.0,
        "drift_ms_per_min": round(drift_ms_min, 2), "confidence": conf,
        "correlation": round(corr, 3), "accent": round(dest, 1), "coarse_offset": round(d0, 4),
        "overlap_s": round(over, 2), "overlap_on_primary": [round(t_lo, 3), round(t_hi, 3)],
        "windows": [{"at": r["at"], "offset": round(r["offset"], 5), "sharpness": r["sharpness"],
                     "used": r in used} for r in rows],
        "max_deviation_ms": round(worst * 1000, 1), 'frame_s': round(step, 5), "warnings": warnings_value,
    }


# ------------------------------------------------------------------ visual evidence


def strongest_hit(env: np.ndarray, t0: float, t1: float) -> float:
    """Strongest attack, measured as the largest 30 ms level rise between t0 and t1 in the main source."""
    i0, i1 = max(3, int((t0 + 1.6) / HOP_S)), min(len(env), int((t1 - 1.6) / HOP_S))
    if i1 <= i0:
        return 0.5 * (t0 + t1)
    rise = env[i0:i1] - env[i0 - 3:i1 - 3]
    return (i0 + int(np.argmax(rise))) * HOP_S


def make_evidence(doc: dict, name: str, t_p: float, out_dir: Path) -> list[Path]:
    source_records = doc["sources"]
    p, x = source_records[doc["primary"]], source_records[name]
    pinfo, xinfo = probe_video(p["file"]), probe_video(x["file"])
    if not pinfo.get("has_video") or not xinfo.get("has_video"):
        return []
    t_x = x["offset"] + x.get("adjustment_s", 0.0) + x["scale"] * t_p
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4, png = out_dir / f'sync_{name}.mp4', out_dir / f'sync_{name}.png'
    fps = float(pinfo.get("fps") or 30.0)
    half = 1.5
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-ss", f'{max(0.0, t_p - half):.4f}', "-t", f'{2 * half}',
                    "-i", p["file"], "-ss", f'{max(0.0, t_x - half):.4f}', "-t", f'{2 * half}', "-i", x["file"],
                    "-filter_complex", "[0:v]scale=-2:640,setsar=1[a];[1:v]scale=-2:640,setsar=1[b];[a][b]hstack[v]",
                    "-map", "[v]", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(mp4)], capture_output=True)
    with tempfile.TemporaryDirectory() as tmp:
        rows = []
        for i, (src, t) in enumerate(((p["file"], t_p), (x["file"], t_x))):
            row = Path(tmp) / f'line{i}.png'
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-ss", f'{max(0.0, t - 2 / fps):.4f}', "-i", src,
                            "-frames:v", "5", "-vf", "scale=-2:300,setsar=1,tile=5x1:padding=4:color=white",
                            "-frames:v", "1", str(row)], capture_output=True)
            if row.exists():
                rows.append(row)
        if len(rows) == 2:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", str(rows[0]), "-i", str(rows[1]),
                            "-filter_complex", "[0:v]pad=iw:ih+8:0:0:white[a];[1:v][a]scale2ref=iw:ow/mdar[b][a2];"
                            "[a2][b]vstack", "-frames:v", "1", str(png)], capture_output=True)
            if not png.exists():
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-i", str(rows[0]), "-i", str(rows[1]),
                                "-filter_complex", "[1:v][0:v]scale2ref=iw:ih[b][a];[a][b]vstack", "-frames:v", "1",
                                str(png)], capture_output=True)
    return [f for f in (mp4, png) if f.exists()]


# ------------------------------------------------------------------ main


def parse_sources(items: list[str]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for it in items:
        name, sep, rest = it.partition("=")
        if sep and rest and "/" not in name and "\\" not in name:
            path = Path(rest).expanduser()
        else:
            path = Path(it).expanduser()
            name = path.stem
        if name in out:
            raise SyncError(f'two files share the name {name!r}: use NAME=path to assign distinct names')
        out[name] = Path(os.path.abspath(path))
    return out


def sources_from_edl(edl_path: Path) -> dict[str, Path]:
    edl = load_json(edl_path)
    if not isinstance(edl, dict) or not isinstance(edl.get("sources"), dict) or not edl["sources"]:
        raise SyncError(f'{edl_path} lacks "sources"')
    base = edl_path.parent
    out = {}
    for k, v in edl["sources"].items():
        p = Path(str(v)).expanduser()
        if not p.is_absolute():
            cands = [base / p, base.parent / p]
            p = next((c for c in cands if c.exists()), cands[0])
        out[str(k)] = Path(os.path.abspath(p))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Synchronize simultaneous cameras and lavalier recordings using their waveforms.')
    ap.add_argument('sources', nargs="*", help='files or NAME=file entries; the first is primary unless --primary is supplied')
    ap.add_argument("--edl", type=Path, help="edl_manual.json: synchronize files in \"sources\"", dest='edl')
    ap.add_argument("--primary", help='primary source name (default: first source)', dest='primary')
    ap.add_argument("-o", "--output", type=Path, help='output JSON (with --edl, defaults to synchronization.json beside the EDL)', dest='output_value')
    ap.add_argument("--evidence", action="store_true", help='write synchronization verification MP4 and PNG files under verify/', dest='evidence')
    ap.add_argument("--at", type=float, help='with --evidence: primary-source time for verification; defaults to the strongest attack', dest='at_value')
    args = ap.parse_args(argv)
    utf8_console()
    try:
        require_binary("ffmpeg")
        require_binary("ffprobe")
        sources = sources_from_edl(args.edl.expanduser().absolute()) if args.edl else {}
        sources.update(parse_sources(args.sources))
        if len(sources) < 2:
            raise SyncError('at least two simultaneous recordings are required')
        missing = [f'{k} ({v})' for k, v in sources.items() if not v.is_file()]
        if missing:
            raise SyncError('not found: ' + ", ".join(missing))
        primary = args.primary or next(iter(sources))
        if primary not in sources:
            raise SyncError(f"--primary {primary!r} is not a source ({', '.join(sources)})")
        output_value = args.output_value or (args.edl.expanduser().absolute().parent / "synchronization.json" if args.edl else None)
        if output_value is None:
            raise SyncError("specify the output path: -o <EDIT>/synchronization.json")
        output_value = Path(os.path.abspath(output_value.expanduser()))

        data: dict[str, dict] = {}
        for name, path in sources.items():
            info = audio_facts(path)
            env, how = envelope(path, info)
            data[name] = {"path": path, "info": info, "env": env, "how": how, "feat": feature(env),
                          "dur": len(env) * HOP_S}
        old = load_json(output_value, default={}) or {}
        old_sources = old.get("sources") if isinstance(old, dict) and isinstance(old.get("sources"), dict) else {}
        p = data[primary]
        fps = float(p["info"].get("fps") or 0) or 30.0
        source_records: dict[str, dict] = {}
        for name, d in data.items():
            base = {"file": str(d["path"]), "signature": file_signature(d["path"]),
                    "has_video": bool(d["info"].get("has_video")), "channel": d["how"]}
            if name == primary:
                source_records[name] = {**base, "offset": 0.0, "scale": 1.0, "adjustment_s": 0.0, "confidence": "primary"}
                continue
            res = measure_pair(p, d, fps)
            prev = old_sources.get(name) if isinstance(old_sources.get(name), dict) else None
            if prev and prev.get("file") == str(d["path"]) and old.get("primary") == primary:
                try:
                    res["adjustment_s"] = float(prev.get("adjustment_s") or 0.0)
                except (TypeError, ValueError):
                    pass
            source_records[name] = {**base, **res}
        doc = {"version": VERSION, "created_at": now_iso(), "primary": primary,
               "rule": "source time = offset + adjustment_s + scale x main time",
               "sources": source_records}
        save_json(output_value, doc)
    except SyncError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

    print(f"Synchronization against {primary} ({Path(source_records[primary]['file']).name}):")
    weak_value = False
    for name, f in source_records.items():
        if name == primary:
            continue
        frames = f["offset"] * fps
        extra = f", adjust manually {f['adjustment_s']:+.3f} s" if f.get("adjustment_s") else ""
        drift = f", drift {f['drift_ms_per_min']:+.1f} ms/min" if f["scale"] != 1.0 else ""
        print(f"  {name}: {f['offset']:+.3f} s ({frames:+.1f} frames){drift}{extra} · confidence {f['confidence']} (correlation {f['correlation']:.2f}, prominence {f['accent']:.0f}, {sum((1 for j in f['windows'] if j['used']))}/{len(f['windows'])} fine windows)")
        for a in f["warnings"]:
            print(f'    warning: {a}')
        weak_value = weak_value or f["confidence"] == "weak"
    print(f'  saved at {output_value}')
    if args.evidence:
        for name, f in source_records.items():
            if name == primary:
                continue
            lo, hi = f["overlap_on_primary"]
            t = args.at_value if args.at_value is not None else strongest_hit(data[primary]["env"], lo, hi)
            made = make_evidence(doc, name, t, output_value.parent / "verify")
            if made:
                print(f'  evidence for {name} at {t:.2f} s in the primary: ' + ", ".join(str(m) for m in made))
            else:
                print(f'  evidence for {name}: one source has no video; verify by listening')
    return 1 if weak_value else 0


if __name__ == "__main__":
    sys.exit(main())
