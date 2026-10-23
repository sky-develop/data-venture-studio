#!/usr/bin/env python3
"""
Zero-dependency mechanical first-pass CSV profiler for exploratory-data-analyst skill.
Uses pure Python stdlib (csv, statistics, datetime, collections) so it runs in any environment.
Outputs shape, exact duplicate rows, missingness, IQR outliers, cardinality, and date range detection.
"""
import csv
import datetime
import json
import math
import statistics
import sys
from collections import Counter

DATE_FORMATS = ("%Y-%m-%d", "%Y-%m", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S")

def try_parse_float(val: str):
    try:
        v = float(val.replace(",", ""))
        return v if math.isfinite(v) else None
    except Exception:
        return None

def try_parse_date(val: str):
    s = val.strip()
    if not s:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(s[:19], fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return None

def quantile(sorted_vals, q: float) -> float:
    if not sorted_vals:
        return 0.0
    pos = (len(sorted_vals) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_vals[lo])
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo))

def profile_csv(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = list(csv.reader(f))
    if not reader:
        return {"file": path, "rows": 0, "columns": 0, "profiles": []}

    headers = reader[0]
    rows = reader[1:]
    n_rows = len(rows)
    n_cols = len(headers)
    seen = set()
    dup_rows = 0
    for r in rows:
        t = tuple(r)
        if t in seen:
            dup_rows += 1
        else:
            seen.add(t)

    profiles = []
    for c_idx, col in enumerate(headers):
        raw_vals = [r[c_idx].strip() if c_idx < len(r) else "" for r in rows]
        non_empty = [v for v in raw_vals if v != "" and v.lower() not in ("null", "na", "nan", "none")]
        nulls = n_rows - len(non_empty)
        null_pct = round((nulls / max(1, n_rows)) * 100.0, 2)
        nunique = len(set(non_empty))

        num_vals = [try_parse_float(v) for v in non_empty]
        is_numeric = len(non_empty) > 0 and all(v is not None for v in num_vals)

        info = {
            "column": col,
            "dtype": "numeric" if is_numeric else "categorical/text",
            "nulls": nulls,
            "null_pct": null_pct,
            "nunique": nunique,
        }
        if is_numeric:
            clean = sorted(float(v) for v in num_vals if v is not None)
            q1 = quantile(clean, 0.25)
            q3 = quantile(clean, 0.75)
            iqr = q3 - q1
            low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            outliers = sum(1 for v in clean if v < low or v > high)
            info.update({
                "mean": round(statistics.mean(clean), 4),
                "std": round(statistics.stdev(clean), 4) if len(clean) > 1 else 0.0,
                "min": clean[0],
                "p25": round(q1, 4),
                "median": round(quantile(clean, 0.50), 4),
                "p75": round(q3, 4),
                "max": clean[-1],
                "iqr_outliers": outliers,
            })
        else:
            counts = Counter(non_empty).most_common(5)
            info["top_values"] = {k: v for k, v in counts}
            sample = non_empty[:50]
            parsed_dates = [try_parse_date(v) for v in sample]
            if sample and sum(1 for d in parsed_dates if d is not None) / len(sample) >= 0.8:
                all_dates = sorted(d for d in (try_parse_date(v) for v in non_empty) if d is not None)
                if all_dates:
                    info["dtype"] = "date"
                    info["detected_date_range"] = [all_dates[0], all_dates[-1]]

        profiles.append(info)

    return {
        "file": path,
        "rows": n_rows,
        "columns": n_cols,
        "exact_duplicate_rows": dup_rows,
        "profiles": profiles,
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 profile.py <path-to-csv>")
        sys.exit(1)
    print(json.dumps(profile_csv(sys.argv[1]), ensure_ascii=False, indent=2))
