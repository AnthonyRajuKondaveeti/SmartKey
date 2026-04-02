"""
test_clipboard.py
-----------------
Basic tests for clipboard read/write logic.
These run headlessly — no display or active app needed.

Run with:  python3 -m pytest tests/test_clipboard.py -v
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pyperclip


def test_clipboard_write_and_read():
    """Clipboard should store and return text correctly."""
    sample = "Hello, this is a test selection."
    pyperclip.copy(sample)
    result = pyperclip.paste()
    assert result == sample, f"Expected '{sample}', got '{result}'"
    print("  ✓ Clipboard write + read works")


def test_clipboard_empty_string():
    """Writing an empty string should return an empty string."""
    pyperclip.copy("")
    result = pyperclip.paste()
    assert result == "", f"Expected empty string, got '{result}'"
    print("  ✓ Clipboard empty string works")


def test_clipboard_unicode():
    """Clipboard should handle Hindi and multilingual text."""
    hindi = "नमस्ते, यह एक परीक्षण है।"
    pyperclip.copy(hindi)
    result = pyperclip.paste()
    assert result == hindi, f"Unicode mismatch: got '{result}'"
    print("  ✓ Clipboard Unicode (Hindi) works")


def test_clipboard_long_text():
    """Clipboard should handle longer inputs without truncation."""
    long_text = "This is a sentence. " * 50  # ~1000 chars
    pyperclip.copy(long_text)
    result = pyperclip.paste()
    assert result == long_text
    print("  ✓ Clipboard long text (1000 chars) works")


if __name__ == "__main__":
    print("\nRunning clipboard tests...\n")
    test_clipboard_write_and_read()
    test_clipboard_empty_string()
    test_clipboard_unicode()
    test_clipboard_long_text()
    print("\nAll clipboard tests passed ✓\n")
