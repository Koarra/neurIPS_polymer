#!/usr/bin/env python3
"""
Build productivity.json (KMPI #4 input) from the GFIU article upload folder.

Expected folder layout under KMPI_FOLDER / "teams" / "GFIU":

    GFIU/
      <article_folder_1>/
        source.pdf and/or source.docx
      <article_folder_2>/
        source.docx
      ...

Each subfolder that contains at least one .pdf or .docx file counts as ONE
processed article (a folder with both a .pdf and a .docx is still one
article, not two). The article's month is taken from the earliest
modification time among its .pdf/.docx files.

There is no per-person attribution available from this folder structure, so
the team-wide monthly total is divided by config.GFIU_TEAM_SIZE to produce
avg_articles_per_person. The script fully rebuilds productivity.json from
the current state of the folder on every run (safe to re-run after new
articles are added or backdated).

Usage: python3 build_productivity_metrics.py
"""

import sys
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config as config
from utils.const import KMPI_FOLDER

ARTICLE_EXTENSIONS = {".pdf", ".docx"}
GFIU_ARTICLES_DIR = KMPI_FOLDER / "teams" / "GFIU"


def article_month(article_dir: Path):
    """Return the YYYY-MM month for an article folder, or None if it has no article files."""
    article_files = [
        f for f in article_dir.iterdir()
        if f.is_file() and f.suffix.lower() in ARTICLE_EXTENSIONS
    ]
    if not article_files:
        return None

    earliest_mtime = min(f.stat().st_mtime for f in article_files)
    return datetime.fromtimestamp(earliest_mtime).strftime("%Y-%m")


def count_articles_by_month(gfiu_dir: Path) -> tuple[dict, list]:
    """Count one article per subfolder containing a .pdf/.docx, bucketed by month."""
    counts = defaultdict(int)
    skipped = []

    for entry in sorted(gfiu_dir.iterdir()):
        if not entry.is_dir():
            continue
        month = article_month(entry)
        if month is None:
            skipped.append(entry.name)
            continue
        counts[month] += 1

    return counts, skipped


def main():
    print(f"\n{'='*60}")
    print(f"Build Productivity Metrics (KMPI #4 input)")
    print(f"{'='*60}\n")

    if not GFIU_ARTICLES_DIR.exists():
        print(f"ERROR: GFIU articles folder not found at {GFIU_ARTICLES_DIR}")
        return 1

    counts, skipped = count_articles_by_month(GFIU_ARTICLES_DIR)

    if not counts:
        print(f"ERROR: No article subfolders with .pdf/.docx files found under {GFIU_ARTICLES_DIR}")
        return 1

    team_size = config.GFIU_TEAM_SIZE
    if team_size <= 0:
        print(f"ERROR: config.GFIU_TEAM_SIZE must be a positive integer, got {team_size}")
        return 1

    productivity = [
        {
            "month": month,
            "avg_articles_per_person": round(count / team_size, 2),
        }
        for month, count in sorted(counts.items())
    ]

    output_file = config.PRODUCTION_METRICS_DIR / "productivity.json"
    with open(output_file, "w") as f:
        json.dump(productivity, f, indent=2)

    print(f"Scanned: {GFIU_ARTICLES_DIR}")
    print(f"Team size: {team_size}")

    if skipped:
        print(f"\nSkipped {len(skipped)} subfolder(s) with no .pdf/.docx file:")
        for name in skipped:
            print(f"  {name}")

    print(f"\nMonthly totals:")
    for month, count in sorted(counts.items()):
        avg = count / team_size
        print(f"  {month}: {count} articles / {team_size} people = {avg:.2f} avg/person")

    print(f"\nWrote {len(productivity)} month(s) to: {output_file}")
    print(f"{'='*60}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
