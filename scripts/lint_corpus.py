"""Lint every note in the repo and fail if the finding count grew.

    .venv/bin/python scripts/lint_corpus.py            # report
    .venv/bin/python scripts/lint_corpus.py --baseline 16

``recipe.py --lint-all`` is the interactive version: it prints every finding and
exits non-zero on the first file that has any. That is the right tool when you are
fixing things, but it cannot be used as a CI gate while the corpus still carries a
known backlog, and "ignore the failures" would make the gate meaningless.

So this script compares against a baseline instead. It only fails when the count
*grows*, which is the thing worth stopping: a new style violation that slipped in
alongside a real bug fix. Lower the baseline as findings get fixed -- do not raise it
to silence a new one.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import recipe  # noqa: E402

# Known backlog, all of it in notes generated before the current extractor existed.
# Five are the "4 l should be 40 dl" house rule, which the unit pass now fixes on
# regeneration; the rest are hand-written notes and deliberate deviations. They need
# a regeneration pass from live sources, and several of those sites now 403.
BASELINE = 16

FOLDERS = ("bread", "dessert", "dinner", "drinks", "normies")


def sweep(folders=FOLDERS) -> tuple[list[str], int]:
    problems: list[str] = []
    files = 0
    for folder in folders:
        root = ROOT / folder
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            files += 1
            for problem in recipe.lint(path):
                problems.append(f"{path.relative_to(ROOT)}: {problem}")
    return problems, files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=int, default=BASELINE,
                        help=f"allowed number of findings (default {BASELINE})")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    args = parser.parse_args()

    problems, files = sweep()
    if not args.quiet:
        for problem in problems:
            print(problem)
    print(f"{len(problems)} findings across {files} files (baseline {args.baseline})")

    if len(problems) > args.baseline:
        print(f"::error::corpus findings grew from {args.baseline} to {len(problems)}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())