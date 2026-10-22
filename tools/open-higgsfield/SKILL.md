---
name: open-higgsfield
description: Use for free/local Higgsfield media via open-hf CLI.
version: 1.0.0
author: Dr. Mohammed Lubbad (Sky Develop), Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [media, image, video, audio, 3d, higgsfield, mflux, veo, lyria]
    related_skills: [higgsfield-generate, voicestudio, onetake, 3daistudio, comfyui-character-gen, comfyui-video-pipeline]
---

# Open Higgsfield Studio (`open-hf`)

## When to Use
- Replacing any Higgsfield AI capability (image generation, product photoshoot, marketplace cards, YouTube/Reels thumbnails, Soul ID character consistency, video generation, music, TTS, background removal, 3D mesh, or Virality Predictor) at zero Higgsfield credit cost.
- Running local Apple Silicon M5 Max (`mflux` 0.23.0 + `rembg` ONNX + `OmniVoice` v9.2 + `Onetake`) or direct `GOOGLE_API_KEY` (`Nano Banana Pro`, `Nano Banana 2.1`, `Gemini 3 Pro Image`, `Veo 3.1`, `Lyria 3.5`, `Gemini 3.8 TTS`).

## CLI Binary
`~/.local/bin/open-hf` (`/Users/muhammed/.hermes/tools/open-higgsfield/open_hf.py`)

## Capability Routing Matrix (1-to-1 Higgsfield Replacement)

| Higgsfield Feature | `open-hf` Command | Default Engine | Local / Offline Option |
|---|---|---|---|
| **Image Generation & Editing** (`gpt_image_2_5`, `nano_banana_pro`, `flux_2`) | `open-hf image` | `nano_banana_pro` (`nano-banana-pro-preview`) or `nano_banana_2` (`gemini-nano-banana-2.1`) | `--model mflux_z_turbo`, `mflux_flux2`, `mflux_qwen2_1` (44 native MLX binaries in `~/.local/bin/mflux-*`) |
| **Product Photoshoot & Marketplace Cards** (`higgsfield-product-photoshoot`) | `open-hf photoshoot` | `nano_banana_pro` with `--mode studio\|lifestyle\|hero\|marketplace_card\|virtual_try_on` and `--brand skydevelop\|mlubbad` | `mflux-generate-kontext` / `mflux-generate-in-context-catvton` |
| **YouTube & Reels Thumbnails** (`higgsfield-youtube-thumbnail`) | `open-hf thumbnail` | `nano_banana_pro` with `--face <ref.png>` + `--text "<max 3 words>"` + `--brand mlubbad\|skydevelop` | `mflux-generate-qwen-2.1` |
| **Soul ID & AI Influencer Sheets** (`higgsfield-soul-id`) | `open-hf soul` | `open-hf soul register --id <id> -i <photo.png>` then `open-hf soul generate --id <id> --mode portrait\|cinematic\|sheet` | `mflux-generate-kontext` / `comfyui-character-gen` (PuLID-FLUX / InstantID) |
| **Background Removal** (`remove_background`) | `open-hf remove-bg` | 100% local `rembg` (ONNX `u2net`) | Default is local offline |
| **Video Generation & Image-to-Video** (`seedance_2_5`, `kling3_0`, `veo3_1`, `gemini_omni`) | `open-hf video` | `gemini_omni` / `veo_3_1_fast` (`models/gemini-omni-1.1-flash` via `/v1beta/interactions`; outputs 720p 24fps MP4 with native synchronized AAC audio and returns `interaction_id` for stateful edits) | `comfyui-video-pipeline` (Wan 2.2) or `clawd-animation` / `collage-broll-explainers` |
| **Music Generation** (`sonilo_music`, `seed_audio`) | `open-hf music` | `lyria_clip` (`lyria-3-clip-preview`) or `lyria_pro` (`lyria-3-pro-preview`) with optional `--duration` FFmpeg fade-out trim | `acestep` / `comfyui_music` (ACE-Step 1.5 on Mac MPS) |
| **Speech & Voice Cloning** (`text2speech_v2`) | `open-hf tts` | `--engine dr_lubbad` (locked local `OmniVoice v9.2` baritone preset on M5 Max MPS), `--engine dr_lubbad_gemini` (`gemini-3.8-flash-tts` replicated voice `voice_kbwqaiqajk3y` + 5-pass Studio Cepstral Calibration `SIM >= 0.9995` for Arabic+English code-switching & inline tags), or `--engine gemini_tts` | Default `dr_lubbad` is 100% local |
| **3D Mesh Generation** (`multi_image_to_3d`) | `open-hf 3d` | `3daistudio` (`TRELLIS.2` / `Hunyuan3D`) or `trellis_hf` (ZeroGPU) | Blender local processing |
| **Virality Predictor** (`brain_activity`) | `open-hf virality` | Multimodal 4-frame + `ffprobe` cognitive & hook analyzer (`Overall score`, `Peak hook`, `Sustain`, `Strongest region`, `Default Mode risk`) | Combined with `yt-script/hookscore.py` and `ig-reel/hookscore.py` |

## Quick Commands

```bash
# 1. Check all engines
~/.local/bin/open-hf status

# 2. Generate image (Cloud Nano Banana Pro or 100% local Apple Silicon MLX)
~/.local/bin/open-hf image -p "Architectural AI diagram" -m nano_banana_pro -a 16:9 -o out.png
~/.local/bin/open-hf image -p "Cinematic neon street" -m mflux_z_turbo -a 16:9 -o local_out.png

# 3. Studio Product Photoshoot
~/.local/bin/open-hf photoshoot --mode studio --brand skydevelop -p "AI hardware cube" -o product.png

# 4. YouTube / Reels Thumbnail with 1-shot Face Consistency
~/.local/bin/open-hf thumbnail -f face.png -t "وهم الوكلاء" --brand mlubbad -p "Dramatic server room" -o thumb.png

# 5. Register & Generate Soul ID (0 training wait, 0 credits)
~/.local/bin/open-hf soul register --id mlubbad -i photo1.jpg -i photo2.jpg -d "Dr. Mohammed Lubbad"
~/.local/bin/open-hf soul generate --id mlubbad --mode cinematic -p "Speaking on a dark tech stage" -o soul.png

# 6. Video Generation (Text-to-Video, Image-to-Video, or Multi-Scene Last-Frame Chaining)
~/.local/bin/open-hf video -p "Slow camera dolly into glowing neural core" -m veo_3_1_lite -a 16:9 -o clip.mp4
~/.local/bin/open-hf video -p "Subtle camera orbit and glowing particles" -i product.png -m veo_3_1_lite -o anim.mp4
~/.local/bin/open-hf story-chain --scenes "Scene 1: Rover driving across golden desert dunes at sunset" "Scene 2: Continue seamlessly from this exact frame: the rover stops and projects a cyan 3D hologram" -o chained_story.mp4

# 7. Music & TTS (Local OmniVoice v9.2 vs Replicated Gemini 3.8 Flash TTS)
~/.local/bin/open-hf music -p "Modern corporate tech ambient underscore, 115 BPM, instrumental" -d 15 -o bgm.mp3
~/.local/bin/open-hf tts --engine dr_lubbad -t "مرحبا بكم في ستوديو سكاي ديفيلوب." -o vo.wav
~/.local/bin/open-hf tts --engine dr_lubbad_gemini -t "هلأ خليني أحكيلك شي <short pause> يعني بالآخر the model is only as good as the reference you give it <breath> وهاد اللي عم نبنيه في Sky Develop." -o vo_codeswitch.wav

# 8. Local Background Removal
~/.local/bin/open-hf remove-bg -i product.png -o product_nobg.png

# 9. Virality Predictor (brain_activity replacement)
~/.local/bin/open-hf virality -v clip.mp4
```

## Critical Pitfalls Learned
1. **Default Route Mandate:** Always route every image, edit, photoshoot, marketplace card, thumbnail, Soul ID, video, motion, music, TTS, background removal, 3D, and virality request directly to `open-higgsfield` (`~/.local/bin/open-hf`) instead of spending paid Higgsfield credits.
2. **`mflux` Pillow version on Python 3.12:** Always keep `pillow<13` (`pillow==12.3.0`) in `~/.local/share/uv/tools/mflux` and `~/.hermes/tools/open-higgsfield/.venv`; `pillow==13.0.0` fails on Python 3.12 with `ImportError: cannot import name '_IntegralLike' from 'numbers'`.
3. **`gemini-3.8-flash-tts` Voice Replication (`voice_kbwqaiqajk3y`), Style Metadata, Inline Tags & WAV Header:**
   - **Replicated Custom Voice (`--engine dr_lubbad_gemini`):** Uses persistent replicated voice ID `voice_kbwqaiqajk3y` (`POST /v1beta/voices` with `type="replicated"`, `store=True`, 1-year auto-renewing TTL) passed in `speechConfig.voiceConfig.voice`, followed by 5-pass Zero-Phase Studio Mel-Cepstral Envelope Transfer Calibration (`SIM >= 0.9995`). Use `dr_lubbad_gemini` whenever a script mixes colloquial/white Arabic with English technical phrases (`Code-Switching`), uses inline vocal tags, or requires 2-speaker dialogue (`multiSpeakerVoiceConfig`).
   - **Creating/Refreshing a Replicated Voice (`POST /v1beta/voices`):** Pass two `24kHz` mono 16-bit WAV files (`source_audio` of 10-30s clean speech and `consent_audio` reciting the exact locale consent phrase — for `ar-XA`: `"أنا مالك هذا الصوت وأوافق على أن تستخدم Google هذا الصوت لإنشاء نموذج صوتي اصطناعي."`). In Colloquial/White Arabic synthesis for Dr. Lubbad, always write `ق` or `أ` (`هلأ`, `أقلك`) rather than `ك`, and omit diacritics (`حركات`).
   - **Style vs Verbatim Transcript:** Pass ONLY the verbatim words + angle-bracket human vocal tags (`<breath>`, `<sigh>`, `<chuckle>`, `<laugh>`, `<cough>`, `<short pause>`, `<long pause>`, and `|backchannel|` pipes in multi-speaker dialogue) inside `contents[0].parts[].text`. Put sustained delivery instructions in `parts[].speech_metadata.style` (and `parts[].speech_metadata.speaker` for multi-speaker turns) — never prepend stage directions in `text` or the model will read them aloud.
   - **Default WAV Header:** Unary responses from `gemini-3.8-flash-tts` (`mimeType: "audio/wav"`) already include a complete `RIFF....WAVEfmt ` header (`24kHz` 16-bit mono PCM) — write the decoded bytes directly to `.wav` without wrapping in Python's `wave` module.
4. **`lyria-3-clip-preview` Prompt Specificity:** Always specify `BPM`, key/mode, instruments, and `"instrumental"` in the music prompt — vague prompts cause the model to return a text clarification question instead of an `audio/mpeg` `inlineData` payload.
5. **Higgsfield MCP SSE Event Cap:** `https://mcp.higgsfield.ai/mcp` returns a `1,062,970`-byte `tools/list` SSE event (183 tools). Ensure `DEFAULT_MAX_SSE_EVENT_SIZE = 10 * 1024 * 1024` in `mcp/client/streamable_http.py` and `max_sse_event_size=_MCP_HTTP_MAX_BODY_BYTES` in `tools/mcp_tool_transport.py`.
6. **Gemini Omni 1.1 Flash Video Endpoint (`/v1beta/interactions`) & Last-Frame Continuity Chaining (`open-hf story-chain`):** Legacy `models/veo-3.1-*-generate-preview:predictLongRunning` endpoints return HTTP 404; always route video generation through `POST https://generativelanguage.googleapis.com/v1beta/interactions?key=$GOOGLE_API_KEY` with `{"model": "models/gemini-omni-1.1-flash", "input": "<timecoded prompt>"}` and decode the base64 MP4 from `steps[].content[]` where `type == "video"`. For Image-to-Video or multi-scene continuous stories (`open-hf story-chain`), extract the exact final frame of Scene `i` (`ffmpeg -y -sseof -0.08 -i scene_i.mp4 -frames:v 1 last.png`) and pass `"input": [{"type": "image", "mime_type": "image/png", "data": "<b64>"}, {"type": "text", "text": "Continue seamlessly from this exact frame: ..."}]` — this preserves pixel-exact character, lighting, and background continuity across scene boundaries. Do not pass `generation_config.response_modalities` to `/v1beta/interactions` (rejected with HTTP 400).
7. **Arabic ASS Subtitle BiDi Punctuation & Audio Mixing:** When burning Arabic subtitles via `ffmpeg -vf ass=...`, omit trailing commas (`،`) and periods (`.`) at line ends because `libass` defaults to LTR paragraph base direction and flips trailing punctuation to the right (start) of the Arabic line. When mixing voiceover with video background audio, always set `amix=inputs=2:duration=first:dropout_transition=0:normalize=0,loudnorm=I=-14.0:TP=-1.0:LRA=11` so `amix` does not halve voiceover amplitude (`-6 dB`).


## 6 Viral Commercial Video Ad Archetypes (`Wan 3.0` / `open-hf story-chain`)
When asked to produce high-converting social/e-commerce video ads, offer or execute these 6 proven archetypes via `open-hf story-chain` (using `Nano Banana Pro` for consistent character/product reference frames + `Veo 3.1` / `Wan 3.0` for motion):

1. **`hyper-motion` (Hyper Motion Product Assembly):**
   - High-velocity kinetic camera orbiting the product as its parts/ingredients levitate, explode outward in slow-motion, and snap together on beat with crisp mechanical/whoosh SFX.
2. **`clay-motion` (Tactile Stop-Motion Claymation):**
   - Handcrafted plasticine/clay world (`24fps stop-motion feel, visible thumbprints, soft studio key light`) where the product and characters morph playfully.
3. **`fake-podcast` (Two-Host Studio Podcast Hook):**
   - Split or over-the-shoulder 4K podcast studio (`Shure SM7B mics, warm tungsten rim light, shallow DOF`) where Host A states a shocking industry truth/problem and Host B reveals the product/service.
4. **`zack-d-3d` (Zack D. Films 3D Cross-Section Explainer):**
   - Glossy educational 3D medical/mechanical cutaway animation zooming inside the human body, machine, or system to show *why* the problem happens and *how* the product solves it at the root.
5. **`crochet-world` (Cozy Amigurumi Knitted World):**
   - Entire scene, environment, and mascot rendered in macro knitted wool & crochet yarn texture (`macro fiber detail, warm sunlight, tactile softness`).
6. **`pixar-story-ad` (Pixar 3D Pain-to-Transformation Story — 4-Beat Structure):**
   - **Required Inputs:** (a) Brand/Doctor/Founder social card screenshot, (b) 2–3 front/3-quarter photos of the founder/specialist, (c) interior photo of clinic/office/product, (d) target city & language.
   - **Beat 1 `[0–6s]` (Relatable Setup):** Protagonist in everyday public setting (e.g., bus, office, cafe); subtle visual cue of the problem.
   - **Beat 2 `[6–12s]` (Comic/Emotional Trigger):** A micro-interaction exposes the pain point (e.g., someone offers them a seat thinking they look old/exhausted); comic deflation SFX, no dialogue needed.
   - **Beat 3 `[12–18s]` (Discovery Bridge):** Close-up over-the-shoulder shot of smartphone screen searching the problem on Google/Instagram and tapping the real Brand/Founder profile card.
   - **Beat 4 `[18–24s]` (Real-to-Pixar Transformation):** Protagonist enters the Pixar-stylized real clinic/store, meets the Pixar-stylized real founder/doctor, undergoes the solution, and checks the mirror with an amazed expression.
