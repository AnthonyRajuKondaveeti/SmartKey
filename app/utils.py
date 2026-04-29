"""
utils.py
--------
Shared utilities used across the app.
"""
import re

# Abbreviations whose trailing period must not trigger a sentence split.
# Used identically in translation.py and grammar.py — defined once here.
ABBREV_RE = re.compile(
    r'\b(Dr|Mr|Mrs|Ms|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e|approx|dept|govt)\.',
    re.IGNORECASE,
)

# Single source of truth for supported languages.
# Imported by popup.py and hotkey_dialog.py so adding a language only
# requires one edit here.
LANGUAGES = [
    ("Hindi",     "hin_Deva"),
    ("Bengali",   "ben_Beng"),
    ("Marathi",   "mar_Deva"),
    ("Telugu",    "tel_Telu"),
    ("Tamil",     "tam_Taml"),
    ("Kannada",   "kan_Knda"),
    ("Punjabi",   "pan_Guru"),
    ("Malayalam", "mal_Mlym"),
]


def is_english_input(text: str) -> bool:
    """True if text is predominantly Latin-script (safe to send to grammar/translation).

    Uses explicit Indic Unicode block ranges (U+0900–U+0DFF) rather than the
    old `ord(c) > 0x024F` threshold which incorrectly rejected Vietnamese and
    Extended Latin characters as non-English.
    """
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return True
    indic = sum(1 for c in alpha if 'ऀ' <= c <= '෿')
    return indic / len(alpha) < 0.10
