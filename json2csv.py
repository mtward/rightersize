#!/usr/bin/env python3
import csv
import glob
import json
import os
import sys


def flatten(obj, parent_key="", sep="."):
    """
    Recursively flattens a nested JSON object.

    Example:
      {"cpu": {"p95.0_busy_pct": 5.3}}
    becomes:
      {"cpu.p95.0_busy_pct": 5.3}
    """
    items = {}

    if isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            items.update(flatten(v, new_key, sep=sep))

    elif isinstance(obj, list):
        # Join lists into a pipe-delimited string
        items[parent_key] = " | ".join(str(x) for x in obj)

    else:
        items[parent_key] = obj

    return items


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <json_dir> <output.csv>")
        sys.exit(1)

    json_dir = sys.argv[1]
    out_csv = sys.argv[2]

    files = sorted(glob.glob(os.path.join(json_dir, "*.json")))
    if not files:
        print(f"No JSON files found in {json_dir}")
        sys.exit(1)

    rows = []
    all_fields = set()

    # First pass: load + flatten all JSON, collect full schema
    for fn in files:
        try:
            with open(fn) as f:
                data = json.load(f)
        except Exception as e:
            print(f"Skipping {fn}: {e}")
            continue

        flat = flatten(data)
        rows.append(flat)
        all_fields.update(flat.keys())

    # Stable, readable column ordering:
    # - host first
    # - then everything else sorted
    fieldnames = []
    if "host" in all_fields:
        fieldnames.append("host")
        all_fields.remove("host")

    fieldnames.extend(sorted(all_fields))

    # Write CSV
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            writer.writerow(row)

    print(f"Wrote CSV: {out_csv}")
    print(f"Hosts processed: {len(rows)}")
    print(f"Columns written: {len(fieldnames)}")


if __name__ == "__main__":
    main()
