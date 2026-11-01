'Suggest visual animations from the actual cut transcript.\n\nRead edit/transcripts/cut.json and, when available, cut_timeline.json and edit_data.json. Detect comment invitations, spoken prices, negated lists, checklists, ordered steps, percentages, counts, and money. Map these to the English local template IDs and measured word times; values must come from the recording. Never invent data.\n\nSuggestions are printed, not written into edit_data.json. Keep at most one suggestion about every six seconds, avoid hooks and closing calls to action unless the requested visual belongs there, and use automatic face-safe placement. Every suggestion includes its source phrase and reason.\n\nUsage: uv run python helpers/suggest_visuals.py --edit-dir EDIT --json\nThe last stdout line is a JSON object containing suggested items and skipped candidates. Exit 2 for missing transcripts or invalid input.'

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spoken_numbers as vl  # noqa: E402
from common import file_signature, fmt_time, load_json  # noqa: E402
from visuals import engine as ME  # noqa: E402
from visuals import registry as Rg  # noqa: E402
from visuals.engine import VisualError  # noqa: E402

MIN_GAP = 6.0            # s between two suggestions (starts)
HOOK_DEFAULT = 3.0       # s kept free at the start when edit_data has no hook yet
CTA_DEFAULT = 2.5        # s at the end taken by the CTA card when edit_data does not say
LEAD = 0.25              # s the animation starts before the first word it shows
READ_AFTER = 1.2         # s kept on screen after the last word it shows
MIN_LENGTH = 1.5         # s: a shorter suggestion is dropped
SENTENCE_GAP = 0.7       # s of silence that ends a sentence
PRIORITY = {"comment_cta": 100, "price_tag": 90, "strikeout": 80, "checklist": 70, "steps": 65,
            "progress_bar": 50, "counter": 40}

FUNCTION_WORDS = {"de", "do", "da", "dos", "das", "o", "a", "os", "as", "um", "uma", "uns", "umas", "nem", "e",
                  "ou", "que", "pra", "para", "com", "no", "na", "em", "tambem", "mais", "nenhum", "nenhuma",
                  "isso", "so", "ai", "aqui", "tipo", "ne", "entao", "voce"}
FUNCTION_WORDS |= {"the", "a", "an", "of", "to", "in", "on", "and", "or", "for", "with", "at", "by"}
KEYWORD_STOP = FUNCTION_WORDS | {"embaixo", "comentario", "comentarios", "mensagem", "direct", "dm", "eu", "la",
                                 "palavra", "seu", "sua", "ele", "ela", "gente", "agora", "ja", "se", "me", "te"}
UNITS = ("students", "people", "clients", "followers", "hours", "days", "weeks", "months", "years", "alunos", "alunas", "aluno", "pessoas", "clientes", "seguidores", "dias", "horas", "minutos", "aulas",
         "modulos", "videos", "produtos", "fornecedores", "lojas", "vezes", "anos", "meses", "semanas",
         "vagas", "passos", "etapas", "cursos", "ferramentas", "bonus", "mentorias", "encontros", "lives")
UNIT_LABEL = {"alunas": "alunas", "aluno": "aluno", "modulos": "módulos", "videos": "vídeos", "bonus": "bônus"}
PROGRESS_WORDS = re.compile(r"\b(conclu\w*|complet\w*|pront\w*|preench\w*|vagas?|do curso|das aulas|meta|"
                            r"progresso|falta\w*|lotad\w*|esgotad\w*|acabando)\b")
PRICE_CONTEXT = re.compile(r"\b(por\s+(apenas|so)|apenas|custa\w*|investimento|valor|preco|paga\w*|sai\s+por|"
                           r"parcela\w*|\d+\s*x\s+de|a\s+vista|promocao|desconto|oferta|por\s+menos\s+de)\b")
# money said as what someone made (not what something costs): "faturei 50 mil", "30 mil de faturamento"
GAIN_BEFORE = re.compile(r"\b(?:fatur\w*|ganh(?:ei|ou|amos|aram|ava|avam|ando|e|ar)|lucr\w*|fiz|fez|fizemos|fizeram|"
                         r"fazendo|tirei|tirou|tiramos|tiro|tirando|caiu|cairam|recebi|recebeu|recebemos|vendi|vendeu|"
                         r"vendemos|renda|salario|comiss\w*|saldo)\b")
GAIN_AFTER = re.compile(r"\s*(?:de|em|no|na)?\s*(?:faturamento|lucro|renda|comiss\w*|vendas|salario)\b")
PERIOD_AFTER = re.compile(r"\s*(?:/\s*|por\s+|ao\s+|no\s+|na\s+|em\s+(?:um|uma)\s+)(?:mes|dia|semana|ano|hora)\b")


@dataclass
class Word:
    text: str          # as transcribed
    normal_value: str          # lower case, no accents, no punctuation
    start: float
    end: float


@dataclass
class Sentence:
    words: list[Word]
    text: str = ""
    ntext: str = ""
    spans: list[tuple[int, int]] = field(default_factory=list)   # char span of each word in ntext

    def __post_init__(self) -> None:
        parts, pos = [], 0
        for w in self.words:
            t = vl.normal_value(w.text)
            parts.append(t)
            self.spans.append((pos, pos + len(t)))
            pos += len(t) + 1
        self.ntext = " ".join(parts)
        self.text = " ".join(w.text for w in self.words)

    def word_at(self, char: int) -> int:
        for i, (a, b) in enumerate(self.spans):
            if char < b:
                return i
        return len(self.words) - 1

    def words_in(self, a: int, b: int) -> list[Word]:
        return [w for w, (x, y) in zip(self.words, self.spans) if x < b and a < y]


@dataclass
class Candidate:
    template: str
    start: float                  # first word it shows
    last: float                   # last word it shows (end of that word)
    params: dict
    quote: str
    note: str = ""
    times: list[float] = field(default_factory=list)   # absolute seconds of each item, when it has items
    score: int | None = None      # a moment's own weight (None = the template's PRIORITY)
    marked: bool = False          # asked for at this point in the preview: always kept

    @property
    def priority(self) -> int:
        if self.marked:
            return 1000
        return PRIORITY[self.template] if self.score is None else self.score


class SuggestError(Exception):
    pass


# ------------------------------------------------------------------ reading

def clean_word(text: str) -> str:
    return re.sub(r"[^\w%$]+", "", vl.normal_value(text))


def load_words(edit_dir: Path) -> tuple[list[Word], list[str]]:
    path = edit_dir / "transcripts" / "cut.json"
    notes: list[str] = []
    if not path.is_file():
        raise SuggestError(f"could not find {path}. Transcribe the cut first: transcribe.py '{edit_dir / 'cut.mp4'}' --edit-dir '{edit_dir}'")
    try:
        data = load_json(path)
    except ValueError as e:
        raise SuggestError(f'{path.name} invalid ({e})') from None
    raw = (data or {}).get("words") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise SuggestError(f'{path.name} has no timed words; transcribe the cut again')
    cut = edit_dir / "cut.mp4"
    if cut.is_file() and data.get("source_sig") and data.get("source_sig") != file_signature(cut):
        notes.append('transcripts/cut.json belongs to another cut version: timing may be stale. Transcribe cut.mp4 again')
    words = []
    for w in raw:
        try:
            text, a, b = str(w["text"]).strip(), float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if text:
            words.append(Word(text, clean_word(text), a, max(a, b)))
    words.sort(key=lambda w: w.start)
    return words, notes


def sentences_of(words: list[Word]) -> list[Sentence]:
    out: list[Sentence] = []
    cur: list[Word] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or re.search(r"[.!?]$", w.text) or nxt.start - w.end > SENTENCE_GAP:
            out.append(Sentence(cur))
            cur = []
    return out


def edit_windows(edit_dir: Path, duration: float) -> tuple[float, float | None, dict]:
    """(hook end, CTA start or None, facts) from edit_data.json, with defaults for an edit not written yet."""
    data = None
    path = edit_dir / "edit_data.json"
    if path.is_file():
        try:
            data = load_json(path)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return HOOK_DEFAULT, max(0.0, duration - CTA_DEFAULT), {"edit_data": False}

    def on(key: str) -> dict | None:
        sec = data.get(key)
        return sec if isinstance(sec, dict) and sec and sec.get("enabled", True) is not False else None

    hook_end = 0.3
    hook = on("hook")
    if hook:
        try:
            hook_end = float(hook.get("end") if hook.get("end") is not None else 3.0)
        except (TypeError, ValueError):
            hook_end = 3.0
    cta_start = None
    cta = on("cta")
    if cta:
        try:
            dur = float(cta.get("duration", 2.5) or 2.5)
            cta_start = float(cta["start"]) if cta.get("start") is not None else max(0.0, duration - dur)
        except (TypeError, ValueError):
            cta_start = max(0.0, duration - CTA_DEFAULT)
    return hook_end, cta_start, {"edit_data": True}


# ------------------------------------------------------------------ small text helpers

def title_case(text: str) -> str:
    text = text.strip(" ,.;:!?-")
    return text[:1].upper() + text[1:] if text else text


def original_span(s: Sentence, a: int, b: int) -> str:
    """The transcribed words for a char range of the normalized sentence, without trailing punctuation."""
    return " ".join(w.text for w in s.words_in(a, b)).strip(" ,.;:!?")


def short_item(s: Sentence, a: int, b: int, max_words: int, max_len: int) -> tuple[str, float] | None:
    """A list item from a char range: leading function words dropped, at most max_words words and max_len letters.
    Returns (text, start of its first word)."""
    ws = s.words_in(a, b)
    while ws and ws[0].normal_value in FUNCTION_WORDS:
        ws = ws[1:]
    ws = ws[:max_words]
    while ws and ws[-1].normal_value in FUNCTION_WORDS:
        ws = ws[:-1]
    if not ws:
        return None
    text = title_case(" ".join(w.text for w in ws))
    while len(text) > max_len and len(ws) > 1:
        ws = ws[:-1]
        text = title_case(" ".join(w.text for w in ws))
    if len(text) > max_len or not re.search(r"\w", text):
        return None
    return text, ws[0].start


def split_list(s: Sentence, a: int, b: int) -> list[tuple[int, int]]:
    """Char ranges of the pieces of an enumeration ("x, y e z", "x, nor y")."""
    seg = s.ntext[a:b]
    cuts = [0]
    for m in re.finditer(r",|\s(?:e|nem|ou)\s(?!sim\b)", seg):
        if m.group(0) != ",":
            said = s.words[s.word_at(a + m.start() + 1)].text.lower().strip(",.;:!?")
            if said not in ("e", "nem", "ou"):
                continue          # "é" (the verb) loses its accent in the normalized text
        cuts.append(m.start())
        cuts.append(m.end())
    cuts.append(len(seg))
    out = []
    for x, y in zip(cuts[::2], cuts[1::2]):
        if y > x and seg[x:y].strip(" ,"):
            out.append((a + x, a + y))
    return out


def is_gain(s: Sentence, m: vl.Money) -> bool:
    """The money is what someone made ("faturei 50 mil", "30 mil de faturamento"), not a price."""
    return bool(GAIN_BEFORE.search(s.ntext[max(0, m.start - 45):m.start]) or GAIN_AFTER.match(s.ntext, m.end))


# ------------------------------------------------------------------ detectors

COMMENT_RE = re.compile(r"\b(?:comenta|comente|comentem|comentar|digita|digite|escreve|escreva|"
                        r"(?:manda|mande)(?=\s+(?:a|essa)\s+palavra))\b((?:\s+(?:a|essa|esta|aqui|ai|embaixo|nos|"
                        r"comentarios|palavra|so|apenas))*)\s+[\"']?([a-z0-9]{2,20})\b")


def find_comment(sents: list[Sentence]) -> list[Candidate]:
    out = []
    for s in sents:
        for m in COMMENT_RE.finditer(s.ntext):
            key_i = s.word_at(m.start(2))
            word = s.words[key_i]
            keyword = re.sub(r"[^\w]", "", word.text).upper()
            if word.normal_value == "eu" and key_i + 1 < len(s.words) and s.words[key_i + 1].normal_value not in KEYWORD_STOP:
                word = s.words[key_i + 1]          # "comenta EU QUERO"
                keyword = keyword + " " + re.sub(r"[^\w]", "", word.text).upper()
            elif word.normal_value in KEYWORD_STOP:
                continue
            keyword = keyword[:20].strip()
            if not keyword:
                continue
            trigger = s.words[s.word_at(m.start())]
            params = {"keyword": keyword, "prompt_text": "Comente *{keyword}*",
                      "delay": round(min(5.0, max(0.3, word.start - (trigger.start - LEAD))), 2)}
            out.append(Candidate("comment_cta", trigger.start, word.end, params,
                                 original_span(s, m.start(), m.end())))
    return out


def price_value(expr: str) -> tuple[float | None, str]:
    value = vl.parse_money(expr)
    prefix = vl.currency_prefix(expr)
    return value, prefix


def find_price(sents: list[Sentence]) -> list[Candidate]:
    out = []
    for s in sents:
        monies = vl.find_money(s.ntext)
        if not monies:
            continue
        if not PRICE_CONTEXT.search(s.ntext):
            continue
        if any(is_gain(s, m) for m in monies):
            continue          # money someone made is not a price: find_numbers makes it a counter
        used_old = None
        # "de 97 por 37": the old price and the new one
        for a, b in zip(monies, monies[1:]):
            between = s.ntext[a.end:b.start]
            before = s.ntext[max(0, a.start - 4):a.start]
            if re.search(r"\bde\s*$", before) and re.fullmatch(r"\s+(?:por|para|pra)(?:\s+(?:apenas|so))?\s+", between):
                used_old = (a, b)
                break
        if used_old:
            old_m, new_m = used_old
        else:
            new_m, old_m = next((m for m in monies if not m.bare), monies[0]), None
            if new_m.bare and not re.search(r"\b(?:por\s+(?:apenas|so)|apenas|custa\w*)\s+$",
                                            s.ntext[max(0, new_m.start - 16):new_m.start]):
                continue
        price, prefix = price_value(s.ntext[new_m.start:new_m.end])
        if price is None or price <= 0 or price > 1e7:
            continue
        w_new = s.words_in(new_m.start, new_m.end)
        params: dict = {"price": round(price, 2), "prefix": prefix,
                        "label": "only" if re.search(r"\b(apenas|so)\b", s.ntext[:new_m.start]) else ""}
        first = w_new[0].start
        if old_m is not None:
            old, _ = price_value(s.ntext[old_m.start:old_m.end])
            if old is not None and old > price:
                w_old = s.words_in(old_m.start, old_m.end)
                params["old_price"] = round(old, 2)
                first = w_old[0].start
                params["tag_delay"] = round(min(5.0, max(0.45, w_new[0].start - first)), 2)
        cut = max(new_m.end, old_m.end if old_m else 0)
        out.append(Candidate("price_tag", first, w_new[-1].end, params,
                             original_span(s, min(new_m.start, old_m.start if old_m else new_m.start), cut),
                             note='the former price must be factual'))
    return out


NOT_NEED_RE = re.compile(r"\b(?:nao\s+(?:vai\s+)?(?:precisa|precisar|e\s+preciso)(?:\s+mais)?|sem\s+precisar)"
                         r"(?:\s+(?:de|ter\s+que))?\b")


def find_not_needed(sents: list[Sentence]) -> list[Candidate]:
    'Detect a spoken list of unnecessary items, including triggers across adjacent sentences.'
    hits = []          # (sentence, trigger match, end of its clause)
    for s in sents:
        ms = list(NOT_NEED_RE.finditer(s.ntext))
        for k, m in enumerate(ms):
            end = ms[k + 1].start() if k + 1 < len(ms) else len(s.ntext)
            hits.append((s, m, end))
    groups: list[list] = []
    for h in hits:
        t = h[0].words[h[0].word_at(h[1].start())].start
        if groups and t - groups[-1][-1][3] <= 4.0:
            groups[-1].append((*h, t))
        else:
            groups.append([(*h, t)])
    out = []
    for g in groups:
        items: list[tuple[str, float]] = []
        last = 0.0
        for s, m, end, _t in g:
            pieces = split_list(s, m.end(), end)
            for a, b in pieces:
                it = short_item(s, a, b, 4, 32)
                if it and it[0].lower() not in (x[0].lower() for x in items):
                    items.append(it)
                    last = max(last, s.words_in(a, b)[-1].end)
        items = items[:6]
        if len(items) < 2:
            continue
        s0, m0 = g[0][0], g[0][1]
        start = s0.words[s0.word_at(m0.start())].start
        quote = " ".join(original_span(s, m.start(), end) for s, m, end, _ in g)
        out.append(Candidate("strikeout", start, last,
                             {"items": [x[0] for x in items], "title": 'You do *not* need:'},
                             quote, times=[x[1] for x in items]))
    return out


GET_RE = re.compile(r"\b(?:voce\s+)?(?:vai\s+)?(?:recebe|receber|ganha|ganhar|leva|levar|inclui|incluso|encontra|"
                    r"encontrar|"
                    r"tem\s+acesso\s+(?:a|ao|as|aos)|vai\s+ter|tem)\b(?:\s+(?:tambem|ainda|na\s+hora))?")
WITHOUT_RE = re.compile(r"\bsem\s+\w+")


def find_checklist(sents: list[Sentence]) -> list[Candidate]:
    out = []
    for s in sents:
        sems = list(WITHOUT_RE.finditer(s.ntext))
        if len(sems) >= 2:
            items = []
            for k, m in enumerate(sems):
                end = sems[k + 1].start() if k + 1 < len(sems) else len(s.ntext)
                piece = split_list(s, m.start(), end)
                if not piece:
                    continue
                ws = s.words_in(*piece[0])[:3]
                if ws:
                    items.append((title_case(" ".join(w.text for w in ws))[:32], ws[0].start, ws[-1].end))
            if len(items) >= 2:
                out.append(Candidate("checklist", items[0][1], items[-1][2],
                                     {"items": [x[0] for x in items[:6]]},
                                     original_span(s, sems[0].start(), len(s.ntext)), times=[x[1] for x in items[:6]]))
                continue
        m = GET_RE.search(s.ntext)
        if not m:
            generic = find_enumeration(s)
            if generic:
                out.append(generic)
            continue
        pieces = split_list(s, m.end(), len(s.ntext))
        if len(pieces) < 2:
            continue
        items = []
        for a, b in pieces:
            rel = re.search(r"\s(?:que|pra|para|onde)\s", s.ntext[a:b])
            if rel and rel.start() > 0:        # "um mecanismo ... que ajuda a ...": the item ends before the clause
                b = a + rel.start()
            if len(s.words_in(a, b)) > 9:      # a long piece is shortened by short_item; longer is prose
                break
            it = short_item(s, a, b, 4, 32)
            if it:
                items.append((it[0], it[1], s.words_in(a, b)[-1].end))
        if len(items) < 2:
            continue
        title = 'What you *receive*:' if re.search(r"recebe|ganha|leva|inclui|acesso", m.group(0)) else ""
        out.append(Candidate("checklist", items[0][1], items[-1][2],
                             {"items": [x[0] for x in items[:6]], "title": title},
                             original_span(s, m.start(), len(s.ntext)), times=[x[1] for x in items[:6]]))
    return out


def find_enumeration(s: Sentence) -> Candidate | None:
    """A plain list said in one sentence ("o tamanho, o acabamento, os detalhes e o material"): at least two
    commas and three short pieces."""
    if s.ntext.count(",") < 2:
        return None
    items = []
    for a, b in split_list(s, 0, len(s.ntext)):
        ws = s.words_in(a, b)
        if len(ws) > 4:
            return None
        it = short_item(s, a, b, 4, 32)
        if it:
            items.append((it[0], it[1], ws[-1].end))
    if len(items) < 3:
        return None
    items = items[:6]
    return Candidate("checklist", items[0][1], items[-1][2], {"items": [x[0] for x in items]}, s.text.strip(),
                     note="spoken list: check that each item stands on its own", times=[x[1] for x in items])


STEP_RE = re.compile(r"\b(?:primeiro|primeira\s+coisa|passo\s+(?:um|1)|depois|em\s+seguida|segundo|passo\s+(?:dois|2)|"
                     r"terceiro|passo\s+(?:tres|3)|por\s+(?:fim|ultimo)|no\s+final|e\s+pronto)\b")


def find_steps(sents: list[Sentence]) -> list[Candidate]:
    out = []
    # 1. very short sentences in a row: "Compra. Conecta. Usa."
    run: list[Sentence] = []

    def flush() -> None:
        if 2 <= len(run) <= 4:
            items = [title_case(" ".join(w.text for w in x.words))[:20] for x in run]
            if all(items):
                out.append(Candidate("steps", run[0].words[0].start, run[-1].words[-1].end, {"items": items},
                                     " ".join(x.text for x in run), times=[x.words[0].start for x in run]))

    for s in sents:
        short = len(s.words) <= 2 and all(w.normal_value not in FUNCTION_WORDS for w in s.words)
        if short and (not run or s.words[0].start - run[-1].words[-1].end <= 1.2):
            run.append(s)
            continue
        flush()
        run = [s] if short else []
    flush()
    # 2. "primeiro ..., depois ..., por fim ..."
    words = [w for s in sents for w in s.words]
    whole = Sentence(words) if words else None
    if whole is None:
        return out
    marks = list(STEP_RE.finditer(whole.ntext))
    i = 0
    while i < len(marks):
        if not re.match(r"primeir|passo", marks[i].group(0)):
            i += 1
            continue
        chain = [marks[i]]
        j = i + 1
        while j < len(marks) and len(chain) < 4:
            t_prev = whole.words[whole.word_at(chain[-1].start())].start
            t_here = whole.words[whole.word_at(marks[j].start())].start
            if t_here - t_prev > 5.0:
                break
            chain.append(marks[j])
            j += 1
        if len(chain) >= 2:
            items = []
            for k, m in enumerate(chain):
                end = chain[k + 1].start() if k + 1 < len(chain) else min(len(whole.ntext), m.end() + 40)
                ws = [w for w in whole.words_in(m.end(), end) if w.normal_value not in FUNCTION_WORDS][:2]
                if not ws:
                    break
                items.append((title_case(" ".join(w.text for w in ws))[:20], ws[0].start, ws[-1].end))
            if len(items) == len(chain):
                out.append(Candidate("steps", whole.words[whole.word_at(chain[0].start())].start, items[-1][2],
                                     {"items": [x[0] for x in items]},
                                     original_span(whole, chain[0].start(), chain[-1].end() + 30),
                                     times=[x[1] for x in items]))
        i = j if len(chain) >= 2 else i + 1
    return out


PERCENT_RE = re.compile(f'(?<![\\w,.])({vl.DIGITS_RE}|{vl.WORDNUM_RE})\\s*(?:%|por\\s*cento\\b)')
COUNT_RE = re.compile(f"(?<![\\w,.$])(?:mais\\s+de\\s+)?({vl.DIGITS_RE}|{vl.WORDNUM_RE})(?:\\s+({vl.MULT_RE}))?\\s+(?:de\\s+)?({'|'.join(UNITS)})\\b((?:\\s+(?!e\\b|que\\b|com\\b|pra\\b|para\\b)\\w+){{0,3}})")


def find_numbers(sents: list[Sentence]) -> list[Candidate]:
    """Percentages, counts with a unit and money that is not a price (what someone made, spent or has)."""
    out = []
    for s in sents:
        found = []
        for m in PERCENT_RE.finditer(s.ntext):
            value = vl.parse_number(m.group(1))
            if value is None or not 0 < value <= 1000:
                continue
            found.append(("pct", m, value))
        for m in COUNT_RE.finditer(s.ntext):
            value = vl.parse_number(m.group(1))
            if value is None:
                continue
            if m.group(2):
                value *= vl.MULTIPLIERS[m.group(2)]
            if value < 2 or value > 1e9:
                continue
            found.append(("count", m, value))
        monies = vl.find_money(s.ntext)
        price_talk = PRICE_CONTEXT.search(s.ntext) and not any(is_gain(s, mo) for mo in monies)
        for mo in monies if not price_talk else []:
            if mo.value is None or not 0 < mo.value <= 1e12 or (mo.bare and not is_gain(s, mo)):
                continue      # a bare "10 mil" is money only when said as what someone made
            found.append(("money", mo, mo.value))
        if not found:
            continue
        kind, m, value = found[0]
        if kind == "money":
            ws = s.words_in(m.start, m.end)
            _, prefix = price_value(s.ntext[m.start:m.end])
            params = {"value": round(value, 2), "prefix": prefix}
            period = PERIOD_AFTER.match(s.ntext, m.end)
            if period:
                params["label"] = original_span(s, m.end, period.end())[:40]
            out.append(Candidate("counter", ws[0].start, ws[-1].end, params, original_span(s, m.start, m.end)))
            continue
        ws = s.words_in(m.start(), m.end())
        if kind == "pct":
            if value <= 100 and PROGRESS_WORDS.search(s.ntext):
                label = ""
                pm = PROGRESS_WORDS.search(s.ntext)
                if pm:
                    label = title_case(original_span(s, pm.start(), pm.end()))[:30]
                cand = Candidate("progress_bar", ws[0].start, ws[-1].end,
                                 {"value": value, "label": label or 'Completed'}, original_span(s, m.start(), m.end()))
            else:
                cand = Candidate("counter", ws[0].start, ws[-1].end, {"value": value, "suffix": "%"},
                                 original_span(s, m.start(), m.end()))
        else:
            if m.group(3) == "anos" and re.search(r"\b(meus|minha\s+idade|aos|tinha|tenho|com)\s*$",
                                                  s.ntext[:m.start()].rstrip()):
                continue      # "antes dos meus 24 anos", "aos 19 anos": an age, not a result
            unit = UNIT_LABEL.get(m.group(3), m.group(3))
            label = original_span(s, m.end(3), m.end(4)).strip()[:40] if m.group(4).strip() else ""
            params = {"value": value, "suffix": unit}
            if label:
                params["label"] = label
            if re.match(r"mais\s+de", m.group(0)):
                params["prefix"] = "+"
            cand = Candidate("counter", ws[0].start, ws[-1].end, params, original_span(s, m.start(), m.end()))
        out.append(cand)
    return out


# ------------------------------------------------------------------ strong moments (no list, price or number)

# the turn of the story: what comes after these words is the point the viewer must keep
TURN_RE = re.compile(r"\b(?:foi\s+ai\s+que|ate\s+que|so\s+que|mas|porem|o\s+segredo\s+(?:e|foi)|a\s+verdade\s+e|"
                     r"o\s+caminho\s+que|(?:eu\s+)?descobri|a\s+virada|mudou\s+tudo|o\s+problema\s+e)\b")
# the offer: free, unlocked, limited time, bonus, guarantee
OFFER_RE = re.compile(r"\b(gratuit\w*|gratis|de\s+graca|liberad\w*|bonus|exclusiv\w*|"
                      r"(\d+|vinte\s+e\s+quatro)\s+horas|garantia\s+de\s+(\d+|sete|trinta)\s+dias|ultimas?\s+vagas?)\b")
EMPHASIS_RE = re.compile(r"\b(nunca|sempre|ninguem|zero|nada|todos\s+os\s+dias|de\s+verdade|simples|facil|"
                         r"rapido|sozinh\w*)\b")
NUMWORDS = {"sete": "7", "trinta": "30", "vinte e quatro": "24"}
MOMENT_MAX_WORDS = 3
OUTRANKS = 90            # only a moment this strong (a free offer) replaces what the patterns found there


def key_words(s: Sentence, from_word: int, limit: int = MOMENT_MAX_WORDS) -> tuple[list[Word], int] | None:
    """Up to `limit` content words from word index `from_word` (function words skipped at the edges)."""
    ws = [w for w in s.words[from_word:] if w.normal_value]
    while ws and ws[0].normal_value in FUNCTION_WORDS | {"eu", "que", "mas", "foi", "ai", "descobri"}:
        ws = ws[1:]
    ws = ws[:limit]
    while ws and ws[-1].normal_value in FUNCTION_WORDS:
        ws = ws[:-1]
    if not ws:
        return None
    return ws, s.words.index(ws[0])


def impact_text(ws: list[Word]) -> str:
    text = " ".join(w.text for w in ws).strip(" ,.;:!?…").upper()
    words = text.split()
    while len(words) >= 2 and words[0] in ("O", "A", "OS", "AS", "UM", "UMA", "E"):
        words = words[1:]          # "O CAMINHO" -> "CAMINHO": a lone article on its own line reads as a symbol
    if len(words) >= 2:            # the last word (usually the noun) in the accent color
        words[-1] = f'*{words[-1]}*'
    elif words:
        words[0] = f'*{words[0]}*'
    return " ".join(words)[:32]


OFFER_NOUNS = {"aula": "AULA", "aulas": "AULAS", "acesso": "ACESSO", "curso": "CURSO", "mentoria": "MENTORIA",
               "bonus": "BÔNUS", "treinamento": "TREINAMENTO", "material": "MATERIAL", "lista": "LISTA",
               "live": "LIVE", "masterclass": "MASTERCLASS", "ebook": "E-BOOK"}
FREE_RE = re.compile(r"\b(gratuit\w*|gratis|de\s+graca)\b")
HOURS_RE = re.compile(r"\b(\d+|vinte\s+e\s+quatro)\s+horas\b")


def find_offer(s: Sentence) -> Candidate | None:
    """The offer said in the sentence: guarantee -> guarantee_badge; free / unlocked / for N hours -> stamp with
    what is offered ("AULA GRÁTIS", "por 24 horas" underneath). A free offer outranks a price."""
    m = OFFER_RE.search(s.ntext)
    if not m:
        return None
    g = re.search(r"garantia\s+de\s+(\d+|sete|trinta)\s+dias", s.ntext)
    if g:
        k = s.word_at(g.start())
        num = NUMWORDS.get(g.group(1), g.group(1))
        return Candidate("guarantee_badge", s.words[k].start, s.words[min(len(s.words) - 1, k + 3)].end,
                         {"number": num, "unit": "days", "title": "GUARANTEE"}, s.text, score=78)
    noun = next((OFFER_NOUNS[w.normal_value] for w in s.words if w.normal_value in OFFER_NOUNS), "")
    free, hours = FREE_RE.search(s.ntext), HOURS_RE.search(s.ntext)
    if free:
        text_value, key = (f'{noun} GRÁTIS' if noun else "GRÁTIS"), free
    elif re.search(r"\bliberad\w*", s.ntext):
        text_value, key = (f'{noun} LIBERADA' if noun in ("AULA", "AULAS", "LISTA", "MENTORIA", "LIVE") else
                      f'{noun} LIBERADO' if noun else "LIBERADO"), re.search(r"\bliberad\w*", s.ntext)
    elif re.search(r"\bexclusiv\w*|\bultimas?\s+vagas?", s.ntext):
        key = re.search(r"\bexclusiv\w*|\bultimas?\s+vagas?", s.ntext)
        text_value = "EXCLUSIVO" if "exclusiv" in key.group(0) else "ÚLTIMAS VAGAS"
    elif re.search(r"\bbonus\b", s.ntext):
        text_value, key = "BÔNUS", re.search(r"\bbonus\b", s.ntext)
    else:
        return None
    sub = ""
    if hours:
        n = NUMWORDS.get(hours.group(1), hours.group(1))
        sub = f'por {n} horas'
    ends = [x.end() for x in (free, hours) if x] + [key.end()]
    w0, w1 = s.words[s.word_at(key.start())], s.words[s.word_at(max(ends) - 1)]
    return Candidate("stamp", min(w0.start, w1.start), max(w0.end, w1.end),
                     {"text": text_value[:22], "subtitle": sub}, s.text, score=92 if free else 75,
                     note='offer: verify that this matches the actual offer')


def find_moments(sents: list[Sentence]) -> list[Candidate]:
    """The strong points of a talk that has no list, price or number: the offer (stamp / guarantee_badge),
    the turn of the story (impact_word with what comes after "mas", "descobri"...) and short emphatic
    lines."""
    out: list[Candidate] = []
    for s in sents:
        if len(s.words) < 2:
            continue
        offer = find_offer(s)
        if offer:
            out.append(offer)
            continue
        m = TURN_RE.search(s.ntext)
        if m and re.match(r"(o|a)\s", m.group(0)):          # "o caminho que", "o segredo é": the marker is the idea
            ws = [w for w in s.words_in(m.start(), m.end()) if w.normal_value not in ("que", "e", "foi")]
            if ws:
                out.append(Candidate("impact_word", ws[0].start, ws[-1].end, {"text": impact_text(ws)}, s.text,
                                     score=60, note="turn in the speech: rewrite the text if the idea calls for it"))
                continue
        if m:
            got = key_words(s, s.word_at(m.end() - 1) + 1)
            if got and len(got[0]) >= 1:
                ws = got[0]
                out.append(Candidate("impact_word", ws[0].start, ws[-1].end, {"text": impact_text(ws)}, s.text,
                                     score=60, note="turn in the speech: rewrite the text if the idea calls for it"))
                continue
        if len(s.words) <= 8:
            m = EMPHASIS_RE.search(s.ntext)
            if m:
                k = s.word_at(m.start())
                a = max(0, k - 1)
                ws = [w for w in s.words[a:a + MOMENT_MAX_WORDS] if w.normal_value]
                while ws and ws[0].normal_value in FUNCTION_WORDS:
                    ws = ws[1:]
                if ws:
                    out.append(Candidate("impact_word", ws[0].start, ws[-1].end, {"text": impact_text(ws)},
                                         s.text, score=52, note="short, strong phrase"))
    return out


def find_marked(sents: list[Sentence], marks: list[tuple[float, str]], detectors) -> tuple[list[Candidate], list[str]]:
    """One candidate per point marked in the preview: what the detectors find in that sentence, else the key
    words of the sentence as impact_word. Marked candidates are always kept."""
    out, notes = [], []
    for t, asked in marks:
        s = next((x for x in sents if x.words[0].start - 0.4 <= t <= x.words[-1].end + 0.4), None)
        if s is None:
            s = min(sents, key=lambda x: min(abs(x.words[0].start - t), abs(x.words[-1].end - t)), default=None)
        if s is None:
            continue
        found = [c for det in detectors for c in det([s])]
        if found:
            c = max(found, key=lambda c: c.priority)
        else:
            k = min(range(len(s.words)), key=lambda i: abs(s.words[i].start - t))
            got = key_words(s, k) or key_words(s, 0)
            if not got:
                continue
            ws = got[0]
            c = Candidate("impact_word", ws[0].start, ws[-1].end, {"text": impact_text(ws)}, s.text,
                          note="point marked in the preview: fit the text and template to the sentence's idea")
        c.marked = True
        c.note = ("marked in the preview; " + c.note) if c.note else "marked in the preview"
        if asked:
            c.note += f'. REQUEST: "{asked}" (honor the requested change, including choosing another template)'
        out.append(c)
        notes.append(f'point marked at {fmt_time(t)} -> {c.template}')
    return out, notes


def spread(items: list[dict], prio: list[int], duration: float, hook_end: float, n: int) -> list[int]:
    """Indexes of the n strongest items plus every marked one (priority 1000), one automatic item per part of
    the video when possible: the time after the hook is split in n equal parts."""
    marked = [k for k, p in enumerate(prio) if p >= 1000]
    auto = [k for k in range(len(items)) if k not in marked]
    if n <= 0:
        return marked
    if len(auto) <= n:
        return sorted(marked + auto)
    span = max(1e-6, duration - hook_end)
    part = lambda k: min(n - 1, max(0, int((items[k]["start"] - hook_end) / span * n)))  # noqa: E731
    keep: list[int] = []
    used: set[int] = set()
    for k in sorted(auto, key=lambda k: -prio[k]):
        if len(keep) >= n:
            break
        if part(k) not in used:
            keep.append(k)
            used.add(part(k))
    for k in sorted(auto, key=lambda k: -prio[k]):     # parts without a candidate: fill by priority
        if len(keep) >= n:
            break
        if k not in keep:
            keep.append(k)
    return sorted(marked + keep)


# ------------------------------------------------------------------ choosing and timing

def relative_params(c: Candidate, start: float) -> dict:
    p = dict(c.params)
    if c.times:
        rel = [round(max(0.0, t - start), 2) for t in c.times]
        for k in range(1, len(rel)):
            rel[k] = max(rel[k], round(rel[k - 1] + 0.1, 2))
        p["times"] = rel
        if c.template == "strikeout":
            p["cross_times"] = [round(t + 0.45, 2) for t in rel]
    return p


def choose(cands: list[Candidate], duration: float, hook_end: float, cta_start: float | None,
           gap: float, skipped: list[dict]) -> list[dict]:
    picked: list[tuple[Candidate, float, float, dict, str]] = []
    for c in sorted(cands, key=lambda c: (-c.priority, c.start)):
        start = round(max(0.0, c.start - LEAD), 2)
        where = {"time": round(c.start, 2), "quote": c.quote, "template": c.template}
        if start < hook_end:
            skipped.append({**where, "why": f'falls within the hook (until {fmt_time(hook_end)})'})
            continue
        if any(abs(start - other[1]) < gap for other in picked):
            skipped.append({**where, "why": f'too close to another animation (at most one every {gap:g} s)'})
            continue
        params = relative_params(c, start)
        tpl = Rg.get(c.template)
        try:
            resolved = Rg.resolve_params(tpl, params)
        except VisualError as e:
            skipped.append({**where, "why": f'parameters rejected: {e}'})
            continue
        end = max(c.last + READ_AFTER, start + tpl.suggested_duration(resolved))
        if c.times:
            end = max(end, c.times[-1] + 1.6)
        note = c.note
        limit = duration
        if cta_start is not None:
            if c.template == "comment_cta":
                if end > cta_start and start < duration:
                    note = (note + "; " if note else "") + ("the final call to action overlaps this scene: finish the scene before it "
                                                            "or disable the call-to-action card")
            else:
                limit = min(limit, cta_start - 0.2)
        end = round(min(end, limit), 2)
        if end - start < MIN_LENGTH:
            skipped.append({**where, "why": 'insufficient time before the call to action or video ending'})
            continue
        picked.append((c, start, end, params, note))
    picked.sort(key=lambda x: x[1])
    # an item never runs into the next one
    out = []
    for k, (c, start, end, params, note) in enumerate(picked):
        if k + 1 < len(picked):
            end = min(end, round(picked[k + 1][1] - 0.3, 2))
        item = {"template": c.template, "start": start, "end": end, "region": "auto", "sfx": True,
                "params": params,
                "note": f'speech at {fmt_time(c.start)}: "{c.quote}"' + (f'. {note}' if note else "")}
        out.append((item, c.priority))
    return out


def load_marks(edit_dir: Path) -> list[tuple[float, str]]:
    """Points asked for in the preview with "Visual here" (marks with "kind": "motion" in
    <edit>/preview_marks.json) and in <edit>/preview_motion.json: [(time, note)]."""
    out: list[tuple[float, str]] = []
    for name, only_motion in (("preview_marks.json", True), ("preview_motion.json", False)):
        path = edit_dir / name
        data = load_json(path) if path.is_file() else None
        for m in (data or {}).get("marks") or [] if isinstance(data, dict) else []:
            if isinstance(m, dict):
                if only_motion and m.get("kind") != "motion":
                    continue
                t = m.get("in", m.get("time"))
                note = str(m.get("note") or "").strip()
            else:
                t, note = m, ""
            try:
                out.append((float(t), note))
            except (TypeError, ValueError):
                continue
    return sorted(set(out))


def suggest(edit_dir: Path, gap: float = MIN_GAP, count_value: int | None = None, theme_value: str | None = None) -> dict:
    edit_dir = Path(edit_dir).expanduser().resolve()
    if not edit_dir.is_dir():
        raise SuggestError(f'folder not found: {edit_dir}')
    words, notes = load_words(edit_dir)
    timeline = load_json(edit_dir / "cut_timeline.json") if (edit_dir / "cut_timeline.json").is_file() else None
    duration = float((timeline or {}).get("duration") or 0.0) or max(w.end for w in words)
    hook_end, cta_start, facts = edit_windows(edit_dir, duration)
    sents = sentences_of(words)
    skipped: list[dict] = []
    detectors = [find_comment, find_price, find_not_needed, find_checklist, find_steps, find_numbers, find_moments]
    pattern = (find_comment(sents) + find_price(sents) + find_not_needed(sents) + find_checklist(sents)
               + find_steps(sents) + find_numbers(sents))
    # a strong moment only where the patterns found nothing in the same stretch, unless it outranks them
    # (a free offer beats the price said right before it; a list with a guarantee stays a list)
    cands = list(pattern)
    for mo in find_moments(sents):
        rivals = [p for p in cands if p.start - 0.5 <= mo.last and mo.start <= p.last + 0.5]
        if not rivals or (mo.priority >= OUTRANKS and all(mo.priority > p.priority for p in rivals)):
            cands = [p for p in cands if p not in rivals] + [mo]
    marks = load_marks(edit_dir)
    marked, mark_notes = find_marked(sents, marks, detectors)
    notes += mark_notes
    # a marked point replaces what the detectors found in the same sentence
    cands = [c for c in cands if not any(abs(c.start - m.start) < 0.05 or m.start <= c.start <= m.last
                                         for m in marked)] + marked
    chosen = choose(cands, duration, hook_end, cta_start, gap, skipped)
    if count_value is not None:
        keep = spread([it for it, _ in chosen], [p for _, p in chosen], duration, hook_end, count_value)
        for k, (it, _) in enumerate(chosen):
            if k not in keep:
                skipped.append({"time": it["start"], "quote": it["note"][:80], "template": it["template"],
                                "why": f'not selected: you requested {count_value} animations and the others rank higher'})
        chosen = [chosen[k] for k in keep]
    motion = [it for it, _ in chosen]
    if theme_value:
        for it in motion:
            it["params"].setdefault("theme", theme_value)
    return {"edit_dir": str(edit_dir), "duration": round(duration, 3), "hook_end": round(hook_end, 3),
            "cta_start": round(cta_start, 3) if cta_start is not None else None, "edit_data": facts["edit_data"],
            "marks": [{"time": t, "note": n} for t, n in marks], "motion": motion, "skipped": sorted(skipped, key=lambda x: x["time"]), "notes": notes}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description='Suggest visual animations from recorded speech. Does not modify edit_data.json.')
    ap.add_argument("--edit-dir", required=True, help='edit folder containing transcripts/cut.json', dest='edit_dir')
    ap.add_argument("--json", action="store_true", help='JSON only, without the readable summary', dest='json')
    ap.add_argument("--interval", type=float, default=MIN_GAP, help=f'seconds between animations (default {MIN_GAP:g})', dest='interval')
    ap.add_argument("--count", type=int, default=None,
                    help='maximum number of strongest animations, spread across the video; preview marks are always included. Defaults to the configured count, or all', dest='count_value')
    ap.add_argument("--theme", choices=list(ME.THEME_NAMES), default=None,
                    help='animation theme (default: the configured visual theme)', dest='theme_value')
    ap.add_argument("-o", "--output", help='also write JSON to this file', dest='output_value')
    args = ap.parse_args(argv)
    try:
        if not 1.0 <= args.interval <= 60.0:
            raise SuggestError('--interval must be between 1 and 60 seconds')
        ed = load_json(Path(args.edit_dir) / "edit_data.json") if (Path(args.edit_dir) / "edit_data.json").is_file() else None
        mcfg = ed.get("motion") if isinstance(ed, dict) and isinstance(ed.get("motion"), dict) else {}
        count_value = args.count_value if args.count_value is not None else mcfg.get("count")
        result = suggest(Path(args.edit_dir), args.interval, int(count_value) if count_value else None,
                         args.theme_value or mcfg.get("theme"))
    except SuggestError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    if not args.json:
        for note in result["notes"]:
            print(f'notice: {note}')
        if not result["motion"]:
            print('no animation suggested: no list, price, sequence, number, offer, change, or comment request detected')
        for k, item in enumerate(result["motion"], start=1):
            print(f"suggestion {k}: {item['template']} from {fmt_time(item['start'])} to {fmt_time(item['end'])} · {item['note']}")
        for sk in result["skipped"]:
            print(f'''excluded: {fmt_time(sk['time'])} "{sk['quote'][:70]}" · {sk['why']}''')
        print('These are suggestions. Review each item against the recording and apply the choices within the authorized scope.')
    text = json.dumps(result, ensure_ascii=False)
    if args.output_value:
        out = Path(args.output_value).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not args.json:
            print(f'ok: {out}')
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
