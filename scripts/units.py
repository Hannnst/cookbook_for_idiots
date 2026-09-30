"""Unit normalization for the cookbook house style.

The repo writes metric first (g, kg, dl) and shows Fahrenheit in parentheses
after a round Celsius value: ``200°C (392°F)``, ``175°C (347°F)``. Nothing here
guesses -- when a value cannot be converted safely the original text is kept and
the caller reports it instead of inventing a number.
"""

from __future__ import annotations

import re

# Fractions arrive as unicode glyphs or as "1/2"; normalize glyphs to "n/d".
_FRACTION_CHARS = {
    "¼": "1/4", "½": "1/2", "¾": "3/4", "⅐": "1/7", "⅑": "1/9", "⅒": "1/10",
    "⅓": "1/3", "⅔": "2/3", "⅕": "1/5", "⅖": "2/5", "⅗": "3/5", "⅘": "4/5",
    "⅙": "1/6", "⅚": "5/6", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8",
}

_VOLUME_TO_ML = {
    "tsp": 4.93, "teaspoon": 4.93, "teaspoons": 4.93,
    "tbsp": 14.79, "tablespoon": 14.79, "tablespoons": 14.79, "tbs": 14.79,
    "cup": 236.59, "cups": 236.59,
    "fl oz": 29.57, "floz": 29.57, "fluid ounce": 29.57, "fluid ounces": 29.57,
    "pint": 473.18, "pints": 473.18, "pt": 473.18,
    "quart": 946.35, "quarts": 946.35, "qt": 946.35,
    "gallon": 3785.41, "gallons": 3785.41, "gal": 3785.41,
    "ml": 1.0, "milliliter": 1.0, "millilitre": 1.0, "milliliters": 1.0,
    "cl": 10.0, "centiliter": 10.0, "centilitre": 10.0,
    "dl": 100.0, "deciliter": 100.0, "decilitre": 100.0, "deciliters": 100.0,
    "l": 1000.0, "liter": 1000.0, "litre": 1000.0, "liters": 1000.0, "litres": 1000.0,
}

_MASS_TO_G = {
    "g": 1.0, "gram": 1.0, "grams": 1.0, "gr": 1.0,
    "kg": 1000.0, "kilogram": 1000.0, "kilograms": 1000.0, "kilo": 1000.0, "kilos": 1000.0,
    "oz": 28.35, "ounce": 28.35, "ounces": 28.35,
    "lb": 453.59, "lbs": 453.59, "pound": 453.59, "pounds": 453.59,
}

_VOLUME_ALIASES = {
    "cup": "cup", "cups": "cup", "c": "cup",
    "tbsp": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp", "tbs": "tbsp",
    "tblsp": "tbsp", "tbsps": "tbsp", "T": "tbsp",
    "tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp", "t": "tsp",
    "fl oz": "fl oz", "fl. oz": "fl oz", "floz": "fl oz", "fluid ounce": "fl oz",
    "pint": "pint", "pints": "pint", "pt": "pint",
    "quart": "quart", "quarts": "quart", "qt": "quart",
    "gallon": "gallon", "gallons": "gallon", "gal": "gallon",
}

_MASS_ALIASES = {
    "oz": "oz", "ozs": "oz", "ounce": "oz", "ounces": "oz",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "g": "g", "gram": "g", "grams": "g", "gr": "g",
    "kg": "kg", "kilogram": "kg", "kilograms": "kg", "kilo": "kg", "kilos": "kg",
}

# Cups -> grams, for dry and semi-dry staples only.
_DENSITY_G_PER_CUP = {
    "all-purpose flour": 120, "all purpose flour": 120, "plain flour": 120,
    "flour": 120, "bread flour": 120, "cake flour": 113, "whole wheat flour": 120,
    "granulated sugar": 200, "white sugar": 200, "caster sugar": 200, "sugar": 200,
    "brown sugar": 213, "packed brown sugar": 213, "light brown sugar": 213,
    "dark brown sugar": 213,
    "powdered sugar": 120, "icing sugar": 120, "confectioners' sugar": 120,
    "confectioners sugar": 120,
    "unsalted butter": 227, "butter": 227, "salted butter": 227, "margarine": 227,
    "cocoa powder": 85, "dutch-process cocoa": 85, "cocoa": 85,
    "cornstarch": 125, "corn starch": 125, "cornflour": 125,
    "almond flour": 96, "almond meal": 96, "rolled oats": 89, "oats": 89,
    "chocolate chips": 170, "chocolate chunks": 170, "granulated garlic": 136,
    "shredded coconut": 85, "breadcrumbs": 108, "panko": 50, "panko breadcrumbs": 50,
    "salt": 292, "table salt": 292, "kosher salt": 145,
}

# Ingredients measured by volume even without a density -- these stay in dl.
_LIQUIDS = (
    "water", "milk", "cream", "buttermilk", "oil", "juice", "syrup", "honey",
    "stock", "broth", "wine", "beer", "vinegar", "extract", "brandy", "rum",
    "liqueur", "wine", "yogurt", "yoghurt", "sour cream", "coconut milk",
    "espresso", "coffee", "wine", "glaze", "sauce",
)

# A measurement: number (incl. mixed fraction or range), then the unit.
_MEASURE_RE = re.compile(
    r"(?P<num>\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+[.,]\d+|\d+)"
    r"(?:\s*(?:-|–|—|to|or)\s*(?P<num2>\d+\s*/\s*\d+|\d+[.,]\d+|\d+))?"
    r"\s*(?P<unit>fl\.?\s*oz|fluid\s+ounces?|tablespoons?|teaspoons?|cups?|tbsp|tbsps?|tblsp|"
    r"tbs|ounces?|pounds?|lbs?|g|grams?|kilos?|kg|oz|lb|pints?|quarts?|gallons?|ml|dL|dl|cl|l)\b",
    re.I,
)

_GLYPH_RE = re.compile("[" + "".join(_FRACTION_CHARS) + "]")


def normalize_fractions(text: str) -> str:
    """Rewrite unicode fraction glyphs as "n/d" so the number regex can see them."""
    return _GLYPH_RE.sub(lambda m: " " + _FRACTION_CHARS[m.group(0)] + " ", text)


def parse_number(token: str) -> float | None:
    """Parse "2", "2.5", "1/2", "1 1/2" into a float."""
    token = normalize_fractions(token).strip()
    token = re.sub(r"\s+", " ", token)
    match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s+(\d+)\s*/\s*(\d+)", token)
    if match:
        whole, num, den = match.groups()
        try:
            return float(whole.replace(",", ".")) + float(num) / float(den)
        except ZeroDivisionError:
            return None
    match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", token)
    if match:
        num, den = match.groups()
        try:
            return float(num) / float(den)
        except ZeroDivisionError:
            return None
    try:
        return float(token.replace(",", "."))
    except ValueError:
        return None


def _fmt(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def fmt_mass_parts(grams: float) -> tuple[str, str]:
    """Grams below a kilo, otherwise kg. Rounded to something a human would measure."""
    if grams >= 1000:
        return (_fmt(round(grams / 1000 * 100) / 100), "kg")
    if grams < 10:
        return (_fmt(round(grams)), "g")
    return (str(int(round(grams / 5.0)) * 5), "g")


def fmt_mass(grams: float) -> str:
    number, unit = fmt_mass_parts(grams)
    return f"{number} {unit}"


def fmt_volume_parts(ml: float) -> tuple[str, str]:
    """The repo uses dl for anything from a few tablespoons up (see cookies.md: 1.6 dl),
    and ml below that so a 1-tbsp measure does not become a misleading 0.1 dl.
    """
    if ml >= 20:
        return (_fmt(round(ml / 10.0) / 10.0), "dl")
    return (_fmt(round(ml)), "ml")


def fmt_volume(ml: float) -> str:
    number, unit = fmt_volume_parts(ml)
    return f"{number} {unit}"


def density_for(line: str) -> float | None:
    """Grams per cup for a dry ingredient mentioned in ``line``, if known.

    Longest matching name wins so "all-purpose flour" beats "flour".
    """
    lowered = " " + normalize_fractions(line).lower() + " "
    matches = [
        name for name in _DENSITY_G_PER_CUP
        if re.search(rf"\b{re.escape(name)}\b", lowered)
    ]
    if not matches:
        return None
    return _DENSITY_G_PER_CUP[max(matches, key=len)]


def ingredient_after(text: str, start: int, stop: int) -> str:
    """The phrase a measurement applies to: the words right after it, bounded by
    the next measurement. "1 tsp vanilla extract and a pinch of salt" must not
    read as vanilla extract having salt's density.
    """
    tail = text[stop: stop + 60]
    tail = _MEASURE_RE.split(tail)[0] if _MEASURE_RE.search(tail) else tail
    tail = re.split(r"\band\b|\bplus\b|\bor\b|;|\,", tail)[0]
    return tail


def density_after(text: str, stop: int) -> float | None:
    return density_for(ingredient_after(text, stop, stop))


def _is_liquid(line: str) -> bool:
    lowered = normalize_fractions(line).lower()
    return any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in _LIQUIDS)


def convert_quantity(line: str) -> tuple[str, str | None]:
    """Convert every measurement in ``line`` to the house unit for that ingredient.

    Returns (text, notes). Each note describes one conversion, or says why a
    measurement was left alone. Returns (line, []) when there is nothing to do.
    """
    text = normalize_fractions(line)
    notes: list[str] = []

    # Convert right-to-left so earlier match offsets stay valid as text shrinks.
    for match in reversed(list(_MEASURE_RE.finditer(text))):
        converted, note = _convert_one(text, match)
        if converted != text:
            text = converted
        if note:
            notes.append(note)
    notes.reverse()

    if not notes and text == line:
        return (line, None)
    return (text, notes) if notes else (line, None)


def _convert_one(text: str, match: re.Match) -> tuple[str, str | None]:
    """Convert the single measurement described by ``match``."""
    raw_unit = re.sub(r"\s+", " ", match.group("unit").strip().lower())
    value = parse_number(match.group("num"))
    if value is None:
        return (text, None)

    value2 = parse_number(match.group("num2")) if match.group("num2") else None
    is_range = value2 is not None

    mass_key = _MASS_ALIASES.get(raw_unit)
    volume_key = _VOLUME_ALIASES.get(raw_unit)

    # Already metric -- leave alone.
    if raw_unit in ("ml", "dl", "cl", "l", "g", "kg"):
        return (text, None)

    grams: float | None = None
    ml: float | None = None
    route = ""

    if mass_key:
        grams = value * _MASS_TO_G[mass_key]
        route = "mass"
    elif volume_key:
        ml = value * _VOLUME_TO_ML[volume_key]
        density = density_after(match.string, match.end())
        if density and volume_key in ("cup", "tbsp", "tsp"):
            # A known dry ingredient measured by spoon or cup: grams is more useful.
            divisor = {"cup": 1.0, "tbsp": 16.0, "tsp": 48.0}[volume_key]
            grams = round(value / divisor * density, 2)
            ml = None
            route = "mass-by-density"
        elif not density and not _is_liquid(ingredient_after(match.string, match.start(), match.end())):
            return (text, f"kept as-is: no density for '{raw_unit}' of this ingredient")

    if route not in ("mass", "mass-by-density") and ml is None:
        return (text, None)

    render = fmt_mass_parts if route in ("mass", "mass-by-density") else fmt_volume_parts
    factor = (grams if route in ("mass", "mass-by-density") else ml) / value

    if is_range and value2 is not None:
        # "2 to 3 tbsp" -> "6-9 g": one shared unit for the whole range.
        low_number, low_unit = render(value * factor)
        high_number, high_unit = render(value2 * factor)
        unit = low_unit if low_unit == high_unit else f"{low_unit}/{high_unit}"
        replacement = f"{low_number}-{high_number} {unit}"
    else:
        number, unit = render(value * factor)
        replacement = f"{number} {unit}"

    note = f"{match.group('num').strip()} {raw_unit} -> {replacement}"
    if route == "mass-by-density":
        note += " (by density)"
    return (text[: match.start()] + replacement + text[match.end():], note)


def convert_temperature(text: str) -> tuple[str, list[str]]:
    """Rewrite temperatures as ``N°C (N°F)``, Celsius rounded to 5 and F derived from it.

    Already-converted text (a "C (F)" pair) is shielded first so a file like
    ``200°C (392°F)`` is left exactly as it is.
    """
    notes: list[str] = []
    pairs: list[str] = []

    def stash(value: str) -> str:
        pairs.append(value)
        return f"\x00{chr(0xE000 + len(pairs) - 1)}\x00"

    def from_f(match: re.Match) -> str:
        fahrenheit = float(match.group(1))
        rounded_c = int(round(((fahrenheit - 32) * 5 / 9) / 5.0) * 5)
        derived_f = int(round(rounded_c * 9 / 5 + 32))
        notes.append(f"{match.group(0).strip()} -> {rounded_c}°C ({derived_f}°F)")
        return stash(f"{rounded_c}°C ({derived_f}°F)")

    def from_c(match: re.Match) -> str:
        celsius = float(match.group(1))
        fahrenheit = int(round(celsius * 9 / 5 + 32))
        notes.append(f"{match.group(0).strip()} -> {int(round(celsius))}°C ({fahrenheit}°F)")
        return stash(f"{int(round(celsius))}°C ({fahrenheit}°F)")

    # Shield existing pairs, then stash newly converted ones, so neither pass
    # ever re-reads a "°C (…°F)" it just produced.
    text = re.sub(r"\d{2,3}\s*[°º]?\s*C\s*\(\s*\d{2,3}\s*[°º]?\s*F\s*\)",
                  lambda m: stash(m.group(0)), text, flags=re.I)
    text = re.sub(r"(?<![\d.])(-?\d{2,3})\s*[°º]?\s*(?:degrees\s+)?F\b", from_f, text, flags=re.I)
    text = re.sub(r"(?<![\d.])(-?\d{2,3})\s*[°º]?\s*(?:degrees\s+)?C\b", from_c, text, flags=re.I)
    text = re.sub(r"\x00(.)\x00", lambda m: pairs[ord(m.group(1)) - 0xE000], text)
    return (text, notes)


def normalize_line(line: str) -> tuple[str, list[str]]:
    """Full pass over one line: temperature, measurement, spacing."""
    line, notes = convert_temperature(line)
    line, more = convert_quantity(line)
    if more:
        notes.extend(more)
    line = re.sub(r"\s+", " ", line).strip()
    return (line, notes)


def normalize_text(text: str) -> tuple[str, list[str]]:
    """Normalize units across a block, one note per converted line."""
    notes: list[str] = []
    output = []
    for line in text.split("\n"):
        converted, line_notes = normalize_line(line)
        output.append(converted)
        notes.extend(line_notes)
    return ("\n".join(output), notes)
