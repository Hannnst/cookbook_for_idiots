#!/usr/bin/env python3
"""Run the recipe extractor with this project's virtualenv.

The real work lives in scripts/recipe.py. This shim exists so the script runs the
same way whichever Python starts it and whichever directory you are in:

    ./recipe.py URL
    python3 recipe.py URL
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ROOT, "scripts", "recipe.py")
VENV_PYTHON = os.path.join(ROOT, ".venv", "bin", "python")

if not os.path.exists(VENV_PYTHON):
    sys.exit(
        f"No virtualenv found at {VENV_PYTHON}.\n"
        f"Create it with:\n"
        f'  /opt/homebrew/opt/python@3.14/bin/python3.14 -m venv .venv\n'
        f'  .venv/bin/python -m pip install -r requirements.txt'
    )

os.execv(VENV_PYTHON, [VENV_PYTHON, SCRIPT, *sys.argv[1:]])
