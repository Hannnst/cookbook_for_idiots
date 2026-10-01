# cookbook_for_idiots
My personal fool-proof recipes for complete dinguses

To generate a new idiot-proof recipe for your local repo:
```
python recipe.py URL
```

Or
```
python3 recipe.py URL
```

For multiple URLS:
```
python3 recipe.py URL1 URL2 URL3
python3 recipe.py -x https://www.google.com/collections/s/list/...   # a Google saved list
python3 recipe.py -x links.txt  
## Recipes
```

| Folder | What's in it |
|---|---|
| [`bread/`](bread) | breads and buns |
| [`dinner/`](dinner) | mains and gratins |
| [`dessert/`](dessert) | cakes, cookies, pies, plus the pie dough and frosting they use |
| [`drinks/`](drinks) | lattes, espresso drinks, coolers |
| [`normies/`](normies) | bulk-extracted drafts, filed into `bread/`, `dinner/`, `dessert/`, `drinks/` or `unclassified/` by a keyword guess |

New drafts from a single URL are written to the repo root, so move the file into the
matching folder once you have reviewed it. A batch writes into `normies/` instead — see
below.

## House style

Metric first, Fahrenheit in parentheses, `g`/`kg`/`dl`, numbered steps, `## Ingredients:`
and `## TODO:` for the method. Check a single file with `./recipe.py --lint path/to/recipe.md`,
or lint every recipe in the folders above at once:

```
./recipe.py --lint-all
```

## Recipe extractor

Pulls a recipe off a web page and rewrites it in the house style above.

```
./recipe.py https://example.com/some-recipe
./recipe.py URL -o new_recipe.md
./recipe.py URL -o -                 # print to stdout instead of saving
./recipe.py --lint existing_recipe.md
```

The saved file is named after the recipe title in `snake_case`
(`Earl Grey Tea Cake` becomes `earl_grey_tea_cake.md`), prints the filename at the end of
the review so you can find it again, and opens it in `$VISUAL`/`$EDITOR` — or your default
editor on macOS — when run from a terminal. It never silently replaces an existing recipe:
you get `--force`, `-o NAME`, or `-o -` instead, so hand-edited recipes are safe.

### Extracting a whole list at once

Two or more URLs run as a batch, and each one is saved into `normies/<category>/` instead of
the repo root:

```
./recipe.py URL1 URL2 URL3
./recipe.py -x https://www.google.com/collections/s/list/...   # a Google saved list
./recipe.py -x links.txt                                     # one URL per line
```

`-x` takes a Google saved-list page or a text file containing one URL per line, with `#`
comments. Google saved lists must be public; a private one returns a sign-in page and the
script tells you to export the URLs to a text file instead.

Batch extras:

| Flag | Effect |
|---|---|
| `--dry-run` | show the `normies/` tree without fetching anything |
| `--limit N` | stop after N URLs |
| `--delay S` | pause between pages, default `1.0` |
| `--folder NAME` | save everything into one folder instead of classifying |
| `--refresh` | overwrite files left by an earlier run (otherwise they are skipped) |

Two recipes that reduce to the same filename get `name.md` and `name_2.md`. One page failing
never stops the rest. It ends with a summary of what was saved, skipped, had no recipe, or
failed to fetch, and exits non-zero only if a page could not be fetched at all.

Batch extraction is a first pass, not a finished recipe: expect roughly a third of a saved
list to yield anything, because plenty of saved links are not recipes at all, and plenty of
recipe sites publish no machine-readable recipe. The classifier is keyword-based, so check
`normies/unclassified/` — anything it was not confident about lands there for you to file.

`recipe.py` at the repo root is a shim that hands over to `scripts/recipe.py` using this
project's own `.venv`, so it does not matter which `python` starts it (`python3`, the
Homebrew 3.14, the venv itself) or which directory you are in. First-time setup:

```
/opt/homebrew/opt/python@3.14/bin/python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

The draft goes to stdout and the report to stderr, so `./recipe.py URL -o - > draft.md`
gives a clean file while you still see the report in the terminal. It never commits
anything, and never invents a number: anything it cannot convert is left as written and
listed in the report for you to decide.

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
