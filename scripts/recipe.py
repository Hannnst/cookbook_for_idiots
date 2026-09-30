#!/usr/bin/env python3
"""Extract a recipe from a URL and render it in this repo's house style.

    ./recipe.py https://example.com/some-recipe
    ./recipe.py URL -o earl_grey_cake.md
    ./recipe.py --lint broccoli_cauliflower_gratin.md

The output is always a draft for review. Nothing is committed.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Re-exec under this project's virtualenv when started by an interpreter that does
# not have the dependencies installed (e.g. the system python3). Runs before the
# third-party imports so that a bare `./recipe.py URL` cannot fail on import.
_VENV_PYTHON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".venv",
    "bin",
    "python",
)
if os.path.exists(_VENV_PYTHON) and os.path.realpath(sys.executable) != os.path.realpath(
    _VENV_PYTHON
):
    os.execv(_VENV_PYTHON, [_VENV_PYTHON, os.path.abspath(__file__), *sys.argv[1:]])

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

sys.path.insert(0, str(Path(__file__).parent))
from units import normalize_line  # noqa: E402

# (heading, lines) pairs: one entry per sub-group such as "For the cake".
Groups = list[tuple[str, list[str]]]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

# ---------------------------------------------------------------- noise filter

# Verified against steepbean.com and typical WordPress recipe blogs: affiliate
# blocks, ad slots and table-of-contents widgets all carry their own classes.
_JUNK_SELECTORS = [
    "script", "style", "noscript", "iframe", "form", "button", "svg", "ins",
    '[class*="wbl-"]', '[id*="wbl-"]',            # WP Wolfborn Loot affiliate carousel
    '[id^="ezoic-pub-ad-placeholder"]',           # Ezoic ad slots
    '[class*="ezoic"]', '[id*="ezoic"]',
    '[class*="ez-toc"]', '[id*="ez-toc"]',         # table of contents
    '[class*="amazon-"]', '[class*="product-box"]', '[class*="shop-the"]',
    '[class*="wprm-"]', '[class*="tasty-recipes"]', '[class*="mv-create"]',
    '[class*="recipe-card"]', '[class*="affiliate"]', '[class*="newsletter"]',
    '[class*="subscribe"]', '[class*="comment"]', '[id*="comments"]',
    '[class*="related"]', '[class*="author"]', '[class*="breadcrumb"]',
    '[class*="share"]', '[class*="social"]', '[class*="jumps"]',
    '[class*="rating"]', '[class*="nutrition"]', '[class*="recipe-conversion"]',
    '[class*="cook-mode"]', '[class*="pagination"]', '[class*="sidebar"]',
    '[class*="entry-meta"]', '[class*="post-meta"]', '[class*="bio"]',
    '[role="navigation"]', "footer", "header",
]

# Headings that are boilerplate rather than recipe content.
_JUNK_HEADINGS = re.compile(
    r"^(equipment|tools|kit|what you(?:'|’)ll need|nutrition|notes|reviews?|comments?|"
    r"related|you may also like|more recipes|conclusion|wrap up|final thoughts|"
    r"faq|frequently asked questions|toc|table of contents|jump to|shop|"
    r"ingredients you'll need|about (?:the )?author|leave a comment|"
    r"thank you|disclosure|affiliate disclosure|advertisement|pin it|save|share|"
    r"print|references?|sources?)\b",
    re.I,
)

_INGREDIENT_HEADINGS = re.compile(
    r"^(ingredients?|what you(?:'|’)ll need|you need|shopping list|what you need)\b", re.I
)
_METHOD_HEADINGS = re.compile(
    r"^(instructions?|directions?|method|steps?|preparation|procedure|how to make|"
    r"instructions$|making it|let(?:'|’)s (?:make|bake|cook)|step[- ]by[- ]step|"
    r"method$|what to do|process)\b",
    re.I,
)

# Structural elements that must survive the noise filter even if a selector
# happens to match them (see drop_junk).
_PROTECTED_TAGS = {"html", "body", "main", "article", "[document]", "section"}

# Text that is clearly not a recipe step.
_JUNK_LINE = re.compile(
    r"^(?:view (?:latest price|on amazon|price)|as an affiliate|we may earn|"
    r"buy (?:now|on amazon)|add to (?:cart|bag)|check price|shop now|save \d+%|"
    r"click here|read more|learn more|sign up|subscribe|advertisement|"
    r"photo of|author|updated on|published on|posted (?:on|by)|share this|"
    r"facebook|instagram|pinterest|twitter|linkedin|leave a comment|"
    r"cancel reply|your email address|privacy policy|terms (?:and|of) conditions|"
    r"all rights reserved|copyright|disclaimer|cookie|skip to content|"
    # Bare recipe-widget buttons that sit on their own line.
    r"save|print|share|pin it|pin|embed|jump to recipe|toc|home|next|previous)",
    re.I,
)

_SKIP_SMALL = re.compile(r"^(?:ok|okay|yes|no|hi|hello|thanks|thank you|dear\b|note:?|tip:?|update:)", re.I)

# Section openers that describe the section rather than instruct or list.
_INTRO_BLURB = re.compile(
    r"^(?:we(?:['’]re| are| have|['’]ve)\b|this (?:recipe|section|guide|method)\b|"
    r"in this (?:recipe|section|post|article|guide)\b|"
    r"(?:we['’]?ll|we will|you['’]?ll|you will)\s+walk\b|"
    r"before (?:we|you) (?:start|begin)\b|"
    r"(?:here['’]?s|this is) (?:how|what)\b|"
    r"we['’]?ve (?:carefully|selected|chosen|put together|designed)\b|"
    r"the (?:process|method) involves\b|"
    r"(?:please )?note:?\s|"
    r"whether you(?:['’]| a)re\b|"
    r"let['’]?s\b)",
    re.I,
)


def looks_like_intro(text: str) -> bool:
    """True for a paragraph that introduces a section instead of doing it."""
    if len(text) > 320:
        return False
    return bool(_INTRO_BLURB.match(text.strip()))


# ------------------------------------------------------------------- helpers


def clean_text(node: Tag | NavigableString | str) -> str:
    """Visible text of a node, with the whitespace blogs actually serve."""
    text = node.get_text(" ", strip=True) if isinstance(node, Tag) else str(node)
    text = text.replace("", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def is_junk(text: str) -> bool:
    return bool(_JUNK_LINE.match(text) or _SKIP_SMALL.match(text))


def strip_anchor_spans(node: Tag) -> None:
    """Remove TOC anchor spans, which WordPress injects inside every heading."""
    for span in node.find_all(["span", "a"]):
        classes = " ".join(span.get("class", [])) + " " + str(span.get("id", ""))
        if re.search(r"ez-toc|ezoic|wp-block-heading-anchor|anchor", classes, re.I):
            span.decompose()


def drop_junk(root: Tag) -> None:
    """Remove boilerplate inside ``root``.

    WordPress themes put long class lists on <body> ("right-sidebar",
    "postid-1837"), so a substring selector can otherwise match the container
    that holds the entire recipe. Never decompose a structural element.
    """
    for selector in _JUNK_SELECTORS:
        for node in root.select(selector):
            if node.decomposed or node.name in _PROTECTED_TAGS or node is root:
                continue
            classes = node.attrs.get("class") or [] if node.attrs else []
            if len(classes) >= 8:
                continue
            node.decompose()
    # Unwrap affiliate links, keeping their text: sites wrap the ingredient name
    # itself in a sponsored link ("<li>1 large <a rel=sponsored>frozen banana</a></li>").
    # The surrounding product blocks were already removed above.
    for link in list(root.select("a[href*='amazon.'], a[rel~='sponsored'], a[data-il]")):
        if link.decomposed:
            continue
        if link.name in _PROTECTED_TAGS:
            continue
        link.unwrap()


def fetch(url: str, timeout: int = 30) -> str:
    response = requests.get(
        url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}, timeout=timeout
    )
    response.raise_for_status()
    return response.text


def content_root(soup: BeautifulSoup) -> Tag:
    """The element most likely to hold the recipe body."""
    for selector in (
        "[itemprop=recipeInstructions]", ".entry-content", "article .entry-content",
        "article", "main", "[role=main]", "#recipe", ".recipe", ".post-content",
        ".hrecipe", ".recipe-content", "body",
    ):
        node = soup.select_one(selector)
        if node and len(clean_text(node)) > 400:
            return node
    return soup.body or soup


def page_title(soup: BeautifulSoup, url: str) -> str:
    for selector in ("[itemprop=name]", "h1.entry-title", "h1.post-title", ".entry-title h1",
                     "article h1", "main h1", "h1"):
        node = soup.select_one(selector)
        if node:
            title = clean_text(node)
            if title:
                return shorten_title(title)
    og = soup.select_one("meta[property='og:title']")
    if og and og.get("content"):
        return shorten_title(og["content"].strip())
    return url.rstrip("/").split("/")[-1].replace("-", " ").title()


def shorten_title(title: str) -> str:
    """Trim SEO padding: "Earl Grey Cake Recipe: Easy Homemade Bergamot Cake" -> "Earl Grey Cake"."""
    title = re.split(r"\s*[|–—]\s*", title)[0].strip()
    head = re.split(r":\s+", title)[0].strip()
    if len(head.split()) >= 2:
        title = head
    title = re.sub(r"\s+recipe\s*$", "", title, flags=re.I).strip()
    return title or title.strip()


# ------------------------------------------------------------------ extraction


class Section:
    """One heading and the blocks that follow it."""

    def __init__(self, title: str, level: int) -> None:
        self.title = title
        self.level = level
        self.items: list[tuple[str, str]] = []   # (kind, text); kind is list/para/table


def walk_blocks(root: Tag) -> list[tuple[str, str, int | None]]:
    """Flatten the content root into (tag, text, heading_level) in document order.

    Nested lists are flattened: an <li> with a nested list yields both lines.
    """
    out: list[tuple[str, str, int | None]] = []

    def emit_list(node: Tag, out: list[tuple[str, str, int | None]]) -> None:
        """Emit each <li>, keeping nested lists as items of their own.

        "<li>1 large<ul><li>mango</li></ul></li>" is two ingredients, not one
        fragment reading "1 large".
        """
        for item in node.find_all("li", recursive=False):
            nested = item.find(("ul", "ol"))
            if nested:
                clone = BeautifulSoup(str(item), "html.parser")
                for sub in clone.find_all(["ul", "ol"]):
                    sub.decompose()
                text = clean_text(clone)
                if text:
                    out.append(("li", text, None))
                emit_list(nested, out)
            else:
                text = clean_text(item)
                if text:
                    out.append(("li", text, None))

    def emit(node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name.lower()
            if re.fullmatch(r"h[1-6]", name):
                strip_anchor_spans(child)
                text = clean_text(child)
                if text:
                    out.append(("h", text, int(name[1])))
            elif name in ("ul", "ol"):
                emit_list(child, out)
            elif name == "p":
                text = clean_text(child)
                if text:
                    out.append(("p", text, None))
            elif name in ("div", "section", "table", "figure"):
                if child.find(["ul", "ol", "p"]) or child.name == "table":
                    emit(child)
                else:
                    text = clean_text(child)
                    if text:
                        out.append(("p", text, None))
            else:
                text = clean_text(child)
                if text:
                    out.append(("p", text, None))

    emit(root)
    return out


def group_sections(blocks: list[tuple[str, str, int | None]]) -> list[Section]:
    sections: list[Section] = []
    current = Section("", 0)
    for kind, text, level in blocks:
        if kind == "h":
            if current.title or current.items:
                sections.append(current)
            current = Section(text, level or 2)
        else:
            current.items.append((kind, text))
    if current.title or current.items:
        sections.append(current)
    return sections


def find_ingredient_sections(sections: list[Section]) -> list[Section]:
    """The ingredient block, including any 'For the cake'-style sub-headings."""
    start = None
    for index, section in enumerate(sections):
        if section.level == 2 and _INGREDIENT_HEADINGS.match(section.title):
            start = index
            break
    if start is None:
        return []
    collected = [sections[start]]
    for section in sections[start + 1:]:
        if section.level <= 2:
            if _JUNK_HEADINGS.match(section.title) or _METHOD_HEADINGS.match(section.title):
                break
            if not _INGREDIENT_HEADINGS.match(section.title):
                break
            collected.append(section)
        else:
            collected.append(section)
    return collected


def find_method_sections(sections: list[Section]) -> list[Section]:
    start = None
    for index, section in enumerate(sections):
        if section.level == 2 and _METHOD_HEADINGS.match(section.title):
            start = index
            break
    if start is None:
        return []
    collected = [sections[start]]
    for section in sections[start + 1:]:
        if section.level <= 2:
            if _JUNK_HEADINGS.match(section.title):
                continue
            break
        collected.append(section)
    return collected


# --------------------------------------------------------------- normalization


def prose_to_steps(text: str) -> str:
    """Second person, imperative. Leaves the author's wording otherwise.

    Must handle possessives: a blanket we/our -> you gives "in you saucepan".
    """
# Contractions and possessives first, longest forms before shorter ones.
    # Keep the original apostrophe: sites use a mix of ' and ’.
    text = re.sub(r"\b([Ww]e)(['’])(re|ll|ve)\b",
                  lambda m: ("You" if m.group(1)[0].isupper() else "you") + m.group(2) + m.group(3),
                  text)
    text = re.sub(r"\b[Oo]ur\b", "your", text)
    text = re.sub(r"\b[Oo]urs\b", "yours", text)
    text = re.sub(r"\b([Ww]e)\b", lambda m: "You" if m.group(1)[0].isupper() else "you", text)
    text = re.sub(r"\bhere's\b", "here is", text, flags=re.I)

    # Re-capitalise a "you" word that now starts a sentence ("Our oven" -> "Your oven").
    text = re.sub(r"(^|(?<=[.!?:])\s+)(you|your|yours|you're|you'll|you've)\b",
                  lambda m: m.group(1) + m.group(2).capitalize(), text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tidy_heading(text: str) -> str:
    text = text.strip().rstrip(":")
    text = re.sub(r"^(?:step\s*)?\d+[.):]\s*", "", text, flags=re.I)
    return text


def convert_ingredient(line: str) -> tuple[str, list[str]]:
    return normalize_line(re.sub(r"\s+", " ", line).strip())


def convert_step(line: str) -> tuple[str, list[str]]:
    return normalize_line(prose_to_steps(line))


# ------------------------------------------------------------- conflict check


_QTY_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|dl|ml|l|cl)\b", re.I)


def quantities(text: str) -> list[str]:
    return [m.group(1).replace(",", ".") for m in _QTY_RE.finditer(text)]


_NOISE_WORDS = (
    r"softened|finely ground|sifted|divided|chopped|optional|drained|at room temperature|"
    r"room temperature|warmed|melted|plus more|for serving|for dusting|to taste|beaten|"
    r"peeled|thinly sliced|freshly ground|powdered|packed|ground|minced|halved|"
    r"cut into|plus|about|approximately"
)


def ingredient_keyword(line: str) -> str | None:
    """The last significant word of an ingredient line, used to find it in a step."""
    text = re.sub(r"^[\d\s.,/\-½¼¾⅓⅔⅛⅜⅝⅞+()to-]+", "", line).lower()
    text = re.sub(rf"\b(?:{_NOISE_WORDS})\b", " ", text)
    words = [w for w in re.findall(r"[a-z]+", text) if len(w) > 3]
    if not words:
        return None
    return words[-1]


def quantity_near(text: str, keyword: str, window: int = 25) -> str | None:
    """The metric quantity that modifies ``keyword`` in ``text``.

    Looks at the number just before the ingredient ("with 150 g flour") and just
    after it ("150 g of the flour"), ignoring quantities belonging to other
    ingredients in the same sentence.
    """
    for match in re.finditer(rf"\b{re.escape(keyword)}", text, re.I):
        before = text[max(0, match.start() - window): match.start()]
        found = re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:kg|g|dl|ml|l|cl)\b", before, re.I)
        if found:
            return found[-1].replace(",", ".")
        after = text[match.end(): match.end() + window]
        found = re.findall(r"^\W{0,6}(\d+(?:[.,]\d+)?)\s*(?:kg|g|dl|ml|l|cl)\b", after, re.I)
        if found:
            return found[0].replace(",", ".")
    return None


def find_conflicts(ingredient_lines: list[str], step_lines: list[str]) -> list[str]:
    """Flag ingredients whose listed quantity differs from the one the steps use.

    This is the failure mode worth catching: a blog's own ingredient list and its
    own instructions frequently disagree, and neither side is labelled wrong.
    """
    conflicts: list[str] = []
    for ingredient in ingredient_lines:
        listed = quantities(ingredient)
        if not listed:
            continue
        keyword = ingredient_keyword(ingredient)
        if not keyword:
            continue
        for step in step_lines:
            if not re.search(rf"\b{re.escape(keyword)}", step, re.I):
                continue
            used = quantity_near(step, keyword)
            if used and used != listed[0]:
                conflicts.append(
                    f"  {ingredient!r} lists {listed[0]}, the steps use {used}: {step[:95]!r}"
                )
            break
    return conflicts


# ------------------------------------------------------------------- rendering


def render(title: str, ingredient_groups: Groups, step_groups: Groups) -> str:
    blocks: list[str] = [f"# {title}"]

    ingredients = ["## Ingredients:", ""]
    if not ingredient_groups:
        ingredients.append("- (none found)")
    for heading, lines in ingredient_groups:
        if heading:
            ingredients.extend([f"### {heading}", ""])
        ingredients.extend(f"- {line}" for line in lines)
        if heading:
            ingredients.append("")
    blocks.append("\n".join(ingredients).rstrip())

    steps = ["## TODO:", ""]
    if not step_groups:
        steps.append("1. (no instructions found)")
    number = 0
    for heading, lines in step_groups:
        if heading:
            steps.extend([f"### {heading}", ""])
        for line in lines:
            number += 1
            steps.append(f"{number}. {line}")
        if heading:
            steps.append("")
    blocks.append("\n".join(steps).rstrip())

    return "\n\n".join(blocks) + "\n"


# ------------------------------------------------------------------ extraction


def from_schema(soup: BeautifulSoup) -> tuple[str, Groups, Groups] | None:
    """Tier 1: JSON-LD schema.org/Recipe, plus microdata."""
    groups_ing: Groups = []
    groups_steps: Groups = []
    name = ""

    script = soup.select_one("script[type='application/ld+json']")
    if script:
        try:
            import json

            data = json.loads(script.string or "{}")
        except (ValueError, TypeError):
            data = None

        def find_recipe(node) -> dict | None:
            if isinstance(node, dict):
                types = node.get("@type", "")
                types = types if isinstance(types, list) else [types]
                if any(str(t).lower() == "recipe" for t in types):
                    return node
                for value in node.values():
                    found = find_recipe(value)
                    if found:
                        return found
            elif isinstance(node, list):
                for value in node:
                    found = find_recipe(value)
                    if found:
                        return found
            return None

        recipe = find_recipe(data) if data else None
        if recipe:
            name = str(recipe.get("name") or "").strip()
            ingredients = recipe.get("recipeIngredient") or recipe.get("ingredients") or []
            if isinstance(ingredients, str):
                ingredients = [ingredients]
            if ingredients:
                groups_ing.append(("", [str(i) for i in ingredients]))

            raw_steps = recipe.get("recipeInstructions") or []
            steps: list[str] = []
            if isinstance(raw_steps, str):
                raw_steps = [raw_steps]
            for entry in raw_steps:
                if isinstance(entry, str):
                    steps.append(entry)
                elif isinstance(entry, dict):
                    if entry.get("name") and entry.get("itemListElement"):
                        lines = [
                            re.sub(r"<[^>]+>", " ", clean_text(item)).strip()
                            for item in entry["itemListElement"]
                        ]
                        steps.append(f"{entry['name']}: " + " ".join(lines))
                    else:
                        text = clean_text(entry.get("text", ""))
                        if text:
                            steps.append(text)
            if steps:
                groups_steps.append(("", steps))

    if not groups_ing:
        items = soup.select("[itemprop=recipeIngredient], [itemprop=ingredients] li")
        if items:
            groups_ing.append(("", [clean_text(i) for i in items]))
    if not groups_steps:
        nodes = soup.select("[itemprop=recipeInstructions] li, [itemprop=recipeDirections] li")
        if not nodes:
            nodes = soup.select("[itemprop=recipeInstructions] p, [itemprop=recipeDirections] p")
        if nodes:
            groups_steps.append(("", [clean_text(n) for n in nodes]))

    if groups_ing and groups_steps:
        return (name, groups_ing, groups_steps)
    return None


def from_headings(root: Tag) -> tuple[str, Groups, Groups] | None:
    """Tier 2: walk the heading tree. Works on sites with no structured data."""
    sections = group_sections(walk_blocks(root))

    ingredient_sections = find_ingredient_sections(sections)
    method_sections = find_method_sections(sections)
    if not ingredient_sections and not method_sections:
        return None

    # If any ingredient subsection uses bullets, prose in a section is a blurb
    # (the "## Ingredients" heading is often followed by an intro paragraph and
    # then ### subsections holding the actual list).
    uses_bullets = any(
        kind == "li" for section in ingredient_sections for kind, _ in section.items
    )

    groups_ing: Groups = []
    for section in ingredient_sections:
        items = [(kind, text) for kind, text in section.items if not is_junk(text)]
        if any(kind == "li" for kind, _ in items):
            # Widget text ("Save") and the section blurb sit before the first bullet.
            while items and items[0][0] != "li":
                items.pop(0)
        lines = [text for kind, text in items if kind == "li"]
        if not lines and not uses_bullets:
            lines = [text for kind, text in items if kind in ("p", "table")]
        if lines:
            groups_ing.append((tidy_heading(section.title) if section.level > 2 else "", lines))

    groups_steps: Groups = []
    for section in method_sections:
        items = [
            text for kind, text in section.items
            if kind in ("li", "p", "table") and not is_junk(text) and len(text) > 25
        ]
        # Drop a leading blurb such as "We'll walk through each step...".
        while items and looks_like_intro(items[0]):
            items.pop(0)
        if items:
            groups_steps.append((tidy_heading(section.title) if section.level > 2 else "", items))

    if groups_ing or groups_steps:
        return ("", groups_ing, groups_steps)
    return None


def extract(html: str) -> tuple[str, Groups, Groups, str]:
    soup = BeautifulSoup(html, "lxml")
    title = page_title(soup, "")
    # Choose the content root before cleaning: noise selectors can match the
    # body element itself, which would empty the document.
    root = content_root(soup)
    drop_junk(root)

    result = from_schema(soup)
    tier = "schema.org/Recipe"
    if result is None:
        result = from_headings(root)
        tier = "heading heuristic"
    if result is None:
        return (title, [], [], "none")
    schema_name, ingredient_groups, step_groups = result
    if schema_name:
        title = shorten_title(schema_name)
    return (title, ingredient_groups, step_groups, tier)


# ---------------------------------------------------------------------- lint

def lint(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []

    for label, pattern in (
        ("no '# Title' heading", r"^#\s+\S"),
        ("no '## Ingredients:' heading", r"^##\s+Ingredients:?\s*$"),
        ("no '## Instructions:' or '## TODO:' heading", r"^##\s+(?:Instructions|TODO):?\s*$"),
    ):
        if not re.search(pattern, text, re.M):
            problems.append(label)

    for label, pattern in (
        ("cups", r"\bcups?\b"),
        ("imperial mass (oz/lb)", r"\bfl\.?\s*oz\b|\bounces?\b|\b(?:lbs?|pounds?)\b"),
        ("ml where the house style uses dl", r"\b\d\s?ml\b"),
    ):
        match = re.search(pattern, text, re.M | re.I)
        if match:
            problems.append(f"{label}: {match.group(0)!r}")

    # The house style is "N°C (N°F)" on every temperature. Remove the pairs that
    # already comply, then look for leftovers.
    stripped = re.sub(r"\d{2,3}\s*[°º]?\s*C\s*\(\s*\d{2,3}\s*[°º]?\s*F\s*\)", "", text)
    bare_c = re.findall(r"\b(\d{2,3})\s*[°º]?\s*C\b", stripped)
    bare_f = re.findall(r"\b(\d{2,3})\s*[°º]?\s*F\b", stripped)
    if bare_c:
        problems.append(f"temperature without Fahrenheit: {bare_c[0]}°C")
    if bare_f:
        problems.append(f"temperature without Celsius: {bare_f[0]}°F")
    if not re.search(r"\d+°C \(\d+°F\)", text) and (bare_c or bare_f):
        problems.append("temperatures should be written as 'N°C (N°F)'")

    # Steps restart at 1 per stage (see butter_chicken.md), so only upward
    # skips are a problem.
    numbers = [int(m.group(1)) for m in re.finditer(r"^(\d+)\.\s", text, re.M)]
    for index, number in enumerate(numbers[1:], start=1):
        if number != numbers[index - 1] + 1 and number != 1:
            problems.append(f"step numbering jumps from {numbers[index - 1]} to {number}")
            break
    return problems


# ------------------------------------------------------------------------ cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", nargs="?", help="recipe page to extract")
    parser.add_argument("-o", "--output", type=Path, help="write the draft here instead of stdout")
    parser.add_argument("--lint", type=Path, action="append", default=[], help="check a file for house style")
    parser.add_argument("--title", help="override the title")
    parser.add_argument("--no-report", action="store_true", help="hide the conversion/conflict report")
    return parser


def run(args: argparse.Namespace) -> int:
    exit_code = 0

    for target in args.lint:
        problems = lint(target)
        if problems:
            print(f"{target}:")
            for problem in problems:
                print(f"  - {problem}")
            exit_code = 1
        else:
            print(f"{target}: clean")

    if args.url:
        print(f"fetching {args.url} ...", file=sys.stderr)
        html = fetch(args.url)
        title, ingredient_groups, step_groups, tier = extract(html)

        if tier == "none":
            print("Could not find a recipe on that page.", file=sys.stderr)
            return 1

        notes: list[str] = []
        converted_ing: Groups = []
        for heading, lines in ingredient_groups:
            converted: list[str] = []
            for line in lines:
                value, line_notes = convert_ingredient(line)
                converted.append(value)
                notes.extend(line_notes)
            converted_ing.append((heading, converted))

        converted_steps: Groups = []
        for heading, lines in step_groups:
            converted = []
            for line in lines:
                value, line_notes = convert_step(line)
                converted.append(value)
                notes.extend(line_notes)
            converted_steps.append((heading, converted))

        markdown = render(args.title or title, converted_ing, converted_steps)

        if args.output:
            args.output.write_text(markdown, encoding="utf-8")
            print(f"wrote {args.output} ({tier})", file=sys.stderr)
        else:
            print(markdown)

        if not args.no_report:
            sys.stdout.flush()
            flat_ing = [line for _, lines in converted_ing for line in lines]
            flat_steps = [line for _, lines in converted_steps for line in lines]
            conflicts = find_conflicts(flat_ing, flat_steps)

            print("\n--- review ---", file=sys.stderr)
            print(f"extraction tier: {tier}", file=sys.stderr)
            print(f"ingredients: {len(flat_ing)}   steps: {len(flat_steps)}", file=sys.stderr)
            unconverted = [n for n in notes if n.startswith("kept")]
            if unconverted:
                print(f"\nkept as-is ({len(unconverted)}):", file=sys.stderr)
                for note in unconverted:
                    print(f"  - {note}", file=sys.stderr)
            if notes and unconverted:
                print(f"\nconverted {len(notes) - len(unconverted)} measurements", file=sys.stderr)
            elif notes:
                print(f"converted {len(notes)} measurements", file=sys.stderr)
            if conflicts:
                print("\nCONFLICTS between the ingredient list and the steps:", file=sys.stderr)
                for conflict in conflicts:
                    print(conflict, file=sys.stderr)
                print("\n  Check each of these against the source before using.", file=sys.stderr)

    if not args.url and not args.lint:
        build_parser().print_help()
        return 1
    return exit_code


if __name__ == "__main__":
    sys.exit(run(build_parser().parse_args()))
