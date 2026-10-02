"""Guess which recipe folder a recipe belongs in, from its title alone.

Deliberately conservative: anything not clearly one category returns
``unclassified`` rather than guessing, because a silently mis-filed recipe is
worse than one that needs moving by hand.
"""

from __future__ import annotations

import re

# Long, specific words score higher than short or ambiguous ones. Short tokens that
# appear inside other words (Norwegian "te" in "grateng", "ris" in "ristet") are
# matched on word boundaries only.
KEYWORDS: dict[str, list[tuple[str, int]]] = {
    "bread": [
        ("surdeigsbrød", 3), ("surdeigsbød", 3), ("rundstykke", 3), ("hamburgerbrød", 3),
        ("knekkebrød", 3), ("pitabrød", 3), ("lefse", 3), ("focaccia", 3), ("baguette", 3),
        ("brød", 2), ("brot", 2), ("boller", 2), ("toast", 2), ("bread", 2),
        ("rundstykker", 3),
        # English
        ("sourdough", 3), ("ciabatta", 3), ("flatbread", 3), ("focaccia", 3),
        ("naan", 3), ("pita", 3), ("cornbread", 3), ("doughnut", 3), ("donut", 3),
        ("scones", 3), ("scone", 3), ("waffle", 3), ("crumpet", 3), ("buns", 2), ("rolls", 2),
        ("loaf", 2), ("rye", 2), ("bagels", 3), ("muffin", 3), ("tortilla", 2),
    ],
    "dessert": [
        ("brownie", 3), ("brownies", 3), ("cupcake", 3), ("sjokolade", 2), ("chocolate", 2),
        ("kake", 2), ("kaker", 2), ("kjeks", 2), ("cookie", 2), ("cookies", 2), ("pudding", 3),
        ("dessert", 3), ("iskrem", 3), ("sorbet", 3), ("gelé", 3), ("gele", 3), ("tart", 3),
        ("cake", 2), ("pie", 2), ("piecrust", 3), ("kakebunn", 3), ("bakst", 2),
        ("krem", 1), ("søt", 1), ("riskota", 3), ("cheesecake", 3), ("browni", 3),
        # English
        ("ice cream", 3), ("icecream", 3), ("macaron", 3), ("macarons", 3),
        ("mousse", 3), ("fudge", 3), ("meringue", 3), ("eclair", 3),
        ("pudding", 3), ("cupcakes", 3), ("trifle", 3), ("parfait", 3), ("cobbler", 3),
        ("blondie", 3), ("whoopie", 3), ("pavlova", 3), ("gelato", 3), ("crumble", 2),
    ],
    "drinks": [
        ("smoothie", 3), ("cocktail", 3), ("mocktail", 3), ("cappuccino", 3), ("espresso", 3),
        ("kaffe", 2), ("coffee", 2), ("latte", 2), ("lemonade", 3), ("juice", 2), ("saft", 3),
        ("drikk", 2), ("drink", 2), ("brus", 2), ("te", 1), ("iced", 2), ("iste", 2),
        # English
        ("tea", 2), ("chai", 3), ("milkshake", 3), ("kombucha", 3), ("sangria", 3),
        ("punch", 3), ("herbal tea", 3), ("iced tea", 3), ("lemon squash", 3),
        ("hot chocolate", 3), ("eggnog", 3), ("cold brew", 3),
    ],
    "dinner": [
        ("gryte", 3), ("gryter", 3), ("lapskaus", 3), ("karbonade", 3), ("koteletter", 3),
        ("grateng", 3), ("gratin", 3), ("kjøtt", 2), ("kjott", 2), ("kylling", 2),
        ("karri", 2), ("svin", 2), ("skinke", 2), ("bacon", 2), ("biff", 2), ("fisk", 2),
        ("laks", 2), ("torsk", 2), ("reker", 2), ("pasta", 2), ("spaghetti", 2),
        ("risotto", 3), ("nudler", 3), ("taco", 2), ("tacos", 2), ("pizza", 2),
        ("burger", 2), ("suppe", 2), ("stek", 2), ("wok", 2), ("middag", 1), ("lunsj", 2),
        ("ris", 1), ("potet", 2), ("gulrot", 1), ("kål", 1), ("egg", 1), ("eggs", 2),
        ("pancakes", 3), ("pancake", 3), ("onion", 1), ("onion rings", 3), ("cheese", 1),
        ("chicken", 3), ("lasagne", 3), ("meatballs", 3), ("chili", 2),
        # English
        ("soup", 3), ("stew", 3), ("curry", 3), ("beef", 2), ("pork", 2), ("lamb", 2),
        ("turkey", 2), ("roast", 2), ("casserole", 3), ("meatloaf", 3), ("lasagna", 3),
        ("goulash", 3), ("sausage", 2), ("sausages", 2), ("noodles", 2), ("rice", 2),
        ("sandwich", 2), ("shepherd", 3), ("potato", 2), ("mashed potato", 3),
        ("salad", 2), ("bangers", 3), ("kebab", 2), ("gnocchi", 2), ("paella", 3),
        ("pilaf", 3), ("enchi", 3), ("quesadilla", 3), ("burrito", 2), ("wings", 2),
        ("braise", 2), ("casseroles", 3), ("mince", 1), ("sausage rolls", 3),
    ],
}

FOLDERS = ("bread", "dinner", "dessert", "drinks")
UNCLASSIFIED = "unclassified"

# The winner must score at least this, and beat the runner-up by this factor,
# otherwise we treat the title as ambiguous.
MIN_SCORE = 2
MIN_MARGIN = 1.5


# Words that must be matched on a word boundary even though they are long enough to
# be substring-matched, because they occur inside unrelated words:
#   "saft"  in "Saftig" (juicy)          "stek" in "o-STEK-ake" (cheesecake)
#   "tart"  in "s-tart" (school start)   "krem" in "KREM-ost" (cream cheese)
#   "iced"  in "sl-iced"
NEEDS_BOUNDARY = {
    "krem", "ris", "te", "egg", "kål", "søt", "saft", "stek", "tart", "iced", "burger",
    # English ones that hide inside longer words: "bun" in "bundle", "rye" in
    # "crystal", "tea" in "steamed", "pie" in "copied", "scone" in "sconed".
    "bun", "rye", "tea", "pie", "punch", "rolls", "roast", "stew", "salad", "wings",
    "eggs", "onion", "cheese",
    "kebab", "curry", "rice", "beef", "pork", "lamb", "mince", "loaf",
}

# Long enough to be safe as a substring, so compounds like "kyllinggryte",
# "ostekake" and "fiskegrateng" still match.
SUBSTRING_MIN_LENGTH = 4


def _score(title: str, keywords: list[tuple[str, int]]) -> int:
    lowered = title.lower()
    total = 0
    for word, weight in keywords:
        if not weight:
            continue
        if len(word) >= SUBSTRING_MIN_LENGTH and word not in NEEDS_BOUNDARY:
            if word in lowered:
                total += weight
        elif re.search(rf"(?<!\w){re.escape(word)}(?!\w)", lowered):
            total += weight
    return total


def classify_category(title: str) -> tuple[str, float]:
    """Return (folder, confidence) for a title. Confidence is 0.0-1.0."""
    if not title or not title.strip():
        return UNCLASSIFIED, 0.0

    scores = {folder: _score(title, keywords) for folder, keywords in KEYWORDS.items()}
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_folder, best_score = ranked[0]
    runner_up = ranked[1][1]

    if best_score < MIN_SCORE:
        return UNCLASSIFIED, 0.0
    if runner_up and best_score < runner_up * MIN_MARGIN:
        # Two categories genuinely compete, so we do not pick one.
        return UNCLASSIFIED, best_score / (best_score + runner_up)

    total = sum(scores.values())
    return best_folder, best_score / total if total else 0.0


def target_path(title: str, root, folder_override: str | None = None):
    """Where a batch-extracted recipe belongs: <root>/normies/<category>/<name>.md."""
    from recipe import recipe_filename

    folder = folder_override or classify_category(title)[0]
    return root / "normies" / folder / recipe_filename(title)