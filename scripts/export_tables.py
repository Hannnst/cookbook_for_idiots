"""Emit ``Tables.kt`` from the Python tables, so the two cannot drift.

    .venv/bin/python scripts/export_tables.py            # write
    .venv/bin/python scripts/export_tables.py --check     # fail if stale

The bilingual vocabulary is the part of the extractor most likely to rot quietly:
a word added to the Norwegian list and forgotten in the English one is invisible
until a recipe is filed in the wrong folder. Duplicating those lists by hand in
Kotlin makes that guaranteed, so they are generated from the Python source instead.

This script is also the compatibility gate. Every regex it exports is checked for
Python-only constructs that ``java.util.regex`` either rejects or interprets
differently (``\\A``, possessive quantifiers, inline flags, ``(?P<name>``), variable
length lookbehind). A new one fails the export rather than emitting Kotlin that
compiles and quietly matches something else.

What this does **not** do: check that Jsoup parses HTML the way lxml did. That is
what the golden fixtures are for.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import classify  # noqa: E402
import recipe  # noqa: E402
import units  # noqa: E402

TARGET = ROOT / "core" / "src" / "main" / "kotlin" / "no" / "cookbook" / "tables" / "Tables.kt"
PACKAGE = "no.cookbook.tables"

FLAG_MAP = {
    re.IGNORECASE: "RegexOption.IGNORE_CASE",
    re.MULTILINE: "RegexOption.MULTILINE",
    re.DOTALL: "RegexOption.DOT_MATCHES_ALL",
    re.VERBOSE: "RegexOption.COMMENTS",
}

# Rewrites we are willing to do mechanically, because the result is provably
# identical in java.util.regex. Anything not listed here has to be hand-ported.
MECHANICAL_REWRITES = (
    (re.compile(r"\(\?P<([A-Za-z_][A-Za-z_0-9]*)>"), r"(?<\1>"),
)

# Patterns that mean "this will not survive the trip", with the reason.
BLOCKED = (
    (r"\(\?P<(?![A-Za-z_])", "malformed Python named group"),
    (r"\\A", r"no \A in java.util.regex; use ^"),
    (r"\\Z", r"no \Z in java.util.regex, and it means something different there"),
    (r"\\z", r"no \z in java.util.regex"),
    (r"\(\?[imsx]+\)", "inline flags; pass RegexOption instead"),
    (r"[*+?]\+", "possessive quantifier, unsupported in java.util.regex"),
    (r"\(\?>", "atomic group, unsupported in java.util.regex"),
    (r"\(\?<=", "positive lookbehind, unsupported in java.util.regex"),
    (r"\(\?<!", "negative lookbehind, unsupported in java.util.regex"),
    (r"\(\?<\!", "negative lookbehind, unsupported in java.util.regex"),
)


class ExportError(Exception):
    pass


# --------------------------------------------------------------------- kotlin

def kstring(value: str) -> str:
    """A Kotlin string literal.

    Uses an escaped literal rather than a raw ``\"\"\"...\"\"\"`` block, because
    regexes are full of ``$`` and in a Kotlin raw string ``$`` opens a template --
    ``\\A`` and ``$`` are exactly what these tables are made of.
    """
    out = ['"']
    for char in value:
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif char == "$":
            out.append("\\$")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def kdouble(value: float) -> str:
    number = float(value)
    text = repr(number)
    return text if ("." in text or "e" in text or "E" in text) else f"{text}.0"


def kint(value: int) -> str:
    return str(int(value))


def kmap(name: str, mapping: dict, value_fn, value_type: str,
         key_fn=kstring, key_type: str = "String") -> str:
    lines = [f"    val {name}: Map<{key_type}, {value_type}> = mapOf("]
    for key, value in mapping.items():
        lines.append(f"        {key_fn(key)} to {value_fn(value)},")
    lines.append("    )")
    return "\n".join(lines)


def kset(name: str, values, value_fn=kstring, ktype: str = "String") -> str:
    """A set from a Python set or tuple, always sorted.

    Python string sets iterate in hash order, which changes between processes, so
    an unsorted export would make ``--check`` fail at random.
    """
    lines = [f"    val {name}: Set<{ktype}> = setOf("]
    for value in sorted(values, key=lambda v: (str(type(v)), str(v))):
        lines.append(f"        {value_fn(value)},")
    lines.append("    )")
    return "\n".join(lines)


def klist(name: str, values, value_fn=kstring) -> str:
    lines = [f"    val {name}: List<String> = listOf("]
    for value in values:
        lines.append(f"        {value_fn(value)},")
    lines.append("    )")
    return "\n".join(lines)


def kregex(name: str, pattern: "re.Pattern") -> str:
    source = pattern.pattern
    for token, reason in BLOCKED:
        if re.search(token, source):
            raise ExportError(
                f"{name}: {reason}. Hand-port this one and add it to MECHANICAL_REWRITES "
                f"if the rewrite is provably equivalent."
            )
    for rule, replacement in MECHANICAL_REWRITES:
        source = rule.sub(replacement, source)

    options = []
    for flag, kotlin_name in FLAG_MAP.items():
        if pattern.flags & flag:
            options.append(kotlin_name)
    supported = 0
    for flag in FLAG_MAP:
        supported |= int(flag)
    # re.UNICODE is implicit for str patterns and Java regexes are Unicode by
    # default too, so it needs no counterpart. re.ASCII does change matching and
    # has no safe translation, so it stays a hard failure.
    supported |= int(re.UNICODE)
    extra = int(pattern.flags) & ~supported
    if extra:
        raise ExportError(f"{name}: unsupported regex flags {extra:#x}")

    joined = ", ".join(options)
    if joined:
        return f"    val {name} = Regex({kstring(source)}, {joined})"
    return f"    val {name} = Regex({kstring(source)})"


def unit_alternatives(source: str) -> list[str]:
    """``_INGREDIENT_UNITS`` is one ``a|b|c`` string; split it into a list.

    Asserted flat, because a naive split on a pattern containing groups would
    produce fragments that no longer compile as a unit on their own.
    """
    if "(" in source or "[" in source:
        raise ExportError(
            "_INGREDIENT_UNITS contains grouping, so it cannot be split naively; "
            "export it as a single regex instead."
        )
    return source.split("|")


# --------------------------------------------------------------------- export

def render() -> str:
    parts: list[str] = []
    add = parts.append

    add(f"package {PACKAGE}")
    add("")
    add("/**")
    add(" * GENERATED FILE - do not edit by hand.")
    add(" *")
    add(" * Source of truth is the Python reference implementation; regenerate with")
    add(" * `.venv/bin/python scripts/export_tables.py`. CI fails if this file is stale,")
    add(" * so a word added in `scripts/classify.py` without regenerating here cannot")
    add(" * reach a release build.")
    add(" */")
    add("object Tables {")

    add("    // --------------------------------------------------------------- units.py")
    add("")
    add(kmap("MASS_TO_G", units._MASS_TO_G, kdouble, "Double"))
    add("")
    add(kmap("VOLUME_TO_ML", units._VOLUME_TO_ML, kdouble, "Double"))
    add("")
    add(kmap("MASS_ALIASES", units._MASS_ALIASES, kstring, "String"))
    add("")
    add(kmap("VOLUME_ALIASES", units._VOLUME_ALIASES, kstring, "String"))
    add("")
    add(kmap("DENSITY_G_PER_CUP", units._DENSITY_G_PER_CUP, kdouble, "Double"))
    add("")
    add(kset("LIQUIDS", units._LIQUIDS))
    add("")
    add(kmap("FRACTION_CHARS", units._FRACTION_CHARS, kstring, "String"))
    add("")
    # int -> int: gas mark numbers are the one table with no string keys.
    add(kmap("GAS_MARK_C", units._GAS_MARK_C, kint, "Int", key_fn=kint, key_type="Int"))
    add("")

    add("    // -------------------------------------------------------------- recipe.py")
    add("")
    add(klist("JUNK_SELECTORS", recipe._JUNK_SELECTORS))
    add("")
    add(kset("PROTECTED_TAGS", recipe._PROTECTED_TAGS))
    add("")
    add(kset("BLOCK_TAGS", recipe._BLOCK_TAGS))
    add("")
    add(kset("NOISE_WORDS", recipe._NOISE_WORDS))
    add("")
    add(kmap("TRANSLITERATIONS", recipe._TRANSLITERATIONS, kstring, "String"))
    add("")
    add(klist("INGREDIENT_UNITS", unit_alternatives(recipe._INGREDIENT_UNITS)))
    add("")

    add("    // Regexes. Kotlin passes options separately, so re.I becomes")
    add("    // RegexOption.IGNORE_CASE rather than an inline (?i) that Java rejects.")
    add("")
    for name in ("_MEASURE_RE", "_REDUNDANT_METRIC_RE"):
        add(kregex(name.lstrip("_"), getattr(units, name)))
        add("")
    for name in ("_JUNK_HEADINGS", "_INGREDIENT_HEADINGS", "_METHOD_HEADINGS", "_JUNK_LINE",
                 "_SKIP_SMALL", "_INTRO_BLURB", "_LABEL_PARAGRAPH", "_GROUP_LABEL", "_QTY_RE"):
        add(kregex(name.lstrip("_"), getattr(recipe, name)))
        add("")

    add("    // ----------------------------------------------------------- classify.py")
    add("")
    add("    /** category -> keywords, each weighted. Higher weight is a stronger signal. */")
    lines = ["    val CATEGORY_KEYWORDS: Map<String, List<Pair<String, Int>>> = mapOf("]
    for category, words in classify.KEYWORDS.items():
        lines.append(f"        {kstring(category)} to listOf(")
        for word, weight in words:
            lines.append(f"            {kstring(word)} to {kint(weight)},")
        lines.append("        ),")
    lines.append("    )")
    add("\n".join(lines))
    add("")
    add(kset("NEEDS_BOUNDARY", classify.NEEDS_BOUNDARY))
    add("")
    add(klist("FOLDERS", classify.FOLDERS))
    add("")
    add(f'    const val UNCLASSIFIED: String = {kstring(classify.UNCLASSIFIED)}')
    add("")
    add(f"    const val MIN_SCORE: Int = {kint(classify.MIN_SCORE)}")
    add("")
    add(f"    const val MIN_MARGIN: Double = {kdouble(classify.MIN_MARGIN)}")
    add("")
    add(f"    const val SUBSTRING_MIN_LENGTH: Int = {kint(classify.SUBSTRING_MIN_LENGTH)}")
    add("}")
    add("")

    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args()

    try:
        text = render()
    except ExportError as exc:
        print(f"cannot export: {exc}", file=sys.stderr)
        return 1

    if args.check:
        if not TARGET.exists():
            print(f"{TARGET.relative_to(ROOT)} is missing; run scripts/export_tables.py",
                  file=sys.stderr)
            return 1
        if TARGET.read_text(encoding="utf-8") != text:
            print(f"{TARGET.relative_to(ROOT)} is stale; run scripts/export_tables.py "
                  f"and commit the result", file=sys.stderr)
            return 1
        print("Tables.kt in sync")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())