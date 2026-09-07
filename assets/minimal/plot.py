"""Simple editable SVG example; visual QA remains a separate responsibility."""
import csv
import html
import json
import os
from pathlib import Path

params = json.loads(os.environ["ANALYSIS_PARAMS"])
with Path("output/summary.tsv").open(encoding="utf-8", newline="") as source:
    rows = list(csv.DictReader(source, delimiter="\t"))
maximum = max(float(row["mean"]) for row in rows) or 1
height = 100 + 48 * len(rows)
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="620" height="{height}" viewBox="0 0 620 {height}">',
       '<rect width="100%" height="100%" fill="white"/>',
       f'<text x="24" y="32" font-family="sans-serif" font-size="20">{html.escape(params["title"])}</text>']
for index, row in enumerate(rows):
    y = 60 + index * 48
    value = float(row["mean"])
    width = value / maximum * 350
    svg.extend([
        f'<text x="24" y="{y + 21}" font-family="sans-serif" font-size="15">{html.escape(row["group"])}</text>',
        f'<rect x="100" y="{y}" width="{width:.2f}" height="30" fill="{html.escape(params["color"], quote=True)}"/>',
        f'<text x="{112 + width:.2f}" y="{y + 21}" font-family="sans-serif" font-size="15">{value:g}</text>'])
svg.append('</svg>')
Path(os.environ["ANALYSIS_OUTPUT_DIR"], "means.svg").write_text("\n".join(svg), encoding="utf-8")
