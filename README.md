# cookbook_for_idiots
My personal fool-proof recipes for complete dinguses

## House style

Metric first, Fahrenheit in parentheses, `g`/`kg`/`dl`, numbered steps, `## Ingredients:`
and `## TODO:` for the method. Check a file with `scripts/recipe.py --lint`.

## Recipe extractor

Pulls a recipe off a web page and rewrites it in the house style above.

```
./recipe.py https://example.com/some-recipe
./recipe.py URL -o new_recipe.md
./recipe.py --lint existing_recipe.md
```

`recipe.py` at the repo root is a shim that hands over to `scripts/recipe.py` using this
project's own `.venv`, so it does not matter which `python` starts it (`python3`, the
Homebrew 3.14, the venv itself) or which directory you are in. First-time setup:

```
/opt/homebrew/opt/python@3.14/bin/python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

The draft goes to stdout and the report to stderr, so `./recipe.py URL > draft.md` gives
a clean file while you still see the report in the terminal. It never commits anything,
and never invents a number: anything it cannot convert is left as written and listed in the
report for you to decide.

The report also flags **conflicts** — cases where the source's own ingredient list and its
own instructions use different quantities for the same ingredient. Blogs get this wrong
often and neither side is labelled as the incorrect one.

Extraction tries JSON-LD `schema.org/Recipe` first, then microdata and recipe-plugin
markup, and finally walks the page headings. The last tier is what handles sites with no
structured recipe data at all, which is most of them.

### What it rewrites

| Source | Output |
|---|---|
| `2 cups all-purpose flour` | `240 g all-purpose flour` |
| `1½ cups granulated sugar` | `300 g granulated sugar` |
| `1 cup whole milk` | `2.4 dl whole milk` |
| `8 oz cream cheese` | `225 g cream cheese` |
| `Bake at 350°F` | `Bake at 175°C (347°F)` |
| affiliate product blocks | removed |
| `<li>1 large <a rel="sponsored">frozen banana</a></li>` | `1 large frozen banana` |
| "We'll walk through each step..." intro paragraph | removed |

Cups become grams only for dry ingredients with a known density. Liquids become `dl`/`ml`.
Anything else — `2 teaspoons baking powder`, `4 Earl Grey tea bags`, `a handful of
parsley` — is left exactly as written, because a gram figure would be a guess.
