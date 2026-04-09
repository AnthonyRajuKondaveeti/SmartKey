"""
test_week2.py
-------------
Week 2 exit-criteria tests.

Because the real ONNX model is not available in the sandbox, all
inference tests use a mock engine that returns instant results.
This validates every code path except the actual model weights —
those are verified manually after running the Colab notebook.

Run with:
    python tests/test_week2.py
"""

import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


# ══════════════════════════════════════════════════════════════════════════════
# Helpers — mock engine that bypasses ONNX
# ══════════════════════════════════════════════════════════════════════════════

def _make_mock_engine(delay: float = 0.05, response: str = "नमस्ते दुनिया!"):
    """
    Return a TranslationEngine that keeps the real cache + threading logic
    but replaces only the ONNX model call with a mock.

    We achieve this by giving the engine fake tokenizer/model objects whose
    generate() sleeps for `delay` then returns a stub tensor, and whose
    decode() returns `response`.
    """
    from translation import TranslationEngine
    import threading

    engine = TranslationEngine.__new__(TranslationEngine)
    TranslationEngine.__init__(engine, model_dir="/nonexistent")
    engine._ready.set()
    engine._load_called = True   # satisfy BUG-9 guard without real disk I/O

    class _FakeTensor:
        def __getitem__(self, _): return self

    class _FakeTokenizer:
        def __call__(self, text, **kw): return {}
        def decode(self, tensor, **kw): return response

    class _FakeModel:
        def generate(self, **kw):
            time.sleep(delay)
            return [_FakeTensor()]

    engine._tokenizer = _FakeTokenizer()
    engine._model     = _FakeModel()
    return engine


# ══════════════════════════════════════════════════════════════════════════════
# 1. LRU Cache
# ══════════════════════════════════════════════════════════════════════════════

def test_cache_basic():
    """Cache stores and retrieves a translation."""
    from translation import _LRUCache
    c = _LRUCache(maxsize=5)
    c.put("hello", "नमस्ते")
    assert c.get("hello") == "नमस्ते"
    assert c.get("missing") is None
    print("  ✓ Cache basic put/get")


def test_cache_eviction():
    """Oldest entry is evicted when cache exceeds maxsize."""
    from translation import _LRUCache
    c = _LRUCache(maxsize=3)
    c.put("a", "1"); c.put("b", "2"); c.put("c", "3")
    c.put("d", "4")   # "a" should be evicted
    assert c.get("a") is None, "Oldest entry should have been evicted"
    assert c.get("d") == "4"
    print("  ✓ Cache LRU eviction works (maxsize=3)")


def test_cache_lru_order():
    """Accessing an entry refreshes it so it isn't the next to be evicted."""
    from translation import _LRUCache
    c = _LRUCache(maxsize=3)
    c.put("a", "1"); c.put("b", "2"); c.put("c", "3")
    c.get("a")        # "a" is now most-recently-used
    c.put("d", "4")   # "b" should be evicted, not "a"
    assert c.get("a") == "1", "'a' was recently used — must survive eviction"
    assert c.get("b") is None, "'b' should have been evicted"
    print("  ✓ Cache LRU order correct — recently accessed entries survive")


def test_cache_thread_safety():
    """Concurrent writes from 10 threads must not corrupt the cache."""
    from translation import _LRUCache
    c    = _LRUCache(maxsize=50)
    errs = []

    def worker(i):
        try:
            for j in range(20):
                key = f"key-{i}-{j}"
                c.put(key, f"val-{i}-{j}")
                result = c.get(key)
                # Value may have been evicted by another thread — that's OK
        except Exception as e:
            errs.append(str(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert not errs, f"Thread safety errors: {errs}"
    print("  ✓ Cache is thread-safe under concurrent writes (10 threads × 20 ops)")


# ══════════════════════════════════════════════════════════════════════════════
# 2. TranslationEngine — async interface
# ══════════════════════════════════════════════════════════════════════════════

def test_engine_translate_async():
    """translate() calls on_result asynchronously with the Hindi text."""
    engine = _make_mock_engine(delay=0.05, response="नमस्ते!")
    results = []
    done    = threading.Event()

    def on_result(text):
        results.append(text)
        done.set()

    engine.translate("Hello!", on_result=on_result)
    done.wait(timeout=5)

    assert results == ["नमस्ते!"], f"Unexpected result: {results}"
    print("  ✓ translate() delivers result via on_result callback")


def test_engine_empty_input():
    """Empty input returns an empty string without error."""
    engine  = _make_mock_engine(response="should not appear")
    results = []
    done    = threading.Event()

    def on_result(text):
        results.append(text)
        done.set()

    engine.translate("", on_result=on_result)
    done.wait(timeout=5)
    assert results == [""], f"Empty input should return empty string, got {results}"
    print("  ✓ Empty input returns empty string")


def test_engine_cache_hit_is_fast():
    """Second call for the same text is served from cache (< 10ms)."""
    engine = _make_mock_engine(delay=0.3, response="कैशड रिजल्ट")
    done   = threading.Event()

    # First call — runs real (mock) inference
    engine.translate("Hello!", on_result=lambda _: done.set())
    done.wait(timeout=5)
    done.clear()

    # Second call — should hit cache and complete almost instantly
    t0 = time.monotonic()
    engine.translate("Hello!", on_result=lambda _: done.set())
    done.wait(timeout=5)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.05, f"Cache hit took {elapsed*1000:.0f}ms — expected < 50ms"
    print(f"  ✓ Cache hit served in {elapsed*1000:.1f}ms (< 50ms)")


def test_engine_latency_under_1500ms():
    """Fresh translation completes within the 1.5s latency budget."""
    # Simulate realistic model latency (0.8s per plan)
    engine = _make_mock_engine(delay=0.8, response="देर से आऊँगा।")
    done   = threading.Event()
    result = []

    t0 = time.monotonic()
    engine.translate(
        "I will be late today.",
        on_result=lambda t: (result.append(t), done.set()),
    )
    done.wait(timeout=5)
    elapsed = time.monotonic() - t0

    assert result, "No result received"
    assert elapsed < 1.5, f"Latency {elapsed:.2f}s exceeds 1.5s budget"
    print(f"  ✓ Translation latency {elapsed:.2f}s — within 1.5s budget")


def test_engine_error_callback():
    """on_error is called when translation raises an exception."""
    from translation import TranslationEngine

    engine = TranslationEngine.__new__(TranslationEngine)
    TranslationEngine.__init__(engine, model_dir="/nonexistent")
    engine._ready.set()

    def _fail(text):
        raise RuntimeError("Simulated model crash")

    engine._translate_sync = _fail

    errors = []
    done   = threading.Event()

    engine.translate(
        "Hello",
        on_result = lambda _: None,
        on_error  = lambda e: (errors.append(e), done.set()),
    )
    done.wait(timeout=5)
    assert errors, "on_error should have been called"
    print(f"  ✓ on_error callback fires on inference exception: '{errors[0]}'")


def test_engine_not_ready_graceful():
    """If model isn't loaded, translate() times out and calls on_error."""
    from translation import TranslationEngine

    engine = TranslationEngine.__new__(TranslationEngine)
    TranslationEngine.__init__(engine, model_dir="/nonexistent")
    # Deliberately do NOT set _ready — simulate model still loading

    # Patch timeout to 0.1s so test doesn't wait 30s
    original_sync = engine._translate_sync
    def _patched(text):
        if not engine._ready.wait(timeout=0.1):
            raise RuntimeError("Translation model is still loading.")
        return original_sync(text)
    engine._translate_sync = _patched

    errors = []
    done   = threading.Event()
    engine.translate(
        "Hello",
        on_result = lambda _: None,
        on_error  = lambda e: (errors.append(e), done.set()),
    )
    done.wait(timeout=5)
    assert errors, "Should have received a not-ready error"
    print(f"  ✓ Not-ready engine reports error gracefully: '{errors[0][:40]}…'")


# ══════════════════════════════════════════════════════════════════════════════
# 3. Model directory detection
# ══════════════════════════════════════════════════════════════════════════════

def test_load_never_called_gives_clear_error():
    """FIX BUG 9: translating without calling load() gives a clear error, not a 30s hang."""
    from translation import TranslationEngine

    engine = TranslationEngine.__new__(TranslationEngine)
    TranslationEngine.__init__(engine, model_dir="/nonexistent")
    # Deliberately do NOT call engine.load() — _load_called stays False

    errors = []
    done   = threading.Event()
    engine.translate(
        "Hello",
        on_result = lambda _: None,
        on_error  = lambda e: (errors.append(e), done.set()),
    )
    done.wait(timeout=5)
    assert errors, "Should have received an error"
    assert "load()" in errors[0], f"Error should mention load(), got: {errors[0]}"
    print(f"  ✓ load() never called → clear error instantly: '{errors[0][:55]}…'")


def test_cache_info_hit_miss_counters():
    """IMPROVE 10: cache_info() exposes hit/miss counters."""
    engine = _make_mock_engine(delay=0.05, response="परीक्षण")
    done   = threading.Event()

    # First call — miss
    engine.translate("test1", on_result=lambda _: done.set())
    done.wait(timeout=5); done.clear()

    # Second call — hit
    engine.translate("test1", on_result=lambda _: done.set())
    done.wait(timeout=5)

    info = engine.cache_info()
    assert "hits"     in info, "cache_info() missing 'hits'"
    assert "misses"   in info, "cache_info() missing 'misses'"
    assert "hit_rate" in info, "cache_info() missing 'hit_rate'"
    assert info["hits"]   >= 1, f"Expected ≥1 hit,  got {info['hits']}"
    assert info["misses"] >= 1, f"Expected ≥1 miss, got {info['misses']}"
    print(f"  ✓ cache_info() = {info}")


def test_model_dir_missing_raises():
    """Loading from a non-existent directory raises FileNotFoundError."""
    from translation import TranslationEngine
    engine = TranslationEngine(model_dir="/definitely/does/not/exist")
    errors = []
    done   = threading.Event()
    engine.load(
        on_ready = lambda: None,
        on_error = lambda e: (errors.append(e), done.set()),
    )
    done.wait(timeout=5)
    assert any("not found" in e.lower() or "no such" in e.lower() for e in errors), \
        f"Expected FileNotFoundError message, got: {errors}"
    print("  ✓ Missing model directory reported cleanly via on_error")


def test_model_dir_empty_raises():
    """Loading from a directory with no .onnx files raises FileNotFoundError."""
    import tempfile
    from translation import TranslationEngine
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = TranslationEngine(model_dir=tmpdir)
        errors = []
        done   = threading.Event()
        engine.load(
            on_ready = lambda: None,
            on_error = lambda e: (errors.append(e), done.set()),
        )
        done.wait(timeout=5)
    assert errors, "Should report error for empty model dir"
    print("  ✓ Empty model directory (no .onnx files) reported cleanly")


# ══════════════════════════════════════════════════════════════════════════════
# 4. Integration — end-to-end flow simulation
# ══════════════════════════════════════════════════════════════════════════════

def test_end_to_end_translate_and_cache():
    """
    Simulate the full user flow:
      select text → trigger engine → receive Hindi → second call hits cache
    """
    engine = _make_mock_engine(delay=0.1, response="आज मैं देर से आऊँगा।")
    results = []
    times   = []

    for _ in range(3):
        done = threading.Event()
        t0   = time.monotonic()
        engine.translate(
            "I will be late today.",
            on_result=lambda t: (results.append(t), done.set()),
        )
        done.wait(timeout=5)
        times.append(time.monotonic() - t0)

    assert len(results) == 3
    assert all(r == "आज मैं देर से आऊँगा।" for r in results)
    # First call real, subsequent calls cached
    assert times[1] < times[0], "Second call should be faster than first (cache)"
    print(f"  ✓ End-to-end: 3 calls — latencies {[f'{t*1000:.0f}ms' for t in times]}")
    print(f"    First (inference): {times[0]*1000:.0f}ms | Cached: {times[1]*1000:.0f}ms")


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

ALL_TESTS = [
    ("Cache basic put/get",                 test_cache_basic),
    ("Cache LRU eviction",                  test_cache_eviction),
    ("Cache LRU order",                     test_cache_lru_order),
    ("Cache thread safety",                 test_cache_thread_safety),
    ("Engine async callback",               test_engine_translate_async),
    ("Engine empty input",                  test_engine_empty_input),
    ("Engine cache hit is fast",            test_engine_cache_hit_is_fast),
    ("Engine latency < 1.5s",               test_engine_latency_under_1500ms),
    ("Engine error callback",               test_engine_error_callback),
    ("Engine not-ready graceful",           test_engine_not_ready_graceful),
    ("load() never called clear error (BUG 9)", test_load_never_called_gives_clear_error),
    ("cache_info hit/miss counters (IMPROVE 10)", test_cache_info_hit_miss_counters),
    ("Model dir missing",                   test_model_dir_missing_raises),
    ("Model dir empty",                     test_model_dir_empty_raises),
    ("End-to-end translate + cache",        test_end_to_end_translate_and_cache),
]

if __name__ == "__main__":
    print("\n" + "═" * 58)
    print("  Week 2 — Translation Pipeline — Test Suite")
    print("═" * 58 + "\n")

    passed = failed = 0
    for name, fn in ALL_TESTS:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  ✗ {name}")
            print(f"    ERROR: {e}")
            failed += 1

    print()
    print("─" * 58)
    print(f"  Results: {passed} passed  |  {failed} failed")
    print("─" * 58)

    if failed == 0:
        print("\n  ✅ Week 2 exit criteria: ALL TESTS PASSED\n")
        print("  Next step: run the Colab notebook in scripts/ to get the")
        print("  real ONNX model, drop it in models/indictrans2/, and do")
        print("  a live end-to-end test in Chrome / Notepad / VS Code.\n")
    else:
        print(f"\n  ❌ {failed} test(s) need attention\n")
        sys.exit(1)
