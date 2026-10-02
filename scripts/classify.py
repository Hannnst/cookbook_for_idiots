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
    ],
    "dessert": [
        ("brownie", 3), ("brownies", 3), ("cupcake", 3), ("sjokolade", 2), ("chocolate", 2),
        ("kake", 2), ("kaker", 2), ("kjeks", 2), ("cookie", 2), ("cookies", 2), ("pudding", 3),
        ("dessert", 3), ("iskrem", 3), ("sorbet", 3), ("gelé", 3), ("gele", 3), ("tart", 3),
        ("cake", 2), ("pie", 2), ("piecrust", 3), ("kakebunn", 3), ("bakst", 2),
        ("krem", 1), ("søt", 1), ("riskota", 3), ("cheesecake", 3), ("browni", 3),
    ],
    "drinks": [
        ("smoothie", 3), ("cocktail", 3), ("mocktail", 3), ("cappuccino", 3), ("espresso", 3),
        ("kaffe", 2), ("coffee", 2), ("latte", 2), ("lemonade", 3), ("juice", 2), ("saft", 3),
        ("drikk", 2), ("drink", 2), ("brus", 2), ("te", 1), ("iced", 2), ("iste", 2),
    ],
    "dinner": [
        ("gryte", 3), ("gryter", 3), ("lapskaus", 3), ("karbonade", 3), ("koteletter", 3),
        ("grateng", 3), ("gratin", 3), ("kjøtt", 2), ("kjott", 2), ("kylling", 2),
        ("karri", 2), ("svin", 2), ("skinke", 2), ("bacon", 2), ("biff", 2), ("fisk", 2),
        ("laks", 2), ("torsk", 2), ("reker", 2), ("pasta", 2), ("spaghetti", 2),
        ("risotto", 3), ("nudler", 3), ("taco", 2), ("tacos", 2), ("pizza", 2),
        ("burger", 2), ("suppe", 2), ("stek", 2), ("wok", 2), ("middag", 1), ("lunsj", 2),
        ("ris", 1), ("potet", 2), ("gulrot", 1), ("kål", 1), ("egg", 1),
        ("chicken", 3), ("lasagne", 3), ("meatballs", 3), ("chili", 2),
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