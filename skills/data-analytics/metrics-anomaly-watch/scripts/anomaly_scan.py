#!/usr/bin/env python3
"""
Zero-dependency anomaly scanner for metrics-anomaly-watch skill.
Uses pure Python stdlib (csv, json, math, statistics) so it works everywhere.
Filters to Actual rows (if a 'status' column exists), compares the latest period
against the prior period and trailing 3-period trend, and prints a structured JSON summary.
"""
import csv
import json
import math
import statistics
import sys

LOWER_IS_WORSE = {
    "gross_margin_pct", "conversion_rate_pct", "repeat_order_share_pct",
    "m1_second_purchase_pct", "m3_second_purchase_pct", "email_opt_in_pct",
    "revenue_usd", "orders", "sessions", "aov_usd", "new_customers",
}
HIGHER_IS_WORSE = {
    "paid_cac_usd", "refund_rate_pct", "unsubscribe_rate_pct",
    "support_contact_rate_pct", "churn_rate_pct",
}

def try_float(val: str):
    try:
        v = float(val.replace(",", "").strip())
        return v if math.isfinite(v) else None
    except Exception:
        return None

def scan_csv(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {"error": "Empty CSV"}

    headers = list(rows[0].keys())
    status_col = next((h for h in headers if h.lower() == "status"), None)
    if status_col:
        rows = [r for r in rows if r.get(status_col, "").strip().lower() == "actual"]
    if len(rows) < 2:
        return {"error": "Need at least 2 Actual rows to compare."}

    period_col = headers[0]
    latest_row = rows[-1]
    prior_row = rows[-2]

    metrics = []
    for col in headers[1:]:
        if status_col and col == status_col:
            continue
        vals = [try_float(r.get(col, "")) for r in rows]
        if any(v is None for v in vals):
            continue
        fvals = [float(v) for v in vals if v is not None]
        latest = fvals[-1]
        prior = fvals[-2]
        abs_delta = latest - prior
        rel_delta_pct = ((latest - prior) / abs(prior) * 100.0) if prior != 0 else 0.0

        diffs = [fvals[i] - fvals[i - 1] for i in range(1, len(fvals))]
        prior_diffs = diffs[-4:-1] if len(diffs) >= 4 else diffs[:-1]
        trailing_3_avg_delta = statistics.mean(prior_diffs) if prior_diffs else 0.0

        same_sign = (abs_delta > 0 and trailing_3_avg_delta > 0) or (abs_delta < 0 and trailing_3_avg_delta < 0)
        accel = same_sign and abs(abs_delta) > (abs(trailing_3_avg_delta) * 1.25 + 1e-6)
        wrong_way = (col in LOWER_IS_WORSE and abs_delta < 0) or (col in HIGHER_IS_WORSE and abs_delta > 0)

        metrics.append({
            "metric": col,
            "latest": round(latest, 4),
            "prior": round(prior, 4),
            "abs_delta": round(abs_delta, 4),
            "rel_delta_pct": round(rel_delta_pct, 2),
            "trailing_3_avg_delta": round(trailing_3_avg_delta, 4),
            "wrong_direction": bool(wrong_way),
            "accelerated_vs_trend": bool(accel),
            "flagged": bool(wrong_way and (accel or abs(rel_delta_pct) >= 3.0)),
        })

    return {
        "latest_period": latest_row.get(period_col, ""),
        "prior_period": prior_row.get(period_col, ""),
        "total_actual_rows": len(rows),
        "metrics": metrics,
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 anomaly_scan.py <path-to-csv>")
        sys.exit(1)
    print(json.dumps(scan_csv(sys.argv[1]), ensure_ascii=False, indent=2))
