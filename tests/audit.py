"""
audit.py
--------
End-to-end quality audit for Smart Keyboard pipelines.

Tests short sentences (in-distribution) and long sentences (out-of-distribution)
across: Grammar, Translation (Hindi + Telugu), and all 4 Tone personas.

Run from project root with venv active:
    python tests/audit.py
"""

import sys
import os
import time

# Force UTF-8 output on Windows (avoids cp1252 UnicodeEncodeError for Devanagari)
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add app/ to path so imports resolve exactly as the app does
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import transformers  # must be imported on main thread before any bg threads start
from transformers import AutoTokenizer, AlbertTokenizer  # noqa: F401 — pre-init module locks
from grammar     import GrammarEngine
from translation import TranslationEngine
from tone        import ToneEngine, TONE_MAX_CHARS

# ── Model paths (relative to project root) ────────────────────────────────────
_ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAMMAR_DIR  = os.path.join(_ROOT, "models", "grammar")
TRANS_DIR    = os.path.join(_ROOT, "models", "indictrans2")
TONE_DIR     = os.path.join(_ROOT, "models", "tone", "hin")

# ── Test sentences ────────────────────────────────────────────────────────────
SHORT = [
    "i love you",
    "i'm coming today",
    "i dislike him",
    "are you home?",
    "i'm sorry i was wrong",
    "can we meet tomorrow?",
]

LONG = [
    (
        "OK, I thought this was already handled so I didn't follow up earlier. "
        "Maybe that's on me, idk. Now it seems like we're back to square one "
        "and I'm not sure what to do next."
    ),
    (
        "I have been trying to reach you for the past few days but you haven't "
        "responded. I understand you might be busy but this is really important "
        "and I need your help to sort this out as soon as possible."
    ),
]

PERSONAS = ["Mother", "Friend", "Partner", "Stranger"]

# ── Helpers ───────────────────────────────────────────────────────────────────

PASS  = "OK"
WARN  = "!!"
SKIP  = "--"

def _load_engines():
    import threading

    # Load sequentially to avoid OOM on constrained machines
    grammar_engine = GrammarEngine(GRAMMAR_DIR)
    trans_engine   = TranslationEngine(TRANS_DIR)
    tone_engine    = ToneEngine(TONE_DIR)
    events = {n: threading.Event() for n in ("grammar", "translation", "tone")}

    grammar_engine.load(
        on_ready=lambda: events["grammar"].set(),
        on_error=lambda e: (print(f"  [LOAD ERROR] grammar: {e}"), events["grammar"].set()),
    )
    trans_engine.load(
        on_ready=lambda: events["translation"].set(),
        on_error=lambda e: (print(f"  [LOAD ERROR] translation: {e}"), events["translation"].set()),
    )
    tone_engine.load(
        on_ready=lambda: events["tone"].set(),
        on_error=lambda e: (print(f"  [LOAD ERROR] tone: {e}"), events["tone"].set()),
    )

    print("Loading models (this may take a minute)...")
    for name, ev in events.items():
        ev.wait(timeout=180)
        status = PASS if (
            (name == "grammar"     and grammar_engine.is_ready) or
            (name == "translation" and trans_engine.is_ready) or
            (name == "tone"        and tone_engine.is_ready)
        ) else WARN
        print(f"  {status} {name}")

    return grammar_engine, trans_engine, tone_engine


def _timed(fn, *args, **kwargs):
    t0 = time.monotonic()
    result = fn(*args, **kwargs)
    return result, (time.monotonic() - t0) * 1000


def _flag_tone(source_hi: str, output: str) -> str:
    """Return a short quality flag for the tone output."""
    if output == source_hi:
        return f"{SKIP} (skipped — too long)" if len(source_hi) > TONE_MAX_CHARS else f"{WARN} (unchanged)"
    # Check for obvious meaning reversal (e.g. नापसंद→पसंद)
    src_words = set(source_hi.split())
    out_words = set(output.split())
    overlap = len(src_words & out_words) / max(len(src_words), 1)
    if overlap < 0.2:
        return f"{WARN} (low overlap — possible hallucination)"
    return PASS


def _section(title: str):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _row(label: str, value: str, ms: float = None, flag: str = ""):
    ms_str = f"  [{ms:.0f}ms]" if ms is not None else ""
    print(f"  {label:<14} {value}{ms_str}  {flag}")


# ── Audit sections ────────────────────────────────────────────────────────────

def audit_grammar(engine: GrammarEngine):
    _section("1. GRAMMAR CORRECTION")
    all_inputs = SHORT + LONG
    for text in all_inputs:
        label = "SHORT" if text in SHORT else "LONG"
        result, ms = _timed(engine._correct_sync, text)
        changed = PASS if result.strip() != text.strip() else SKIP
        print(f"\n  [{label}]")
        _row("INPUT:",  text[:90] + ("..." if len(text) > 90 else ""))
        _row("OUTPUT:", result[:90] + ("..." if len(result) > 90 else ""), ms=ms, flag=changed)


def audit_translation(engine: TranslationEngine):
    _section("2. TRANSLATION  (English -> Hindi + Telugu)")
    for text in SHORT + LONG:
        label = "SHORT" if text in SHORT else "LONG"
        print(f"\n  [{label}] {text[:70]}{'...' if len(text) > 70 else ''}")
        for lang_code, lang_name in [("hin_Deva", "Hindi"), ("tel_Telu", "Telugu")]:
            result, ms = _timed(engine._translate_sync, text, lang_code)
            _row(f"{lang_name}:", result[:80] + ("..." if len(result) > 80 else ""), ms=ms)


def audit_tone(tone_engine: ToneEngine, trans_engine: TranslationEngine):
    _section("3. TONE  (Hindi translation → persona)")
    for text in SHORT + LONG:
        label = "SHORT" if text in SHORT else "LONG"
        hindi, ms_tr = _timed(trans_engine._translate_sync, text, "hin_Deva")
        print(f"\n  [{label}] EN: {text[:60]}{'...' if len(text) > 60 else ''}")
        _row("-> Hindi:", hindi[:80] + ("..." if len(hindi) > 80 else ""), ms=ms_tr)

        if len(hindi) > TONE_MAX_CHARS:
            print(f"  {SKIP} Tone skipped — {len(hindi)} chars > {TONE_MAX_CHARS} limit")
            continue

        for idx, name in enumerate(PERSONAS):
            result, ms_tone = _timed(tone_engine._apply_sync, hindi, idx)
            flag = _flag_tone(hindi, result)
            _row(f"{name}:", result[:80] + ("..." if len(result) > 80 else ""), ms=ms_tone, flag=flag)


def audit_full_pipeline(grammar: GrammarEngine, trans: TranslationEngine, tone: ToneEngine):
    _section("4. FULL PIPELINE  (Grammar → Translate → Tone)")
    for text in SHORT + LONG:
        label = "SHORT" if text in SHORT else "LONG"
        print(f"\n  [{label}] RAW: {text[:70]}{'...' if len(text) > 70 else ''}")

        corrected, ms_g  = _timed(grammar._correct_sync, text)
        hindi,     ms_t  = _timed(trans._translate_sync, corrected, "hin_Deva")

        _row("Corrected:", corrected[:80] + ("..." if len(corrected) > 80 else ""), ms=ms_g)
        _row("Hindi:",     hindi[:80]     + ("..." if len(hindi) > 80 else ""),     ms=ms_t)

        if len(hindi) > TONE_MAX_CHARS:
            print(f"  {SKIP} Tone skipped — text too long for tone models")
            continue

        for idx, name in enumerate(PERSONAS):
            result, ms_tone = _timed(tone._apply_sync, hindi, idx)
            flag = _flag_tone(hindi, result)
            _row(f"{name}:", result[:80] + ("..." if len(result) > 80 else ""), ms=ms_tone, flag=flag)


def print_summary():
    _section("SUMMARY — Strengths & Honest Limitations")
    print("""
  STRENGTHS
  ---------
  OK  Grammar engine reliably fixes capitalisation, punctuation, and common
      errors in short-to-medium English sentences (<=2 sentences).
      Fast: ~100ms on cached sentences.

  OK  Translation (English -> Hindi) is accurate and fluent for sentences up
      to ~50 words. IndicTrans2 handles colloquial input well (idk, gonna,
      etc.) after grammar pre-processing. Telugu output is structurally correct.

  OK  Tone engine (Mother, Friend personas) works well on short 1-sentence
      inputs -- adds culturally appropriate markers while preserving core meaning.

  OK  All three models run fully offline with no internet dependency.

  OK  Caching makes repeated phrases (common in messaging) instant after
      the first run.

  LIMITATIONS
  -----------
  !!  Tone models are trained on short sentence pairs (<=64 tokens). Inputs
      longer than ~150 Hindi characters are bypassed with a UI notice --
      intentional safety guard.

  !!  Partner (gf_wife) and Stranger tone models show hallucination and
      meaning drift on negative-sentiment inputs. Need more diverse training
      data, especially on negation.

  !!  Translation of very long texts (3+ sentences, >250 chars) can truncate
      or produce incomplete output -- IndicTrans2 was trained on sentence pairs.

  !!  Telugu tone models do not exist yet. Tone UI is hidden for Telugu.

  !!  Grammar engine corrects conservatively on heavy slang/abbreviations
      (e.g. "idk lol gonna b late") -- may leave them unchanged.
""")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  SMART KEYBOARD -- END-TO-END AUDIT")
    print("=" * 70)

    grammar, trans, tone = _load_engines()

    if grammar.is_ready:
        audit_grammar(grammar)
    else:
        print("\n[SKIP] Grammar engine not ready — check models/grammar/")

    if trans.is_ready:
        audit_translation(trans)
    else:
        print("\n[SKIP] Translation engine not ready — check models/indictrans2/")

    if trans.is_ready and tone.is_ready:
        audit_tone(tone, trans)
    else:
        print("\n[SKIP] Tone audit requires both translation + tone engines ready")

    if grammar.is_ready and trans.is_ready and tone.is_ready:
        audit_full_pipeline(grammar, trans, tone)
    else:
        print("\n[SKIP] Full pipeline audit requires all three engines ready")

    print_summary()
    print("=" * 70 + "\n")
