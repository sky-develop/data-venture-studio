# Sky Develop — Data Venture Studio & Open-Source Arabic AI Toolkit

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/sky-develop/data-venture-studio/blob/main/starter/first_win.ipynb)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-training.sky--develop.net-0ea5e9)](https://training.sky-develop.net)

Open-source starter notebooks, **Arabic NLP / RTL developer tooling**, and **Claude Code / Autonomous Agent Skills** built and maintained by **Dr. Mohammed Lubbad** ([@mlubbad](https://github.com/mlubbad)) and **[Sky Develop LLC](https://sky-develop.net)**.

---

## Repository Architecture

| Module | Path | Description |
| :--- | :--- | :--- |
| **Data Venture Studio Starter** | [`starter/first_win.ipynb`](starter/first_win.ipynb) | Reproducible Python/Colab pipeline for Stage 0–3 data exploration, KPI verification, and venture validation. |
| **Arabic AI Atlas & RTL Tooling** | [`skills/arabic-ai-atlas/`](skills/arabic-ai-atlas/) & [`tools/arabic-ai-atlas/`](tools/arabic-ai-atlas/) | Open-source Arabic LLM/ASR/TTS selection advisor, dialect prompt templates, Arabic tokenizer cost benchmarks, RTL/BiDi linter (`rtl-bidi-lint`), and diacritics (`tashkeel-check`) validator. |
| **Arabic Document Linker** | [`skills/arabic-nlp/arabic-doc-linker/`](skills/arabic-nlp/arabic-doc-linker/) | Arabic morphological entity extraction and cross-document knowledge graph linking. |
| **Editonny RTL Video Engine** | [`tools/editonny-rtl/`](tools/editonny-rtl/) | Word-level Arabic RTL subtitle shaping (`arabic-reshaper` + `python-bidi`), spoken-number normalization, and FFmpeg talking-head video editing pipeline. |
| **Open-Higgsfield CLI (`open-hf`)** | [`tools/open-higgsfield/`](tools/open-higgsfield/) | Local Apple Silicon (`mflux`, `rembg`, `OmniVoice`) + Gemini/Veo/Lyria multimodal CLI & agent skill. |
| **Automated EDA & Anomaly Watch** | [`skills/data-analytics/`](skills/data-analytics/) | Senior Exploratory Data Analysis (`exploratory-data-analyst`) and automated time-series KPI anomaly detection (`metrics-anomaly-watch`). |

---

## Quick Start

### 1. Run the Starter Notebook in Google Colab
Click **[Open in Colab](https://colab.research.google.com/github/sky-develop/data-venture-studio/blob/main/starter/first_win.ipynb)** to launch the interactive Data Venture Studio notebook directly in your browser.

### 2. Install Agent Skills (Claude Code / Hermes Agent)
```bash
git clone https://github.com/sky-develop/data-venture-studio.git
cd data-venture-studio
cp -r skills/* ~/.claude/skills/
```

### 3. Run Arabic RTL / BiDi Linter & Token Cost Analyzer
```bash
python3 tools/arabic-ai-atlas/scripts/rtl_bidi_lint.py --help
python3 tools/arabic-ai-atlas/scripts/token_cost.py --help
```

---

## License

Released under the [MIT License](LICENSE). Maintained by **Dr. Mohammed Lubbad** ([Sky Develop LLC](https://sky-develop.net)).
