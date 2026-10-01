"""Discover recipe URLs from a source: a Google saved list or a plain text file."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

# Saved-list items are marked with a data-item-id attribute holding the payload:
#   item_type: WEB_PAGE
#   url: "https://example.com/recipe/"
#   storage_id: "..."
# BeautifulSoup decodes entities on parse, so the quotes are plain here even though
# they arrive as &quot; in the raw HTML.
_SAVED_ITEM = 'div[data-item-id*="WEB_PAGE"]'
_ITEM_URL = re.compile(r'url:\s*"(https?://[^"]+)"')

# Query parameters that identify the click rather than the page.
_TRACKING_PARAMS = {"usg", "fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref_src"}
_TRACKING_PREFIXES = ("utm_",)

# Words a recipe title is usually split on; the site name follows one of these.
_TITLE_SEPARATORS = re.compile(r"\s*[|–—]\s*")


def clean_url(url: str) -> str:
    """Drop the fragment and any click-tracking query parameters."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in _TRACKING_PARAMS and not key.startswith(_TRACKING_PREFIXES)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def dedupe(pairs: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """Keep the first occurrence of each URL, preserving order."""
    seen: set[str] = set()
    result: list[tuple[str, str | None]] = []
    for url, title in pairs:
        url = clean_url(url)
        if not url or url in seen:
            continue
        seen.add(url)
        result.append((url, title))
    return result


def _item_title(node) -> str:
    """The saved item's title, found on an anchor inside the item's container.

    The walk stops before it can reach an ancestor shared with other saved items,
    otherwise an untitled item would pick up its neighbour's title.
    """
    container = node
    for _ in range(6):
        if container is None:
            break
        if container is not node and len(container.select(_SAVED_ITEM)) > 1:
            break
        for anchor in container.select("a[href]"):
            text = (anchor.get("aria-label") or anchor.get_text(" ", strip=True) or "").strip()
            if len(text) > 12 and "google" not in (anchor.get("href") or ""):
                return _TITLE_SEPARATORS.split(text)[0].strip()
        container = container.parent
    return ""


def links_from_google_list(html: str) -> list[tuple[str, str | None]]:
    """Every saved item in a Google saved-list page, as (url, title) pairs."""
    soup = BeautifulSoup(html, "lxml")
    pairs: list[tuple[str, str | None]] = []
    for node in soup.select(_SAVED_ITEM):
        match = _ITEM_URL.search(node.get("data-item-id") or "")
        if not match:
            continue
        title = _item_title(node) or None
        pairs.append((match.group(1), title))
    return dedupe(pairs)


def looks_like_login_wall(html: str) -> bool:
    """True when a Google page is asking us to sign in rather than showing a list."""
    lowered = html.lower()
    return "accounts.google" in lowered and ("sign in" in lowered or "log in" in lowered)


def links_from_text_file(path: Path) -> list[tuple[str, str | None]]:
    """One URL per line; blank lines and '#' comments are ignored."""
    pairs: list[tuple[str, str | None]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            pairs.append((line, None))
    return dedupe(pairs)


def links_from_args(urls: list[str]) -> list[tuple[str, str | None]]:
    return dedupe([(url, None) for url in urls])


class SourceError(Exception):
    """The source could not be turned into a list of recipe URLs."""


def discover(source: str, html: str | None = None) -> list[tuple[str, str | None]]:
    """Turn a user-supplied source into (url, title) pairs.

    A source starting with http is a page to fetch and read; anything else is a
    path to a text file containing one URL per line.
    """
    if source.startswith(("http://", "https://")):
        if html is None:
            import recipe  # local import to avoid a circular import

            html = recipe.fetch(source)
        pairs = links_from_google_list(html)
        if pairs:
            return pairs
        if looks_like_login_wall(html):
            raise SourceError(
                f"{source}\nThis saved list is private, so Google returns a sign-in page "
                "instead of the links.\n"
                "Open the list in a browser and pass the URLs to a text file instead:\n"
                "  ./recipe.py -x links.txt"
            )
        raise SourceError(
            f"No saved items found in {source}\n"
            "Expected a Google saved-list page. For any other list of links, save them "
            "to a text file, one URL per line, and run:\n"
            "  ./recipe.py -x links.txt"
        )

    path = Path(source).expanduser()
    if not path.is_file():
        raise SourceError(f"No such file: {path}")
    return links_from_text_file(path)