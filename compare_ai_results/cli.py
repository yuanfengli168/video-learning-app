"""cli.py — run / report subcommands.

  python -m compare_ai_results run input.json [--out DIR]
  python -m compare_ai_results report comparison-runs/<ts>/
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="compare_ai_results",
        description="Standing model-comparison tool (input-contract v1)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="validate input → LLM run → score")
    run_p.add_argument("input", help="path to the input JSON (contract v1)")
    run_p.add_argument("--out", default=None,
                       help="output root (default ./comparison-runs/)")

    rep_p = sub.add_parser("report", help="generate RESULTS.md from a run")
    rep_p.add_argument("run_dir", help="the run directory")

    args = parser.parse_args(argv)

    if args.cmd == "run":
        from compare_ai_results.engine import run
        run(args.input, args.out)
        return 0

    if args.cmd == "report":
        from compare_ai_results.report import write_report
        write_report(Path(args.run_dir))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())