"""Regenerate the golden files for the fixture pages.

    .venv/bin/python scripts/make_goldens.py            # write goldens
    .venv/bin/python scripts/make_goldens.py --check     # fail if any differ

A golden records what the Python extractor produces for one fixture: the raw
ingredient/step groups from the tiers, plus the finished markdown. tests/
test_goldens.py asserts the current code still matches, and the Kotlin port is
diffed against these same files.

Regenerating is a deliberate act, not a way to make a failure go away. If the
Python and Kotlin implementations disagree, decide which one is right and edit
the golden by hand: regenerating from a broken run writes the regression into
the contract. Read the diff before committing one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import recipe  # noqa: E402
from units import normalize_line  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "pages"
SOURCE_DIR = ROOT / "tests" / "fixtures" / "sources"
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "goldens"


def groups_to_json(groups: recipe.Groups) -> list[dict[str, object]]:
    return [{"heading": heading, "lines": list(lines)} for heading, lines in groups]


def convert_groups(groups: recipe.Groups) -> tuple[recipe.Groups, list[list[str]]]:
    """Convert each line, collecting [original_line, note, ...] for every note."""
    notes: list[list[str]] = []
    converted: recipe.Groups = []
    for heading, lines in groups:
        out: list[str] = []
        for line in lines:
            text, line_notes = normalize_line(line)
            out.append(text)
            notes.append([line, text, *line_notes])
        converted.append((heading, out))
    return converted, notes


def build(fixture: Path) -> dict[str, object]:
    html = fixture.read_text(encoding="utf-8")
    title, ingredient_groups, step_groups, tier = recipe.extract(html)

    source: dict[str, str] = {}
    source_file = SOURCE_DIR / f"{fixture.stem}.json"
    if source_file.exists():
        source = json.loads(source_file.read_text(encoding="utf-8"))

    # The markdown the note would be saved as, frontmatter and all. The notes
    # matter as much as the text: they record why a measurement was left alone.
    converted_ing, notes = convert_groups(ingredient_groups)
    converted_steps, step_notes = convert_groups(step_groups)
    markdown = recipe.render(
        title,
        converted_ing,
        converted_steps,
        meta={"source": source.get("url", ""), "tier": tier, "reviewed": "false"},
    )

    return {
        "fixture": fixture.stem,
        "source": source.get("url", ""),
        "captured": source.get("captured", ""),
        "title": title,
        "tier": tier,
        "ingredient_groups": groups_to_json(ingredient_groups),
        "step_groups": groups_to_json(step_groups),
        "conversion_notes": notes + step_notes,
        "markdown": markdown,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report differences instead of writing")
    args = parser.parse_args()

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    fixtures = sorted(FIXTURE_DIR.glob("*.html"))
    if not fixtures:
        print(f"no fixtures in {FIXTURE_DIR}", file=sys.stderr)
        return 1

    stale: list[str] = []
    for fixture in fixtures:
        golden = build(fixture)
        target = GOLDEN_DIR / f"{fixture.stem}.json"
        text = json.dumps(golden, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
        if args.check:
            if not target.exists():
                stale.append(f"{target.name}: missing")
            elif target.read_text(encoding="utf-8") != text:
                stale.append(f"{target.name}: differs")
        else:
            target.write_text(text, encoding="utf-8")

    known = {p.stem for p in GOLDEN_DIR.glob("*.json")}
    for orphan in sorted(known - {f.stem for f in fixtures}):
        stale.append(f"{orphan}.json: no fixture")

    if stale:
        print("goldens out of date:", file=sys.stderr)
        for line in stale:
            print(f"  {line}", file=sys.stderr)
        if args.check:
            print("run .venv/bin/python scripts/make_goldens.py", file=sys.stderr)
            return 1
    else:
        print(f"{len(fixtures)} goldens {'checked' if args.check else 'written'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())