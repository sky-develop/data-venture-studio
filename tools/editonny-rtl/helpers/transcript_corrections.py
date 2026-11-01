"""Shared timed text corrections for native captions and subtitle delivery."""
import re
import unicodedata

def _norm(s):
    s = s.strip().lower().strip('.,!?…:;"\'()«»“”')
    return ''.join((c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn'))
END_PUNCTUATION = '.,!?…:;'

def _split(text):
    """Split."""
    return [x for x in re.split('[ \\t\\r\\n]+', str(text).strip()) if x]

def correct_words(words, fix):
    """Apply longest transcript corrections first, preserving punctuation and timing. Replacement words share the original interval by character count; nonbreaking spaces keep a phrase together."""
    if not fix:
        return words
    rules = []
    for (k, v) in fix.items():
        parts = _split(k)
        if parts:
            rules.append((parts, str(v)))
    rules.sort(key=lambda r: -len(r[0]))
    (out, i) = ([], 0)
    while i < len(words):
        for (parts, v) in rules:
            n = len(parts)
            interval = words[i:i + n]
            if len(interval) < n:
                continue
            exact = n == 1 and interval[0]['text'] == parts[0]
            if not exact and (not all((_norm(w['text']) == _norm(p) for (w, p) in zip(interval, parts)))):
                continue
            new = _split(v)
            if new and (not exact):
                ending_punctuation = ''
                last = interval[-1]['text']
                while last and last[-1] in END_PUNCTUATION:
                    ending_punctuation = last[-1] + ending_punctuation
                    last = last[:-1]
                if ending_punctuation and new[-1][-1] not in END_PUNCTUATION:
                    new[-1] += ending_punctuation
            if len(new) == n:
                out += [dict(w, text=t) for (w, t) in zip(interval, new)]
            elif new:
                (a, z) = (interval[0]['start'], interval[-1]['end'])
                total = sum((max(1, len(t)) for t in new))
                t0 = a
                for t in new:
                    t1 = t0 + (z - a) * max(1, len(t)) / total
                    out.append(dict(text=t, start=round(t0, 3), end=round(t1, 3)))
                    t0 = t1
            i += n
            break
        else:
            out.append(words[i])
            i += 1
    return out
