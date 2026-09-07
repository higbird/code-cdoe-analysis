"""Synthetic arithmetic example; all outputs go to the supplied staging directory."""
import csv
import json
import os
from pathlib import Path

params = json.loads(os.environ["ANALYSIS_PARAMS"])
groups = {}
with Path("input/data.tsv").open(encoding="utf-8", newline="") as source:
    for row in csv.DictReader(source, delimiter="\t"):
        groups.setdefault(row["group"], []).append(float(row["value"]) * params["scale"])
output = Path(os.environ["ANALYSIS_OUTPUT_DIR"])
with (output / "summary.tsv").open("w", encoding="utf-8", newline="") as target:
    writer = csv.writer(target, delimiter="\t")
    writer.writerow(["group", "n", "mean"])
    for group, values in sorted(groups.items()):
        writer.writerow([group, len(values), sum(values) / len(values)])
