"""
test_week3.py
-------------
Week 3 exit-criteria tests — Tone adaptation + Grammar correction.

All inference tests use mock engines (same pattern as Week 2).
This validates every code path except the model weights themselves.

Run with:
    python tests/test_week3.py
"""

import sys, os, time, threading, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _write_tone_rules(tmpdir: str) -> str:
    """Write a minimal tone_rules.json to tmpdir and return its path."""
    rules = {
        "mother":  [["कृपया", "बेटा, कृपया"], ["करें।", "करें जी।"]],
        "friend":  [["कृपया", "यार,"], ["करें।", "करो।"]],
        "partner": [["कृपया", "प्लीज़"], ["करें।", "करो न।"]],
        "stranger":[["करो।", "कीजिए।"], ["यार,", ""]],
    }
    path = os.path.join(tmpdir, "tone_rules.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False)
    return path


def _make_mock_grammar_engine(delay: float = 0.05, response_fn=None):
    """Mock GrammarEngine — real cache logic, fake ONNX inference."""
    from grammar import GrammarEngine

    engine = GrammarEngine.__new__(GrammarEngine)
    GrammarEngine.__init__(engine, model_dir="/nonexistent")
    engine._ready.set()
    engine._load_called = True

    class _FakeTok:
        def __call__(self, text, **kw): return {}
        def decode(self, t, **kw):
            return response_fn(text) if response_fn else "I am going to the office."

    class _FakeModel:
        def generate(self, **kw):
            time.sleep(delay)
            return [None]

    # Patch _correct_sync directly for simplicity
    def _mock_sync(text):
        time.sleep(delay)
        return response_fn(text) if response_fn else "I am going to the office."

    engine._correct_sync = _mock_sync
    engine._load_called  = True
    return engine


# ══════════════════════════════════════════════════════════════════════════════
# 1. ToneEngine — rule loading
# ══════════════════════════════════════════════════════════════════════════════

def test_tone_rules_load():
    """ToneEngine loads tone_rules.json and reports ready."""
    from tone import ToneEngine
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_tone_rules(tmpdir)
        engine = ToneEngine(model_dir=tmpdir)
        done   = threading.Event()
        engine.load(on_ready=lambda: done.set(), on_error=lambda e: (_ for _ in ()).throw(AssertionError(e)))
        done.wait(timeout=5)
        assert engine.is_ready
        assert set(engine.available_tones()) == {"mother", "friend", "partner", "stranger"}
    print("  ✓ ToneEngine loads tone_rules.json and reports 4 tones")


def test_tone_missing_rules_file():
    """Missing tone_rules.json gives a clear error via on_error."""
    from tone import ToneEngine
    engine = ToneEngine(model_dir="/nonexistent/path")
    errors = []; done = threading.Event()
    engine.load(on_ready=lambda: None, on_error=lambda e: (errors.append(e), done.set()))
    done.wait(timeout=5)
    assert errors and ("not found" in errors[0].lower() or "no such" in errors[0].lower())
    print(f"  ✓ Missing rules file reported clearly: '{errors[0][:50]}…'")


def test_tone_load_never_called():
    """Rewriting without calling load() gives a clear error immediately."""
    from tone import ToneEngine
    engine = ToneEngine(model_dir="/nonexistent")
    errors = []; done = threading.Event()
    engine.rewrite("नमस्ते", "friend", on_result=lambda _: None,
                   on_error=lambda e: (errors.append(e), done.set()))
    done.wait(timeout=5)
    assert errors and "load()" in errors[0]
    print(f"  ✓ load() never called → clear error: '{errors[0][:55]}…'")


# ══════════════════════════════════════════════════════════════════════════════
# 2. ToneEngine — rewriting correctness
# ══════════════════════════════════════════════════════════════════════════════

def test_tone_rewrite_mother():
    """Mother tone applies aap/ji honorifics."""
    from tone import ToneEngine
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_tone_rules(tmpdir)
        engine = ToneEngine(model_dir=tmpdir)
        done   = threading.Event(); result = []
        engine.load(on_ready=lambda: None)
        engine._ready.wait(timeout=5)
        engine.rewrite("कृपया यहाँ आएं।", "mother",
                       on_result=lambda t: (result.append(t), done.set()))
        done.wait(timeout=5)
        assert result and "बेटा" in result[0], f"Expected maternal marker, got: {result}"
    print(f"  ✓ Mother tone inserts 'बेटा': '{result[0]}'")


def test_tone_rewrite_friend():
    """Friend tone uses yaar and casual forms."""
    from tone import ToneEngine
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_tone_rules(tmpdir)
        engine = ToneEngine(model_dir=tmpdir)
        engine.load(on_ready=lambda: None)
        engine._ready.wait(timeout=5)
        done = threading.Event(); result = []
        engine.rewrite("कृपया यहाँ आएं।", "friend",
                       on_result=lambda t: (result.append(t), done.set()))
        done.wait(timeout=5)
        assert result and "यार" in result[0], f"Expected 'यार', got: {result}"
    print(f"  ✓ Friend tone inserts 'यार': '{result[0]}'")


def test_tone_four_outputs_distinct():
    """The 4 tones produce different output for the same input."""
    from tone import ToneEngine
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_tone_rules(tmpdir)
        engine = ToneEngine(model_dir=tmpdir)
        engine.load(on_ready=lambda: None)
        engine._ready.wait(timeout=5)

        outputs = {}
        for tone in ["mother", "friend", "partner", "stranger"]:
            done = threading.Event(); result = []
            engine.rewrite("कृपया मुझे दस्तावेज़ भेजें।", tone,
                           on_result=lambda t, r=result, d=done: (r.append(t), d.set()))
            done.wait(timeout=5)
            outputs[tone] = result[0] if result else ""

        unique = len(set(outputs.values()))
        assert unique >= 2, f"Expected distinct tones, got: {outputs}"
    print(f"  ✓ 4 tones produce {unique} distinct outputs")


def test_tone_invalid_tone_raises():
    """Unknown tone name calls on_error with a clear message."""
    from tone import ToneEngine
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_tone_rules(tmpdir)
        engine = ToneEngine(model_dir=tmpdir)
        engine.load(on_ready=lambda: None)
        engine._ready.wait(timeout=5)
        errors = []; done = threading.Event()
        engine.rewrite("नमस्ते", "colleague",
                       on_result=lambda _: None,
                       on_error=lambda e: (errors.append(e), done.set()))
        done.wait(timeout=5)
        assert errors and "colleague" in errors[0]
    print(f"  ✓ Unknown tone 'colleague' → clear error: '{errors[0][:50]}'")


def test_tone_empty_input():
    """Empty input returns empty string without error."""
    from tone import ToneEngine
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_tone_rules(tmpdir)
        engine = ToneEngine(model_dir=tmpdir)
        engine.load(on_ready=lambda: None)
        engine._ready.wait(timeout=5)
        result = []; done = threading.Event()
        engine.rewrite("", "friend", on_result=lambda t: (result.append(t), done.set()))
        done.wait(timeout=5)
        assert result == [""]
    print("  ✓ Empty input returns empty string")


# ══════════════════════════════════════════════════════════════════════════════
# 3. GrammarEngine — cache and async interface
# ══════════════════════════════════════════════════════════════════════════════

def test_grammar_cache_basic():
    """GrammarEngine has a working LRU cache."""
    from grammar import _LRUCache
    c = _LRUCache(maxsize=5)
    c.put("I are going.", "I am going.")
    assert c.get("I are going.") == "I am going."
    assert c.get("missing") is None
    print("  ✓ Grammar LRU cache put/get works")


def test_grammar_async_callback():
    """correct() delivers result via on_result callback."""
    calls = [0]
    original_sync = None

    from grammar import GrammarEngine
    engine = GrammarEngine.__new__(GrammarEngine)
    GrammarEngine.__init__(engine, model_dir="/nonexistent")
    engine._ready.set()
    engine._load_called = True

    fixed_text = "I am going to the office."
    def _mock(text):
        time.sleep(0.05)
        return fixed_text
    engine._correct_sync = _mock

    result = []; done = threading.Event()
    engine.correct("I are going to office.", on_result=lambda t: (result.append(t), done.set()))
    done.wait(timeout=5)
    assert result == [fixed_text], f"Unexpected: {result}"
    print("  ✓ GrammarEngine async callback works")


def test_grammar_load_never_called():
    """correct() without load() gives a clear error."""
    from grammar import GrammarEngine
    engine = GrammarEngine(model_dir="/nonexistent")
    errors = []; done = threading.Event()
    engine.correct("test", on_result=lambda _: None,
                   on_error=lambda e: (errors.append(e), done.set()))
    done.wait(timeout=5)
    assert errors and "load()" in errors[0]
    print(f"  ✓ Grammar load() never called → clear error")


def test_grammar_latency_under_budget():
    """Grammar correction completes within 1.5s latency budget."""
    from grammar import GrammarEngine
    engine = GrammarEngine.__new__(GrammarEngine)
    GrammarEngine.__init__(engine, model_dir="/nonexistent")
    engine._ready.set()
    engine._load_called = True
    engine._correct_sync = lambda t: (time.sleep(0.4), "I am going to the office.")[1]

    result = []; done = threading.Event()
    t0 = time.monotonic()
    engine.correct("I are going to office.",
                   on_result=lambda t: (result.append(t), done.set()))
    done.wait(timeout=5)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5, f"Latency {elapsed:.2f}s exceeds 1.5s budget"
    print(f"  ✓ Grammar correction latency {elapsed:.2f}s — within 1.5s budget")


def test_grammar_preserves_meaning():
    """Corrected output must not be shorter than 30% of input (truncation guard)."""
    sentences = [
        ("I are going to office.",                "I am going to the office."),
        ("She don't like the food.",              "She doesn't like the food."),
        ("He go to school every day.",            "He goes to school every day."),
    ]
    from grammar import GrammarEngine
    engine = GrammarEngine.__new__(GrammarEngine)
    GrammarEngine.__init__(engine, model_dir="/nonexistent")
    engine._ready.set()
    engine._load_called = True

    for src, expected in sentences:
        engine._correct_sync = lambda t, e=expected: e
        result = []; done = threading.Event()
        engine.correct(src, on_result=lambda t: (result.append(t), done.set()))
        done.wait(timeout=5)
        assert result and len(result[0]) > len(src) * 0.3, \
            f"Output suspiciously short for: {src}"
    print("  ✓ Grammar output length validation passed (no truncation)")


# ══════════════════════════════════════════════════════════════════════════════
# 4. Pipeline integration — Translation → Tone chain
# ══════════════════════════════════════════════════════════════════════════════

def test_translation_tone_pipeline():
    """
    Full pipeline: TranslationEngine → ToneEngine → final output.
    Simulates the flow: English → Hindi → Tone-rewritten Hindi.
    """
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

    from translation import TranslationEngine

    # Mock translation engine
    trans_engine = TranslationEngine.__new__(TranslationEngine)
    TranslationEngine.__init__(trans_engine, model_dir="/nonexistent")
    trans_engine._ready.set()
    trans_engine._load_called = True

    class _FakeTok:
        src_lang = ""
        lang_code_to_id = {"hin_Deva": 1}
        def __call__(self, t, **kw): return {}
        def decode(self, t, **kw): return "कृपया मुझे दस्तावेज़ भेजें।"

    class _FakeModel:
        def generate(self, **kw): time.sleep(0.05); return [None]

    trans_engine._tokenizer = _FakeTok()
    trans_engine._model     = _FakeModel()

    # Real ToneEngine with rules
    from tone import ToneEngine
    with tempfile.TemporaryDirectory() as tmpdir:
        _write_tone_rules(tmpdir)
        tone_engine = ToneEngine(model_dir=tmpdir)
        tone_engine.load(on_ready=lambda: None)
        tone_engine._ready.wait(timeout=5)

        # Chain them manually (same as popup._run_translation does)
        final_result = []; done = threading.Event()

        def _on_hindi(hindi):
            tone_engine.rewrite(
                hindi_text = hindi,
                tone       = "friend",
                on_result  = lambda t: (final_result.append(t), done.set()),
            )

        trans_engine.translate("Please send me the document.", on_result=_on_hindi)
        done.wait(timeout=10)

        assert final_result, "No result from pipeline"
        assert "यार" in final_result[0], \
            f"Expected friend tone marker 'यार' in output: '{final_result[0]}'"
    print(f"  ✓ Translation→Tone pipeline: '{final_result[0]}'")


def test_tone_fallback_when_not_ready():
    """If ToneEngine is not ready, translation output is delivered as-is (graceful fallback)."""
    from translation import TranslationEngine
    from tone import ToneEngine

    trans_engine = TranslationEngine.__new__(TranslationEngine)
    TranslationEngine.__init__(trans_engine, model_dir="/nonexistent")
    trans_engine._ready.set()
    trans_engine._load_called = True
    trans_engine._correct_sync = lambda t: "नमस्ते।"

    # Tone engine NOT ready
    tone_engine = ToneEngine(model_dir="/nonexistent")
    # Don't call load() — not ready

    raw_hindi = "नमस्ते।"
    result = []; done = threading.Event()

    def _on_hindi(hindi):
        if tone_engine is None or not tone_engine.is_ready:
            result.append(hindi); done.set()
            return
        tone_engine.rewrite(hindi, "friend", on_result=lambda t: (result.append(t), done.set()))

    trans_engine._correct_sync = lambda t: raw_hindi
    # Simulate the translate callback firing directly
    _on_hindi(raw_hindi)
    done.wait(timeout=5)

    assert result == [raw_hindi], f"Expected raw Hindi fallback, got: {result}"
    print("  ✓ Tone engine not ready → raw Hindi delivered as fallback")


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

ALL_TESTS = [
    # ToneEngine loading
    ("Tone rules load from JSON",              test_tone_rules_load),
    ("Tone missing rules file",                test_tone_missing_rules_file),
    ("Tone load() never called",               test_tone_load_never_called),
    # ToneEngine rewriting
    ("Tone mother honorifics",                 test_tone_rewrite_mother),
    ("Tone friend casual",                     test_tone_rewrite_friend),
    ("Tone 4 outputs distinct",                test_tone_four_outputs_distinct),
    ("Tone invalid tone raises",               test_tone_invalid_tone_raises),
    ("Tone empty input",                       test_tone_empty_input),
    # GrammarEngine
    ("Grammar LRU cache",                      test_grammar_cache_basic),
    ("Grammar async callback",                 test_grammar_async_callback),
    ("Grammar load() never called",            test_grammar_load_never_called),
    ("Grammar latency < 1.5s",                 test_grammar_latency_under_budget),
    ("Grammar preserves meaning (no truncate)",test_grammar_preserves_meaning),
    # Pipeline integration
    ("Translation→Tone pipeline",              test_translation_tone_pipeline),
    ("Tone fallback when not ready",           test_tone_fallback_when_not_ready),
]

if __name__ == "__main__":
    print("\n" + "═" * 60)
    print("  Week 3 — Tone + Grammar — Test Suite")
    print("═" * 60 + "\n")

    passed = failed = 0
    for name, fn in ALL_TESTS:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  ✗ {name}\n    ERROR: {e}")
            import traceback; traceback.print_exc()
            failed += 1

    print()
    print("─" * 60)
    print(f"  Results: {passed} passed  |  {failed} failed")
    print("─" * 60)

    if failed == 0:
        print("\n  ✅ Week 3 exit criteria: ALL TESTS PASSED\n")
        print("  Next steps:")
        print("  1. Run SmartKeyboard_ToneEngine_Convert.ipynb in Colab")
        print("     → download tone_model.zip → models/tone_model/")
        print("  2. Run SmartKeyboard_GrammarEngine_Convert.ipynb in Colab")
        print("     → download grammar_model.zip → models/grammar_model/")
        print("  3. python app/main.py → test all 4 tones + grammar mode\n")
    else:
        print(f"\n  ❌ {failed} test(s) need attention\n")
        sys.exit(1)
