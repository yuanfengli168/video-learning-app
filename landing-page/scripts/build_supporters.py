#!/usr/bin/env python3
"""build_supporters.py — Convert supporters.xlsx (or .csv) to supporters.json.

WHY THIS EXISTS
---------------
The Hall of Fame page on the landing site is a static JSON file
(`supporters.json`). But the project owner wants to maintain the
source-of-truth in a spreadsheet (more familiar, easier to sort/filter
in Excel/Google Sheets, no need to learn JSON).

This script bridges the two:

  supporters.xlsx  (private, owned by project owner)
       |
       |  python3 scripts/build_supporters.py
       v
  supporters.json  (public, committed to the repo, served by the site)

USAGE
-----

  # First time (or after schema change): generate a template
  python3 scripts/build_supporters.py --init

  # After editing the spreadsheet: regenerate the JSON
  python3 scripts/build_supporters.py supporters.xlsx

  # Or read from CSV (Google Sheets export)
  python3 scripts/build_supporters.py supporters.csv

  # Skip validation (if you have a malformed row you want to ship anyway)
  python3 scripts/build_supporters.py supporters.xlsx --no-validate

SPREADSHEET FORMAT
------------------

The spreadsheet must have these columns (case-insensitive header row):

  | name             | amount_usd | date       | comment                | tier  | claimed_talk | anonymous |
  | ---------------- | ---------- | ---------- | ---------------------- | ----- | ------------ | --------- |
  | Alice C.         | 20         | 2026-07-25 | Loved the transcript!  | talk  | true         |           |
  | Bob              | 5          | 2026-07-24 |                        | bronze|              |           |
  | Anonymous donor  | 100        | 2026-07-20 |                        | gold  |              | true      |

  - `name`: full name or "Anonymous" (or set `anonymous=true` to display as Anonymous)
  - `amount_usd`: exact amount, used to AUTO-COMPUTE the tier if you leave tier blank
  - `date`: ISO 8601 (YYYY-MM-DD)
  - `comment`: optional 1-line comment, max 200 chars
  - `tier`: optional override; one of coffee/bronze/silver/gold/talk
            (if blank, computed from amount_usd: <5=coffee, 5-9=bronze, 10-19=silver, 20+=gold)
  - `claimed_talk`: true if the donor has already scheduled their 30-min talk
  - `anonymous`: true to display as "Anonymous" (overrides `name`)

PRIVACY
-------

The default privacy policy is:
  - Show full name as entered (NOT "Alice C.")
  - NEVER show the exact amount — only the tier badge
  - Show the comment if present

To show only first-name + last-initial, set `name` to that format in
the spreadsheet. Example: "Alice C." instead of "Alice Chen".

INSTALLATION
------------

The script needs `openpyxl` for .xlsx support. Install with:

  pip install openpyxl

For .csv only, no install is needed (uses Python stdlib).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent.resolve()
PROJECT_ROOT = HERE.parent
SUPPORTERS_JSON = PROJECT_ROOT / "supporters.json"

VALID_TIERS = ("coffee", "bronze", "silver", "gold", "talk")
TIER_THRESHOLDS = [
    (1, "coffee"),
    (5, "bronze"),
    (10, "silver"),
    (20, "gold"),
    # 20+ AND wants a talk → "talk" (set tier explicitly in the sheet)
]


def tier_for_amount(amount_usd: float) -> str:
    """Map a dollar amount to the default tier (smallest tier >= amount)."""
    chosen = "coffee"
    for threshold, tier in TIER_THRESHOLDS:
        if amount_usd >= threshold:
            chosen = tier
    return chosen


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a row from the spreadsheet into the supporters.json schema.

    - Lower-case all keys
    - Strip whitespace from string fields
    - Validate types (amount_usd is float, date is ISO, etc.)
    - Auto-compute tier if blank
    - Truncate comment to 200 chars
    """
    # Lower-case keys for case-insensitive header matching
    out = {k.strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}

    # Required fields
    name = out.get("name") or ""
    amount_raw = out.get("amount_usd") or 0
    date_str = out.get("date") or ""
    comment = (out.get("comment") or "").strip()
    tier = (out.get("tier") or "").strip().lower()
    claimed_talk_raw = (out.get("claimed_talk") or "").strip().lower()
    anonymous_raw = (out.get("anonymous") or "").strip().lower()

    # Type coercion
    try:
        amount_usd = float(amount_raw)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid amount_usd: {amount_raw!r} (must be a number)")

    # Date validation
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"Invalid date: {date_str!r} (must be YYYY-MM-DD)")

    # Tier validation / auto-compute
    if tier and tier not in VALID_TIERS:
        raise ValueError(
            f"Invalid tier: {tier!r} (must be one of: {', '.join(VALID_TIERS)})"
        )
    if not tier:
        tier = tier_for_amount(amount_usd)

    # Boolean coercion
    claimed_talk = claimed_talk_raw in ("true", "1", "yes", "y")
    anonymous = anonymous_raw in ("true", "1", "yes", "y")

    # Truncate comment
    if len(comment) > 200:
        comment = comment[:197] + "..."

    return {
        "name": name,
        "amount_usd": amount_usd,
        "date": date_str,
        "comment": comment or None,
        "tier": tier,
        "claimed_talk": claimed_talk,
        "anonymous": anonymous,
    }


def read_xlsx(path: Path) -> list[dict[str, Any]]:
    """Read an .xlsx file using openpyxl. Returns list of row dicts."""
    try:
        import openpyxl  # type: ignore
    except ImportError:
        print(
            "ERROR: openpyxl is required for .xlsx files. Install with:\n"
            "  pip install openpyxl",
            file=sys.stderr,
        )
        sys.exit(1)

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        raise ValueError(f"{path}: workbook has no active sheet")

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    return [dict(zip(header, row)) for row in rows[1:] if any(c is not None for c in row)]


def read_csv(path: Path) -> list[dict[str, Any]]:
    """Read a .csv file using stdlib csv module."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def validate(supporters: list[dict[str, Any]]) -> list[str]:
    """Return a list of validation errors (empty list = OK)."""
    errors: list[str] = []
    for i, s in enumerate(supporters, 1):
        if not s.get("name"):
            errors.append(f"Row {i}: missing name")
        if s.get("amount_usd", 0) <= 0:
            errors.append(f"Row {i}: amount_usd must be > 0 (got {s.get('amount_usd')!r})")
        if s.get("tier") not in VALID_TIERS:
            errors.append(f"Row {i}: invalid tier {s.get('tier')!r}")
        if s.get("claimed_talk") and s.get("tier") != "talk":
            errors.append(
                f"Row {i}: claimed_talk=true but tier is {s.get('tier')!r} "
                f"(should be 'talk' — only $20+ donors qualify for a 30-min talk)"
            )
    return errors


def build_json(supporters: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the final supporters.json structure."""
    # Read existing file to preserve tier definitions + generated_at reset
    if SUPPORTERS_JSON.exists():
        with SUPPORTERS_JSON.open(encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {
            "tiers": {
                "coffee": {"label": "Coffee", "emoji": "☕", "min_usd": 1, "color": "#a16207"},
                "bronze": {"label": "Bronze", "emoji": "🥉", "min_usd": 5, "color": "#92400e"},
                "silver": {"label": "Silver", "emoji": "🥈", "min_usd": 10, "color": "#71717a"},
                "gold":   {"label": "Gold",   "emoji": "🥇", "min_usd": 20, "color": "#ca8a04"},
                "talk":   {"label": "Talk (30-min)", "emoji": "🎙️", "min_usd": 20, "color": "#6366f1"},
            }
        }

    data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Sort: most recent first
    supporters_sorted = sorted(
        supporters, key=lambda s: s["date"], reverse=True
    )
    data["supporters"] = supporters_sorted
    return data


def write_init_template(path: Path) -> None:
    """Write a starter CSV the owner can open in Excel/Sheets."""
    template = [
        ["name", "amount_usd", "date", "comment", "tier", "claimed_talk", "anonymous"],
        ["Alice C.", "20", "2026-07-25", "Loved the Mandarin transcript!", "talk", "true", ""],
        ["Bob", "5", "2026-07-24", "", "bronze", "", ""],
        ["Anonymous donor", "100", "2026-07-20", "Keep up the great work!", "gold", "", "true"],
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(template)
    print(f"✓ Wrote template: {path}")
    print()
    print("Next steps:")
    print("  1. Open the file in Excel or Google Sheets")
    print("  2. Edit the rows (or add new ones)")
    print("  3. Save as supporters.xlsx (or supporters.csv)")
    print(f"  4. Run: python3 scripts/build_supporters.py {path.with_suffix('.xlsx').name}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert supporters.xlsx/csv to supporters.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Path to supporters.xlsx or supporters.csv (omit for --init)",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Write a starter CSV template and exit",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip validation (ship the JSON even if rows look bad)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=SUPPORTERS_JSON,
        help=f"Output path (default: {SUPPORTERS_JSON.relative_to(PROJECT_ROOT)})",
    )
    args = parser.parse_args()

    # ── --init: write template ──
    if args.init:
        template_path = PROJECT_ROOT / "supporters.template.csv"
        write_init_template(template_path)
        return 0

    # ── Validate input ──
    if not args.input:
        parser.error("input file is required (or use --init)")
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: {input_path} not found", file=sys.stderr)
        return 1

    # ── Read ──
    suffix = input_path.suffix.lower()
    if suffix == ".xlsx":
        rows = read_xlsx(input_path)
    elif suffix == ".csv":
        rows = read_csv(input_path)
    else:
        print(f"ERROR: unsupported file type {suffix!r} (use .xlsx or .csv)", file=sys.stderr)
        return 1

    print(f"Read {len(rows)} row(s) from {input_path.name}")

    # ── Normalize ──
    supporters: list[dict[str, Any]] = []
    for i, row in enumerate(rows, 1):
        try:
            supporters.append(normalize_row(row))
        except ValueError as exc:
            print(f"ERROR row {i}: {exc}", file=sys.stderr)
            if not args.no_validate:
                return 1

    # ── Validate ──
    if not args.no_validate:
        errors = validate(supporters)
        if errors:
            print("Validation errors:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1

    # ── Build + write ──
    data = build_json(supporters)
    with args.out.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"✓ Wrote {args.out.relative_to(PROJECT_ROOT)} ({len(supporters)} supporter(s))")
    if supporters:
        print(f"  Most recent: {supporters[0]['name']} ({supporters[0]['date']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
