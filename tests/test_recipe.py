"""Tests for the recipe extractor and the unit engine.

Run with:
    .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import classify  # noqa: E402
import recipe  # noqa: E402
import sources  # noqa: E402
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

    def test_norwegian_letters_are_transliterated_not_dropped(self):
        self.assertEqual(
            recipe.recipe_filename("Enkelt surdeigsbrød i form"), "enkelt_surdeigsbrod_i_form.md"
        )
        self.assertEqual(
            recipe.recipe_filename("Rask gryte med grønnsaker og svinekjøtt"),
            "rask_gryte_med_gronnsaker_og_svinekjott.md",
        )
        self.assertEqual(
            recipe.recipe_filename("Glutenfri julekaker og påskeferdig bløtkake"),
            "glutenfri_julekaker_og_paskeferdig_blotkake.md",
        )

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
            "house style uses dl: write 250 ml as 2.5 dl",
            self._lint("250 ml milk"),
        )

    def test_cl_and_l_are_converted_before_comparing(self):
        self.assertIn(
            "house style uses dl: write 20 cl as 2 dl",
            self._lint("20 cl double cream"),
        )

    def test_litre_is_flagged(self):
        self.assertIn(
            "house style uses dl: write 1.5 l as 15 dl",
            self._lint("1.5 l stock"),
        )


class TestCleanUrl(unittest.TestCase):
    def test_tracking_params_are_dropped(self):
        self.assertEqual(
            sources.clean_url("https://a.com/r?x=1&utm_source=list&utm_medium=x&usg=abc"),
            "https://a.com/r?x=1",
        )

    def test_fragment_is_dropped(self):
        self.assertEqual(sources.clean_url("https://a.com/r#ingredients"), "https://a.com/r")

    def test_meaningful_query_survives(self):
        self.assertEqual(
            sources.clean_url("https://a.com/r?m=1&id=7"),
            "https://a.com/r?m=1&id=7",
        )

    def test_non_http_is_rejected(self):
        for bad in ("mailto:a@b.com", "ftp://a.com/x", "javascript:void(0)", "", "   "):
            self.assertEqual(sources.clean_url(bad), "")


class TestDedupe(unittest.TestCase):
    def test_order_is_preserved_and_first_title_wins(self):
        pairs = [("https://a.com/1", "First"), ("https://a.com/2", None), ("https://a.com/1", "Second")]
        self.assertEqual(sources.dedupe(pairs), [("https://a.com/1", "First"), ("https://a.com/2", None)])

    def test_urls_differing_only_by_tracking_are_one_entry(self):
        pairs = [("https://a.com/1?utm_source=x", "T"), ("https://a.com/1", "T2")]
        self.assertEqual(len(sources.dedupe(pairs)), 1)

    def test_unusable_urls_are_dropped(self):
        self.assertEqual(sources.dedupe([("mailto:a@b.com", None), ("https://a.com/", "X")]),
                         [("https://a.com/", "X")])


class TestGoogleSavedList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = Path(__file__).parent / "fixtures" / "google_saved_list.html"
        cls.pairs = sources.links_from_google_list(fixture.read_text(encoding="utf-8"))

    def test_finds_every_saved_item(self):
        self.assertEqual(
            [url for url, _ in self.pairs],
            [
                "https://www.matprat.no/oppskrifter/kos/brent-baskisk-ostekake/",
                "https://nuftenoft.wordpress.com/2009/02/24/saftig-svinestek/",
                "https://www.godt.no/mat/oppskrifter/kake/glutenfrie-julekaker",
            ],
        )

    def test_title_comes_from_aria_label(self):
        self.assertEqual(self.pairs[0][1], "Brent baskisk ostekake")

    def test_title_falls_back_to_anchor_text_and_strips_site_name(self):
        self.assertEqual(self.pairs[1][1], "Saftig svinestek med søor")

    def test_untitled_item_is_none_not_empty_string(self):
        self.assertIsNone(self.pairs[2][1])

    def test_tracking_params_stripped(self):
        self.assertNotIn("utm_source", self.pairs[1][0])
        self.assertNotIn("usg", self.pairs[1][0])

    def test_duplicate_item_is_collapsed(self):
        self.assertEqual(len(self.pairs), 3)

    def test_google_navigation_links_are_not_items(self):
        self.assertFalse(any("google.no/save" in url for url, _ in self.pairs))


class TestTextFileSource(unittest.TestCase):
    def _write(self, body):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "links.txt"
        path.write_text(body, encoding="utf-8")
        return path

    def test_reads_urls_and_ignores_comments_and_blanks(self):
        path = self._write("# my list\n\nhttps://a.com/1\n  \nhttps://a.com/2 # inline\n")
        self.assertEqual(
            sources.links_from_text_file(path),
            [("https://a.com/1", None), ("https://a.com/2", None)],
        )

    def test_missing_file_is_a_clear_error(self):
        with self.assertRaises(sources.SourceError) as caught:
            sources.discover("/nonexistent/links.txt")
        self.assertIn("No such file", str(caught.exception))


class TestDiscoverErrors(unittest.TestCase):
    LOGIN_PAGE = "<html><body><a href='https://accounts.google.com'>Sign in</a></body></html>"

    def test_private_list_explains_how_to_proceed(self):
        with self.assertRaises(sources.SourceError) as caught:
            sources.discover("https://www.google.com/collections/s/list/abc", html=self.LOGIN_PAGE)
        message = str(caught.exception)
        self.assertIn("private", message)
        self.assertIn("-x links.txt", message)

    def test_page_that_is_not_a_saved_list(self):
        with self.assertRaises(sources.SourceError) as caught:
            sources.discover("https://example.com/blog", html="<html><body><p>hi</p></body></html>")
        self.assertIn("No saved items", str(caught.exception))


class TestClassify(unittest.TestCase):
    """Every case here is a real Norwegian title from a saved list, and every one
    of them was a bug before it was a test."""

    def _folder(self, title):
        return classify.classify_category(title)[0]

    def test_compound_words_still_match(self):
        # "gryte" and "kylling" are both buried inside one compound noun.
        self.assertEqual(self._folder("Enkel kyllinggryte med kikerter"), "dinner")
        self.assertEqual(self._folder("Makaronigrateng med kylling og brokkoli"), "dinner")

    def test_juicy_is_not_juice(self):
        # "Saftig" (juicy) contains "saft" (juice); a substring match filed
        # whole stews under drinks.
        self.assertEqual(self._folder("Saftig svinestek med søor i leirgryte."), "dinner")

    def test_cheesecake_is_not_steak(self):
        # "ostekake" contains "stek".
        self.assertEqual(self._folder("Brent baskisk ostekake"), "dessert")

    def test_school_start_is_not_a_tart(self):
        self.assertEqual(self._folder("Skolestart - MatPrat"), "unclassified")

    def test_hamburger_bread_is_bread_not_a_burger(self):
        self.assertEqual(self._folder("Hamburgerbrød"), "bread")

    def test_bread_and_dessert_conflict_stays_unclassified(self):
        # Chocolate banana bread: genuinely two categories.
        self.assertEqual(self._folder("GLUTENFRITT BANANBRØD MED SJOKOLADE"), "unclassified")

    def test_non_recipe_titles_are_unclassified(self):
        for title in ("Alt i en form", "Boeuf Bourguignon - Klassisk fransk", ""):
            self.assertEqual(self._folder(title), "unclassified")

    def test_confidence_is_zero_when_unclassified_by_score(self):
        self.assertEqual(classify.classify_category("Alt i en form")[1], 0.0)

    def test_english_titles_are_handled(self):
        self.assertEqual(self._folder("Butter chicken"), "dinner")
        self.assertEqual(self._folder("Classic chocolate chip cookies"), "dessert")

    def test_target_path_lands_in_normies(self):
        path = classify.target_path("Enkel kyllinggryte med kikerter", Path("/repo"))
        self.assertEqual(
            path, Path("/repo/normies/dinner/enkel_kyllinggryte_med_kikerter.md")
        )

    def test_folder_override_wins(self):
        path = classify.target_path("Enkel kyllinggryte", Path("/repo"), folder_override="unclassified")
        self.assertEqual(path.parent, Path("/repo/normies/unclassified"))


class TestBatchMode(unittest.TestCase):
    """Two or more URLs run as a batch and land under normies/."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.cwd)
        self.real_fetch = recipe.fetch
        self.addCleanup(setattr, recipe, "fetch", self.real_fetch)

        def fake_fetch(url):
            if "missing" in url:
                raise requests.HTTPError("404 Client Error")
            if "empty" in url:
                return "<html><body><p>nothing here</p></body></html>"
            titles = {
                "https://a.com/dinner": "Butter chicken",
                "https://a.com/one": "Butter chicken",
                "https://b.com/two": "Chocolate cake",
                "https://b.com/three": "Chocolate cake",
                "https://b.com/dessert": "Chocolate cake",
            }
            title = titles.get(url, "Chocolate cake")
            return (
                f"<!doctype html><html><head><title>x</title></head><body>"
                f'<div class="entry-content"><h1>{title}</h1>'
                f"<h2>Ingredients</h2><ul><li>1 tablespoon olive oil</li></ul>"
                f"<h2>Instructions</h2><ol><li>Heat the oil until hot and add the vegetables.</li></ol>"
                f"</div></body></html>"
            )

        recipe.fetch = fake_fetch

    def _run(self, argv):
        return recipe.run(recipe.build_parser().parse_args(argv))

    def test_two_urls_write_into_normies_by_category(self):
        self._run(["https://a.com/dinner", "https://b.com/dessert", "--delay", "0"])
        self.assertTrue(os.path.exists("normies/dinner/butter_chicken.md"))
        self.assertTrue(os.path.exists("normies/dessert/chocolate_cake.md"))

    def test_single_url_does_not_touch_normies(self):
        self._run(["https://a.com/dinner", "--no-open"])
        self.assertTrue(os.path.exists("butter_chicken.md"))
        self.assertFalse(os.path.exists("normies"))

    def test_same_title_twice_gets_a_numbered_name(self):
        self._run(["https://b.com/one", "https://b.com/two", "--delay", "0"])
        self.assertTrue(os.path.exists("normies/dessert/chocolate_cake.md"))
        self.assertTrue(os.path.exists("normies/dessert/chocolate_cake_2.md"))

    def test_one_failure_does_not_abort_the_batch(self):
        code = self._run(
            ["https://missing.com/x", "https://a.com/dinner", "https://empty.com/y", "--delay", "0"]
        )
        self.assertEqual(code, 1)  # one page failed hard, so the run reports failure
        self.assertTrue(os.path.exists("normies/dinner/butter_chicken.md"))

    def test_hard_failure_gives_exit_code_one(self):
        self.assertEqual(self._run(["https://missing.com/x", "--delay", "0"]), 1)

    def test_existing_file_is_skipped_on_a_second_run(self):
        self._run(["https://a.com/dinner", "--delay", "0"])
        self._run(["https://a.com/dinner", "--delay", "0"])
        self.assertFalse(os.path.exists("normies/dinner/chicken_dinner_2.md"))

    def test_refresh_overwrites_in_place(self):
        self._run(["https://a.com/dinner", "https://b.com/two", "--delay", "0"])
        Path("normies/dinner/butter_chicken.md").write_text("STALE", encoding="utf-8")
        self._run(["https://a.com/dinner", "https://b.com/two", "--delay", "0", "--refresh"])
        self.assertIn("# Butter chicken", Path("normies/dinner/butter_chicken.md").read_text())
        self.assertFalse(os.path.exists("normies/dinner/chicken_dinner_2.md"))

    def test_output_flag_is_rejected_for_a_batch(self):
        self.assertEqual(self._run(["https://a.com/x", "https://b.com/y", "-o", "out.md"]), 1)

    def test_folder_override(self):
        self._run(["https://a.com/dinner", "https://b.com/two",
                   "--folder", "unclassified", "--delay", "0"])
        self.assertTrue(os.path.exists("normies/unclassified/butter_chicken.md"))

    def test_folder_override_is_rejected_for_a_single_url(self):
        self.assertEqual(
            self._run(["https://a.com/dinner", "--folder", "unclassified"]), 1
        )

    def test_limit_caps_the_batch(self):
        self._run(["https://a.com/one", "https://b.com/two", "https://b.com/three",
                   "--delay", "0", "--limit", "2"])
        self.assertTrue(os.path.exists("normies/dinner/butter_chicken.md"))
        self.assertFalse(os.path.exists("normies/dessert/chocolate_cake_2.md"))

    def test_recipe_without_instructions_is_not_written(self):
        self.assertEqual(self._run(["https://empty.com/y"]), 1)
        self.assertEqual(os.listdir("."), [])

    def test_recipe_without_instructions_does_not_stop_the_batch(self):
        code = self._run(["https://empty.com/y", "https://a.com/dinner", "--delay", "0"])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists("normies/dinner/butter_chicken.md"))
        self.assertEqual(sorted(os.listdir("normies")), ["dinner"])

    def test_dry_run_fetches_nothing_and_writes_nothing(self):
        code = self._run(["https://a.com/dinner", "https://b.com/x", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(os.listdir("."), [])


class TestNextFreeName(unittest.TestCase):
    def test_first_duplicate_gets_underscore_two(self):
        path = Path("normies/dessert/kake.md")
        self.assertEqual(recipe.next_free_name(path, {path}), Path("normies/dessert/kake_2.md"))

    def test_skips_names_already_taken(self):
        base = Path("normies/dessert/kake.md")
        taken = {base, Path("normies/dessert/kake_2.md")}
        self.assertEqual(recipe.next_free_name(base, taken), Path("normies/dessert/kake_3.md"))

    def test_unrelated_name_is_returned_unchanged(self):
        self.assertEqual(
            recipe.next_free_name(Path("a/b.md"), {Path("a/c.md")}), Path("a/b.md")
        )


class TestLintAllIncludesNormies(unittest.TestCase):
    def test_normies_are_walked_recursively(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)
        (root / "dinner").mkdir()
        (root / "normies" / "dinner").mkdir(parents=True)
        (root / "bread").mkdir()
        for path in ("dinner/a.md", "bread/b.md", "normies/dinner/c.md", "normies/dinner/d.md"):
            (root / path).write_text("x", encoding="utf-8")
        found = [str(p.relative_to(root)) for p in recipe.recipe_folder_paths(root)]
        self.assertEqual(sorted(found), ["bread/b.md", "dinner/a.md",
                                         "normies/dinner/c.md", "normies/dinner/d.md"])


class TestEscapedEntities(unittest.TestCase):
    """godfisk.no and friends put escaped HTML inside the JSON-LD strings.

    Nothing decodes entities inside a <script> element, so these used to reach
    the recipe file as `p&aring; 200 &deg;C` and the temperature converter
    never saw them.
    """

    def _schema_page(self, payload):
        return (
            '<!doctype html><html><head><title>x</title>'
            f'<script type="application/ld+json">{payload}</script>'
            '</head><body><h1>Sei med rotgr&oslash;nnsaker</h1></body></html>'
        )

    def test_entities_in_plain_string_instructions_are_decoded(self):
        payload = json.dumps(
            {
                "@type": "Recipe",
                "name": "Sei med dillsaus",
                "recipeIngredient": ["600 g seifilet"],
                "recipeInstructions": [
                    "Sett stekeovnen p&aring; 200 &deg;C. "
                    "Skj&aelig;r seien, server med poteter.&nbsp;"
                ],
            }
        )
        page = self._schema_page(payload)
        _, _, steps, tier = recipe.extract(page)
        self.assertEqual(tier, "schema.org/Recipe")
        self.assertEqual(len(steps), 1)
        self.assertEqual(
            steps[0][1][0], "Sett stekeovnen på 200 °C. Skjær seien, server med poteter."
        )

    def test_temperature_is_converted_once_entities_are_decoded(self):
        payload = json.dumps(
            {
                "@type": "Recipe",
                "name": "Torsk i ovn",
                "recipeIngredient": ["600 g torsk"],
                "recipeInstructions": ["Sett stekeovnen p&aring; 200 &deg;C."],
            }
        )
        # Mirror the CLI pipeline: extract, convert, render.
        title, ingredients, steps, _ = recipe.extract(self._schema_page(payload))
        converted = [(h, [recipe.convert_step(line)[0] for line in lines]) for h, lines in steps]
        rendered = recipe.render(title, ingredients, converted)
        self.assertIn("200°C (392°F)", rendered)
        self.assertNotIn("&deg;", rendered)

    def test_entities_in_name_and_ingredients_are_decoded(self):
        payload = json.dumps(
            {
                "@type": "Recipe",
                "name": "Brent b&aring;skisk ostekake",
                "recipeIngredient": ["200 g sm&oslash;r", "1 ss vaniljesukker"],
                "recipeInstructions": ["Stek i 35 minutter."],
            }
        )
        title, ingredients, _, _ = recipe.extract(self._schema_page(payload))
        self.assertEqual(title, "Brent båskisk ostekake")
        self.assertEqual(ingredients[0][1], ["200 g smør", "1 ss vaniljesukker"])

    def test_entities_in_howtostep_dict_text_are_decoded(self):
        payload = json.dumps(
            {
                "@type": "Recipe",
                "name": "Kake",
                "recipeIngredient": ["2 egg"],
                "recipeInstructions": [
                    {
                        "@type": "HowToStep",
                        "text": "Sett ovnen p&aring; 180 &deg;C og r&oslash;r sm&oslash;ret.",
                    }
                ],
            }
        )
        _, _, steps, _ = recipe.extract(self._schema_page(payload))
        self.assertEqual(steps[0][1][0], "Sett ovnen på 180 °C og rør smøret.")

    def test_nbsp_becomes_a_plain_space(self):
        page = (
            '<!doctype html><html><body><div class="entry-content">'
            "<h1>Kake</h1><h2>Ingredients</h2><ul><li>2 egg</li></ul>"
            "<h2>Instructions</h2><ol><li>R&oslash;r sm&oslash;ret godt "
            "og hvil i ti minutter her p&aring; siden.</li></ol></div></body></html>"
        )
        _, _, steps, tier = recipe.extract(page)
        self.assertEqual(tier, "heading heuristic")
        self.assertEqual(steps[0][1][0], "Rør smøret godt og hvil i ti minutter her på siden.")

    def test_entities_are_unescaped_only_once(self):
        # A genuinely doubled entity must not collapse all the way to "&".
        self.assertEqual(recipe.clean_text("kongen &amp;amp; megleren"), "kongen &amp; megleren")
        self.assertEqual(recipe.clean_text("salt &amp; pepper"), "salt & pepper")


class TestCharsetDecoding(unittest.TestCase):
    """nrk.no sends a bare `text/html`, and requests would guess ISO-8859-1."""

    class FakeResponse:
        def __init__(self, content, encoding, apparent="utf-8"):
            self.content = content
            self.encoding = encoding
            self.apparent_encoding = apparent

        @property
        def text(self):
            return self.content.decode(self.encoding or "utf-8", errors="replace")

    def test_meta_charset_wins_when_the_header_omits_one(self):
        body = '<html><head><meta charset="utf-8"></head><body>smør</body></html>'
        decoded = recipe.decode_response(self.FakeResponse(body.encode("utf-8"), "ISO-8859-1"))
        self.assertIn("smør", decoded)

    def test_norwegian_letters_survive_the_iso8859_default(self):
        body = "<html><body>også, blåbærsyltetøy</body></html>"
        decoded = recipe.decode_response(self.FakeResponse(body.encode("utf-8"), "ISO-8859-1"))
        self.assertIn("også, blåbærsyltetøy", decoded)
        self.assertNotIn("Ã", decoded)

    def test_explicit_latin1_header_is_trusted(self):
        body = "<html><body>sm\xf8r</body></html>".encode("latin-1")
        decoded = recipe.decode_response(self.FakeResponse(body, "ISO-8859-1"))
        self.assertIn("sm\xf8r", decoded)


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


# --- plugin-markup recipes that have no JSON-LD -------------------------------
# Three shapes showed up in the saved Google lists. None of them carry
# schema.org data, so the extractor has to read the rendered markup, and each
# one broke a different assumption in the heading heuristic.

WP_ZOOM = """<html><body><div class="entry-content">
  <div class="wp-block-wpzoom-recipe-card-block-recipe-card">
    <h3>Ingredienser</h3>
    <ul><li class="wpzoom-rcb-ingredient">200 g hvetemel</li>
        <li class="wpzoom-rcb-ingredient">2 ss olje</li></ul>
    <h3>Fremgangsmåte</h3>
    <p>Bland alle ingrediensene godt sammen i en bolle.</p>
  </div>
</div></body></html>"""

TABLE_CARD = """<html><body><article class="post">
  <h3>Ingredienser</h3>
  <table class="ingredients"><tbody><tr><td>300 g kylling</td></tr>
    <tr><td>1 ss olje</td></tr></tbody></table>
  <h3>SLIK GJØR DU</h3>
  <ol><li>Stek kyllingen gylden på alle sider i oljen.</li></ol>
</article></body></html>"""

LABEL_PARAGRAPHS = """<html><body><div class="entry-content">
  <p>Denne oppskriften gir et saftig brød med bokhvete og havre.</p>
  <p>Du trenger</p>
  <ul><li>200 g havregryn</li><li>200 g bokhvetemel</li><li>2 ts salt</li></ul>
  <p>Utstyr</p>
  <ul><li>Kjøkkenmaskin med k-spade</li></ul>
  <p>Slik gjør du</p>
  <p>1. Bland sammen alle de tørre ingrediensene i en bolle.</p>
  <p>2. Elt deigen i ti minutter på middels hastighet.</p>
</div></body></html>"""


class TestPluginMarkup(unittest.TestCase):
    def test_junk_selector_cannot_delete_the_recipe_card(self):
        # '[class*="recipe-card"]' exists to strip sidebar widgets, but the main
        # card on detgladekjokken.no has "recipe-card" in its class too.
        title, ing, steps, tier = recipe.extract(WP_ZOOM)
        self.assertEqual(tier, "heading heuristic")
        self.assertEqual(title, "")
        self.assertEqual([l for _, lines in ing for l in lines],
                         ["200 g hvetemel", "2 ss olje"])
        self.assertEqual(len([l for _, lines in steps for l in lines]), 1)

    def test_h3_headings_are_read(self):
        title, ing, steps, tier = recipe.extract(WP_ZOOM)
        self.assertEqual(tier, "heading heuristic")
        self.assertEqual(ing[0][0], "Ingredienser")
        self.assertEqual(steps[0][0], "Fremgangsmåte")

    def test_table_rows_are_ingredients(self):
        title, ing, steps, tier = recipe.extract(TABLE_CARD)
        self.assertEqual([l for _, lines in ing for l in lines],
                         ["300 g kylling", "1 ss olje"])
        self.assertEqual([l for _, lines in steps for l in lines],
                         ["Stek kyllingen gylden på alle sider i oljen."])

    def test_label_paragraphs_are_treated_as_headings(self):
        title, ing, steps, tier = recipe.extract(LABEL_PARAGRAPHS)
        self.assertEqual([l for _, lines in ing for l in lines],
                         ["200 g havregryn", "200 g bokhvetemel", "2 ts salt"])
        self.assertEqual(len([l for _, lines in steps for l in lines]), 2)

    def test_equipment_list_is_not_an_ingredient_list(self):
        title, ing, _, _ = recipe.extract(LABEL_PARAGRAPHS)
        self.assertNotIn("Kjøkkenmaskin med k-spade", [l for _, g in ing for l in g])

    def test_prose_steps_are_not_numbered_twice(self):
        _, ing, steps, _ = recipe.extract(LABEL_PARAGRAPHS)
        rendered = recipe.render("Brød", ing, steps)
        self.assertIn("1. Bland sammen alle de tørre ingrediensene i en bolle.", rendered)
        self.assertNotIn("1. 1.", rendered)
        self.assertNotIn("2. 2.", rendered)


# --- shapes that have no heading in front of the ingredient list ---------------

DIV_ROWS = """<html><body><article class="recipe">
  <div class="data__ingredients">
    <div class="ing__row"><span>1</span><span>stk kyllingskrog</span></div>
    <div class="ing__row"><span>2</span><span>stk purreløk</span></div>
    <div class="ing__row"><span>4</span><span>l vann</span></div>
  </div>
  <h2>Slik gjør du</h2>
  <ol><li>Skyll kyllingen og kutt den i biter.</li></ol>
</article></body></html>"""

ADJACENT_LIST = """<html><body><article>
  <h2>Hønsebuljong</h2>
  <ul><li>3 stk verpehøner</li><li>4 l vann</li><li>1 ts salt</li></ul>
  <h2>Slik gjør du</h2>
  <ol><li>Blansjer kyllingen i saltede vann i tjue minutter.</li></ol>
</article></body></html>"""

METHOD_ONLY = """<html><body><div class="entry-content">
  <h2>Instructions</h2>
  <ol><li>Heat the oil until hot and add the vegetables to the pan.</li></ol>
</div></body></html>"""


class TestHeadinglessIngredientLists(unittest.TestCase):
    def test_div_rows_read_as_separate_ingredients(self):
        # denstoltehane.no renders 7 "ingredienser__data--row" divs and no list
        # tag, so the wrapper used to collapse onto one line.
        title, ing, steps, tier = recipe.extract(DIV_ROWS)
        self.assertEqual([l for _, lines in ing for l in lines],
                         ["1 stk kyllingskrog", "2 stk purreløk", "4 l vann"])
        self.assertEqual(len([l for _, lines in steps for l in lines]), 1)

    def test_list_directly_above_the_method_is_the_ingredient_list(self):
        # dn.no gives no "Ingredients" heading at all.
        title, ing, steps, tier = recipe.extract(ADJACENT_LIST)
        self.assertEqual([l for _, lines in ing for l in lines],
                         ["3 stk verpehøner", "4 l vann", "1 ts salt"])
        self.assertEqual(len([l for _, lines in steps for l in lines]), 1)

    def test_bulleted_notes_are_not_mistaken_for_ingredients(self):
        notes = ADJACENT_LIST.replace(
            "<ul><li>3 stk verpehøner</li><li>4 l vann</li><li>1 ts salt</li></ul>",
            "<ul><li>Great with potatoes</li><li>Also good cold</li></ul>",
        )
        title, ing, steps, tier = recipe.extract(notes)
        self.assertEqual(ing, [])

    def test_repeated_ingredient_block_is_not_read_as_instructions(self):
        # The page repeats its ingredient list after the method.
        repeated = TABLE_CARD.replace(
            "</ol>", "</ol><h3>Ingredienser</h3><ul><li>1 stk løk</li></ul>",
        )
        title, ing, steps, tier = recipe.extract(repeated)
        self.assertEqual([l for _, lines in ing for l in lines],
                         ["300 g kylling", "1 ss olje"])
        self.assertEqual([l for _, lines in steps for l in lines],
                         ["Stek kyllingen gylden på alle sider i oljen."])


class TestMethodWithoutIngredients(unittest.TestCase):
    """Instructions with no ingredient list are as unusable as the reverse."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.cwd)
        self.real_fetch = recipe.fetch
        self.addCleanup(setattr, recipe, "fetch", self.real_fetch)
        recipe.fetch = lambda url: METHOD_ONLY

    def test_batch_writes_nothing(self):
        recipe.run(recipe.build_parser().parse_args(["https://a.com/x", "https://a.com/y",
                                                     "--delay", "0"]))
        written = [p for p in Path("normies").rglob("*.md")] if Path("normies").exists() else []
        self.assertEqual(written, [])

    def test_single_url_fails(self):
        self.assertEqual(recipe.run(recipe.build_parser().parse_args(["https://a.com/x"])), 1)


# ---------------------------------------------------------------- both languages
# The saved lists are Norwegian but English pages are the common case elsewhere,
# so every rule that reads a heading or a widget label has to answer to both.


ENGLISH_CARD = """<html><body><div class="entry-content">
  <h2>What You'll Need</h2>
  <ul><li>2 cups all-purpose flour</li><li>1 stick unsalted butter</li>
      <li>3 oz dark chocolate</li><li>1/2 cup granulated sugar</li>
      <li>2 large eggs</li><li>1 tsp vanilla extract</li><li>1/4 tsp salt</li></ul>
  <h2>Directions</h2>
  <ol><li>Preheat the oven to 350 F and line a baking tray.</li>
      <li>Cream the butter and sugar together until pale, then beat in the eggs.</li>
      <li>Fold in the flour, the chocolate and a pinch of salt.</li></ol>
  <p>Print Recipe</p>
</div></body></html>"""

NORWEGIAN_CARD = """<html><body><div class="entry-content">
  <h2>Hva du trenger</h2>
  <ul><li>2 dl hvetemel</li><li>1 pk smør</li><li>3 oz mørk sjokolade</li>
      <li>2 ss sukker</li><li>2 egg</li><li>1 ts vaniljeekstrakt</li><li>1 ts salt</li></ul>
  <h2>Slik lager du</h2>
  <ol><li>Forvarm ovnen til 175 grader og klekk et tray.</li>
      <li>Pisk smør og sukker til det er lyst, og tilsett eggene.</li>
      <li>Vend inn melet, sjokoladen og en knapp spiss salt.</li></ol>
  <p>Skriv ut</p>
</div></body></html>"""


class TestEnglishPages(unittest.TestCase):
    def test_extracts_like_the_norwegian_equivalent(self):
        title, ing, steps, tier = recipe.extract(ENGLISH_CARD)
        self.assertEqual(tier, "heading heuristic")
        self.assertEqual(sum(len(lines) for _, lines in ing), 7)
        self.assertEqual(sum(len(lines) for _, lines in steps), 3)

    def test_widget_button_does_not_become_a_step(self):
        _, _, steps, _ = recipe.extract(ENGLISH_CARD)
        self.assertNotIn("Print Recipe", [l for _, g in steps for l in g])


class TestNorwegianPages(unittest.TestCase):
    def test_slik_lager_du_is_a_method_heading(self):
        _, _, steps, _ = recipe.extract(NORWEGIAN_CARD)
        self.assertEqual(sum(len(lines) for _, lines in steps), 3)

    def test_widget_button_does_not_become_a_step(self):
        _, _, steps, _ = recipe.extract(NORWEGIAN_CARD)
        self.assertNotIn("Skriv ut", [l for _, g in steps for l in g])


class TestEnglishUnits(unittest.TestCase):
    def test_imperial_measures_become_metric(self):
        self.assertEqual(units.normalize_line("2 sticks butter")[0], "225 g butter")
        self.assertEqual(units.normalize_line("3 oz dark chocolate")[0], "85 g dark chocolate")
        self.assertEqual(units.normalize_line("1 cup sugar")[0], "200 g sugar")
        self.assertEqual(units.normalize_line("2 tbsp olive oil")[0], "0.3 dl olive oil")

    def test_fahrenheit_gets_a_celsius_pair(self):
        text, notes = units.convert_temperature("bake at 350 F")
        self.assertEqual(text, "bake at 175°C (347°F)")
        self.assertTrue(notes)

    def test_cup_of_flour_uses_density(self):
        self.assertEqual(units.normalize_line("2 cups all-purpose flour")[0], "240 g all-purpose flour")


class TestBilingualVocabulary(unittest.TestCase):
    HEADINGS = {
        recipe._INGREDIENT_HEADINGS: [
            "Ingredients", "What You'll Need", "You'll need this", "What to Buy",
            "Ingredienser", "Du trenger", "Hva du trenger", "Ingredienser til",
        ],
        recipe._METHOD_HEADINGS: [
            "Instructions", "Directions", "Method", "Steps", "How to Make It",
            "Let's Cook", "Fremgangsmåte", "Slik gjør du", "Slik lager du", "Tilberedning",
        ],
    }

    def test_every_heading_spelling_is_recognised(self):
        for pattern, headings in self.HEADINGS.items():
            for heading in headings:
                self.assertTrue(pattern.match(heading), f"{pattern.pattern[:20]} misses {heading!r}")

    def test_ingredient_words_look_measured_in_both_languages(self):
        for line in ("2 cups flour", "1 tbsp olive oil", "3 oz butter", "1 lb beef",
                     "2 large eggs", "1 onion", "4 l vann", "2 ss rømme", "3 stk løk",
                     "200 g hvetemel", "1 pinch of salt"):
            self.assertTrue(recipe._MEASURED_INGREDIENT.match(line), f"{line!r} not measured")

    def test_prose_is_not_measured(self):
        for line in ("Great with potatoes", "1. Heat the oil", "Salt and pepper to taste"):
            self.assertIsNone(recipe._MEASURED_INGREDIENT.match(line), f"{line!r} looks measured")

    def test_widget_labels_are_junk_in_both_languages(self):
        for text in ("Print Recipe", "Save Recipe", "Skriv ut", "Del", "Kommenter", "Følg oss"):
            self.assertTrue(recipe.is_junk(text), f"{text!r} not treated as junk")

    def test_ingredients_are_never_junk(self):
        # "cookies" and "del" both start inside real ingredients and steps.
        for text in ("225 g chocolate cookies", "1 cup cookies and cream",
                     "Delicious and chewy", "Del deigen i to like biter"):
            self.assertFalse(recipe.is_junk(text), f"{text!r} wrongly treated as junk")

    def test_keywords_survive_norwegian_letters(self):
        # A plain [a-z] class reduced "rømme" to "mme" and disabled the check.
        self.assertEqual(recipe.ingredient_keyword("2 ss rømme"), "rømme")
        self.assertEqual(recipe.ingredient_keyword("200 g hvetemel"), "hvetemel")
        self.assertEqual(recipe.ingredient_keyword("3 oz butter"), "butter")


class TestEnglishClassification(unittest.TestCase):
    CASES = {
        "Beef Stew": "dinner", "Vegetable Soup": "dinner", "French Onion Soup": "dinner",
        "Turkey Roast": "dinner", "Lasagna": "dinner", "Potato Salad": "dinner",
        "Mashed Potatoes": "dinner", "Grilled Cheese Sandwich": "dinner",
        "Eggs Benedict": "dinner", "Shepherd's Pie": "dinner", "Onion Rings": "dinner",
        "Pancakes": "dinner", "Beef Wellington": "dinner",
        "Vanilla Ice Cream": "dessert", "Macarons": "dessert", "Chocolate Chip Cookies": "dessert",
        "Apple Pie": "dessert", "Chocolate Mousse": "dessert", "Banana Pudding": "dessert",
        "Sourdough Bread": "bread", "Banana Bread": "bread", "Garlic Bread": "bread",
        "Chocolate Chip Scones": "bread", "Homemade Pizza": "dinner",
        "Lemonade": "drinks", "Iced Coffee": "drinks", "Strawberry Smoothie": "drinks",
        "Mint Tea": "drinks",
    }

    def test_english_titles_land_in_the_right_folder(self):
        for title, folder in self.CASES.items():
            got, _ = classify.classify_category(title)
            self.assertEqual(got, folder, f"{title!r} -> {got}, wanted {folder}")

    def test_norwegian_titles_are_unaffected(self):
        for title, folder in {
            "Kyllinggryte": "dinner", "Surdeigsbrød": "bread", "Sjokoladekake": "dessert",
            "Kaffe": "drinks", "Fiskesuppe": "dinner", "Gulrotkake": "dessert",
        }.items():
            got, _ = classify.classify_category(title)
            self.assertEqual(got, folder, f"{title!r} -> {got}, wanted {folder}")


class TestBritishGasMark(unittest.TestCase):
    """English recipes print a gas mark where a Norwegian one prints Celsius."""

    def test_gas_mark_becomes_a_celsius_pair(self):
        self.assertEqual(units.normalize_line("bake at gas mark 6")[0], "bake at 200°C (392°F)")

    def test_every_mark_converts(self):
        for mark in range(1, 10):
            text, notes = units.convert_gas_mark(f"gas mark {mark}")
            self.assertIn("°C (", text)
            self.assertTrue(notes)

    def test_a_pair_is_not_converted_twice(self):
        text, _ = units.normalize_line("gas mark 6")[0], None
        self.assertEqual(text, "200°C (392°F)")


class TestTemperatureRanges(unittest.TestCase):
    """A live English page (simplyrecipes) printed "warm water (105F-115F)" and
    produced "-112F", because the hyphen was read as a minus sign."""

    def test_fahrenheit_range_keeps_both_ends(self):
        self.assertEqual(
            units.normalize_line("warm water (105°F-115°F)")[0],
            "warm water (40-45°C (104-113°F))",
        )

    def test_celsius_range_gets_both_fahrenheit_ends(self):
        self.assertEqual(
            units.normalize_line("bake at 180-200 C")[0],
            "bake at 180-200°C (356-392°F)",
        )

    def test_unit_first_range(self):
        self.assertEqual(units.convert_gas_mark("no match here")[0], "no match here")
        self.assertEqual(units.normalize_line("105 F to 115 F")[0], "40-45°C (104-113°F)")

    def test_bare_unit_range_word(self):
        self.assertEqual(units.normalize_line("105F-115F")[0], "40-45°C (104-113°F)")

    def test_a_range_needs_the_unit(self):
        # Without the mandatory unit, "2-3 forks" would read as a temperature.
        for line in ("2-3 forks", "2-3 forks diced", "3-4 cloves garlic"):
            self.assertEqual(units.normalize_line(line)[0], line)

    def test_such_a_range_is_reported(self):
        _, notes = units.normalize_line("105°F-115°F")
        self.assertTrue(any("105°F-115°F" in n for n in notes))

    def test_existing_pairs_are_untouched(self):
        for line in ("200°C (392°F)", "175°C (347°F)"):
            self.assertEqual(units.normalize_line(line)[0], line)

    def test_plain_scalars_still_convert(self):
        self.assertEqual(units.normalize_line("350 F")[0], "175°C (347°F)")
        self.assertEqual(units.normalize_line("gas mark 6")[0], "200°C (392°F)")


class TestEquipmentIsNotAnIngredient(unittest.TestCase):
    """detsoteliv.no lists "Tips til utstyr" after the ingredients; the heading
    does not start with "utstyr", so it used to be filed as a second ingredient
    group and pull the equipment list into the note."""

    CARD = """<html><body><div class="entry-content">
      <h2>Ingredienser</h2>
      <ul><li>1,5 kg solbær</li><li>1,5 kg rips</li><li>1 kg sukker</li>
          <li>Atamon (konserveringsmiddel)</li></ul>
      <p>Tips til utstyr:</p>
      <ul><li>saftkoker</li><li>saftflasker</li><li>stor kjele</li><li>sleiv og trakt</li></ul>
      <h2>Fremgangsmåte</h2>
      <ol><li>Rens bærene og fjern stilkene på ripsen grundig.</li>
          <li>Hell bærene i saftkokeren og koke til de er myke.</li></ol>
    </div></body></html>"""

    def test_equipment_group_is_excluded(self):
        _, ing, _, _ = recipe.extract(self.CARD)
        flat = [l for _, g in ing for l in g]
        self.assertEqual(len(flat), 4)
        self.assertNotIn("saftkoker", flat)
        self.assertNotIn("sleiv og trakt", flat)

    def test_real_ingredients_survive(self):
        _, ing, _, _ = recipe.extract(self.CARD)
        flat = [l for _, g in ing for l in g]
        self.assertIn("1,5 kg rips", flat)
        self.assertIn("Atamon (konserveringsmiddel)", flat)

    def test_equipment_headings_are_junk_in_both_languages(self):
        for heading in ("Tips til utstyr", "Utstyr du trenger", "Equipment you'll need",
                        "Utstyr", "Verktøy", "Tools"):
            self.assertTrue(recipe._JUNK_HEADINGS.match(heading), f"{heading!r} not junk")


class TestSchemaIngredientsWithMarkupSteps(unittest.TestCase):
    """norwayseafoods.com publishes recipeIngredient in JSON-LD but no
    recipeInstructions, so the ingredients used to come from the markup copy:
    div rows that arrived as "1 stk 1 Norway Seafoods Torskeloin"."""

    CARD = """<html><head><script type="application/ld+json">
      {"@context":"https://schema.org/","@type":"Recipe",
       "name":"Ovnsbakt torsk",
       "recipeIngredient":["1 stk torskefilet","2 stk gulrot","1 ss smør"]}
    </script></head><body><div class="post">
      <h2>Ingredienser</h2>
      <ul><li>1 stk 1 torskefilet</li><li>2 stk 1 gulrot</li><li>1 ss 1 smør</li></ul>
      <h2>Tilberedning</h2>
      <ol><li>Forvarm ovnen til 180 grader og kutt gulroten i skiver.</li>
          <li>Legg fisken i en form og hell over den varme sausen.</li></ol>
    </div></body></html>"""

    def test_ingredients_come_from_the_schema(self):
        _, ing, steps, tier = recipe.extract(self.CARD)
        self.assertEqual(tier, "schema ingredients + heading instructions")
        self.assertEqual(sum(len(g) for _, g in ing), 3)

    def test_no_stuck_number_from_the_markup_rows(self):
        _, ing, _, _ = recipe.extract(self.CARD)
        flat = [l for _, g in ing for l in g]
        self.assertIn("1 stk torskefilet", flat)
        for line in flat:
            self.assertNotRegex(line, r"^(?:\d+[,.]?\d*\s+\w+\s+){2}", f"doubled number in {line!r}")

    def test_instructions_still_come_from_the_markup(self):
        _, _, steps, _ = recipe.extract(self.CARD)
        self.assertEqual(sum(len(g) for _, g in steps), 2)

    def test_a_complete_schema_is_not_downgraded(self):
        card = self.CARD.replace(
            '"recipeIngredient":["1 stk torskefilet","2 stk gulrot","1 ss smør"]}',
            '"recipeIngredient":["1 stk torskefilet","2 stk gulrot","1 ss smør"],'
            '"recipeInstructions":[{"@type":"HowToStep","text":"Stek i 20 minutter."},'
            '{"@type":"HowToStep","text":"Servér varm."}]}',
        )
        _, _, steps, tier = recipe.extract(card)
        self.assertEqual(tier, "schema.org/Recipe")
        self.assertEqual(sum(len(g) for _, g in steps), 2)

    def test_markup_ingredients_are_used_when_the_schema_has_none(self):
        card = self.CARD.replace('"recipeIngredient":["1 stk torskefilet","2 stk gulrot","1 ss smør"]',
                                 '"recipeIngredient":[]')
        _, ing, _, tier = recipe.extract(card)
        self.assertEqual(tier, "heading heuristic")
        self.assertEqual(sum(len(g) for _, g in ing), 3)
