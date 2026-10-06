"""Capture a trimmed HTML fixture for the golden tests.

    .venv/bin/python scripts/capture_fixture.py NAME URL

Real recipe pages are 200 KB - 2 MB of navigation, ads and tracking, which is far
too much to check into the repo and too noisy to diff. This keeps only what the
extractor actually reads:

  * every <script type="application/ld+json">, since tier 1 is JSON-LD
  * the content root subtree, which is where tiers 2-4 look
  * a <title>, for the page-title fallback

and verifies the trimmed copy extracts identically to the page it came from, so a
fixture can never quietly stop reproducing the behaviour it was captured for. The
source URL is recorded next to the fixture for the golden files.

Fixtures are captured from live pages once. They are the reference the Kotlin port
is diffed against, so they must not be regenerated casually: if a fixture stops
being faithful, fix the trim here rather than editing the .html by hand.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import recipe  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "pages"
SOURCE_DIR = ROOT / "tests" / "fixtures" / "sources"


def trim(html: str) -> str:
    """Keep the JSON-LD blocks, the content root and the title."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    # Same order as extract(): the title is read before the noise filter runs,
    # otherwise drop_junk can change which heading page_title settles on.
    title = recipe.page_title(soup, "")
    chosen = None
    for selector in ("[itemprop=name]", "h1.entry-title", "h1.post-title", ".entry-title h1",
                     "article h1", "main h1", "h1"):
        node = soup.select_one(selector)
        if node:
            chosen = node
            break

    root = recipe.content_root(soup)
    recipe.drop_junk(root)

    for tag in soup.select("script:not([type='application/ld+json']), style, link, noscript"):
        tag.decompose()
    for tag in soup.select("meta, base"):
        tag.decompose()

    out = BeautifulSoup("", "lxml")
    head = out.new_tag("head")
    if title:
        tag = out.new_tag("title")
        tag.string = title
        head.append(tag)
        # The page's own og:title is often a shorter variant, and on some sites
        # the article <h1> does not survive the trim, leaving og:title as the only
        # title source. Record the title the extractor computed on the full page
        # so the fixture reproduces it.
        og = out.new_tag("meta")
        og["property"] = "og:title"
        og["content"] = title
        head.append(og)
    for script in soup.select("script[type='application/ld+json']"):
        head.append(script.extract())

    body = out.new_tag("body")
    # The article <h1> often sits outside the content root, and page_title prefers
    # it over <title>. Keep only the one the extractor actually chose: on dn.no a
    # stray weather-widget <h1> would otherwise take the title instead.
    if (
        chosen is not None
        and chosen.name == "h1"
        and chosen is not root
        and root not in chosen.parents
    ):
        body.append(chosen.extract())
    body.append(root.extract())

    out.append(head)
    out.append(body)
    return str(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="fixture name, e.g. norwayseafoods_torsk")
    parser.add_argument("url")
    parser.add_argument("--force", action="store_true", help="overwrite an existing fixture")
    args = parser.parse_args()

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    page = FIXTURE_DIR / f"{args.name}.html"
    if page.exists() and not args.force:
        print(f"{page} exists; pass --force to replace it", file=sys.stderr)
        return 1

    print(f"fetching {args.url} ...", file=sys.stderr)
    original = recipe.fetch(args.url, timeout=60)
    before = recipe.extract(original)

    trimmed = trim(original)
    after = recipe.extract(trimmed)
    if before != after:
        print(
            "the trimmed fixture does not reproduce the original page's extraction:\n"
            f"  original: {before[3]}, {sum(len(g) for _, g in before[1])} ingredients, "
            f"{sum(len(g) for _, g in before[2])} steps\n"
            f"  trimmed:  {after[3]}, {sum(len(g) for _, g in after[1])} ingredients, "
            f"{sum(len(g) for _, g in after[2])} steps",
            file=sys.stderr,
        )
        return 1

    page.write_text(trimmed, encoding="utf-8")
    (SOURCE_DIR / f"{args.name}.json").write_text(
        json.dumps({"url": args.url, "captured": recipe.time.strftime("%Y-%m-%d")}, indent=2)
        + "\n",
        encoding="utf-8",
    )

    print(
        f"saved {page.relative_to(ROOT)} "
        f"({page.stat().st_size // 1024} KB, {after[3]}, "
        f"{sum(len(g) for _, g in after[1])} ingredients, "
        f"{sum(len(g) for _, g in after[2])} steps)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())