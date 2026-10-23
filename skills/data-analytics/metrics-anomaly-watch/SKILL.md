---
name: metrics-anomaly-watch
description: "Use when scanning business metrics CSVs for anomalies."
version: "1.0.0"
author: "Dr. Mohammed Lubbad"
license: "MIT"
metadata:
  hermes:
    tags: [metrics, anomaly-detection, kpi, guardrails, analytics]
    related_skills: [exploratory-data-analyst, statistical-literacy]
---

# Metrics Anomaly Watch

## When to Use
Use when scanning monthly or weekly business metrics tables/CSVs to detect guardrail breaches or rate-of-change accelerations on Actual rows.

Turns a wide monthly/weekly business metrics CSV or table into a short list of what actually moved and whether it matters, using explicit business guardrails and trend-acceleration checks rather than arbitrary statistical noise thresholds.

## Triggers
- "check for anomalies"
- "any red flags in the numbers"
- "metrics watch"
- "did anything change this month"
- "flag anything unusual"

## Run (Agent Path)

### 1. Pull the metrics source and guardrail definitions
Locate the metrics CSV/table and the `Metrics Definitions` document (via local workspace, Obsidian vault, GBrain, or Google Drive `gws` / `google-drive-api`).
- If the file is a local CSV, run the bundled scanner first:
  ```bash
  python3 ~/.hermes/skills/data-analytics/metrics-anomaly-watch/scripts/anomaly_scan.py <path-to-csv>
  ```

### 2. Compare the latest `Actual` row against the prior `Actual` row
- **Always filter to `status == "Actual"`** (or equivalent completed period) before diffing — skip `Plan` / `Forecast` rows so you never compare actuals against a forecast.
- For each metric column, compute:
  1. Absolute delta (`Latest - Prior`)
  2. Relative delta (`%`)
  3. Direction and pace relative to the trailing 3-period average trend (is this move faster than the recent trend, or in line with it?)

### 3. Apply guardrail-aware flagging
Use the guardrails defined in `Metrics Definitions` (e.g., gross margin, refund rate, unsubscribe rate, support contact rate) plus the North Star and core retention metrics:
- **Guardrail breach / wrong-direction move:** Flag if a guardrail metric moved in the wrong direction by more than a trivial amount (e.g., gross margin down, refund rate up, CAC spiking above threshold).
- **Rate-of-change acceleration:** Flag if a core metric's decline or growth rate accelerated vs. its own trailing 3-period trend (e.g., `M3 retention` fell `0.7 pts` this month vs. an average `0.5 pt/month` decline over the prior 3 months — that is acceleration, not mere continuation).
- **Do not flag noise or established status quo:** A metric moving in line with its established trend is not a new anomaly; state that explicitly in one sentence rather than re-flagging every column.

## Output Format

```markdown
## Metrics Anomaly Watch — <latest actual period>

| Metric | Latest | Prior | Δ | Flag |
|---|---|---|---|---|
| <metric> | <value> | <value> | <+/- and %> | <reason flagged: guardrail breach or trend acceleration> |

**Read:** <one sentence — what is actually different this period vs. the established trend, in business terms>
```

Only include rows that are genuinely flagged. If nothing is flagged, state so in one line rather than producing an empty table.

## Gotchas
- **Mixed `Actual` and `Plan` rows:** Always filter `status == "Actual"` before computing deltas.
- **"Worse" is metric-specific:** Higher `CAC` or `refund_rate` is bad; higher `gross_margin` or `retention` is good. Never apply a single "higher = flag" rule across all columns.
- **Trend continuation ≠ anomaly:** Only flag when a guardrail is breached or the *rate of change* shifts materially vs. the trailing 3-period average.
