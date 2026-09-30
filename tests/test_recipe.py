"""Tests for the recipe extractor and the unit engine.

Run with:
    .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import recipe  # noqa: E402
import units  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "steepbean_earl_grey.html"


class TestUnits(unittest.TestCase):
    def test_cup_of_flour_becomes_grams(self):
        self.assertEqual(units.normalize_line("2 cups all-purpose flour")[0], "240 g all-purpose flour")

    def test_mixed_fraction(self):
        self.assertEqual(units.normalize_line("1½ cups granulated sugar")[0], "300 g granulated sugar")

    def test_liquid_stays_volume(self):
        self.assertEqual(units.normalize_line("1 cup whole milk")[0], "2.4 dl whole milk")

    def test_ounces_to_grams(self):
        self.assertEqual(units.normalize_line("8 oz cream cheese")[0], "225 g cream cheese")

    def test_pounds_to_grams(self):
        self.assertEqual(units.normalize_line("1 lb chicken")[0], "455 g chicken")

    def test_range_keeps_both_ends(self):
        self.assertEqual(units.normalize_line("2 to 3 tbsp panko")[0], "6-9 g panko")

    def test_unknown_dry_ingredient_is_left_alone(self):
        # No density for baking powder, and it is not a liquid: do not guess.
        line, notes = units.normalize_line("2 teaspoons baking powder")
        self.assertEqual(line, "2 teaspoons baking powder")
        self.assertTrue(any("kept as-is" in note for note in notes))

    def test_density_is_scoped_to_the_measured_ingredient(self):
        # "salt" appears later in the sentence but must not give vanilla its density.
        line, _ = units.normalize_line("1 teaspoon of vanilla extract and a pinch of salt")
        self.assertEqual(line, "5 ml of vanilla extract and a pinch of salt")

    def test_vague_amounts_survive(self):
        for line in ("A handful of fresh coriander", "Pinch of salt", "Salt, to taste"):
            self.assertEqual(units.normalize_line(line)[0], line)

    def test_already_metric_is_untouched(self):
        for line in ("1.6 dl milk", "250 g plain/all-purpose flour", "1 kg chicken", "4 dl milk"):
            self.assertEqual(units.normalize_line(line)[0], line)

    def test_fahrenheit_becomes_round_celsius(self):
        self.assertEqual(units.normalize_line("Bake at 350°F")[0], "Bake at 175°C (347°F)")
        self.assertEqual(units.normalize_line("Bake at 325°F")[0], "Bake at 165°C (329°F)")

    def test_existing_celsius_fahrenheit_pair_is_not_doubled(self):
        line = "Bake at 175°C (347°F) for 20 minutes"
        self.assertEqual(units.normalize_line(line)[0], line)

    def test_celsius_gains_fahrenheit(self):
        self.assertEqual(units.normalize_line("Bake at 180C")[0], "Bake at 180°C (356°F)")

    def test_durations_are_not_temperatures(self):
        for line in ("Bake for 40 to 45 minutes", "rest for 30 minutes", "Cool for 10 minutes"):
            self.assertEqual(units.normalize_line(line)[0], line)


class TestProse(unittest.TestCase):
    def test_possessive_becomes_your_not_you(self):
        self.assertEqual(
            recipe.prose_to_steps("In the same saucepan we melt butter."),
            "In the same saucepan you melt butter.",
        )

    def test_our_becomes_your(self):
        # A step that starts with "our" becomes "Your".
        self.assertEqual(recipe.prose_to_steps("our small saucepan"), "Your small saucepan")
        self.assertEqual(recipe.prose_to_steps("in our small saucepan"), "in your small saucepan")

    def test_sentence_start_is_capitalised(self):
        self.assertEqual(recipe.prose_to_steps("our preheated oven provides heat."),
                         "Your preheated oven provides heat.")

    def test_contraction(self):
        self.assertEqual(recipe.prose_to_steps("We’ll walk through this."), "You’ll walk through this.")


class TestConflicts(unittest.TestCase):
    def test_detects_mismatched_quantity(self):
        ingredients = ["240 g all-purpose flour"]
        steps = ["You whisk together 150 g all-purpose flour and baking powder."]
        self.assertEqual(len(recipe.find_conflicts(ingredients, steps)), 1)

    def test_no_conflict_when_quantities_agree(self):
        ingredients = ["150 g all-purpose flour"]
        steps = ["You whisk together 150 g all-purpose flour and baking powder."]
        self.assertEqual(recipe.find_conflicts(ingredients, steps), [])

    def test_ignores_other_quantities_in_the_same_sentence(self):
        # 300 g butter belongs to butter, not to the flour being discussed.
        ingredients = ["150 g all-purpose flour"]
        steps = ["Cream 300 g butter, then add 150 g all-purpose flour to the bowl."]
        self.assertEqual(recipe.find_conflicts(ingredients, steps), [])

    def test_curly_apostrophe_conflict_is_found(self):
        ingredients = ["2.4 dl whole milk, warmed"]
        steps = ["You begin by warming 1.2 dl of whole milk in your small saucepan."]
        self.assertEqual(len(recipe.find_conflicts(ingredients, steps)), 1)


class TestExtraction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = FIXTURE.read_text(encoding="utf-8")
        cls.title, cls.ing, cls.steps, cls.tier = recipe.extract(cls.html)

    def test_finds_the_recipe(self):
        self.assertEqual(self.tier, "heading heuristic")
        self.assertTrue(self.ing)
        self.assertTrue(self.steps)

    def test_affiliate_block_never_leaks(self):
        text = " ".join(l for _, lines in self.ing + self.steps for l in lines).lower()
        for banned in ("view latest price", "as an affiliate", "bigelow", "amazon"):
            self.assertNotIn(banned, text)

    def test_ingredient_subgroups_are_kept(self):
        headings = [h for h, _ in self.ing]
        self.assertIn("For the Cake", headings)
        self.assertIn("For the Earl Grey Frosting", headings)

    def test_method_subgroups_are_kept(self):
        headings = [h for h, _ in self.steps]
        self.assertIn("Prep the Earl Grey Tea", headings)
        self.assertIn("Bake the Cake", headings)

    def test_section_blurbs_are_dropped(self):
        first = self.steps[0][1][0].lower()
        self.assertNotIn("carefully selected", first)
        self.assertFalse(first.startswith("you'll walk through"))

    def test_conflicts_present_in_the_real_recipe(self):
        # The source page really does disagree with itself about these, so the
        # detector must fire once the units are metric.
        flat_ing = [recipe.convert_ingredient(l)[0] for _, lines in self.ing for l in lines]
        flat_steps = [recipe.convert_step(l)[0] for _, lines in self.steps for l in lines]
        conflicts = recipe.find_conflicts(flat_ing, flat_steps)
        self.assertGreaterEqual(len(conflicts), 3)
        joined = " ".join(conflicts).lower()
        for expected in ("flour", "granulated sugar", "milk"):
            self.assertIn(expected, joined)


class TestRender(unittest.TestCase):
    def test_markdown_shape(self):
        markdown = recipe.render("Test Cake", [("For the Cake", ["240 g flour"])],
                                [("Bake", ["Bake for 40 minutes."])])
        self.assertTrue(markdown.startswith("# Test Cake\n\n"))
        self.assertIn("## Ingredients:\n\n### For the Cake\n\n- 240 g flour", markdown)
        self.assertIn("## TODO:\n\n### Bake\n\n1. Bake for 40 minutes.", markdown)

    def test_step_numbers_are_sequential_across_subgroups(self):
        markdown = recipe.render(
            "T", [("", ["1 g a"])], [("One", ["step one"]), ("Two", ["step two"])]
        )
        self.assertIn("1. step one", markdown)
        self.assertIn("2. step two", markdown)


class TestAffiliateUnwrapping(unittest.TestCase):
    """Sponsored links often wrap the ingredient name itself and must be unwrapped."""

    HTML = """<!doctype html><html><head><title>x</title></head><body>
    <div class="entry-content">
      <h2>Ingredients</h2>
      <p>We've organized these ingredients into two groups to build the perfect bowl.</p>
      <h3>Fruit</h3>
      <ul><li>1&#xBD; cups frozen mixed berries</li>
          <li>1 large <a href="https://www.amazon.com/s?k=banana" rel="sponsored nofollow">frozen banana, sliced</a></li>
          <li>&#xBD; cup <a data-il="1" rel="nofollow sponsored">frozen mango chunks</a></li></ul>
      <h2>Instructions</h2>
      <ol><li>Blend everything until smooth and thick.</li>
          <li>Spoon into a bowl and add the toppings on top.</li></ol>
    </div></body></html>"""

    @classmethod
    def setUpClass(cls):
        cls.title, cls.ing, cls.steps, cls.tier = recipe.extract(cls.HTML)

    def test_ingredient_name_inside_a_sponsored_link_survives(self):
        lines = [l for _, ls in self.ing for l in ls]
        self.assertIn("1 large frozen banana, sliced", lines)
        self.assertIn("½ cup frozen mango chunks", lines)  # glyph kept until conversion

    def test_no_truncated_ingredient(self):
        for _, lines in self.ing:
            for line in lines:
                self.assertNotEqual(line.strip(), "1 large")
                self.assertNotEqual(line.strip(), "1/2 cup")

    def test_intro_paragraph_before_subsections_is_dropped(self):
        lines = [l for _, ls in self.ing for l in ls]
        self.assertFalse(any("organized these ingredients" in l for l in lines))

    def test_subsections_are_kept(self):
        self.assertIn("Fruit", [h for h, _ in self.ing])


class TestRecipeFilename(unittest.TestCase):
    def test_simple_title(self):
        self.assertEqual(recipe.recipe_filename("Earl Grey Tea Cake"), "earl_grey_tea_cake.md")

    def test_punctuation_and_ampersand(self):
        self.assertEqual(
            recipe.recipe_filename("Steak & Frites: The Classic"),
            "steak_and_frites_the_classic.md",
        )

    def test_diacritics_become_ascii(self):
        self.assertEqual(recipe.recipe_filename("Crème Brûlée"), "creme_brulee.md")

    def test_digits_are_kept(self):
        self.assertEqual(recipe.recipe_filename("7 Layer Dip"), "7_layer_dip.md")
        self.assertEqual(recipe.recipe_filename("5-Minute Salad"), "5_minute_salad.md")

    def test_long_title_is_truncated_on_word_boundary(self):
        name = recipe.recipe_filename("word " * 40)
        self.assertTrue(name.endswith(".md"))
        self.assertLessEqual(len(name) - 4, 80)
        self.assertFalse(name[:-4].endswith("_"))

    def test_empty_title_falls_back(self):
        self.assertEqual(recipe.recipe_filename("!!! ???"), "recipe.md")

    def test_consecutive_separators_collapse(self):
        self.assertEqual(recipe.recipe_filename("A -- B __ C"), "a_b_c.md")


class TestOutputNaming(unittest.TestCase):
    """The draft is saved under the title and an existing recipe is never clobbered."""

    HTML = """<!doctype html><html><head><title>x</title></head><body>
    <div class="entry-content"><h1>Test Bean Soup</h1>
      <h2>Ingredients</h2><ul><li>1 tablespoon olive oil</li></ul>
      <h2>Instructions</h2><ol><li>Heat the oil in a pan until hot.</li></ol>
    </div></body></html>"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.cwd)
        self.real_fetch = recipe.fetch
        recipe.fetch = lambda url: self.HTML
        self.addCleanup(setattr, recipe, "fetch", self.real_fetch)

    def _run(self, extra):
        args = recipe.build_parser().parse_args(["https://example.com/x"] + extra)
        return recipe.run(args)

    def test_saves_file_named_after_the_title(self):
        self.assertEqual(self._run(["--no-open", "--title", "Test Bean Soup"]), 0)
        self.assertTrue(os.path.exists("test_bean_soup.md"))
        self.assertIn("# Test Bean Soup", Path("test_bean_soup.md").read_text())

    def test_existing_file_is_not_overwritten(self):
        with open("test_bean_soup.md", "w") as handle:
            handle.write("MY HAND EDITED RECIPE")
        code = self._run(["--no-open", "--title", "Test Bean Soup"])
        self.assertEqual(code, 1)
        self.assertEqual(Path("test_bean_soup.md").read_text(), "MY HAND EDITED RECIPE")

    def test_force_overwrites(self):
        with open("test_bean_soup.md", "w") as handle:
            handle.write("OLD")
        self.assertEqual(self._run(["--no-open", "--force", "--title", "Test Bean Soup"]), 0)
        self.assertIn("# Test Bean Soup", Path("test_bean_soup.md").read_text())

    def test_dash_output_writes_nothing(self):
        self.assertEqual(self._run(["-o", "-", "--no-open"]), 0)
        self.assertEqual(os.listdir("."), [])

    def test_explicit_output_path_is_honoured(self):
        self.assertEqual(self._run(["-o", "custom.md", "--no-open"]), 0)
        self.assertTrue(os.path.exists("custom.md"))


class TestVolumeUnitLinting(unittest.TestCase):
    """ml is only wrong where dl would actually read better."""

    def _lint(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.md"
            path.write_text(
                f"# T\n\n## Ingredients:\n\n- {body}\n\n## TODO:\n\n1. Do it.\n",
                encoding="utf-8",
            )
            return recipe.lint(path)

    def test_small_ml_is_fine(self):
        self.assertEqual(self._lint("10 ml vanilla extract"), [])

    def test_tiny_ml_is_fine(self):
        self.assertEqual(self._lint("2 ml cream of tartar"), [])

    def test_large_ml_is_flagged(self):
        self.assertIn(
            "ml where the house style uses dl: 250 ml",
            self._lint("250 ml milk"),
        )

    def test_cl_and_l_are_converted_before_comparing(self):
        self.assertIn(
            "ml where the house style uses dl: 20 cl",
            self._lint("20 cl double cream"),
        )

    def test_litre_is_flagged(self):
        self.assertIn(
            "ml where the house style uses dl: 1.5 l",
            self._lint("1.5 l stock"),
        )


class TestSchemaOrg(unittest.TestCase):
    """Tier 1: JSON-LD schema.org/Recipe, which many big recipe sites provide."""

    HTML = """<!doctype html><html><head><title>x</title>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Recipe","name":"Garlic Butter Shrimp Recipe: Easy",
     "recipeIngredient":["1 lb large shrimp","2 cloves garlic","2 tbsp olive oil","1/2 tsp salt"],
     "recipeInstructions":[
       {"@type":"HowToStep","text":"Peel the garlic."},
       {"@type":"HowToStep","text":"Heat the oil in a pan over medium heat until hot."},
       {"@type":"HowToStep","text":"Cook at 400F for 4 minutes."}]}
    </script></head><body><div class="entry-content">
    <p>Affiliate noise that must not appear.</p></div></body></html>"""

    @classmethod
    def setUpClass(cls):
        cls.title, cls.ing, cls.steps, cls.tier = recipe.extract(cls.HTML)

    def test_uses_the_schema_tier(self):
        self.assertEqual(self.tier, "schema.org/Recipe")

    def test_title_is_shortened(self):
        self.assertEqual(self.title, "Garlic Butter Shrimp")

    def test_ingredients_are_read(self):
        lines = [l for _, ls in self.ing for l in ls]
        self.assertEqual(len(lines), 4)

    def test_howtostep_objects_are_read(self):
        steps = [l for _, ls in self.steps for l in ls]
        self.assertEqual(len(steps), 3)
        self.assertIn("Peel the garlic.", steps)

    def test_body_text_is_not_mistaken_for_ingredients(self):
        text = " ".join(l for _, ls in self.ing + self.steps for l in ls)
        self.assertNotIn("Affiliate noise", text)


class TestLint(unittest.TestCase):
    def test_clean_repo_file_passes(self):
        self.assertEqual(recipe.lint(ROOT / "dinner" / "broccoli_cauliflower_gratin.md"), [])

    def test_cups_are_flagged(self):
        path = Path(self.enterContext(__import__("tempfile").TemporaryDirectory())) / "x.md"
        path.write_text("# X\n\n## Ingredients:\n\n- 2 cups flour\n\n## TODO:\n\n1. a\n",
                        encoding="utf-8")
        problems = recipe.lint(path)
        self.assertTrue(any("cup" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
