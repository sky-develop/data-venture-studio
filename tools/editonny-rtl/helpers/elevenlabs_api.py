"""Shared code of gen_music.py and gen_sfx.py: audio made in the buyer's OWN ElevenLabs account (optional).

The key is ELEVENLABS_API_KEY from the environment or from `.env` in the skill root (the same reader as
fetch_images.py and gen_image.py). It only goes into the `xi-api-key` header of the request: never into a
message, a log, a report or credits.json. Every message printed by the two helpers passes through
`redact`, which hides the key and anything that looks like a piece of it.

Nothing generated is kept in the skill folder: the audio goes to <EDIT>/assets/ (an edit folder inside the
skill folder is refused) and an entry goes to <EDIT>/assets/credits.json.

API (read on 16/09/2026 in the official reference, elevenlabs.io/docs/api-reference):
  POST https://api.elevenlabs.io/v1/music?output_format=mp3_44100_128   header xi-api-key
       JSON {"prompt", "music_length_ms" (3000 to 600000), "force_instrumental", "model_id"}
  POST https://api.elevenlabs.io/v1/sound-generation?output_format=mp3_44100_128
       JSON {"text", "duration_seconds" (0.5 to 30), "prompt_influence" (0 to 1), "loop", "model_id"}
Both answer the audio bytes. A prompt that names protected work is refused with
`detail.status == "bad_prompt"` and a `detail.data.prompt_suggestion`.
Costs (pricing pages, 16/09/2026): music about 900 credits per minute; a sound effect with a set duration
40 credits per second. Commercial use of the output needs a paid plan the buyer is eligible for (Starter or
higher for a person; Scale, Business or Enterprise for a company), never film, TV or radio, and the files
must not be passed on, sold or bundled (see PAID_PLAN_LINE).
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import unicodedata
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SKILL_ROOT, now_iso, save_json  # noqa: E402
from fetch_images import EnvFileError, load_credits, read_env_key  # noqa: E402  (one .env reader for all helpers)

KEY_NAME = "ELEVENLABS_API_KEY"
API_BASE = "https://api.elevenlabs.io"
MUSIC_URL = f'{API_BASE}/v1/music'
SFX_URL = f'{API_BASE}/v1/sound-generation'
OUTPUT_FORMAT = "mp3_44100_128"          # MP3 44.1 kHz 128 kbps: available on every plan
USER_AGENT = "editonny-engine/0.1"
MUSIC_CREDITS_PER_MINUTE = 900
SFX_CREDITS_PER_SECOND = 40
COMMERCIAL_NOTE = 'commercial use requires an eligible paid ElevenLabs plan; do not redistribute the generated file'
# Eleven Music Model-Specific Terms (26/05/2026): Starter, Creator and Pro are "for individual use only";
# Scale is for fewer than 10 employees, Business for fewer than 50. Film, TV, radio and studio games are out
# on every self-serve plan. Libraries and resale of the output are prohibited (sound effects too, 9(c)).
PAID_PLAN_LINE = ("Commercial use depends on the ElevenLabs plan and terms in effect when generated. Verify the account's eligibility and intended distribution. Keep generated audio for the authorized user's videos; do not redistribute or bundle it as a standalone sound library.")

# Real artists, songs, soundtracks and sound logos. The API refuses most of them anyway (bad_prompt), but
# checking here saves a paid round trip and keeps the request clean. Only unambiguous names: a plain
# word like "queen" or "prince" would block honest prompts.
PROTECTED_NAMES = (
    # international artists
    "taylor swift", "beyonce", "the weeknd", "billie eilish", "ed sheeran", "dua lipa", "bruno mars",
    "michael jackson", "the beatles", "beatles", "rolling stones", "coldplay", "imagine dragons", "eminem",
    "rihanna", "lady gaga", "ariana grande", "justin bieber", "shakira", "daft punk", "avicii",
    "david guetta", "calvin harris", "marshmello", "hans zimmer", "kanye west", "travis scott", "bad bunny",
    "karol g", "post malone", "metallica", "ac dc", "led zeppelin", "pink floyd", "linkin park",
    "maroon 5", "katy perry", "harry styles", "olivia rodrigo", "doja cat", "kendrick lamar",
    "snoop dogg", "dr dre", "frank sinatra", "elvis presley", "bob marley", "madonna", "adele",
    "sabrina carpenter", "miley cyrus", "j balvin", "daddy yankee", "tiesto", "skrillex",
    "kygo", "alan walker", "lofi girl",
    # Brazilian artists
    "anitta", "ivete sangalo", "marilia mendonca", "gusttavo lima", "jorge e mateus",
    "henrique e juliano", "ze neto e cristiano", "wesley safadao", "alok", "vintage culture", "mc kevinho",
    "kevin o chris", "matue", "racionais mcs", "caetano veloso", "gilberto gil", "tim maia", "roberto carlos",
    "jorge ben", "seu jorge", "pabllo vittar", "luan santana", "zeze di camargo", "legiao urbana",
    "charlie brown jr", "paralamas do sucesso", "djavan", "marisa monte", "joao gilberto", "tom jobim",
    "chitaozinho e xororo", "ana castela", "joao gomes", "mc ryan sp", "mc daniel", "xama",
    "pedro sampaio", "claudia leitte", "zeca pagodinho", "sorriso maroto", "thiaguinho", "exaltasamba",
    "banda calypso", "mamonas assassinas", "titas",
    # songs and soundtracks
    "despacito", "bohemian rhapsody", "billie jean", "shape of you", "blinding lights", "uptown funk",
    "gangnam style", "macarena", "baby shark", "ai se eu te pego", "garota de ipanema", "eye of the tiger",
    "we will rock you", "smells like teen spirit", "hotel california",
    "stairway to heaven", "never gonna give you up", "seven nation army", "tubarao te amo",
    "star wars", "harry potter", "mission impossible", "missao impossivel", "pirates of the caribbean",
    "piratas do caribe", "game of thrones", "the avengers", "vingadores", "jurassic park",
    "james bond", "rocky balboa", "tema da vitoria", "plantao da globo", "jingle da globo",
    # sound logos and product sounds
    "super mario", "mario coin", "windows xp", "iphone", "netflix", "tudum", "whatsapp", "skype", "nokia",
    "playstation", "xbox", "pac man", "zelda", "sonic the hedgehog", "minecraft", "fortnite",
    "discord notification", "discord ping", "tiktok", "instagram", "mcdonalds", "thx deep note",
    "20th century fox", "nubank", "mercado livre", "shopee", "hotmart", "kiwify", "hubla",
)
PROTECTED_PHRASES = (
    "cover of", "cover de", "cover da", "cover do", "remix of", "remix de", "remix da", "remix do",
    "in the voice of", "na voz de", "na voz da", "na voz do", "sample of", "sample de", "sample da",
    "sample do", "same as the song", "igual a musica", "igual a cancao",
)
PT_HINTS = {"musica", "music", "sem", "com", "voice", "upbeat", "calm", "alegre", "hit", "background", "efeito",
            "sound", "caindo", "moeda", "rapida", "rapido", "smooth", "clima", "violao"}


class ElevenError(Exception):
    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ key

_KEY_CHARS = re.compile(r"[\x21-\x7e]+")      # printable ASCII, no space: what a header value can carry


def api_key() -> str | None:
    """ELEVENLABS_API_KEY from the environment or from <SKILL>/.env (None when missing).

    A key with a space, a line break, a curly quote or an accent in the middle is refused here with a plain
    message (requests would fail with an error that can carry the header value)."""
    try:
        key = read_env_key(KEY_NAME)
    except EnvFileError as e:
        raise ElevenError(str(e), 2) from None
    except (UnicodeError, ValueError):
        raise ElevenError('could not read the skill .env file. Save it as UTF-8 and retry',
                          2) from None
    if key and not _KEY_CHARS.fullmatch(key):
        raise ElevenError(f'a {KEY_NAME} contains whitespace, line breaks, typographic quotes, or non-ASCII characters. Verify the local credential without exposing it in chat', 2)
    return key


def check_in_key(text: str, key: str | None) -> None:
    """Refuse a prompt that carries the key (or anything shaped like one): it would go in the body and in
    credits.json."""
    if redact(text, key) != text:
        raise ElevenError('the prompt appears to contain an API key. Remove credentials and describe only the requested sound', 2)


def missing_key_message(what: str, fallback: str) -> str:
    return (f"{KEY_NAME} is not configured. {what} through ElevenLabs is optional; continue without it using {fallback}. To enable it, configure an authorized account key with the required Music or Sound Effects permissions in the local environment or skill .env file: {KEY_NAME}=... in the skill .env file ({SKILL_ROOT / '.env'}). Never repeat the key in conversation or include it in a shell command")


_TOKEN = re.compile(r"[A-Za-z0-9_\-]{8,}")
_LOOKS_LIKE_KEY = re.compile(r"\bsk_[A-Za-z0-9]{4,}")


def redact(text: str, key: str | None) -> str:
    """Hide the key and any word that shares 8 or more characters with it (or looks like an ElevenLabs key)."""
    text = str(text)
    if key:
        key = key.strip()
    if key and len(key) >= 4:
        text = text.replace(key, "[redacted]")
        chunks = {key[i:i + 8] for i in range(max(0, len(key) - 7))}

        def hide(match: re.Match) -> str:
            word = match.group(0)
            return "[redacted]" if any(c in word for c in chunks) else word

        if chunks:
            text = _TOKEN.sub(hide, text)
    return _LOOKS_LIKE_KEY.sub("[redacted]", text)


# ------------------------------------------------------------------ prompt

def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return " " + re.sub(r"[^a-z0-9]+", " ", t).strip() + " "


def protected_name(text: str) -> str | None:
    """The first real artist, song, soundtrack, brand sound or cover/remix phrase found in the prompt."""
    normal_value = normalize(text)
    for name in PROTECTED_NAMES + PROTECTED_PHRASES:
        if f' {normalize(name).strip()} ' in normal_value:
            return name
    return None


def check_prompt(text: str, example: str) -> None:
    found = protected_name(text)
    if found:
        raise ElevenError(
            f"the prompt names an artist, song, film score, or real brand ('{found}'). The provider may reject this request. Describe the musical qualities, instruments, mood, and rhythm instead, for example '{example}').", 2)


def looks_portuguese(text: str) -> bool:
    return bool(set(normalize(text).split()) & PT_HINTS)


def slugify(text: str, limit: int = 24) -> str:
    t = normalize(text).strip().replace(" ", "_")
    return t[:limit].rstrip("_") or "sound"


# ------------------------------------------------------------------ folders and files

def edit_folder(value: str) -> Path:
    edit = Path(value).expanduser().resolve()
    if not edit.is_dir():
        raise ElevenError(f'edit folder not found: {edit}', 2)
    skill = SKILL_ROOT.resolve()
    if edit == skill or skill in edit.parents:
        raise ElevenError("the edit folder is inside the skill. Generated audio belongs in the video's edit folder, not inside the installed skill", 2)
    return edit


def output_stem(value: str) -> str:
    """Bare file stem from --out ('assets/music.mp3' -> 'music'); only .mp3 comes out of the API."""
    name = Path(str(value).replace("\\", "/")).name.strip()
    suffix = Path(name).suffix.lower()
    if suffix and suffix != ".mp3":
        if re.fullmatch(r"\.[a-z0-9]{2,4}", suffix):
            raise ElevenError(f"output is MP3; provide a .mp3 filename (received '{name}')", 2)
        suffix = ""
    stem = name[: -len(suffix)] if suffix else name
    if not stem or stem.startswith("."):
        raise ElevenError(f"invalid filename in --out: '{value}'", 2)
    return stem


def free_paths(assets: Path, names: list[str]) -> list[Path]:
    paths = [assets / n for n in names]
    taken = [p.name for p in paths if p.exists()]
    if taken:
        raise ElevenError(f"already exists in assets/: {', '.join(taken)}. Choose another --out filename; existing files are preserved", 2)
    return paths


def next_number(assets: Path, prefix: str) -> int:
    used = [0]
    if assets.is_dir():
        for p in assets.iterdir():
            m = re.fullmatch(re.escape(prefix) + r"(\d+)\.mp3", p.name, flags=re.I)
            if m:
                used.append(int(m.group(1)))
    return max(used) + 1


def save_audio(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".tmp-" + path.name)
    try:
        tmp.write_bytes(data)
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            tmp.unlink()


def append_credit(edit: Path, entry: dict) -> None:
    path = edit / "assets" / "credits.json"
    credits = load_credits(path) if path.exists() else []
    credits.append(entry)
    save_json(path, credits)


def store(edit: Path, path: Path, data: bytes, entry: dict) -> None:
    """Save one paid file and its credits.json entry, with a plain message when the disk refuses."""
    try:
        save_audio(path, data)
    except OSError as e:
        raise ElevenError(f'ElevenLabs generated audio, but saving failed in assets/{path.name} ({e.__class__.__name__}). Check free space and edit-folder permissions before retrying; generation was already charged')
    try:
        append_credit(edit, entry)
    except (OSError, ValueError) as e:
        raise ElevenError(f"saved assets/{path.name}, but recording assets/credits.json failed ({e.__class__.__name__}). Record the source manually: {entry.get('source')}.")


def credits_estimate(kind: str, seconds: float) -> int:
    if kind == "music":
        return int(math.ceil(seconds * MUSIC_CREDITS_PER_MINUTE / 60.0 - 1e-9))
    return int(math.ceil(seconds * SFX_CREDITS_PER_SECOND - 1e-9))


def dry_run_request(url: str, payload: dict, key: str | None) -> str:
    return json.dumps({"url": url, "query": {"output_format": OUTPUT_FORMAT},
                       "headers": {"xi-api-key": "configured" if key else "missing"},
                       "body": payload}, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ request

def _short(text: str, key: str | None, limit: int) -> str:
    """One line, key hidden BEFORE cutting (a cut must never leave a piece of the key behind)."""
    return redact(" ".join(str(text).split()), key)[:limit]


def _detail(response: requests.Response, key: str | None) -> tuple[set[str], str, str]:
    """(status words, short message, prompt suggestion) from an error answer.

    The error page (docs/eleven-api/resources/errors, 16/09/2026) documents
    {"detail": {"type", "code", "message", "status", "request_id", "param"}}; older answers only have
    "status". All three words are kept."""
    try:
        body = response.json()
    except ValueError:
        return set(), _short(response.text or "", key, 200), ""
    detail = body.get("detail", body) if isinstance(body, dict) else body
    words: set[str] = set()
    message = suggestion = ""
    if isinstance(detail, dict):
        words = {str(detail.get(k) or "").strip().lower() for k in ("status", "code", "type")} - {""}
        message = str(detail.get("message") or detail.get("msg") or "")
        data = detail.get("data")
        if isinstance(data, dict):
            suggestion = str(data.get("prompt_suggestion") or "")
    elif isinstance(detail, list):
        parts = []
        for item in detail[:3]:
            if isinstance(item, dict):
                loc = ".".join(str(x) for x in item.get("loc", []) if x != "body")
                parts.append(f"{loc}: {item.get('msg', '')}".strip(": "))
        message = "; ".join(parts)
    elif detail is not None:
        message = str(detail)
    return words, _short(message, key, 200), _short(suggestion, key, 300)


INVALID_KEY_WORDS = {"invalid_api_key", "missing_api_key", "invalid_authorization_header", "unauthorized",
                     "needs_authorization", "sign_in_required", "authentication_error"}
IN_CREDIT_WORDS = {"quota_exceeded", "insufficient_credits", "payment_required"}
PAID_ONLY_WORDS = {"subscription_required", "feature_not_available"}


def http_error(response: requests.Response, feature: str, key: str | None = None) -> ElevenError:
    code = response.status_code
    words, message, suggestion = _detail(response, key)
    if "bad_prompt" in words or suggestion:
        tip = f" ElevenLabs suggestion: '{suggestion}'." if suggestion else ""
        return ElevenError(f'ElevenLabs refused the request because it cites a protected work, artist, or brand (or something prohibited).{tip} Rewrite without those names and retry', 2)
    if code == 402 or words & IN_CREDIT_WORDS:
        return ElevenError('Insufficient ElevenLabs account credit or key allowance. Review billing or key limits in the provider account. Nothing was generated')
    if code in (401, 403) and any("permission" in w for w in words):
        return ElevenError(f'the ElevenLabs key lacks permission for {feature}. Update its provider-side permissions to enable {feature}, or create another key.', 2)
    if code == 401:
        if words and not words & INVALID_KEY_WORDS and message:
            return ElevenError(f'ElevenLabs rejected the request; HTTP 401: {message}', 2)
        return ElevenError(f'invalid key: ElevenLabs rejected {KEY_NAME}. Verify or replace the credential locally; do not expose it in chat', 2)
    if code == 403 and words & PAID_ONLY_WORDS:
        return ElevenError(f'{feature} through this API requires an eligible paid ElevenLabs plan; HTTP 403', 2)
    if code == 403:
        return ElevenError(f'ElevenLabs denied access to {feature} for this account or key; HTTP 403. Check access to {feature}; check plan eligibility, feature access, and key IP restrictions', 2)
    if code == 429:
        return ElevenError('ElevenLabs rate limit: HTTP 429. Wait briefly and retry one variant at a time')
    if code >= 500:
        return ElevenError(f'ElevenLabs is unavailable or unstable (HTTP {code}). Try again in a few minutes.')
    if code in (400, 422):
        why = f': {message}' if message else ""
        return ElevenError(f'ElevenLabs rejected the request; HTTP {code}){why}. Check the prompt and duration', 2)
    why = f': {message}' if message else ""
    return ElevenError(f'ElevenLabs returned error {code}{why}.')


def looks_like_mp3(data: bytes) -> bool:
    if len(data) < 16:
        return False
    return data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)


def post_audio(url: str, payload: dict, key: str, feature: str, timeout: float) -> bytes:
    """One paid request. Returns the MP3 bytes or raises ElevenError with a plain PT-BR message.

    Redirects are not followed: requests only drops `Authorization` on a redirect to another host, so the
    xi-api-key header would go along. Every failure is reported by its class name only (a requests error can
    carry the request, headers included), and `from None` keeps it out of any traceback."""
    headers = {"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg",
               "User-Agent": USER_AGENT}
    try:
        response = requests.post(url, params={"output_format": OUTPUT_FORMAT}, headers=headers,
                                 data=json.dumps(payload), timeout=timeout, allow_redirects=False)
    except requests.Timeout:
        raise ElevenError('ElevenLabs timed out. Check account history before retrying; generation may already have consumed credits') from None
    except requests.RequestException as e:
        raise ElevenError(f'could not reach ElevenLabs ({e.__class__.__name__}). Check the internet connection.') from None
    except Exception as e:  # noqa: BLE001  (anything else from the HTTP stack: never let its text through)
        raise ElevenError(f'ElevenLabs request failed ({e.__class__.__name__}). Nothing was generated; retry the request.') from None
    if response.status_code >= 400:
        raise http_error(response, feature, key)
    if response.status_code >= 300 or response.is_redirect:
        raise ElevenError(f'ElevenLabs returned a redirect with status {response.status_code}, which was not followed to protect the key. Nothing was saved; retry later')
    data = response.content or b""
    ctype = (response.headers.get("Content-Type") or "").lower()
    if "json" in ctype or "text/" in ctype or not looks_like_mp3(data):
        raise ElevenError('ElevenLabs returned no audio. Nothing was saved; retry later')
    return data


def new_credit(file: str, source: str, prompt: str, seconds: float, **extra) -> dict:
    entry = {"file": file, "source": source, "prompt": prompt, "seconds": seconds}
    entry.update(extra)
    entry.update({"created_at": now_iso(), "license": "AI-generated in the user's own account", "note": COMMERCIAL_NOTE})
    return entry


def print_error(error: Exception, key: str | None) -> None:
    print(f'error: {redact(str(error), key)}', file=sys.stderr)


def unexpected(error: Exception) -> ElevenError:
    """A bug or an odd failure outside the request: plain message, no traceback (its text is redacted)."""
    return ElevenError(f'Unexpected failure ({error.__class__.__name__}: {error}). No files were deleted.')


def utf8_console() -> None:
    """Windows prints through cp1252 when the output is a pipe: accents and emoji would crash print()."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass
