#!/usr/bin/env python3
"""Extract a recipe from a URL and render it in this repo's house style.

    ./recipe.py https://example.com/some-recipe     # saves earl_grey_tea_cake.md
    ./recipe.py URL -o other_name.md                # save under a specific name
    ./recipe.py URL -o -                            # print to stdout instead
    ./recipe.py --lint broccoli_cauliflower_gratin.md

The saved file is named after the recipe title, and is opened in your editor when
run from a terminal. The output is always a draft for review. Nothing is committed.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import time
import unicodedata
from html import unescape
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
import classify  # noqa: E402
import sources  # noqa: E402
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
    # "Tips til utstyr" and "Utstyr du trenger" are equipment headings whose
    # first word is not "utstyr", so the anchor needs the prefix spelled out.
    r"tips til (?:utstyr|verktøy)|(?:utstyr|verktøy)(?:\s+du\s+trenger)?|"
    r"equipment(?:\s+you(?:'|’)ll\s+need)?|tools(?:\s+you(?:'|’)ll\s+need)?|"
    r"related|you may also like|more recipes|conclusion|wrap up|final thoughts|"
    r"faq|frequently asked questions|toc|table of contents|jump to|shop|"
    r"ingredients you'll need|about (?:the )?author|leave a comment|"
    r"thank you|disclosure|affiliate disclosure|advertisement|pin it|save|share|"
    r"print|references?|sources?)\b",
    re.I,
)

_INGREDIENT_HEADINGS = re.compile(
    r"^(?:ingredients?|ingredient list|ingredients list|recipe ingredients|"
    r"what you(?:'|’)ll need|you(?:'|’)ll need|you need|shopping list|what you need|"
    r"what to buy|you will need|"
    r"ingredienser|ingredienslista|ingredienser til|det du trenger|du trenger|"
    r"hva du trenger|du trenger følgende)\b",
    re.I,
)
_METHOD_HEADINGS = re.compile(
    r"^(?:instructions?|instructions for|cooking instructions|directions?|"
    r"directions for|method|steps?|preparation|preparing|procedure|"
    r"how to make|how to prepare|how it(?:'|’)s made|how to cook|"
    r"making it|let(?:'|’)s (?:make|bake|cook)|step[- ]by[- ]step|"
    r"what to do|process|"
    r"fremgangsmåte|framgangsmåte|fremgangs måte|fremgang|tilberedning|"
    r"slik gjør du|så gjør du det|slik lager du|slik lager du det|sådan|"
    r"her er hvordan du)\b",
    re.I,
)

# Some blogs label the blocks with a bare paragraph instead of a heading
# ("Du trenger" / "Slik gjør du" on glutenfrihet.no). Matching the label words
# rather than any short paragraph keeps prose out of it.
# "Bunn:" / "Ostefromasj:" introduce an ingredient group inside one paragraph.
_GROUP_LABEL = re.compile(r"^[^\d:]{1,40}:$")

_LABEL_PARAGRAPH = re.compile(
    r"^(?:du trenger(?:\s+(?:dette|følgende))?|hva du trenger|"
    r"you(?:'|’)ll need|you need(?:\s+this)?|what to buy|"
    r"ingredients?|ingredienser|ingredienslista|"
    r"slik gjør du(?:\s+det)?|så gjør du(?:\s+det)?|slik lager du(?:\s+det)?|"
    r"fremgangsmåte|framgangsmåte|tilberedning|"
    r"how to(?: make it)?|method|instructions|directions|steps|"
    r"utstyr|verktøy|equipment|tools)\s*:?$",
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
    r"all rights reserved|copyright|disclaimer|\bcookie\b|skip to content|"
    # Bare recipe-widget buttons that sit on their own line. Bounded for the
    # same reason: "cookies and cream" is an ingredient, not a cookie notice.
    r"\bsave\b|\bprint\b|\bshare\b|pin it|\bpin\b|\bembed\b|jump to recipe|"
    r"\btoc\b|\bhome\b|\bnext\b|\bprevious\b|"
    r"save recipe|print recipe|share on|copy link|sponsored|promoted|partner link|"
    # Norwegian sites label the same widgets in Norwegian. Each alternative is
    # bounded: this is a prefix match, and a bare "del" would swallow
    # "Delicious" and "Delightfully chewy" from the middle of a real recipe.
    r"\bskriv ut\b|\blagre\b|\bdel(?:\s+denne)?\s*$|\bkommenter\b|\bneste\b|"
    r"\bforrige\b|\bhjem\b|\bfølg oss\b|\babonner\b|\bmeld deg på\b|"
    r"\btil toppen\b|\bles mer\b|\bklikk her\b|\breklame\b|"
    r"\bdenne artikkelen\b|\brelaterte oppskrifter\b)",
    re.I,
)

_SKIP_SMALL = re.compile(
    r"^(?:ok|okay|yes|no|hi|hello|thanks|thank you|dear\b|note:?|tip:?|update:|"
    r"nb:?|tips?:|hint:|psst|morsomt)",
    re.I,
)

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
    r"let['’]?s\b|"
    # Norwegian
    r"i denne oppskriften\b|før vi begynner\b|her er hvordan\b|"
    r"vi (?:skal|går|har)\b|du (?:trenger|vil) følgende\b|"
    r"følgende (?:ting|ingredienser)\b)",
    re.I,
)


def looks_like_intro(text: str) -> bool:
    """True for a paragraph that introduces a section instead of doing it."""
    if len(text) > 320:
        return False
    return bool(_INTRO_BLURB.match(text.strip()))


# ------------------------------------------------------------------- helpers


def clean_text(node: Tag | NavigableString | str) -> str:
    """Visible text of a node, with the whitespace blogs actually serve.

    Unescapes once, which matters for JSON-LD: some sites (godfisk.no) put
    escaped HTML inside the JSON string itself, and nothing decodes entities
    inside a <script> element, so `p&aring; 200 &deg;C` would otherwise reach
    the file as-is and never match the temperature converter. Only one pass, so
    a genuinely doubled `&amp;amp;` stays `&amp;`.
    """
    text = node.get_text(" ", strip=True) if isinstance(node, Tag) else str(node)
    text = unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def is_junk(text: str) -> bool:
    return bool(_JUNK_LINE.match(text) or _SKIP_SMALL.match(text))


def strip_anchor_spans(node: Tag) -> None:
    """Remove TOC anchor spans, which WordPress injects inside every heading."""
    for span in node.find_all(["span", "a"]):
        classes = " ".join(span.get("class", [])) + " " + str(span.get("id", ""))
        if re.search(r"ez-toc|ezoic|wp-block-heading-anchor|anchor", classes, re.I):
            span.decompose()


def holds_the_recipe(node: Tag) -> bool:
    """True when a node looks like the recipe itself rather than a widget.

    A junk selector must never delete the recipe. `[class*="recipe-card"]` was
    meant for related-recipe widgets in a sidebar, but it also matches the main
    card on detgladekjokken.no (`wp-block-wpzoom-recipe-card-block-recipe-card`)
    and took the whole recipe with it. Checking for the block's own headings is
    structural, so it protects the recipe whatever the theme calls its classes.
    """
    for heading in node.select("h1, h2, h3, h4, h5, h6"):
        text = clean_text(heading)
        if _INGREDIENT_HEADINGS.match(text) or _METHOD_HEADINGS.match(text):
            return True
    return False


def drop_junk(root: Tag) -> None:
    """Remove boilerplate inside ``root``.

    WordPress themes put long class lists on <body> ("right-sidebar",
    "postid-1837"), so a substring selector can otherwise match the container
    that holds the entire recipe. Never decompose a structural element, and
    never one that holds the recipe itself.
    """
    for selector in _JUNK_SELECTORS:
        for node in root.select(selector):
            if node.decomposed or node.name in _PROTECTED_TAGS or node is root:
                continue
            classes = node.attrs.get("class") or [] if node.attrs else []
            if len(classes) >= 8:
                continue
            if holds_the_recipe(node):
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


def decode_response(response: requests.Response) -> str:
    """Decode a page, preferring the charset the page itself declares.

    `requests` defaults to ISO-8859-1 whenever the Content-Type header omits a
    charset, which is legal but wrong for most pages in practice: nrk.no sends
    a bare `text/html` while its own meta tag says UTF-8, so following the
    default turns `smør` into `smÃ¸r`. An explicit header is always trusted;
    only the ISO-8859-1 fallback is overridden.
    """
    encoding = response.encoding
    if not encoding or encoding.lower() in ("iso-8859-1", "latin-1"):
        head = response.content[:4096].decode("ascii", errors="ignore")
        match = re.search(r"""charset=["']?([\w-]+)""", head, re.I)
        encoding = match.group(1) if match else (response.apparent_encoding or "utf-8")
    try:
        return response.content.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return response.text


def fetch(url: str, timeout: int = 30) -> str:
    response = requests.get(
        url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}, timeout=timeout
    )
    response.raise_for_status()
    return decode_response(response)


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


_BLOCK_TAGS = ("div", "section", "p", "li", "table", "ul", "ol",
               "h1", "h2", "h3", "h4", "h5", "h6", "article", "tr")


def _is_block_list(node: Tag) -> bool:
    """True when a wrapper holds several sibling blocks, i.e. a list without a
    list tag. Three is the point where a single row stops looking like one."""
    blocks = [child for child in node.find_all(recursive=False)
              if isinstance(child, Tag) and child.name.lower() in _BLOCK_TAGS]
    return len(blocks) >= 3


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
                for line in lines_with_breaks(child):
                    # A bare "Du trenger" or "Slik gjør du" is a section label
                    # that the author typed as a paragraph, and "Bunn:" introduces
                    # an ingredient group. Promote both to headings so the
                    # ingredient/method finder sees them like any other.
                    if len(line) <= 60 and _LABEL_PARAGRAPH.match(line):
                        out.append(("h", tidy_heading(line), 3))
                    elif _GROUP_LABEL.match(line):
                        out.append(("h", line.rstrip(":").strip(), 4))
                    else:
                        out.append(("p", line, None))
            elif name == "tr":
                # One item per row: food blogs list ingredients as table rows
                # (<tr class="ingredient"><td>600-800 g</td><td>laksefilet</td></tr>),
                # and a table has no ul/ol/p to recurse into, so without this the
                # whole table collapses into one merged line.
                text = clean_text(child)
                if text:
                    out.append(("table", text, None))
            elif name in ("div", "section", "table", "figure", "tbody", "thead", "tfoot"):
                # Recurse when the child holds real blocks. A <table> counts even
                # without ul/ol/p, because food blogs list ingredients as rows.
                if child.find(["ul", "ol", "p", "table", "tr"]) or child.name == "table":
                    emit(child)
                elif _is_block_list(child):
                    # Modern themes render each ingredient as its own div
                    # (denstoltehane.no: 7 x "ingredienser__data--row"). With no
                    # list tag anywhere, collapsing the wrapper merged the whole
                    # recipe onto one line, so recurse and read the rows.
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


def _first_section(sections: list[Section], pattern: re.Pattern[str]) -> int | None:
    """Index of the section a heading pattern starts, preferring an <h2>.

    Plenty of food blogs mark up the recipe block as <h3> (trinesmatblogg.no,
    detgladekjokken.no both do), so an h3 has to count too -- but an h2 match
    still wins, because an h3 "Ingredients" is more likely to be a subsection
    of something else than the top of the recipe.
    """
    fallback = None
    for index, section in enumerate(sections):
        if not pattern.match(section.title):
            continue
        if section.level == 2:
            return index
        if fallback is None and section.level == 3:
            fallback = index
    return fallback


def find_ingredient_sections(sections: list[Section]) -> list[Section]:
    """The ingredient block, including any 'For the cake'-style sub-headings."""
    start = _first_section(sections, _INGREDIENT_HEADINGS)
    if start is None:
        return []
    collected = [sections[start]]
    for section in sections[start + 1:]:
        # Stop at a method or junk heading even when it sits at the same level:
        # sites that mark the whole recipe up as <h3> put "Fremgangsmåte" beside
        # "Ingredienser", and it must not be swallowed as a subsection.
        if _METHOD_HEADINGS.match(section.title) or _JUNK_HEADINGS.match(section.title):
            break
        if section.level <= 2:
            if not _INGREDIENT_HEADINGS.match(section.title):
                break
            collected.append(section)
        else:
            collected.append(section)
    return collected


def find_method_sections(sections: list[Section]) -> list[Section]:
    start = _first_section(sections, _METHOD_HEADINGS)
    if start is None:
        return []
    collected = [sections[start]]
    for section in sections[start + 1:]:
        # The mirror image of find_ingredient_sections: a page can repeat its
        # ingredient block after the method (print view, inline widget), and
        # that copy must not be read as the tail of the instructions.
        if _INGREDIENT_HEADINGS.match(section.title) or _JUNK_HEADINGS.match(section.title):
            break
        if section.level <= 2:
            if not _METHOD_HEADINGS.match(section.title):
                break
            collected.append(section)
        else:
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


def lines_with_breaks(node: Tag) -> list[str]:
    """Text lines of a node, treating <br> as a line break.

    Blogs that paste from a document put every ingredient on its own <br>
    line of a single paragraph ("<strong>Bunn:</strong><br/> 250 g kjeks<br/>
    100 g smor"). clean_text() collapses that into one line, which then reads
    as a single ingredient holding the whole recipe.
    """
    parts: list[str] = []
    current: list[str] = []
    for descendant in node.descendants:
        if isinstance(descendant, Tag) and descendant.name == "br":
            parts.append(" ".join(current))
            current = []
            continue
        if descendant.parent and descendant.parent.name in ("br", "style", "script"):
            continue
        if isinstance(descendant, NavigableString):
            current.append(str(descendant))
    parts.append(" ".join(current))
    lines = [clean_text(part) for part in parts]
    return [line for line in lines if line]


def strip_leading_number(text: str) -> str:
    """Drop "1." / "Step 2:" from a step the author already numbered in prose."""
    return re.sub(r"^\s*(?:step\s*)?\d+[.):]\s*", "", text, flags=re.I).strip()


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
    # [^\W\d_] is a Unicode letter, so "rømme" and "kjøtt" survive; a plain [a-z]
    # silently reduced them to "mme" and killed the conflict check in Norwegian.
    words = [w for w in re.findall(r"[^\W\d_]+", text, re.UNICODE) if len(w) > 3]
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
    name, groups_ing, groups_steps = schema_parts(soup)
    if groups_ing and groups_steps:
        return (name, groups_ing, groups_steps)
    return None


def schema_parts(soup: BeautifulSoup) -> tuple[str, Groups, Groups]:
    """Whatever JSON-LD/microdata offers, even when only half of it is there."""
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
            # Every string below comes from JSON, not from the HTML tree, so it
            # has to go through clean_text explicitly to get unescaped.
            name = clean_text(recipe.get("name") or "")
            ingredients = recipe.get("recipeIngredient") or recipe.get("ingredients") or []
            if isinstance(ingredients, str):
                ingredients = [ingredients]
            if ingredients:
                groups_ing.append(("", [clean_text(i) for i in ingredients]))

            raw_steps = recipe.get("recipeInstructions") or []
            steps: list[str] = []
            if isinstance(raw_steps, str):
                raw_steps = [raw_steps]
            for entry in raw_steps:
                if isinstance(entry, str):
                    steps.append(clean_text(entry))
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

    return (name, groups_ing, groups_steps)


# Units a line can be measured in, in both languages: metric, imperial, spoons
# and the count words recipes actually use. The trailing empty alternative this
# pattern used to have made every numbered line "measured", which reduced the
# adjacency check below to "does this line start with a number".
_INGREDIENT_UNITS = (
    r"g|kg|mg|ml|dl|cl|l|oz|lb|lbs|cups?|tbsp|tbsps?|tbs|tsp|stick|sticks?|"
    r"ss|ts|stk|st|pcs?|piece|pieces|can|cans|tin|tins|package|packages|pkg|pk|"
    r"clove|cloves|slice|slices|bunch|bunches|sprig|sprigs|handful|handfuls|"
    r"pinch|pinches|dash|dashes|egg|eggs|"
    r"nekk|bunt|fedd|skive|skiver|klump|klumper|dråpe|tsk|ssk"
)

_MEASURED_INGREDIENT = re.compile(
    r"^\s*\d+(?:[.,]\d+)?(?:\s*(?:-|–|to)\s*\d+(?:[.,]\d+)?)?\s*(?:"
    + _INGREDIENT_UNITS + r")\b"
    # A bare count is still a quantity: "2 large eggs", "1 onion", "3 stk løk".
    r"|^\s*\d+(?:[.,]\d+)?\s+[A-Za-zÆØÅæøå][\wÆØÅæøå-]*",
    re.I,
)


def looks_like_ingredient_rows(items: list[tuple[str, str]]) -> bool:
    """True when list rows read as measured ingredients rather than prose.

    At least two rows, and a quantity in most of them, so neither a bulleted
    note list nor prose above the method is mistaken for one.
    """
    rows = [text for kind, text in items
            if kind in ("li", "p", "table") and not is_junk(text)]
    if len(rows) < 2:
        return False
    measured = sum(1 for text in rows if _MEASURED_INGREDIENT.match(text))
    return measured >= 2 and measured * 2 >= len(rows)


def from_headings(root: Tag) -> tuple[str, Groups, Groups] | None:
    """Tier 2: walk the heading tree. Works on sites with no structured data."""
    sections = group_sections(walk_blocks(root))

    ingredient_sections = find_ingredient_sections(sections)
    method_sections = find_method_sections(sections)
    if not ingredient_sections and method_sections:
        # Some papers print no "Ingredients" heading at all and put the list
        # straight under the recipe title, immediately above the method
        # (dn.no). Take the block just before the method when it reads like one.
        start = _first_section(sections, _METHOD_HEADINGS) or 0
        for section in reversed(sections[:start]):
            if looks_like_ingredient_rows(section.items):
                ingredient_sections = [section]
            break
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
            strip_leading_number(text) for kind, text in section.items
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


def _fallback_roots(soup: BeautifulSoup, exclude: Tag | None) -> list[Tag]:
    """Wider containers to retry when the chosen content root has no recipe.

    Some themes put the recipe card outside `entry-content` -- detgladekjokken.no
    keeps Ingredienser/Fremgangsmåte in a sibling of it -- so the first guess can
    come back empty even though the page does contain the recipe.
    """
    candidates: list[Tag] = []
    for selector in ("article", "main", "[role=main]", ".post", ".entry", "#content", "body"):
        node = soup.select_one(selector)
        if node is not None and node is not exclude:
            candidates.append(node)
    return candidates


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
        # Some publishers list ingredients in JSON-LD but write the instructions
        # only in the markup (norwayseafoods.com). Prefer the author's own
        # ingredient text over the markup copy, which arrives as div rows with
        # a stray number in front of every line.
        if result is not None and result[1] and result[2]:
            name, schema_ing, _ = schema_parts(soup)
            if schema_ing:
                result = (result[0] or name, schema_ing, result[2])
                tier = "schema ingredients + heading instructions"
    if result is None:
        for candidate in _fallback_roots(soup, exclude=root):
            drop_junk(candidate)
            result = from_headings(candidate)
            if result is not None:
                break
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
    ):
        match = re.search(pattern, text, re.M | re.I)
        if match:
            problems.append(f"{label}: {match.group(0)!r}")

    # Only flag ml where dl would actually read better. A spoonful of vanilla or
    # 2 ml of cream of tartar would be absurd as "0.02 dl".
    for amount, unit in re.findall(r"(\d+(?:[.,]\d+)?)\s*(ml|cl|l)\b", text, re.I):
        millilitres = float(amount.replace(",", ".")) * {"ml": 1, "cl": 10, "l": 1000}[unit.lower()]
        if millilitres >= 100:
            problems.append(
                f"house style uses dl: write {amount} {unit.lower()} as {millilitres / 100:g} dl"
            )
            break

    # The house style is "N°C (N°F)" on every temperature. Remove the pairs that
    # already comply, then look for leftovers. A range counts as compliant in
    # the same shape it is written in: "100-120°C (212-248°F)".
    _CELSIUS = r"\d+(?:\s*[-–—]\s*\d+)?\s*[°º]?\s*C"
    _FAHRENHEIT = r"\d+(?:\s*[-–—]\s*\d+)?\s*[°º]?\s*F"
    stripped = re.sub(rf"{_CELSIUS}\s*\(\s*{_FAHRENHEIT}\s*\)", "", text)
    bare_c = re.findall(rf"\b(\d{{2,3}})\s*[°º]?\s*C\b", stripped)
    bare_f = re.findall(rf"\b(\d{{2,3}})\s*[°º]?\s*F\b", stripped)
    if bare_c:
        problems.append(f"temperature without Fahrenheit: {bare_c[0]}°C")
    if bare_f:
        problems.append(f"temperature without Celsius: {bare_f[0]}°F")
    if not re.search(rf"{_CELSIUS}\s*\(\s*{_FAHRENHEIT}\s*\)", text) and (bare_c or bare_f):
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


# Letters that survive NFKD unchanged but have no ASCII equivalent, so they are
# transliterated rather than silently deleted from the filename.
_TRANSLITERATIONS = {
    "æ": "ae", "ø": "o", "å": "aa", "Æ": "ae", "Ø": "o", "Å": "aa",
    "ä": "a", "ö": "o", "ü": "ue", "ß": "ss", "đ": "d", "ħ": "h",
}


def recipe_filename(title: str, max_length: int = 80) -> str:
    """Turn a recipe title into a snake_case filename.

    "Earl Grey Tea Cake" -> "earl_grey_tea_cake.md"

    Letters that NFKD does not decompose are transliterated by hand, otherwise
    Norwegian titles lose characters entirely: "surdeigsbrød" would come out as
    "surdeigsbr_d".
    """
    ascii_title = unicodedata.normalize("NFKD", title)
    ascii_title = "".join(ch for ch in ascii_title if not unicodedata.combining(ch))
    for character, replacement in _TRANSLITERATIONS.items():
        ascii_title = ascii_title.replace(character, replacement)
    ascii_title = ascii_title.lower().replace("&", " and ")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_title).strip("_")
    if len(slug) > max_length:
        truncated = slug[:max_length].rsplit("_", 1)[0].strip("_")
        slug = truncated or slug[:max_length].strip("_")
    if not slug:
        slug = "recipe"
    return f"{slug}.md"


def open_in_editor(path: Path) -> None:
    """Open a file for editing, preferring $VISUAL/$EDITOR over the OS default."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        try:
            subprocess.call([*shlex.split(editor), str(path)])
            return
        except OSError:
            pass
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.call([opener, str(path.resolve())])
    except OSError:
        pass


def collect_targets(args: argparse.Namespace) -> list[tuple[str, str | None]] | None:
    """Every URL to process, from -x and/or the positional arguments.

    Returns None when a source could not be read, so the caller can exit.
    """
    targets: list[tuple[str, str | None]] = []
    if args.batch:
        try:
            targets.extend(sources.discover(args.batch))
        except sources.SourceError as error:
            print(f"error: {error}", file=sys.stderr)
            return None
    if args.url:
        targets.extend(sources.links_from_args(args.url))
    return targets


def next_free_name(path: Path, taken: set[Path]) -> Path:
    """path if it is free, otherwise path_2, path_3, and so on."""
    if path not in taken:
        return path
    stem, suffix = path.stem, path.suffix
    counter = 2
    while path.parent / f"{stem}_{counter}{suffix}" in taken:
        counter += 1
    return path.parent / f"{stem}_{counter}{suffix}"


def show_plan(targets: list[tuple[str, str | None]], args: argparse.Namespace) -> None:
    """Print where a batch would write each recipe, without fetching anything."""
    buckets: dict[str, list[str]] = {}
    for url, title_hint in targets:
        folder = args.folder or classify.classify_category(title_hint or "")[0]
        name = recipe_filename(title_hint) if title_hint else "(unknown until fetched)"
        buckets.setdefault(folder, []).append(f"{name}   <- {url}")

    print(f"{len(targets)} recipes from the source\n", file=sys.stderr)
    for folder in sorted(buckets):
        print(f"normies/{folder}/  ({len(buckets[folder])})", file=sys.stderr)
        for line in buckets[folder][:5]:
            print(f"  {line}", file=sys.stderr)
        if len(buckets[folder]) > 5:
            print(f"  ... and {len(buckets[folder]) - 5} more", file=sys.stderr)
        print("", file=sys.stderr)
    print("Nothing was fetched. Re-run without --dry-run to save these.", file=sys.stderr)


def summarise(outcomes: dict[str, list[tuple[str, str]]], hard_failures: int) -> None:
    labels = {
        "saved": "saved",
        "suffixed": "saved under a numbered name (same title twice)",
        "exists": "already existed, skipped (use --refresh to replace)",
        "no_recipe": "no recipe found on the page",
        "no_steps": "found ingredients but no instructions",
        "failed": "fetch failed",
    }
    print("\n=== batch summary ===", file=sys.stderr)
    for key, label in labels.items():
        entries = outcomes[key]
        print(f"{label}: {len(entries)}", file=sys.stderr)
        for url, extra in entries:
            suffix = f"  -> {extra}" if key in ("saved", "suffixed", "exists") else ""
            print(f"  - {url}{suffix}", file=sys.stderr)
    if hard_failures:
        print(
            f"\n{hard_failures} page(s) could not be fetched. Re-run to retry just those.",
            file=sys.stderr,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "url", nargs="*", help="recipe page to extract (several URLs run as a batch)"
    )
    parser.add_argument(
        "-x",
        "--batch",
        metavar="SOURCE",
        help="extract many recipes from a Google saved list URL or a text file of URLs",
    )
    parser.add_argument(
        "--folder",
        metavar="NAME",
        help="batch only: save into this folder under normies/ instead of the classified one",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="batch only: overwrite files from an earlier run"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="batch only: show which URLs would be fetched and where they would go",
    )
    parser.add_argument("--limit", type=int, metavar="N", help="batch only: stop after N URLs")
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="batch only: pause between pages (default 1.0)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write the draft here instead of auto-naming it from the title ('-' for stdout)",
    )
    parser.add_argument("--lint", type=Path, action="append", default=[], help="check a file for house style")
    parser.add_argument(
        "--lint-all",
        action="store_true",
        help="check every recipe in the recipe folders (bread/, dinner/, dessert/, drinks/)",
    )
    parser.add_argument("--title", help="override the title")
    parser.add_argument("--no-report", action="store_true", help="hide the conversion/conflict report")
    parser.add_argument(
        "--force", action="store_true", help="overwrite the output file if it already exists"
    )
    parser.add_argument(
        "--no-open", action="store_true", help="do not open the saved file in an editor"
    )
    return parser


def recipe_folder_paths(root: Path = None) -> list[Path]:
    """Every recipe markdown file in the repo's category folders, sorted.

    normies/ is walked recursively because it holds one sub-folder per category.
    """
    base = (root or Path(__file__).parent.parent)
    found: list[Path] = []
    for folder in ("bread", "dinner", "dessert", "drinks"):
        found.extend(sorted((base / folder).glob("*.md")))
    found.extend(sorted((base / "normies").rglob("*.md")))
    return found


def run(args: argparse.Namespace) -> int:
    exit_code = 0

    if args.lint_all:
        args.lint.extend(recipe_folder_paths())

    for target in args.lint:
        problems = lint(target)
        if problems:
            print(f"{target}:")
            for problem in problems:
                print(f"  - {problem}")
            exit_code = 1
        else:
            print(f"{target}: clean")

    targets = collect_targets(args)
    if targets is None:
        return 1
    if not targets and not args.lint and not args.lint_all:
        build_parser().print_help()
        return 1

    batch_mode = bool(args.batch) or len(targets) > 1

    if batch_mode and (args.output or args.title):
        print(
            "error: -o and --title need a single URL.\n"
            "For a batch, the filename comes from each recipe's own title, and use "
            "--folder to pick one destination folder.",
            file=sys.stderr,
        )
        return 1

    if not batch_mode and (args.folder or args.refresh):
        flag = "--folder" if args.folder else "--refresh"
        print(
            f"error: {flag} only applies to a batch, and this is a single URL.\n"
            "Pass two or more URLs, or use -x to read them from a list.",
            file=sys.stderr,
        )
        return 1

    if args.limit:
        targets = targets[: args.limit]

    if batch_mode and args.dry_run:
        show_plan(targets, args)
        return 0

    outcomes: dict[str, list[tuple[str, str]]] = {
        "saved": [], "no_recipe": [], "no_steps": [], "failed": [], "exists": [], "suffixed": [],
    }
    hard_failures = 0
    written: set[Path] = set()

    for position, (url, title_hint) in enumerate(targets):
        if position and batch_mode and args.delay > 0:
            time.sleep(args.delay)

        try:
            print(f"fetching {url} ...", file=sys.stderr)
            html = fetch(url)
            title, ingredient_groups, step_groups, tier = extract(html)
        except Exception as error:
            if not batch_mode:
                print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
                return 1
            hard_failures += 1
            outcomes["failed"].append((url, f"{type(error).__name__}: {error}"))
            print(f"  FAILED  {url}\n          {type(error).__name__}: {error}", file=sys.stderr)
            continue

        if tier == "none":
            if not batch_mode:
                print("Could not find a recipe on that page.", file=sys.stderr)
                return 1
            outcomes["no_recipe"].append((url, "no recipe found"))
            print(f"  no recipe  {url}", file=sys.stderr)
            continue

        if not any(lines for _, lines in ingredient_groups):
            # Instructions with no ingredients are the mirror image of the check
            # below: the extractor found a prose block that reads like a method
            # but missed the ingredient list, and the draft is unusable either
            # way, so do not write it.
            if not batch_mode:
                print("Found instructions but no ingredients on that page.", file=sys.stderr)
                return 1
            outcomes["no_steps"].append((url, "instructions but no ingredients"))
            print(f"  no ingredients  {url}", file=sys.stderr)
            continue

        if not any(lines for _, lines in step_groups):
            # An ingredient list with no instructions is a page we only half
            # understood, and writing it produces an empty "## TODO:" section.
            if not batch_mode:
                print("Found ingredients but no instructions on that page.", file=sys.stderr)
                return 1
            outcomes["no_steps"].append((url, "ingredients but no instructions"))
            print(f"  no steps   {url}", file=sys.stderr)
            continue

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

        to_stdout = args.output is not None and str(args.output) == "-"
        if to_stdout:
            print(markdown)
            saved_path: Path | None = None
        else:
            final_title = args.title or title
            if batch_mode:
                saved_path = classify.target_path(
                    title_hint or final_title, Path.cwd(), args.folder
                )
                saved_path.parent.mkdir(parents=True, exist_ok=True)
                suffixed = False
                if saved_path in written:
                    saved_path = next_free_name(saved_path, written)
                    suffixed = True
                elif saved_path.exists() and not args.refresh:
                    outcomes["exists"].append((url, saved_path.name))
                    print(f"  exists    {url} -> {saved_path.name}", file=sys.stderr)
                    continue
                if suffixed:
                    outcomes["suffixed"].append((url, saved_path.name))
            else:
                saved_path = args.output or Path(recipe_filename(final_title))
                if saved_path.exists() and not args.force:
                    print(
                        f"\n{saved_path} already exists and will not be overwritten.\n"
                        f"  Pass --force to replace it, -o NAME to save under another name, "
                        f"or -o - to print to stdout.",
                        file=sys.stderr,
                    )
                    return 1
            saved_path.write_text(markdown, encoding="utf-8")
            written.add(saved_path)
            outcomes["saved"].append((url, saved_path.name))

        if batch_mode:
            flat_ing = [line for _, lines in converted_ing for line in lines]
            flat_steps = [line for _, lines in converted_steps for line in lines]
            conflicts = find_conflicts(flat_ing, flat_steps)
            detail = f"{len(flat_ing)} ingredients, {len(flat_steps)} steps"
            if conflicts:
                detail += f", {len(conflicts)} conflict(s)"
            print(f"  saved     {url} -> {saved_path.name} ({detail})", file=sys.stderr)
            continue

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

        # Always name the file, so the draft is easy to find again.
        if saved_path is not None:
            print(f"\nsaved as: {saved_path.name}", file=sys.stderr)
            print(f"         {saved_path.resolve()}", file=sys.stderr)

            interactive = sys.stdout.isatty() and sys.stderr.isatty()
            if not args.no_open and interactive:
                print("opening in your editor ...", file=sys.stderr)
                sys.stderr.flush()
                open_in_editor(saved_path)
            elif not args.no_open:
                print("         (run this in a terminal to open it automatically)", file=sys.stderr)

    if batch_mode:
        summarise(outcomes, hard_failures)
        return 1 if hard_failures else exit_code
    return exit_code


if __name__ == "__main__":
    sys.exit(run(build_parser().parse_args()))
