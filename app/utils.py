"""
utils.py
--------
Shared utilities used by multiple engine modules.
"""
import re

# Abbreviations whose trailing period must not trigger a sentence split.
# Used identically in translation.py and grammar.py — defined once here.
ABBREV_RE = re.compile(
    r'\b(Dr|Mr|Mrs|Ms|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e|approx|dept|govt)\.',
    re.IGNORECASE,
)
