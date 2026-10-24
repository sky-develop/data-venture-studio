---
name: arabic-doc-linker
description: Use for Arabic NLP entity extraction and cross-doc linking.
version: 1.0.0
---

# Arabic Document Entity & Investigation Linker (`arabic-doc-linker`)

Local, zero-cost, unlimited-file Arabic text & document investigation toolkit inspired by InfoTimes `dlinker.ca` and `newsscope.ca`, built natively for Arabic (`RTL`) with evidence-backed cross-document entity linking.

## When to Use

Use this skill whenever the user wants to:
- Analyze Arabic documents (`PDF`, `DOCX`, `TXT`, `MD`, or scanned images via OCR) to extract **persons (`أشخاص`), organizations (`منظمات/جهات`), locations (`أماكن`), dates (`تواريخ`), financial amounts/numbers (`أرقام ومبالغ`), and contacts/URLs**.
- Compute **TF-IDF weighted Arabic keywords** and **recurring Bigrams/Trigrams (`N-grams`)** to uncover discourse patterns.
- Classify **Arabic sentiment (`إيجابي / سلبي / محايد`)** with explicit cue words (`كلمات الدلالة`).
- Build an **evidence-backed cross-document link network (`شبكة ربط الوثائق بالأدلة والاقتباسات`)** across 2 or more files, showing every shared entity/term with its exact **Keyword-in-Context (`KWIC`) quote** per source document.
- Generate a standalone interactive **RTL HTML investigation graph (`network.html`)**, `CSV` exports, and `JSON` reports.

## Bundled CLI (`scripts/dlinker_local.py`)

Run the bundled analyzer on any set of Arabic files:

```bash
python3 ~/.hermes/skills/research/arabic-doc-linker/scripts/dlinker_local.py \
  --out-dir /path/to/output_dir \
  file1.txt file2.pdf file3.docx
```

### Outputs Produced in `--out-dir`
1. `report.json` — Complete structured output: per-document stats, entities by category (`persons`, `orgs`, `locations`, `dates`, `Money_Numbers`, `contacts`), top TF-IDF terms, top bigrams/trigrams, Arabic sentiment score + cue words, and `shared_links` with verbatim quotes per document.
2. `entities.csv` — UTF-8-SIG CSV of all extracted entities, types, frequencies, documents, and verbatim context snippets.
3. `network.html` — Self-contained interactive RTL Arabic investigation interface showing the cross-document entity graph, clickable evidence quotes (`من الاسم المشترك إلى العلاقة التي يسندها المستند`), TF-IDF keyword weights, N-grams, and sentiment breakdown.

## Core Investigation Rules

1. **Co-occurrence is not proof of causation (`وجود اسمين معاً لا يكفي وحده لإثبات علاقة بينهما`):** Every link between two documents or two entities must display the verbatim sentence quote (`الاقتباس النصي`) and the exact source document name.
2. **Arabic-First Normalization (`تطبيع الصرف العربي`):** Strip diacritics (`التشكيل`) and normalize hamzas (`أ/إ/آ -> ا`) and prefixes (`والـ/بالـ/كالـ/فالـ/للـ/الـ`) ONLY for matching and TF-IDF weighting, while always displaying the original un-normalized Arabic surface form in quotes and tables.
3. **Zero External Data Leakage:** Process all documents 100% locally on-device without uploading sensitive contracts, leaks, or financial statements to third-party cloud SaaS platforms.
