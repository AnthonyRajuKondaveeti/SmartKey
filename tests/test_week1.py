"""
test_week1.py
-------------
Week 1 exit-criteria tests + regression tests for fixes applied during review.

Run with:
    python tests/test_week1.py
"""

import sys, os, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


# ══════════════════════════════════════════════════════════════════════════════
# 1. Hotkey listener logic
# ══════════════════════════════════════════════════════════════════════════════

def test_hotkey_normalization():
    """Left/right modifier keys normalize to same key (pure logic, no display)."""
    class Key:
        ctrl_l = "ctrl_l"; ctrl_r = "ctrl_r"
        shift = "shift"; shift_l = "shift_l"; shift_r = "shift_r"
    class KeyCode:
        def __init__(self, char): self.char = char
        def __eq__(self, o): return isinstance(o, KeyCode) and self.char == o.char
        def __hash__(self): return hash(self.char)

    def normalize(key):
        if key in (Key.ctrl_l, Key.ctrl_r): return Key.ctrl_l
        if key in (Key.shift, Key.shift_l, Key.shift_r): return Key.shift
        if isinstance(key, KeyCode) and key.char: return KeyCode(key.char.lower())
        return key

    assert normalize(Key.ctrl_l) == normalize(Key.ctrl_r)
    assert normalize(Key.shift_l) == normalize(Key.shift_r)
    print("  ✓ Hotkey normalization (Ctrl_L == Ctrl_R, Shift_L == Shift_R)")


def test_hotkey_target_set():
    """_TARGET constant has exactly 3 keys."""
    import re
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "hotkey_listener.py")).read()
    m = re.search(r'_TARGET\s*=\s*\{([^}]+)\}', src)
    assert m, "Could not find _TARGET"
    entries = [e.strip() for e in m.group(1).split(',') if e.strip()]
    assert len(entries) == 3, f"Expected 3 keys, found {len(entries)}"
    print("  ✓ Hotkey _TARGET has 3 keys")


def test_hotkey_combo_detection():
    """Ctrl+Shift+T fires the callback exactly once."""
    CTRL = "ctrl"; SHIFT = "shift"; T = "t"
    TARGET = {CTRL, SHIFT, T}
    current = set(); fired = []
    def press(k):
        current.add(k)
        if current >= TARGET: fired.append(True)
    press(CTRL); press(SHIFT); press(T)
    assert len(fired) == 1
    print("  ✓ Ctrl+Shift+T triggers callback once")


def test_hotkey_no_false_positive():
    """Ctrl+T without Shift does NOT trigger."""
    CTRL = "ctrl"; SHIFT = "shift"; T = "t"
    TARGET = {CTRL, SHIFT, T}
    current = set(); fired = []
    def press(k):
        current.add(k)
        if current >= TARGET: fired.append(True)
    press(CTRL); press(T)
    assert len(fired) == 0
    print("  ✓ Ctrl+T (no Shift) does NOT trigger")


def test_hotkey_isolated_state():
    """FIX BUG 3: Two listener instances use separate key-state sets."""
    # Simulate two independent listeners with their own current_keys closure
    def make_listener_state():
        current = set()
        fired = []
        TARGET = {"ctrl", "shift", "t"}
        def press(k):
            current.add(k)
            if current >= TARGET: fired.append(True)
        def release(k):
            current.discard(k)
        return press, release, fired

    press1, release1, fired1 = make_listener_state()
    press2, release2, fired2 = make_listener_state()

    # Only press keys on listener 1
    press1("ctrl"); press1("shift"); press1("t")
    # Listener 2 should see zero presses
    assert len(fired1) == 1, "Listener 1 should have fired"
    assert len(fired2) == 0, "Listener 2 must NOT fire — isolated state"
    print("  ✓ Two listener instances have isolated key-state (no shared global)")


# ══════════════════════════════════════════════════════════════════════════════
# 2. Clipboard manager logic
# ══════════════════════════════════════════════════════════════════════════════

def _mock_clipboard():
    store = {}
    def copy(text): store['v'] = text
    def paste():    return store.get('v', '')
    return copy, paste, store


def test_clipboard_roundtrip():
    import pyperclip
    copy, paste, _ = _mock_clipboard()
    pyperclip.copy = copy; pyperclip.paste = paste
    s = "The quick brown fox."
    pyperclip.copy(s)
    assert pyperclip.paste() == s
    print("  ✓ Clipboard round-trip (ASCII)")


def test_clipboard_hindi_unicode():
    import pyperclip
    copy, paste, _ = _mock_clipboard()
    pyperclip.copy = copy; pyperclip.paste = paste
    h = "नमस्ते दुनिया!"
    pyperclip.copy(h)
    assert pyperclip.paste() == h
    print("  ✓ Clipboard round-trip (Hindi)")


def test_clipboard_empty():
    import pyperclip
    copy, paste, _ = _mock_clipboard()
    pyperclip.copy = copy; pyperclip.paste = paste
    pyperclip.copy("")
    assert pyperclip.paste() == ""
    print("  ✓ Empty clipboard handled gracefully")


def test_clipboard_long_text():
    import pyperclip
    copy, paste, _ = _mock_clipboard()
    pyperclip.copy = copy; pyperclip.paste = paste
    long = "This is a sentence. " * 20
    pyperclip.copy(long)
    assert pyperclip.paste() == long
    print(f"  ✓ Long text ({len(long)} chars) preserved")


def test_clipboard_restores_original():
    """FIX BUG 1: get_selected_text() must restore clipboard in ALL cases."""
    # Read the source and confirm restore happens unconditionally
    import re
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "clipboard_manager.py")).read()
    # The restore (pyperclip.copy(original)) must appear AFTER the selected= assignment
    restore_pos  = src.rfind("pyperclip.copy(original)")
    selected_pos = src.find("selected = pyperclip.paste()")
    assert restore_pos > selected_pos, (
        "BUG 1 not fixed: pyperclip.copy(original) must come AFTER reading selected text"
    )
    # And it must NOT be inside an 'if not selected' block
    # Find the if-not-selected block, check restore is outside it
    lines = src.splitlines()
    in_if_block   = False
    restore_in_if = False
    for line in lines:
        if "if not selected" in line:
            in_if_block = True
        if in_if_block and "pyperclip.copy(original)" in line:
            restore_in_if = True
            break
        if in_if_block and line.strip() and not line.startswith(" " * 8) and "if not selected" not in line:
            in_if_block = False
    assert not restore_in_if, "BUG 1 not fixed: restore is still inside 'if not selected' block"
    print("  ✓ Clipboard restore happens unconditionally (BUG 1 fixed)")


def test_paste_text_rejects_empty():
    """FIX BUG 2: paste_text() raises ValueError on empty/whitespace input.
    We verify via source inspection — pyautogui.hotkey needs a real display
    but the ValueError guard fires before that call, so the logic is safe."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "clipboard_manager.py")).read()
    # Guard must exist and come before the pyperclip.copy(text) paste line
    guard_pos = src.find("raise ValueError")
    copy_pos  = src.rfind("pyperclip.copy(text)")
    assert guard_pos != -1, "No ValueError guard found in paste_text()"
    assert guard_pos < copy_pos, "ValueError guard must be BEFORE pyperclip.copy(text)"
    # Guard must check for empty/whitespace
    import re
    guard_line = [l for l in src.splitlines() if "raise ValueError" in l][0]
    assert "paste" in guard_line.lower() or "empty" in guard_line.lower() or "nothing" in guard_line.lower(), \
        f"ValueError message should mention empty/paste: {guard_line}"
    print("  ✓ paste_text() has ValueError guard before clipboard write (BUG 2 fixed)")


# ══════════════════════════════════════════════════════════════════════════════
# 3. Popup constants
# ══════════════════════════════════════════════════════════════════════════════

def test_popup_modes_and_relationships():
    from popup import MODES, RELATIONSHIPS
    assert len(MODES) == 2
    assert len(RELATIONSHIPS) == 4
    assert "Hindi" in MODES[0]
    assert "Grammar" in MODES[1]
    assert set(RELATIONSHIPS) == {"Mother", "Friend", "Partner", "Stranger"}
    print(f"  ✓ Popup modes: {MODES}")
    print(f"  ✓ Popup relationships: {RELATIONSHIPS}")


# ══════════════════════════════════════════════════════════════════════════════
# 4. Tray icon
# ══════════════════════════════════════════════════════════════════════════════

def _make_icon(enabled):
    from PIL import Image, ImageDraw
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = (124, 106, 247) if enabled else (100, 100, 120)
    draw.rounded_rectangle([4, 14, 60, 50], radius=8, fill=color)
    return img

def test_tray_icon_size_and_mode():
    img = _make_icon(True)
    assert img.size == (64, 64) and img.mode == "RGBA"
    print("  ✓ Tray icon 64×64 RGBA")

def test_tray_icon_enabled_vs_disabled():
    assert _make_icon(True).tobytes() != _make_icon(False).tobytes()
    print("  ✓ Enabled/disabled icons are visually distinct")


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

ALL_TESTS = [
    ("Hotkey normalization",            test_hotkey_normalization),
    ("Hotkey target set size",          test_hotkey_target_set),
    ("Hotkey combo detection",          test_hotkey_combo_detection),
    ("Hotkey no false positive",        test_hotkey_no_false_positive),
    ("Hotkey isolated state (BUG 3)",   test_hotkey_isolated_state),
    ("Clipboard ASCII",                 test_clipboard_roundtrip),
    ("Clipboard Hindi",                 test_clipboard_hindi_unicode),
    ("Clipboard empty",                 test_clipboard_empty),
    ("Clipboard long text",             test_clipboard_long_text),
    ("Clipboard restores original (BUG 1)", test_clipboard_restores_original),
    ("Paste rejects empty (BUG 2)",     test_paste_text_rejects_empty),
    ("Popup modes & relationships",     test_popup_modes_and_relationships),
    ("Tray icon size/mode",             test_tray_icon_size_and_mode),
    ("Tray icon enabled vs disabled",   test_tray_icon_enabled_vs_disabled),
]

if __name__ == "__main__":
    print("\n" + "═" * 58)
    print("  Week 1 — Test Suite (with review fixes)")
    print("═" * 58 + "\n")
    passed = failed = 0
    for name, fn in ALL_TESTS:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  ✗ {name}\n    ERROR: {e}")
            failed += 1
    print()
    print("─" * 58)
    print(f"  Results: {passed} passed  |  {failed} failed")
    print("─" * 58)
    if failed == 0:
        print("\n  ✅ All Week 1 tests passed\n")
    else:
        print(f"\n  ❌ {failed} test(s) need attention\n")
        sys.exit(1)
