"""Golden tests: every fixture page must still extract to the recorded result.

    .venv/bin/python -m unittest tests.test_goldens

These are the tests the Kotlin port is written against. Each fixture in
tests/fixtures/pages/ has a golden in tests/fixtures/goldens/ recording the
title, the extraction tier, the raw ingredient/step groups, every unit-conversion
note, and the finished markdown. A change in any of those shows up here first,
and the same golden files are what the Kotlin implementation gets diffed against.

To accept an intended change:

    .venv/bin/python scripts/make_goldens.py

Read the diff before committing it. Regenerating a golden to make this test pass
is how a regression gets written into the contract.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import recipe  # noqa: E402
from units import normalize_line  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "pages"
GOLDENS = ROOT / "tests" / "fixtures" / "goldens"

# Still-English measurements are allowed only when the extractor deliberately
# declined to guess, which the conversion notes record.
_IMPERIAL_UNIT = r"(?:fl\.?\s*oz|fluid\s+ounces?|cups?|teaspoons?|tablespoons?|tbsp|tbsps?|tbs|ounces?|oz|pounds?|lbs?|lb)"
_IMPERIAL = re.compile(rf"\b{_IMPERIAL_UNIT}\b", re.I)
# The difference between a measurement and a mention: "1 cup apricots" is a
# measurement the extractor chose not to convert, "about a third a cup of each"
# is prose that happens to name a unit.
_MEASURED = re.compile(rf"\d+(?:\s*[-–—]\s*\d+\s*/\s*\d+|\s+\d+\s*/\s*\d+|\s*/\s*\d+|[.,]\d+)?"
                       rf"\s*{_IMPERIAL_UNIT}\b", re.I)
_KEPT_AS_IS = "kept as-is"


def load(name: str) -> dict:
    return json.loads((GOLDENS / f"{name}.json").read_text(encoding="utf-8"))


def groups_to_json(groups):
    return [{"heading": heading, "lines": list(lines)} for heading, lines in groups]


def convert_groups(groups):
    notes = []
    converted = []
    for heading, lines in groups:
        out = []
        for line in lines:
            text, line_notes = normalize_line(line)
            out.append(text)
            notes.append([line, text, *line_notes])
        converted.append((heading, out))
    return converted, notes


def extract_golden_shape(fixture: Path, source: str = "") -> dict:
    """Everything the goldens assert, recomputed from the fixture."""
    title, ingredient_groups, step_groups, tier = recipe.extract(
        fixture.read_text(encoding="utf-8")
    )
    converted_ing, notes = convert_groups(ingredient_groups)
    converted_steps, step_notes = convert_groups(step_groups)
    return {
        "title": title,
        "tier": tier,
        "ingredient_groups": groups_to_json(ingredient_groups),
        "step_groups": groups_to_json(step_groups),
        "conversion_notes": notes + step_notes,
        "markdown": recipe.render(title, converted_ing, converted_steps,
                                  meta={"source": source, "tier": tier, "reviewed": "false"}),
    }


def fixture_names() -> list[str]:
    return sorted(p.stem for p in FIXTURES.glob("*.html"))


def markdown_lines(markdown: str) -> list[str]:
    """Just the ingredient bullets and numbered instructions, not headings or
    frontmatter."""
    out = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "1. ", "2. ", "3. ", "4. ", "5. ", "6. ",
                               "7. ", "8. ", "9. ")):
            out.append(stripped[2:].strip())
    return out


class TestGoldens(unittest.TestCase):
    def test_there_are_fixtures(self):
        self.assertGreaterEqual(len(fixture_names()), 12,
                                "the harness is only useful once it covers the tiers")

    def test_every_fixture_has_a_golden(self):
        for name in fixture_names():
            with self.subTest(fixture=name):
                self.assertTrue((GOLDENS / f"{name}.json").exists(),
                                f"{name} has no golden; run scripts/make_goldens.py")

    def test_no_golden_without_a_fixture(self):
        known = {p.stem for p in GOLDENS.glob("*.json")}
        self.assertEqual(known - set(fixture_names()), set())

    def test_goldens_are_in_sync(self):
        """One test, every fixture: the whole point is a single loud diff."""
        stale = []
        for name in fixture_names():
            golden = load(name)
            actual = extract_golden_shape(FIXTURES / f"{name}.html", golden["source"])
            expected = {key: golden.get(key) for key in actual}
            if actual != expected:
                differing = [key for key in actual if actual[key] != expected[key]]
                stale.append(f"{name}: {', '.join(differing)}")
        self.assertEqual(stale, [], "\n".join(stale))


class TestGoldenCoverage(unittest.TestCase):
    """The goldens are only worth diffing if they cover the extraction paths."""

    def test_tiers_covered(self):
        tiers = {load(name)["tier"] for name in fixture_names()}
        self.assertIn("schema.org/Recipe", tiers)
        self.assertIn("schema ingredients + heading instructions", tiers)
        self.assertIn("heading heuristic", tiers)
        self.assertIn("none", tiers, "a page that is not a recipe should be pinned too")

    def test_both_languages_are_covered(self):
        english = {"simplyrecipes_pizza", "allrecipes_white_cake", "tasteofhome_chicken_stew"}
        self.assertTrue(english & set(fixture_names()))

    def test_fixtures_are_trimmed(self):
        """A fixture that still carries the whole page will rot in the repo."""
        for name in fixture_names():
            with self.subTest(fixture=name):
                size = (FIXTURES / f"{name}.html").stat().st_size
                self.assertLess(size, 400_000, f"{name} is {size // 1024} KB, expected trimmed")

    def test_goldens_record_a_source_url(self):
        for name in fixture_names():
            with self.subTest(fixture=name):
                self.assertTrue(load(name)["source"].startswith("http"),
                                f"{name} has no recorded source URL")


class TestGoldenHouseStyle(unittest.TestCase):
    """Metric first, paired temperatures, and no silent guessing."""

    celsius = r"\d+(?:\s*[-–—]\s*\d+)?\s*[°º]?\s*C"
    fahrenheit = r"\d+(?:\s*[-–—]\s*\d+)?\s*[°º]?\s*F"

    def markdown_of(self, name: str) -> str:
        golden = load(name)
        return "" if golden["tier"] == "none" else golden["markdown"]

    def test_sections_present(self):
        for name in fixture_names():
            body = self.markdown_of(name)
            if not body:
                continue
            with self.subTest(fixture=name):
                self.assertIn("## Ingredients:", body)
                self.assertIn("## TODO:", body)

    def test_temperatures_are_paired(self):
        for name in fixture_names():
            body = self.markdown_of(name)
            if not body:
                continue
            with self.subTest(fixture=name):
                stripped = re.sub(rf"{self.celsius}\s*\(\s*{self.fahrenheit}\s*\)", "", body)
                self.assertIsNone(re.search(rf"\b\d{{2,3}}\s*[°º]?\s*C\b", stripped),
                                  f"{name} has an unpaired Celsius temperature")
                self.assertIsNone(re.search(rf"\b\d{{2,3}}\s*[°º]?\s*F\b", stripped),
                                  f"{name} has an unpaired Fahrenheit temperature")

    def test_leftover_imperial_units_are_always_explained(self):
        """A measured cup may survive, but only when a note says the extractor
        refused to guess a density for it. An unexplained measurement left in
        imperial is a bug. A unit merely mentioned in prose is not a measurement
        and needs no note."""
        for name in fixture_names():
            golden = load(name)
            if golden["tier"] == "none":
                continue
            # converted line -> the reasons recorded for leaving it imperial
            explained: dict[str, list[str]] = {}
            for note in golden["conversion_notes"]:
                explained.setdefault(note[1], []).extend(note[2:])
            for line in markdown_lines(golden["markdown"]):
                if not _MEASURED.search(line):
                    continue
                with self.subTest(fixture=name, line=line):
                    self.assertIn(line, explained,
                                  f"{name}: {line!r} kept a measured imperial unit with no note")
                    self.assertTrue(
                        any(_KEPT_AS_IS in reason for reason in explained[line]),
                        f"{name}: {line!r} kept a measured imperial unit but was not marked "
                        f"{_KEPT_AS_IS!r}: {explained[line]}",
                    )

    def test_the_surviving_imperial_cases_are_the_ones_we_reviewed(self):
        """Pinned on purpose.

        The unit pass refuses to convert a measured spoon or cup when it has no
        density for that ingredient, and guessing would be worse than leaving
        the imperial unit. That leaves a handful of known survivors. This list is
        the review record: a new entry appearing here is not a test to be
        updated, it is a case to decide on -- either add a density so the line
        converts, or confirm the guess would be wrong and leave it alone.
        """
        reviewed = {
            # No density for baking powder, and teaspoons of it are what the
            # recipe means; a gram figure would be a fabrication.
            ("allrecipes_white_cake", "1.75 teaspoons baking powder"),
            ("steepbean_earl_grey", "2 teaspoons baking powder"),
            # Loose tea: a tablespoon of leaf has no fixed mass.
            ("steepbean_earl_grey", "2 tablespoons loose Earl Grey tea leaves, finely ground"),
            ("steepbean_earl_grey",
             "In the same saucepan we melt 115 g of unsalted butter over low heat. Once melted "
             "we add 1 tablespoon of finely ground loose Earl Grey tea leaves directly to the "
             "warm butter."),
            ("steepbean_earl_grey",
             "In a separate bowl we whisk together 150 g all-purpose flour 1 1/2 teaspoons baking "
             "powder and 3 g salt. We add these dry ingredients to our wet mixture in three "
             "additions alternating with gentle folding motions using our rubber spatula."),
            # Yeast: the package is the real measure, the teaspoons are for the label.
            ("simplyrecipes_pizza", "1 package (2 1/4 teaspoons) active dry yeast"),
            # Ground spices and dried fruit, same reasoning.
            ("tasteofhome_chicken_stew", "1/2 teaspoon ground cinnamon"),
            ("tasteofhome_chicken_stew", "1/2 teaspoon pepper"),
            ("tasteofhome_chicken_stew", "1 cup chopped dried apricots"),
        }

        found = set()
        for name in fixture_names():
            golden = load(name)
            if golden["tier"] == "none":
                continue
            for line in markdown_lines(golden["markdown"]):
                if _IMPERIAL.search(line) and _MEASURED.search(line):
                    found.add((name, line))

        self.assertEqual(found - reviewed, set(),
                         "new measured imperial units need a decision, not a new "
                         "allowlist entry")
        self.assertEqual(reviewed - found, set(),
                         "a reviewed case no longer occurs; drop it from the list")

    def test_no_empty_sections_are_faked_away(self):
        """A fixture that finds nothing must say so rather than render a
        placeholder: that placeholder is a review signal, not a defect."""
        for name in fixture_names():
            golden = load(name)
            if golden["tier"] == "none":
                continue
            with self.subTest(fixture=name):
                ing = sum(len(g["lines"]) for g in golden["ingredient_groups"])
                steps = sum(len(g["lines"]) for g in golden["step_groups"])
                if not ing:
                    self.assertIn("- (none found)", golden["markdown"])
                if not steps:
                    self.assertIn("(no instructions found)", golden["markdown"])
                if ing and steps:
                    self.assertNotIn("- (none found)", golden["markdown"])
                    self.assertNotIn("(no instructions found)", golden["markdown"])