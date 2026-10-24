#!/usr/bin/env python3
"""
Local Arabic Document Entity, TF-IDF, N-gram, Sentiment & Cross-Document Linker
Inspired by dlinker.ca (InfoTimes) — 100% local, unlimited files, evidence-backed.
"""
import argparse
import collections
import csv
import html
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

AR_DIACRITICS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0670\u0640]")
AR_WORD_RE = re.compile(r"[\u0621-\u064A]{2,}")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\!\؟\?\n؛])\s+")

STOPWORDS = {
    "في", "من", "الى", "إلى", "على", "عن", "مع", "هذا", "هذه", "ذلك", "تلك",
    "الذي", "التي", "الذين", "اللذين", "اللتين", "كان", "كانت", "يكون", "تكون",
    "ان", "أن", "إن", "لا", "لم", "لن", "ما", "هو", "هي", "هم", "هن", "نحن",
    "انت", "أنت", "انتم", "بين", "كل", "بعض", "غير", "أو", "او", "ثم", "بل",
    "قد", "لقد", "حيث", "كما", "ايضا", "أيضا", "أي", "اي", "عبر", "خلال",
    "بعد", "قبل", "عند", "منذ", "حتى", "فوق", "تحت", "امام", "أمام", "نحو",
    "حول", "دون", "بدون", "ضد", "لدى", "لدي", "وهو", "وهي", "وفي", "ومن",
    "وعلى", "وأن", "وان", "فإن", "فان", "التي", "الذي", "به", "بها", "فيها",
    "فيه", "منها", "منه", "عنها", "عنه", "لها", "له", "لهم", "إليها", "إليه",
    "عليها", "عليه", "تم", "يتم", "تتم", "اكثر", "أكثر", "اقل", "أقل", "جدا",
}

POS_CUES = {
    "نجاح", "نمو", "تقدم", "ربح", "أرباح", "ارباح", "إيجابي", "ايجابي", "تفوق",
    "تحسن", "ارتفاع", "ممتاز", "قوي", "فرصة", "فرص", "تطوير", "ابتكار", "دعم",
    "تعاون", "اتفاق", "إنجاز", "انجاز", "كفاءة", "موثوق", "آمن", "استقرار", "عائد",
}
NEG_CUES = {
    "خسارة", "خسائر", "فشل", "تراجع", "انخفاض", "أزمة", "ازمة", "خطر", "مخاطر",
    "تهديد", "اختراق", "ثغرة", "فساد", "تلاعب", "نزاع", "خلاف", "غرامة", "عقوبة",
    "سلبي", "ضعف", "عجز", "مديونية", "ديون", "تعثر", "إلغاء", "الغاء", "تأخير",
}

ORG_PREFIXES = (
    "شركة", "مؤسسة", "بنك", "مصرف", "مصنع", "وزارة", "هيئة", "جمعية", "منظمة",
    "معهد", "جامعة", "مركز", "مجموعة", "ائتلاف", "صندوق", "إدارة", "ادارة", "مكتب",
)
PERSON_TITLES = (
    "السيد", "السيدة", "الدكتور", "د.", "المهندس", "م.", "الأستاذ", "الاستاذ",
    "المحامي", "المدير", "الرئيس", "الشيخ", "القاضي", "الوزير", "المستشار",
)
PERSON_ROLES_AFTER = (
    "مدير", "شريك", "مؤسس", "رئيس", "مفوض", "ممثل", "الموقع", "الطرف الأول", "الطرف الثاني",
)
LOC_PREFIXES = (
    "مدينة", "محافظة", "منطقة", "حي", "شارع", "ميدان", "ميناء", "مطار", "دولة",
    "جمهورية", "مملكة", "إمارة",
)
KNOWN_LOCS = {
    "القاهرة", "الرياض", "دبي", "أبوظبي", "الدوحة", "إسطنبول", "اسطنبول", "أنقرة",
    "عمان", "بيروت", "دمشق", "بغداد", "الخرطوم", "تونس", "الجزائر", "الرباط",
    "الكويت", "المنامة", "مسقط", "غزة", "القدس", "الإسكندرية", "جدة", "مكة",
    "المعادي", "أكتوبر", "مصر", "السعودية", "الإمارات", "قطر", "تركيا", "الأردن",
}


def normalize_ar(word: str) -> str:
    w = AR_DIACRITICS.sub("", word.strip())
    w = re.sub(r"[أإآ]", "ا", w)
    w = re.sub(r"ة\b", "ه", w)
    w = re.sub(r"ى\b", "ي", w)
    for pref in ("وال", "بال", "كال", "فال", "لل", "ال"):
        if w.startswith(pref) and len(w) - len(pref) >= 3:
            w = w[len(pref):]
            break
    return w


def read_any_file(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in (".txt", ".md", ".csv", ".json"):
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    if ext == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(path)
            return "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception:
            out = subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True)
            return out.stdout
    if ext == ".docx":
        import zipfile, xml.etree.ElementTree as ET
        with zipfile.ZipFile(path) as z:
            xml_content = z.read("word/document.xml")
        tree = ET.fromstring(xml_content)
        return " ".join(t.text for t in tree.iter() if t.text)
    if ext in (".jpg", ".jpeg", ".png", ".webp"):
        swift_code = f'''
import Foundation
import Vision
import AppKit
let url = URL(fileURLWithPath: "{path}")
if let img = NSImage(contentsOf: url), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) {{
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["ar-SA", "ar", "en-US"]
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    for r in (req.results ?? []) {{
        if let s = r.topCandidates(1).first?.string {{ print(s) }}
    }}
}}
'''
        res = subprocess.run(["swift", "-e", swift_code], capture_output=True, text=True)
        return res.stdout
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def extract_entities(text: str):
    entities = {
        "orgs": collections.Counter(),
        "persons": collections.Counter(),
        "locations": collections.Counter(),
        "dates": collections.Counter(),
        "numbers": collections.Counter(),
        "contacts": collections.Counter(),
    }
    clean = AR_DIACRITICS.sub("", text)

    BOUNDARY_WORDS = {
        "في", "من", "الى", "إلى", "على", "عن", "مع", "بتاريخ", "لصالح", "أمام", "امام",
        "لتوريد", "وتحقيق", "بقيمة", "برأس", "مديرا", "مديراً", "بنسبة", "بعد", "حيث",
        "بالتوقيع", "ومفوضا", "ومفوضاً", "ويشاركه", "تحقيق", "حساب", "توزيع", "تنفيذ",
    }

    def trim_phrase(words_list, max_len=4):
        out = []
        for w in words_list[:max_len]:
            if w in BOUNDARY_WORDS or w in STOPWORDS:
                break
            out.append(w)
        return " ".join(out)

    # Organizations: prefix + 1..4 Arabic words stopping at boundary words
    org_pat = r"\b(" + "|".join(ORG_PREFIXES) + r")\s+([\u0621-\u064A]+(?:\s+[\u0621-\u064A]+){0,4})"
    for m in re.finditer(org_pat, clean):
        rest = trim_phrase(m.group(2).split(), 4)
        if rest:
            entities["orgs"][f"{m.group(1)} {rest}"] += 1

    # Persons: after title OR 3-part Arabic proper names containing عبد/بن/أبو
    title_pat = r"(?:" + "|".join(re.escape(t) for t in PERSON_TITLES) + r")\s+([\u0621-\u064A]{3,}(?:\s+[\u0621-\u064A]{3,}){1,3})"
    for m in re.finditer(title_pat, clean):
        p = trim_phrase(m.group(1).split(), 4)
        if len(p.split()) >= 2:
            entities["persons"][p] += 1

    for m in re.finditer(r"\b([\u0621-\u064A]{3,}\s+عبد\s+[\u0621-\u064A]{3,}(?:\s+[\u0621-\u064A]{3,})?)\b", clean):
        entities["persons"][m.group(1).strip()] += 1

    # Locations: prefix + 1..3 words OR known locations
    loc_pat = r"\b(?:" + "|".join(LOC_PREFIXES) + r")\s+[\u0621-\u064A0-9]+(?:\s+[\u0621-\u064A0-9]+){0,2}"
    for m in re.finditer(loc_pat, clean):
        entities["locations"][m.group(0).strip()] += 1
    for w in AR_WORD_RE.findall(clean):
        if w in KNOWN_LOCS or (w.startswith("ال") and w in KNOWN_LOCS):
            entities["locations"][w] += 1

    # Dates: YYYY-MM-DD, DD/MM/YYYY, or Arabic months
    date_pat = r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{1,2}\s+(?:يناير|فبراير|مارس|أبريل|ابريل|مايو|يونيو|يوليو|أغسطس|اغسطس|سبتمبر|أكتوبر|اكتوبر|نوفمبر|ديسمبر|محرم|رمضان|شوال|ذو الحجة)(?:\s+\d{4})?)\b"
    for m in re.finditer(date_pat, clean):
        entities["dates"][m.group(0).strip()] += 1

    # Financial amounts & numbers
    num_pat = r"\b\d[\d,\.]*\s*(?:مليون|مليار|ألف|الف|جنيه|ريال|دولار|درهم|ليرة|دينار|يورو|%|في المئة|بالمئة)\b"
    for m in re.finditer(num_pat, clean):
        entities["numbers"][m.group(0).strip()] += 1

    # Contacts & URLs
    for m in re.finditer(r"(?:https?://\S+|[\w\.-]+@[\w\.-]+\.\w+|\+?\d[\d\s\-]{8,14}\d)", clean):
        entities["contacts"][m.group(0).strip()] += 1

    return {k: dict(v.most_common(30)) for k, v in entities.items()}


def analyze_corpus(paths: list[str], out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    docs = []
    df_counter = collections.Counter()

    for p in paths:
        raw = read_any_file(p)
        sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(raw) if len(s.strip()) > 10]
        words = [w for w in AR_WORD_RE.findall(AR_DIACRITICS.sub("", raw)) if w not in STOPWORDS and normalize_ar(w) not in STOPWORDS and len(w) >= 3]
        norm_to_surface = {}
        tf = collections.Counter()
        for w in words:
            nw = normalize_ar(w)
            if len(nw) < 3 or nw in STOPWORDS:
                continue
            tf[nw] += 1
            norm_to_surface.setdefault(nw, w)

        for nw in set(tf.keys()):
            df_counter[nw] += 1

        bigrams = collections.Counter(" ".join(words[i:i+2]) for i in range(len(words)-1))
        trigrams = collections.Counter(" ".join(words[i:i+3]) for i in range(len(words)-2))

        pos_hits = [w for w in words if w in POS_CUES or normalize_ar(w) in POS_CUES]
        neg_hits = [w for w in words if w in NEG_CUES or normalize_ar(w) in NEG_CUES]
        score = len(pos_hits) - len(neg_hits)
        label = "إيجابي" if score > 1 else ("سلبي" if score < -1 else "محايد")

        ents = extract_entities(raw)
        docs.append({
            "name": os.path.basename(p),
            "path": os.path.abspath(p),
            "char_count": len(raw),
            "word_count": len(words),
            "sentences": sentences,
            "tf": tf,
            "norm_to_surface": norm_to_surface,
            "entities": ents,
            "top_bigrams": dict(bigrams.most_common(15)),
            "top_trigrams": dict(trigrams.most_common(10)),
            "sentiment": {
                "label": label,
                "score": score,
                "positive_cues": dict(collections.Counter(pos_hits).most_common(10)),
                "negative_cues": dict(collections.Counter(neg_hits).most_common(10)),
            },
        })

    N = max(1, len(docs))
    for d in docs:
        total_w = max(1, sum(d["tf"].values()))
        tfidf = {}
        for nw, count in d["tf"].items():
            idf = math.log(1.0 + N / (1.0 + df_counter[nw])) + 1.0
            tfidf[d["norm_to_surface"][nw]] = round((count / total_w) * idf, 4)
        d["top_tfidf"] = dict(sorted(tfidf.items(), key=lambda x: x[1], reverse=True)[:20])

    # Cross-document shared entities & keywords with verbatim KWIC quotes
    item_docs = collections.defaultdict(lambda: {"type": "", "surface": "", "occurrences": []})
    for d in docs:
        # Register entities
        for etype, edict in d["entities"].items():
            for surface, cnt in edict.items():
                key = f"ent:{etype}:{normalize_ar(surface)}"
                item_docs[key]["type"] = etype
                item_docs[key]["surface"] = surface
                quotes = [s for s in d["sentences"] if surface in s][:3]
                item_docs[key]["occurrences"].append({
                    "doc": d["name"],
                    "count": cnt,
                    "quotes": quotes,
                })
        # Register top keywords
        for surface, weight in list(d["top_tfidf"].items())[:15]:
            nw = normalize_ar(surface)
            key = f"kw:{nw}"
            if not item_docs[key]["surface"]:
                item_docs[key]["type"] = "keyword"
                item_docs[key]["surface"] = surface
            quotes = [s for s in d["sentences"] if surface in s][:2]
            item_docs[key]["occurrences"].append({
                "doc": d["name"],
                "weight": weight,
                "quotes": quotes,
            })

    shared_links = []
    for key, info in item_docs.items():
        doc_names = sorted({o["doc"] for o in info["occurrences"]})
        if len(doc_names) >= 2 or (len(docs) == 1 and info["type"] != "keyword"):
            shared_links.append({
                "term": info["surface"],
                "category": info["type"],
                "documents": doc_names,
                "evidence": info["occurrences"],
            })

    shared_links.sort(key=lambda x: (x["category"] == "keyword", -len(x["documents"])))

    report = {
        "document_count": len(docs),
        "documents": [
            {k: v for k, v in d.items() if k not in ("tf", "norm_to_surface", "sentences")}
            for d in docs
        ],
        "shared_links": shared_links,
    }

    json_path = os.path.join(out_dir, "report.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    csv_path = os.path.join(out_dir, "entities.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["المستند", "نوع الكيان", "الكيان", "التكرار", "الاقتباس من المصدر"])
        for d in docs:
            for etype, edict in d["entities"].items():
                for surface, cnt in edict.items():
                    q = next((s for s in d["sentences"] if surface in s), "")
                    w.writerow([d["name"], etype, surface, cnt, q])

    html_path = os.path.join(out_dir, "network.html")
    render_html(report, html_path)
    return {"json": json_path, "csv": csv_path, "html": html_path, "shared_count": len(shared_links)}


def render_html(report: dict, out_path: str):
    cards_html = []
    type_labels = {
        "orgs": "منظمة / جهة",
        "persons": "شخص",
        "locations": "مكان",
        "dates": "تاريخ",
        "numbers": "مبلغ / رقم",
        "contacts": "تواصل / رابط",
        "keyword": "كلمة مفتاحية مشتركة",
    }
    for link in report["shared_links"][:30]:
        tlabel = type_labels.get(link["category"], link["category"])
        ev_items = []
        for ev in link["evidence"]:
            qs = "".join(f"<blockquote style='margin:6px 0;padding:8px 12px;background:#111927;border-right:3px solid #38bdf8;border-radius:6px;font-size:13px;color:#e2e8f0;'>«{html.escape(q)}»</blockquote>" for q in ev.get("quotes", []))
            ev_items.append(f"<div style='margin-top:8px;'><strong style='color:#7dd3fc;'>📄 {html.escape(ev['doc'])}</strong>{qs}</div>")
        cards_html.append(f"""
        <div style="background:#1e293b;border:1px solid #334155;border-radius:12px;padding:16px;margin-bottom:14px;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:18px;font-weight:700;color:#f8fafc;">🔗 {html.escape(link['term'])}</span>
            <span style="background:#0f172a;color:#38bdf8;padding:4px 10px;border-radius:999px;font-size:12px;">{html.escape(tlabel)}</span>
          </div>
          {''.join(ev_items)}
        </div>
        """)

    page = f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<title>تقرير ربط الوثائق والكيانات العربية — Local dlinker</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; background:#0f172a; color:#f8fafc; margin:0; padding:32px; line-height:1.65; }}
  .container {{ max-width: 1080px; margin: 0 auto; }}
  h1 {{ font-size: 26px; margin-bottom: 6px; }}
  .sub {{ color:#94a3b8; font-size:14px; margin-bottom:24px; }}
  .grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap:16px; margin-bottom:28px; }}
  .box {{ background:#1e293b; border:1px solid #334155; border-radius:12px; padding:16px; }}
  .chip {{ display:inline-block; background:#0f172a; border:1px solid #334155; padding:3px 9px; border-radius:999px; font-size:12px; margin:3px; color:#cbd5e1; }}
</style>
</head>
<body>
<div class="container">
  <h1>تحليل وربط الوثائق العربية بالأدلة النصية (Local dlinker)</h1>
  <div class="sub">عدد المستندات المحللة: {report['document_count']} · الروابط والكيانات المكتشفة: {len(report['shared_links'])} · وجود اسمين معاً لا يكفي وحده لإثبات علاقة بينهما دون مراجعة الاقتباس المسند.</div>
  <div class="grid">
    {''.join(f"<div class='box'><h3>📄 {html.escape(d['name'])}</h3><div>الكلمات: {d['word_count']} · المشاعر: <b>{d['sentiment']['label']}</b> ({d['sentiment']['score']})</div><div style='margin-top:8px;'><b>أهم كلمات TF-IDF:</b><br>{''.join(f'<span class=chip>{html.escape(k)} ({v})</span>' for k,v in list(d['top_tfidf'].items())[:8])}</div></div>" for d in report['documents'])}
  </div>
  <h2>من الاسم المشترك إلى العلاقة التي يسندها المستند</h2>
  {''.join(cards_html)}
</div>
</body>
</html>"""
    Path(out_path).write_text(page, encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("files", nargs="+")
    args = ap.parse_args()
    res = analyze_corpus(args.files, args.out_dir)
    print(json.dumps(res, ensure_ascii=False))
