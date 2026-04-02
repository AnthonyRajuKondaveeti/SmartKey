"""
clipboard_manager.py
--------------------
Handles reading the currently selected text and writing processed
text back to the clipboard, then auto-pasting into the active app.
"""

import time
import pyperclip
import pyautogui


def get_selected_text() -> str:
    """
    Capture the currently selected text in any application.

    Strategy:
      1. Save whatever is already in the clipboard.
      2. Simulate Ctrl+C to copy the selection into the clipboard.
      3. Read the new clipboard value.
      4. Restore the original clipboard content so we don't corrupt it.

    Returns the selected text, or an empty string if nothing was selected.
    """
    # Save current clipboard so we can restore it after failed capture attempts.
    original = ""
    try:
        original = pyperclip.paste()
    except Exception:
        pass

    # Use a sentinel so we can detect clipboard changes caused by Ctrl+C.
    sentinel = f"__SMART_KEYBOARD_SENTINEL__{time.time_ns()}"

    # Hotkey callback can run while Ctrl/Shift are still held.
    # Release them so the synthetic Ctrl+C is interpreted correctly.
    pyautogui.keyUp("shift")
    pyautogui.keyUp("ctrl")
    time.sleep(0.02)

    for _ in range(2):
        pyperclip.copy(sentinel)
        time.sleep(0.03)

        # Simulate Ctrl+C to copy current selection from the active app.
        pyautogui.hotkey("ctrl", "c")

        # Poll briefly for clipboard update (some apps are slower).
        for _ in range(10):
            time.sleep(0.03)
            current = pyperclip.paste()
            if current != sentinel:
                return current or ""

    # Nothing copied: restore previous clipboard and return empty.
    pyperclip.copy(original)
    return ""


def paste_text(text: str) -> None:
    """
    Write processed text to the clipboard and simulate Ctrl+V to
    paste it into whatever app currently has focus.

    This replaces the user's original selection because the cursor
    position hasn't changed since they pressed the hotkey.
    """
    pyperclip.copy(text)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
