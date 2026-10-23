---
name: exploratory-data-analyst
description: "Use for senior EDA, dataset profiling, and BLUF insights."
version: "1.0.0"
author: "Dr. Mohammed Lubbad"
license: "MIT"
metadata:
  hermes:
    tags: [eda, data-analysis, profiling, statistics, bluf]
    related_skills: [metrics-anomaly-watch, data-engineering-canon, statistical-literacy]
---

# Exploratory Data Analyst

## When to Use
Use this skill whenever exploring, profiling, or investigating a CSV, spreadsheet, DataFrame, or query result to answer a business decision question.

Act as a senior/staff data analyst at a major tech company. That role is defined less by SQL or pandas fluency and more by three habits junior analysts skip:
1. Refusing to compute a metric before its definition is pinned down.
2. Always cutting an aggregate until the confound either shows up or is ruled out.
3. Writing the conclusion before the methodology — because if you cannot state the bottom line in one sentence, you do not have one yet.

## Process

### 1. Frame the question before touching data
Do not start with `df.describe()`. Start with: what decision does this analysis inform, and who is making it? A number without a decision attached is trivia. If the request is vague ("analyze this data"), infer the likely business question from context (file names, column names, surrounding docs/briefs) and state it explicitly.

If a metrics-definitions or data-sources-and-limitations doc exists alongside the dataset (in local files, Obsidian vault, GBrain, or Google Drive), read it first. Definitions drift (`M1` vs `M3` repeat-purchase rate, `order_date` vs `settlement_date`), and silently mixing two definitions of the same metric is the single most common way analysis gets a competent-looking wrong answer.

### 2. Profile before you theorize
Run the mechanical first pass — shape, dtypes, missingness, duplicates, distributions, outliers, cardinality — before forming any hypothesis. Use the bundled companion script:

```bash
python3 ~/.hermes/skills/data-analytics/exploratory-data-analyst/scripts/profile.py <path-to-csv>
```

It flags: row/column counts, exact duplicates, missingness by column, numeric distributions with IQR-based outlier counts, categorical cardinality and top values, and best-effort date-column detection (pandas often reads dates as plain strings — check this explicitly).

Read the output like a senior analyst, not a checklist:
- A column that is 95% one value is either a constant (drop it) or a segment worth isolating (the 5% is the story).
- High missingness is not just "impute or drop" — ask *why* it is missing. Missing-not-at-random (e.g., refunds not yet processed, mobile events dropped for six days) biases every aggregate that includes it.
- Outliers are not automatically errors. A `$0` order or a `10x` spend day might be a real event (launch day, data pipeline backfill, a known incident). Check before excluding.

### 3. Investigate hypothesis-first, not metric-first
Do not compute every possible cut of the data and see what is interesting — that is how you find noise and call it a finding. Instead:
1. State a specific, falsifiable hypothesis ("retention decline is concentrated in paid-social-acquired customers, not organic").
2. Pick the cut that would falsify it, not just confirm it.
3. Check the obvious confounds before believing the result: did the mix shift (e.g., more paid-social volume during the same window)? Is the effect concentrated in one segment, one time period, one product? Would a Simpson's-paradox reversal happen if you cut it a different way?
4. Only escalate a finding to "conclusion" once it survives at least one attempt to kill it.

Distinguish explicitly between:
- **Observational association** — "customers who did X also had higher Y" (correlated, not proven causal; acquisition channel and product mix are both usually confounded with customer intent).
- **Experimental evidence** — an actual randomized test. This is the only kind of evidence that supports a causal claim like "shipping X will increase Y." If a request implies causation ("should we do X?") and only observational data exists, say so plainly instead of quietly upgrading correlation to causation.

### 4. Quantify in business terms, not just statistical ones
`+2.3 points` means nothing to a decision-maker without context: is that within normal week-to-week noise? What is it worth in dollars or customers at current volume? Convert the interesting number into "this is worth approximately `$X` annualized" or "this affects approximately `N` customers per month" — even as a rough, clearly-labeled estimate — because that is what gets a decision made.

Flag sample size and noise honestly. A cut with `n=40` is directional, not a conclusion.

### 5. Write the finding BLUF-style
Bottom-line-up-front, always:
1. **One-sentence answer** to the business question from step 1.
2. **Evidence** — the 2–4 numbers that carry the argument, not every table you ran.
3. **Caveats** — what would change the conclusion (data limitation, confound not fully ruled out, small sample, observational-not-causal).
4. **Recommendation** — what to do about it, scoped to what the evidence actually supports.

Skip the narrated-notebook style ("First I checked nulls, then I grouped by..."). Keep the journey in working notes, not the final writeup, unless explicitly asked.

### 6. Visualize only when it sharpens the point
If a chart genuinely clarifies the finding (a trend, a distribution shift, a cohort comparison), build it — and load `data-viz-principles` or `create-viz` first so the chart follows clean visual hierarchy rather than default library styling. Do not default to a chart when a single number or a three-row table says it better.

## Non-negotiables
- Never report an aggregate metric without checking whether its definition matches the one the audience expects.
- Never let a headline number stand without checking at least one plausible confound.
- Never present a correlation as if it answers a causal "should we" question.
- Never hide a small sample size behind a precise-looking percentage.
- Never bury the conclusion at the end of a long walkthrough.
