'Read numbers and money in recorded speech.\n\nNormalize accents and case and parse numeric notation and supported English, Portuguese, and Spanish number words. User-language lexicons are data and remain independent of the product interface language. Distinguish counts from money using currency context.\n\nparse_number(text) returns a number or None; parse_money(text) returns a monetary amount or None; find_money(normalized_text) returns spans with start, end, amount, and context. Public regex components allow intent detectors to reuse the supported grammar.\n\nKeep actual spoken values and currency semantics; never replace them with demonstration amounts or change decimal interpretation based only on the English interface.'

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ------------------------------------------------------------------ normalization

_PUNCT_MAP = {
    "‘": "'", "’": "'", "´": "'", "`": "'",
    "“": '"', "”": '"', "–": "-", "—": "-",
    " ": " ", " ": " ", "…": "...", "*": "",
}


def normalize(text: str) -> tuple[str, list[int]]:
    """Lowercase + strip accents. Returns (normalized, index map norm_pos -> original_pos)."""
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        mapped = _PUNCT_MAP.get(ch)
        pieces = mapped if mapped is not None else unicodedata.normalize("NFKD", ch)
        for piece in pieces:
            if unicodedata.combining(piece):
                continue
            for c in piece.lower():
                out.append(c)
                index.append(i)
    return "".join(out), index


def normal_value(text: str) -> str:
    return normalize(text)[0]


# ------------------------------------------------------------------ numbers & money

WORD_NUMBERS = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
    "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13,
    "quatorze": 14, "catorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17,
    "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30, "quarenta": 40,
    "cinquenta": 50, "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90,
    "cem": 100, "cento": 100, "duzentos": 200, "duzentas": 200, "trezentos": 300,
    "quatrocentos": 400, "quinhentos": 500, "seiscentos": 600, "setecentos": 700,
    "oitocentos": 800, "novecentos": 900, "meio": 0.5, "meia": 0.5,
}
# "milão" is spoken slang for one million ("tá fazendo milão")
MULTIPLIERS = {"mil": 1_000, "k": 1_000, "milhao": 1_000_000, "milhoes": 1_000_000, "milao": 1_000_000,
               "miloes": 1_000_000, "mi": 1_000_000, "bilhao": 1_000_000_000, "bilhoes": 1_000_000_000,
               "bi": 1_000_000_000}

# Language lexicons describe recorded speech, not product interface strings.
WORD_NUMBERS.update({
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "half": 0.5,
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "veinte": 20, "treinta": 30,
    "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80,
    "noventa": 90, "cien": 100, "ciento": 100,
})
MULTIPLIERS.update({"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000,
                    "millon": 1_000_000, "millones": 1_000_000})

WORD_NUMBERS.update({"un": 1, "una": 1, "medio": .5, "media": .5,
                     "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
                     "veintiuno": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24,
                     "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
                     "veintinueve": 29, "doscientos": 200, "trescientos": 300, "cuatrocientos": 400,
                     "quinientos": 500, "seiscientos": 600, "setecientos": 700,
                     "ochocientos": 800, "novecientos": 900})
_WORDNUM_ALT = "|".join(sorted(WORD_NUMBERS, key=len, reverse=True))
MULT_RE = "(?:" + "|".join(sorted(MULTIPLIERS, key=len, reverse=True)) + ")"
# Match grouping before decimals so a thousands separator cannot truncate a value.
DIGITS_RE = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?!\d)"
_WORD_ATOM = rf"(?:{_WORDNUM_ALT}|{MULT_RE})\b"
WORDNUM_RE = rf"{_WORD_ATOM}(?:[ -]+(?:(?:e|and|y)\s+)?(?:a\s+)?{_WORD_ATOM})*"
_NUMBER_ATOM = rf"(?:{DIGITS_RE}|{_WORD_ATOM})"
NUM_RE = rf"{_NUMBER_ATOM}(?:[ -]+(?:(?:e|and|y)\s+)?(?:a\s+)?{_NUMBER_ATOM})*"
CUR_RE = r"(?:dollars?|pounds?|reais|real|contos?|pilas?|dolares|dolar|euros?|usd|brl|gbp)"
DIGIT_COUNT_RE = r"(?:cinco|seis|sete|oito|nove|5|6|7|8|9)\s+digitos\b"
# Explicit currencies take precedence; bare scaled numbers still need context downstream.
MONEY_RE = (
    rf"(?:(?:r\$|us\$|u\$|\$|€|£)\s*{NUM_RE}(?:\s+(?:de\s+)?{CUR_RE}\b)?"
    rf"|(?<![\w$]){NUM_RE}\s+(?:de\s+)?{CUR_RE}\b"
    rf"|(?<![\w$]){NUM_RE}\s*{MULT_RE}\b(?:[ -]+(?:(?:e|and|y)\s+)?(?:a\s+)?{_NUMBER_ATOM})*"
    rf"|\b{MULT_RE}\b(?:[ -]+(?:(?:e|and|y)\s+)?(?:a\s+)?{_NUMBER_ATOM})*|(?<![\w$]){DIGIT_COUNT_RE})"
)
_MONEY_COMPILED = re.compile(MONEY_RE)

# Nouns that make a bare "10 mil" a count, not money.
NON_MONEY_NOUNS = re.compile(
    r"\s*(?:de\s+)?(?:followers?|subscribers?|students?|people|customers?|units?|years?|hours?|seguidor\w*|inscrit\w*|visualiza\w*|views?|curtidas?|likes?|pessoas?|alun\w*|"
    r"clientes?|leads?|vendas?|pedidos?|unidades?|produtos?|pecas?|metros?|km|quilos?|kg|horas?|"
    r"minutos?|dias?|anos?|mensage\w*|comentarios?|compartilhament\w*|downloads?|acessos?|cliques?|"
    r"impress\w*|membros?|usuari\w*|contatos?|numeros?|videos?|posts?|lojas?|fornecedor\w*|itens|"
    r"copias?|livros?|pacotes?|caixas?|vezes|passos|reels|stories|grupos?|mulheres|homens|familias?)\b"
)


@dataclass
class Money:
    start: int
    end: int
    value: float | None
    bare: bool  # no currency marker (R$, reais...): needs context to count as money


def _numeric_literal(token: str) -> float | None:
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", token):
        return float(token.replace(",", ""))
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", token):
        return float(token.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d+(?:[.,]\d{1,2})?", token):
        return float(token.replace(",", "."))
    return None


def parse_number(token: str) -> float | None:
    """Read grouped digits or a compound spoken number without changing its magnitude."""
    token = normal_value(token).strip()
    literal = _numeric_literal(token)
    if literal is not None:
        return literal
    words = [w for w in re.split(r"[ -]+", token) if w not in {"e", "and", "y", "a"}]
    if not words or any(w not in WORD_NUMBERS and w not in MULTIPLIERS and _numeric_literal(w) is None for w in words):
        return None
    total, group, last_scale = 0.0, 0.0, 1.0
    for i, word in enumerate(words):
        if word == "hundred":
            group = (group or 1.0) * 100
        elif word in MULTIPLIERS:
            scale = float(MULTIPLIERS[word])
            if scale > last_scale and total:
                total = (total + group) * scale
            else:
                total += (group or 1.0) * scale
            group, last_scale = 0.0, scale
        else:
            value = WORD_NUMBERS.get(word)
            if value is None:
                value = _numeric_literal(word)
            # A terminal half after a scale means half of that scale, not half a unit.
            group += value * last_scale if value == .5 and i == len(words) - 1 and not group else value
    return total + group


def parse_money(text: str) -> float | None:
    """Read the amount in its recorded currency units, preserving grouped decimals."""
    text = normal_value(text)
    text = re.sub(r"(?:r\$|us\$|u\$|\$|€|£)", " ", text)
    text = re.sub(rf"\b(?:de\s+)?{CUR_RE}\b", " ", text).strip()
    if re.fullmatch(DIGIT_COUNT_RE, text):
        first = text.split()[0]
        count = WORD_NUMBERS.get(first) if first in WORD_NUMBERS else float(first)
        return float(10 ** (int(count) - 1))
    return parse_number(text)


def currency_prefix(text: str) -> str:
    """Preserve explicit currency; a bare amount carries no inferred currency symbol."""
    text = normal_value(text)
    if re.search(r"r\$|\b(?:brl|reais|real|contos?|pilas?)\b", text):
        return "R$"
    if re.search(r"us\$|u\$|\busd\b", text):
        return "US$"
    if "€" in text or re.search(r"\beuros?\b", text):
        return "€"
    if "£" in text or re.search(r"\b(?:pounds?|gbp)\b", text):
        return "£"
    if "$" in text or re.search(r"\b(?:dollars?|dolares|dolar)\b", text):
        return "$"
    return ""


def find_money(ntext: str) -> list[Money]:
    """Money spans in normalized text."""
    found: list[Money] = []
    for m in _MONEY_COMPILED.finditer(ntext):
        s = m.group(0).rstrip()
        start, end = m.start(), m.start() + len(s)
        bare = not re.search(f'(?:r\\$|\\$|€|£|\\b{CUR_RE}\\b)', s)
        if re.search(r"digitos$", s):
            if re.search(r"\b(?:senha|codigo|numero|pin|token|cep|cpf|cnpj|telefone|celular|chave)\s+(?:\w+\s+)?(?:de|com)\s+$",
                         ntext[max(0, start - 30):start]):
                continue  # A six-digit password is a count, not money.
            bare = False
        counted = NON_MONEY_NOUNS.match(ntext, end) if bare else None
        if counted and not re.match(r"\s+(?:passad[oa]|retrasad[oa]|atras)\b", ntext[counted.end():]):
            continue  # "10 mil seguidores" is a count ("1 milhão ano passado" is still money)
        if bare and re.fullmatch(r"\d+\s*(?:mi|bi)", s):  # "5 mi" is too ambiguous alone
            continue
        if s in ("milao", "miloes") and re.search(r"\b(?:em|de|para|pra|pro|a|ao|da|do|na|no|via|ate)\s+$",
                                                  ntext[max(0, start - 8):start]):
            continue  # "fui pra Milão": the city, not the slang
        found.append(Money(start, end, parse_money(s), bare))
    return found
