#!/usr/bin/env python3
import csv
import glob
import json
import os
import sys


def flatten(obj, parent_key="", sep="."):
    """
    Flatten nested JSON into dot-delimited keys.
    Lists are joined into a pipe-delimited string.
    """
    items = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            items.update(flatten(v, new_key, sep))
    elif isinstance(obj, list):
        items[parent_key] = " | ".join(str(x) for x in obj)
    else:
        items[parent_key] = obj
    return items


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <json_dir> <summary.csv>")
        sys.exit(1)

    json_dir = sys.argv[1]
    out_csv = sys.argv[2]

    files = sorted(glob.glob(os.path.join(json_dir, "*.json")))
    if not files:
        print(f"No JSON files found in {json_dir}")
        sys.exit(1)

    rows = []
    all_keys = set()

    # Load and flatten all JSON
    for fn in files:
        try:
            with open(fn) as f:
                data = json.load(f)
        except Exception as e:
            print(f"Skipping {fn}: {e}")
            continue

        flat = flatten(data)
        rows.append(flat)
        all_keys.update(flat.keys())

    # -----------------------------
    # Column selection rules
    # -----------------------------
    host_cols = ["host"] if "host" in all_keys else []

    # cpu*pct means: any cpu.* that contains "pct"
    # plus cpu.p95* (including p95.0_* keys)
    cpu_cols = sorted(
        k for k in all_keys
        if k.startswith("cpu.")
        and ("pct" in k or k.startswith("cpu.p95"))
    )

    # memory.p* means any key starting with memory.p (e.g. memory.p99.0_workingset_mb, memory.p95_pswpin_per_s)
    mem_cols = sorted(k for k in all_keys if k.startswith("memory.p"))

    # confidence + notes are under recommendation
    confidence_col = ["recommendation.confidence"] if "recommendation.confidence" in all_keys else []
    notes_col = ["recommendation.notes"] if "recommendation.notes" in all_keys else []

    # current* columns should be second-to-last group
    current_cols = sorted(k for k in all_keys if k.startswith("current."))

    # recommendation* last group - you asked specifically for these
    recommendation_cols = []
    for k in ("recommendation.mem_mb_recommended", "recommendation.vcpu_recommended"):
        if k in all_keys:
            recommendation_cols.append(k)

    # Final field order: host, cpu, memory, confidence, notes, current*, recommendation*
    fieldnames = (
        host_cols
        + cpu_cols
        + mem_cols
        + confidence_col
        + notes_col
        + current_cols          # second-to-last group
        + recommendation_cols   # last group
    )

    # -----------------------------
    # Write CSV
    # -----------------------------
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for row in rows:
            # Filter row to only the selected columns to avoid DictWriter ValueError
            out_row = {k: row.get(k, "") for k in fieldnames}
            writer.writerow(out_row)

    print(f"Wrote summary CSV: {out_csv}")
    print(f"Hosts processed: {len(rows)}")
    print(f"Columns written: {len(fieldnames)}")


if __name__ == "__main__":
    main()
