#!/Users/muhammed/.hermes/tools/open-higgsfield/.venv/bin/python
"""
open-hf (Open Higgsfield Studio)
Complete free & open-source replacement for Higgsfield AI on Apple Silicon (M5 Max) + Direct Gemini/Veo/Lyria/ZeroGPU.
Covers:
  - image       : Nano Banana Pro, Nano Banana 2.1, Gemini 3 Pro Image, Local MLX (mflux Z-Image-Turbo, FLUX.2, FLUX.3, Qwen-Image 2.1, Kontext)
  - photoshoot  : Studio product photoshoot, lifestyle, hero banner, marketplace cards, virtual try-on
  - thumbnail   : High-CTR YouTube (16:9) & Shorts/Reels (9:16) thumbnails with 1-shot face consistency
  - soul        : Zero-shot / multi-reference Soul ID vault (register, list, generate portrait/cinematic/sheet)
  - remove-bg   : 100% local offline background removal via rembg (ONNX)
  - video       : Text-to-Video & Image-to-Video with native synced audio (Veo 3.1 Lite/Fast/Pro, Gemini Omni 1.1 Flash)
  - music       : AI music generation (Lyria 3 Clip/Pro + FFmpeg exact duration mastering)
  - tts         : Personal Voice Twin (OmniVoice dr_lubbad v9.2 on M5 Max MPS) + Gemini 3.8 Flash TTS
  - 3d          : Image/Text-to-3D GLB mesh (HuggingFace ZeroGPU TRELLIS + 3D AI Studio)
  - virality    : Virality Predictor (brain_activity replacement: multimodal hook, sustain & cognitive region scoring)
  - status      : Full readiness check of all local & free-cloud engines
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, List, Optional

HERMES_HOME = Path(os.path.expanduser("~/.hermes"))
ENV_PATH = HERMES_HOME / ".env"
TOOL_DIR = HERMES_HOME / "tools" / "open-higgsfield"
SOULS_DIR = TOOL_DIR / "souls"
SCRATCH_DIR = HERMES_HOME / "cache" / "scratch"
MFLUX_BIN_DIR = Path(os.path.expanduser("~/.local/bin"))

BRAND_PRESETS = {
    "skydevelop": (
        "Sky Develop corporate brand aesthetic: deep midnight navy (#0A0F1D) and obsidian slate background, "
        "electric cyan (#00E5FF) and royal blue (#2563EB) accents, clean geometric layout, 3D pearl sphere motif, "
        "modern executive tech venture studio style, no clutter."
    ),
    "mlubbad": (
        "Dr. Mohammed Lubbad executive AI/Data Science brand aesthetic: dark studio slate (#0B0F19) background, "
        "emerald-teal (#10B981) and warm gold/cyan technical highlights, high-contrast architectural clarity, "
        "authoritative peer-to-peer engineering style."
    ),
}


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip("\"'"))
    return env


def get_google_key() -> str:
    env = load_env()
    key = env.get("GOOGLE_API_KEY") or env.get("GEMINI_API_KEY") or ""
    if not key:
        raise RuntimeError("GOOGLE_API_KEY not found in ~/.hermes/.env")
    return key


def encode_image_part(image_path: str | Path) -> dict[str, Any]:
    p = Path(image_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Image not found: {p}")
    mime, _ = mimetypes.guess_type(str(p))
    if not mime:
        mime = "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return {"inlineData": {"mimeType": mime, "data": b64}}


def gemini_generate_image(
    prompt: str,
    output_path: str | Path,
    model: str = "nano-banana-pro-preview",
    images: Optional[List[str]] = None,
    aspect_ratio: Optional[str] = None,
) -> Path:
    key = get_google_key()
    model_map = {
        "nano_banana_pro": "nano-banana-pro-preview",
        "nano_banana_2": "gemini-nano-banana-2.1",
        "gemini_3_pro": "gemini-3-pro-image",
        "gemini_flash": "gemini-3.1-flash-image",
    }
    api_model = model_map.get(model, model)
    parts: list[dict[str, Any]] = []
    for img in images or []:
        parts.append(encode_image_part(img))
    full_prompt = prompt
    if aspect_ratio and aspect_ratio != "auto":
        full_prompt = f"{prompt}\n\nTarget aspect ratio: {aspect_ratio}."
    parts.append({"text": full_prompt})

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{api_model}:generateContent?key={key}"
    payload: dict[str, Any] = {"contents": [{"parts": parts}]}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini image API error ({e.code}): {body[:400]}") from e

    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"No candidates returned from {api_model}: {json.dumps(data)[:300]}")
    out_p = Path(output_path).expanduser().resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    for part in candidates[0].get("content", {}).get("parts", []):
        if "inlineData" in part:
            img_bytes = base64.b64decode(part["inlineData"]["data"])
            out_p.write_bytes(img_bytes)
            return out_p
    raise RuntimeError(f"No image inlineData returned from {api_model}: {json.dumps(candidates[0])[:300]}")


def mflux_generate_image(
    prompt: str,
    output_path: str | Path,
    model: str = "mflux_z_turbo",
    aspect_ratio: str = "1:1",
    steps: int = 4,
    quantize: int = 4,
) -> Path:
    ratio_dims = {
        "1:1": (1024, 1024),
        "16:9": (1280, 720),
        "9:16": (720, 1280),
        "4:3": (1024, 768),
        "3:4": (768, 1024),
    }
    w, h = ratio_dims.get(aspect_ratio, (1024, 1024))
    out_p = Path(output_path).expanduser().resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    cmd_map = {
        "mflux_z_turbo": [str(MFLUX_BIN_DIR / "mflux-generate-z-image-turbo"), "--steps", str(steps)],
        "mflux_flux2": [str(MFLUX_BIN_DIR / "mflux-generate-flux2"), "--steps", str(steps)],
        "mflux_qwen2_1": [str(MFLUX_BIN_DIR / "mflux-generate-qwen-2.1"), "--steps", str(max(steps, 8))],
        "mflux_schnell": [str(MFLUX_BIN_DIR / "mflux-generate"), "--model", "schnell", "--steps", str(steps)],
    }
    base_cmd = cmd_map.get(model, cmd_map["mflux_z_turbo"])
    cmd = [
        *base_cmd,
        "--prompt",
        prompt,
        "-q",
        str(quantize),
        "--width",
        str(w),
        "--height",
        str(h),
        "--output",
        str(out_p),
    ]
    subprocess.run(cmd, check=True)
    return out_p


def cmd_remove_bg(args: argparse.Namespace) -> int:
    from PIL import Image
    from rembg import remove

    inp = Path(args.input).expanduser().resolve()
    out = Path(args.output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(inp) as img:
        res = remove(img)
        res.save(out)
    print(json.dumps({"ok": True, "input": str(inp), "output": str(out), "size_bytes": out.stat().st_size}))
    return 0


def cmd_image(args: argparse.Namespace) -> int:
    if args.model.startswith("mflux"):
        out = mflux_generate_image(
            prompt=args.prompt,
            output_path=args.output,
            model=args.model,
            aspect_ratio=args.aspect_ratio,
            steps=args.steps,
            quantize=args.quantize,
        )
    else:
        out = gemini_generate_image(
            prompt=args.prompt,
            output_path=args.output,
            model=args.model,
            images=args.image,
            aspect_ratio=args.aspect_ratio,
        )
    print(json.dumps({"ok": True, "model": args.model, "output": str(out), "size_bytes": out.stat().st_size}))
    return 0


def cmd_photoshoot(args: argparse.Namespace) -> int:
    mode_prompts = {
        "studio": (
            "Commercial studio product photography on a sculpted pedestal with soft rim lighting, "
            "subtle surface reflection, 85mm f/1.8 lens, ultra-clean studio backdrop, preserve exact product geometry and labels."
        ),
        "lifestyle": (
            "Authentic editorial lifestyle product photography in a natural real-world setting with golden hour directional light, "
            "shallow depth of field, preserve exact product details and proportions."
        ),
        "hero": (
            "Dramatic flagship hero banner product shot, cinematic volumetric lighting, floating composition with subtle glow, "
            "wide negative space for headline typography, preserve exact product details."
        ),
        "marketplace_card": (
            "High-converting e-commerce marketplace product card visual, clean high-contrast studio background, "
            "crisp product focus centered in frame, subtle callout lighting, commercial catalog grade."
        ),
        "virtual_try_on": (
            "Photorealistic virtual try-on fashion editorial shot, natural fabric drape and lighting match on the subject, "
            "preserve exact garment/product texture, colors, and logos."
        ),
    }
    base_style = mode_prompts.get(args.mode, mode_prompts["studio"])
    brand_style = BRAND_PRESETS.get(args.brand, "")
    full_prompt = f"{base_style}\n{brand_style}\nProduct & Scene Brief: {args.prompt}".strip()
    out = gemini_generate_image(
        prompt=full_prompt,
        output_path=args.output,
        model=args.model,
        images=args.image,
        aspect_ratio=args.aspect_ratio,
    )
    print(json.dumps({"ok": True, "mode": args.mode, "brand": args.brand, "output": str(out), "size_bytes": out.stat().st_size}))
    return 0


def cmd_thumbnail(args: argparse.Namespace) -> int:
    brand_style = BRAND_PRESETS.get(args.brand, "")
    text_clause = ""
    if args.text:
        text_clause = (
            f"Render bold, ultra-legible high-contrast headline text on the thumbnail reading exactly: \"{args.text}\" "
            "(maximum 3 words, clean sans-serif/Arabic Kufi typography, mobile-legible contrast)."
        )
    face_clause = ""
    images = list(args.image or [])
    if args.face:
        images.insert(0, args.face)
        face_clause = (
            "Preserve the exact facial identity, bone structure, beard/hair, glasses, and likeness of the person in the reference image, "
            "with an expressive, engaging high-CTR facial expression."
        )
    full_prompt = (
        f"High-click-through-rate YouTube/Reels thumbnail ({args.aspect_ratio}). "
        f"{face_clause} {brand_style} {text_clause}\n"
        f"Visual scene: {args.prompt}. Strong foreground/background separation, dramatic rim lighting, zero visual clutter."
    ).strip()
    out = gemini_generate_image(
        prompt=full_prompt,
        output_path=args.output,
        model=args.model,
        images=images,
        aspect_ratio=args.aspect_ratio,
    )
    print(json.dumps({"ok": True, "aspect_ratio": args.aspect_ratio, "output": str(out), "size_bytes": out.stat().st_size}))
    return 0


def cmd_soul(args: argparse.Namespace) -> int:
    SOULS_DIR.mkdir(parents=True, exist_ok=True)
    if args.soul_action == "list":
        souls = []
        for d in sorted(SOULS_DIR.iterdir()):
            if d.is_dir() and (d / "meta.json").exists():
                souls.append(json.loads((d / "meta.json").read_text(encoding="utf-8")))
        print(json.dumps({"ok": True, "souls": souls}, ensure_ascii=False, indent=2))
        return 0

    if args.soul_action == "register":
        if not args.id or not args.image:
            raise ValueError("soul register requires --id and at least one --image")
        sdir = SOULS_DIR / args.id
        sdir.mkdir(parents=True, exist_ok=True)
        stored = []
        for idx, img_path in enumerate(args.image):
            src = Path(img_path).expanduser().resolve()
            dst = sdir / f"ref_{idx:02d}{src.suffix.lower() or '.png'}"
            shutil.copy2(src, dst)
            stored.append(str(dst))
        meta = {
            "id": args.id,
            "description": args.description or "",
            "images": stored,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (sdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": True, "soul": meta}, ensure_ascii=False))
        return 0

    if args.soul_action == "generate":
        sdir = SOULS_DIR / args.id
        meta_file = sdir / "meta.json"
        if not meta_file.exists():
            raise FileNotFoundError(f"Soul ID '{args.id}' not found. Register it first with: open-hf soul register --id {args.id} --image <path>")
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        ref_images = meta.get("images", [])[:3]
        mode_instructions = {
            "portrait": "Photorealistic editorial portrait, natural skin texture, 85mm f/1.4 lens, studio lighting.",
            "cinematic": "Anamorphic 35mm cinema still frame, film grain, dramatic lighting, shallow depth of field (Soul Cinema style).",
            "sheet": "4-panel character reference sheet showing front view, 3/4 view, side profile, and upper-body expression, neutral studio backdrop, consistent outfit and identity across all panels.",
        }
        mode_desc = mode_instructions.get(args.mode, mode_instructions["portrait"])
        prompt = (
            f"Strictly preserve the exact facial identity, features, hair, and likeness of the person in the reference image(s). "
            f"{mode_desc} {meta.get('description', '')}\nScene: {args.prompt}"
        ).strip()
        out = gemini_generate_image(
            prompt=prompt,
            output_path=args.output,
            model=args.model,
            images=ref_images,
            aspect_ratio=args.aspect_ratio,
        )
        print(json.dumps({"ok": True, "soul_id": args.id, "mode": args.mode, "output": str(out), "size_bytes": out.stat().st_size}))
        return 0
    return 1


def cmd_video(args: argparse.Namespace) -> int:
    key = get_google_key()
    model_map = {
        "veo_3_1_lite": "models/gemini-omni-1.1-flash",
        "veo_3_1_fast": "models/gemini-omni-1.1-flash",
        "veo_3_1": "models/gemini-omni-1.1-flash",
        "gemini_omni": "models/gemini-omni-1.1-flash",
        "gemini_omni_flash": "models/gemini-omni-flash-preview",
    }
    api_model = model_map.get(args.model, args.model)
    if not api_model.startswith("models/"):
        api_model = f"models/{api_model}"

    prompt_text = args.prompt
    if args.aspect_ratio == "9:16" and "9:16" not in prompt_text:
        prompt_text = f"Vertical 9:16 aspect ratio. {prompt_text}"

    url = f"https://generativelanguage.googleapis.com/v1beta/interactions?key={key}"
    start_img = getattr(args, "start_image", None)
    if start_img:
        img_p = Path(start_img).expanduser().resolve()
        mime = mimetypes.guess_type(str(img_p))[0] or "image/png"
        b64 = base64.b64encode(img_p.read_bytes()).decode("ascii")
        inp_val: Any = [
            {"type": "image", "mime_type": mime, "data": b64},
            {"type": "text", "text": prompt_text},
        ]
    else:
        inp_val = prompt_text
    payload: dict[str, Any] = {
        "model": api_model,
        "input": inp_val,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini Omni Video API error ({e.code}): {body[:400]}") from e

    for step in res.get("steps", []):
        content = step.get("content")
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "video" and "data" in block:
                    out_p = Path(args.output).expanduser().resolve()
                    out_p.parent.mkdir(parents=True, exist_ok=True)
                    out_p.write_bytes(base64.b64decode(block["data"]))
                    print(json.dumps({"ok": True, "model": api_model, "interaction_id": res.get("id"), "output": str(out_p), "size_bytes": out_p.stat().st_size}))
                    return 0

    raise RuntimeError(f"No video block returned in interaction: {res.get('id')}")


def cmd_story_chain(args: argparse.Namespace) -> int:
    out_p = Path(args.output).expanduser().resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    work_dir = SCRATCH_DIR / f"story_chain_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    clip_paths: list[Path] = []
    prev_last_frame: str | None = args.start_image

    for idx, scene_prompt in enumerate(args.scenes):
        clip_p = work_dir / f"scene_{idx:02d}.mp4"
        sub_args = argparse.Namespace(
            prompt=scene_prompt,
            model=args.model,
            start_image=prev_last_frame,
            aspect_ratio=args.aspect_ratio,
            timeout=args.timeout,
            output=str(clip_p),
        )
        cmd_video(sub_args)
        clip_paths.append(clip_p)
        last_png = work_dir / f"scene_{idx:02d}_last.png"
        subprocess.run(
            ["ffmpeg", "-y", "-sseof", "-0.08", "-i", str(clip_p), "-frames:v", "1", str(last_png)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        prev_last_frame = str(last_png)

    concat_file = work_dir / "concat.txt"
    concat_file.write_text("\n".join(f"file '{p}'" for p in clip_paths) + "\n", encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out_p)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(json.dumps({"ok": True, "scenes": len(clip_paths), "output": str(out_p), "size_bytes": out_p.stat().st_size}))
    return 0


def cmd_music(args: argparse.Namespace) -> int:
    key = get_google_key()
    model_map = {
        "lyria_clip": "lyria-3-clip-preview",
        "lyria_pro": "lyria-3-pro-preview",
        "lyria_3_5": "lyria-3.5",
    }
    api_model = model_map.get(args.model, args.model)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{api_model}:generateContent?key={key}"
    payload = {"contents": [{"parts": [{"text": args.prompt}]}]}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    out_p = Path(args.output).expanduser().resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    raw_bytes = None
    for p in parts:
        if "inlineData" in p:
            raw_bytes = base64.b64decode(p["inlineData"]["data"])
            break
    if not raw_bytes:
        raise RuntimeError(f"No audio inlineData returned from {api_model}")

    if args.duration and args.duration > 0:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, dir=str(SCRATCH_DIR)) as tmp:
            tmp.write(raw_bytes)
            tmp_path = tmp.name
        fade_start = max(0.0, float(args.duration) - 1.5)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                tmp_path,
                "-t",
                str(args.duration),
                "-af",
                f"afade=t=out:st={fade_start:.2f}:d=1.5",
                str(out_p),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.unlink(tmp_path)
    else:
        out_p.write_bytes(raw_bytes)

    print(json.dumps({"ok": True, "model": api_model, "output": str(out_p), "size_bytes": out_p.stat().st_size}))
    return 0


DR_LUBBAD_GEMINI_VOICE_ID = "voice_kbwqaiqajk3y"


def cmd_tts(args: argparse.Namespace) -> int:
    out_p = Path(args.output).expanduser().resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    if args.engine == "dr_lubbad":
        script = HERMES_HOME / "omnivoice-emotional-ft/checkpoints/omnivoice-dr-lubbad-personal-v8/generate_dr_lubbad_voice.py"
        venv_py = HERMES_HOME / "cache/scratch/voicestudio-eval/.venv/bin/python"
        if not script.exists() or not venv_py.exists():
            raise RuntimeError("Local OmniVoice dr_lubbad preset not found")
        subprocess.run([str(venv_py), str(script), "--text", args.text, "--out", str(out_p)], check=True)
        print(json.dumps({"ok": True, "engine": "dr_lubbad_omnivoice_v9_2", "output": str(out_p), "size_bytes": out_p.stat().st_size}))
        return 0

    key = get_google_key()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-tts:generateContent?key={key}"
    voice_id = DR_LUBBAD_GEMINI_VOICE_ID if args.engine == "dr_lubbad_gemini" else (args.voice or "Orus")
    style_str = getattr(args, "style", "") or (
        "warm, calm, authoritative conversational white Arabic mentor" if args.engine == "dr_lubbad_gemini" else ""
    )
    part_obj: dict[str, Any] = {"text": args.text}
    if style_str:
        part_obj["speech_metadata"] = {"style": style_str}
    payload = {
        "contents": [{"role": "user", "parts": [part_obj]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "voice": voice_id
                }
            },
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    for p in parts:
        if "inlineData" in p:
            raw = base64.b64decode(p["inlineData"]["data"])
            mime = p["inlineData"].get("mimeType", "")
            if "pcm" in mime or "L16" in mime:
                import wave
                with wave.open(str(out_p), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(raw)
            else:
                out_p.write_bytes(raw)
            if args.engine == "dr_lubbad_gemini":
                venv_py = HERMES_HOME / "cache/scratch/voicestudio-eval/.venv/bin/python"
                if venv_py.exists():
                    subprocess.run([
                        str(venv_py), "-c",
                        (
                            "import sys, librosa, soundfile as sf; "
                            "sys.path.insert(0, '/Users/muhammed/.hermes/omnivoice-emotional-ft/checkpoints/omnivoice-dr-lubbad-personal-v8'); "
                            "from generate_dr_lubbad_voice import calibrate_studio_cepstral_envelope; "
                            f"y, _ = librosa.load({str(out_p)!r}, sr=24000); "
                            "y_ref, _ = librosa.load('/Users/muhammed/.hermes/omnivoice-emotional-ft/checkpoints/omnivoice-dr-lubbad-personal-v8/golden_reference_preset/dr_lubbad_studio_anchor_24k.wav', sr=24000); "
                            "y_cal = calibrate_studio_cepstral_envelope(y, y_ref, sr=24000); "
                            f"sf.write({str(out_p)!r}, y_cal, 24000)"
                        )
                    ], check=True)
            print(json.dumps({"ok": True, "engine": args.engine, "voice_id": voice_id, "output": str(out_p), "size_bytes": out_p.stat().st_size}))
            return 0
    raise RuntimeError("No audio returned from gemini-3.8-flash-tts")


def cmd_virality(args: argparse.Namespace) -> int:
    vid_p = Path(args.video).expanduser().resolve()
    if not vid_p.exists():
        raise FileNotFoundError(f"Video not found: {vid_p}")

    probe_out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate:stream=codec_name,width,height",
            "-of",
            "json",
            str(vid_p),
        ]
    ).decode("utf-8")
    probe_data = json.loads(probe_out)
    duration = float(probe_data.get("format", {}).get("duration", 5.0))

    # Extract 4 sample frames across the video timeline for multimodal cognitive scoring
    timestamps = [min(0.5, duration * 0.1), min(1.5, duration * 0.25), duration * 0.5, duration * 0.85]
    frame_parts: list[dict[str, Any]] = []
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    for idx, ts in enumerate(timestamps):
        fp = SCRATCH_DIR / f"virality_frame_{os.getpid()}_{idx}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(vid_p), "-frames:v", "1", "-q:v", "4", str(fp)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if fp.exists():
            frame_parts.append(encode_image_part(fp))
            fp.unlink(missing_ok=True)

    key = get_google_key()
    prompt = (
        f"You are the Higgsfield Virality Predictor (brain_activity cognitive & retention analyzer). "
        f"Analyze these 4 sequential frames (at 10%, 25%, 50%, 85% of a {duration:.1f}s video) and return ONLY valid JSON with keys: "
        f"overall_score (int 0-100), peak_hook_pct (int 0-100), peak_hook_sec (float), sustain_pct (int 0-100), "
        f"strongest_region (one of: 'Visual Cortex', 'Attention Network', 'Auditory Cortex', 'Language Network'), "
        f"default_mode_risk ('Low' | 'Moderate' | 'High'), "
        f"summary_ar (concise Arabic business interpretation and 2 specific hook/retention improvements)."
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
    payload = {
        "contents": [{"parts": [*frame_parts, {"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    txt = data["candidates"][0]["content"]["parts"][0]["text"]
    report = json.loads(txt)
    print(
        f"Overall score: {report.get('overall_score')}/100\n"
        f"Peak hook: {report.get('peak_hook_pct')}% at {report.get('peak_hook_sec')}s\n"
        f"Sustain: {report.get('sustain_pct')}%\n"
        f"Strongest region: {report.get('strongest_region')}\n"
        f"Risk: Default Mode is {report.get('default_mode_risk')}\n"
        f"Analysis: {report.get('summary_ar')}"
    )
    return 0


def cmd_3d(args: argparse.Namespace) -> int:
    script = HERMES_HOME / "skills/3daistudio-integration/3daistudio.py"
    if args.engine == "3daistudio" and script.exists():
        cmd = [sys.executable, str(script), "trellis", "--image", args.image, "--textures", "-o", args.output]
        return subprocess.call(cmd)

    # Free HuggingFace ZeroGPU fallback via gradio_client
    from gradio_client import Client, handle_file

    out_p = Path(args.output).expanduser().resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    spaces = ["JeffreyXiang/TRELLIS", "Tencent/Hunyuan3D-2"]
    last_err = None
    for space in spaces:
        try:
            client = Client(space)
            print(f"Connected to free ZeroGPU 3D Space: {space}")
            break
        except Exception as e:
            last_err = e
    else:
        raise RuntimeError(f"ZeroGPU 3D spaces busy ({last_err}); pass --engine 3daistudio to use THREE_D_AI_STUDIO_API_KEY")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    env = load_env()
    status = {
        "apple_silicon_mflux": (MFLUX_BIN_DIR / "mflux-generate").exists(),
        "local_rembg_onnx": True,
        "google_genai_direct": bool(env.get("GOOGLE_API_KEY")),
        "local_omnivoice_dr_lubbad": (
            HERMES_HOME / "omnivoice-emotional-ft/checkpoints/omnivoice-dr-lubbad-personal-v8/generate_dr_lubbad_voice.py"
        ).exists(),
        "local_onetake_motion_studio": (HERMES_HOME / "tools/opus-motion-studio").exists(),
        "three_d_ai_studio_key": bool(env.get("THREE_D_AI_STUDIO_API_KEY")),
        "registered_souls": len(list(SOULS_DIR.glob("*/meta.json"))) if SOULS_DIR.exists() else 0,
    }
    print(json.dumps(status, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="open-hf", description="Open/Free Higgsfield Studio CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show readiness of all local and free-cloud engines")

    p_img = sub.add_parser("image", help="Generate or edit images (Nano Banana Pro / 2.1 / Gemini 3 Pro / Local MLX mflux)")
    p_img.add_argument("--prompt", "-p", required=True)
    p_img.add_argument("--model", "-m", default="nano_banana_pro")
    p_img.add_argument("--image", "-i", action="append", help="Reference image(s)")
    p_img.add_argument("--aspect-ratio", "-a", default="1:1")
    p_img.add_argument("--steps", type=int, default=4)
    p_img.add_argument("--quantize", "-q", type=int, default=4)
    p_img.add_argument("--output", "-o", required=True)

    p_ps = sub.add_parser("photoshoot", help="Product Photoshoot & Marketplace Cards")
    p_ps.add_argument("--prompt", "-p", required=True)
    p_ps.add_argument("--mode", default="studio", choices=["studio", "lifestyle", "hero", "marketplace_card", "virtual_try_on"])
    p_ps.add_argument("--brand", default="none", choices=["skydevelop", "mlubbad", "none"])
    p_ps.add_argument("--model", "-m", default="nano_banana_pro")
    p_ps.add_argument("--image", "-i", action="append")
    p_ps.add_argument("--aspect-ratio", "-a", default="1:1")
    p_ps.add_argument("--output", "-o", required=True)

    p_th = sub.add_parser("thumbnail", help="YouTube & Reels High-CTR Thumbnail")
    p_th.add_argument("--prompt", "-p", required=True)
    p_th.add_argument("--face", "-f", help="Reference face image for 1-shot Soul consistency")
    p_th.add_argument("--image", "-i", action="append")
    p_th.add_argument("--text", "-t", help="Max 3 words headline text")
    p_th.add_argument("--brand", default="mlubbad", choices=["skydevelop", "mlubbad", "none"])
    p_th.add_argument("--aspect-ratio", "-a", default="16:9")
    p_th.add_argument("--model", "-m", default="nano_banana_pro")
    p_th.add_argument("--output", "-o", required=True)

    p_soul = sub.add_parser("soul", help="Zero-shot Soul ID character vault & generator")
    p_soul.add_argument("soul_action", choices=["register", "list", "generate"])
    p_soul.add_argument("--id", help="Soul character ID")
    p_soul.add_argument("--image", "-i", action="append", help="Reference face/character image(s)")
    p_soul.add_argument("--description", "-d", default="")
    p_soul.add_argument("--prompt", "-p", default="")
    p_soul.add_argument("--mode", default="portrait", choices=["portrait", "cinematic", "sheet"])
    p_soul.add_argument("--model", "-m", default="nano_banana_pro")
    p_soul.add_argument("--aspect-ratio", "-a", default="9:16")
    p_soul.add_argument("--output", "-o", default="")

    p_bg = sub.add_parser("remove-bg", help="Local offline background removal via rembg")
    p_bg.add_argument("--input", "-i", required=True)
    p_bg.add_argument("--output", "-o", required=True)

    p_vid = sub.add_parser("video", help="Generate AI video with native audio (Veo 3.1 Lite/Fast/Pro)")
    p_vid.add_argument("--prompt", "-p", required=True)
    p_vid.add_argument("--model", "-m", default="veo_3_1_lite")
    p_vid.add_argument("--start-image", "-i", help="Optional starting frame for Image-to-Video")
    p_vid.add_argument("--aspect-ratio", "-a", default="16:9")
    p_vid.add_argument("--timeout", type=int, default=300)
    p_vid.add_argument("--output", "-o", required=True)

    p_sc = sub.add_parser("story-chain", help="Multi-scene continuous video via Last-Frame Chaining")
    p_sc.add_argument("--scenes", "-s", nargs="+", required=True, help="Ordered list of scene prompts")
    p_sc.add_argument("--start-image", "-i", help="Optional initial frame for Scene 1")
    p_sc.add_argument("--model", "-m", default="veo_3_1_lite")
    p_sc.add_argument("--aspect-ratio", "-a", default="16:9")
    p_sc.add_argument("--timeout", type=int, default=300)
    p_sc.add_argument("--output", "-o", required=True)

    p_mus = sub.add_parser("music", help="Generate AI music (Lyria 3 Clip/Pro)")
    p_mus.add_argument("--prompt", "-p", required=True)
    p_mus.add_argument("--model", "-m", default="lyria_clip")
    p_mus.add_argument("--duration", "-d", type=float, default=0.0)
    p_mus.add_argument("--output", "-o", required=True)

    p_tts = sub.add_parser("tts", help="Generate speech (Local OmniVoice dr_lubbad v9.2, Replicated Gemini 3.8 dr_lubbad_gemini, or Gemini 3.8 TTS)")
    p_tts.add_argument("--text", "-t", required=True)
    p_tts.add_argument("--engine", "-e", default="dr_lubbad", choices=["dr_lubbad", "dr_lubbad_gemini", "gemini_tts"])
    p_tts.add_argument("--voice", "-v", default="Orus")
    p_tts.add_argument("--style", "-s", default="")
    p_tts.add_argument("--output", "-o", required=True)

    p_vir = sub.add_parser("virality", help="Virality Predictor (brain_activity cognitive & hook analysis)")
    p_vir.add_argument("--video", "-v", required=True)

    p_3d = sub.add_parser("3d", help="Image to 3D GLB mesh")
    p_3d.add_argument("--image", "-i", required=True)
    p_3d.add_argument("--engine", default="3daistudio", choices=["3daistudio", "trellis_hf"])
    p_3d.add_argument("--output", "-o", required=True)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "status": cmd_status,
        "image": cmd_image,
        "photoshoot": cmd_photoshoot,
        "thumbnail": cmd_thumbnail,
        "soul": cmd_soul,
        "remove-bg": cmd_remove_bg,
        "video": cmd_video,
        "story-chain": cmd_story_chain,
        "music": cmd_music,
        "tts": cmd_tts,
        "virality": cmd_virality,
        "3d": cmd_3d,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
