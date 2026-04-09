"""
clipboard_manager.py
--------------------
Handles reading the currently selected text and writing processed
text back to the clipboard, then auto-pasting into the active app.
"""

import time
import ctypes
import pyperclip
import pyautogui
from logger import log

_user32 = ctypes.windll.user32


def get_foreground_hwnd() -> int:
    """Return the HWND of the currently active window."""
    hwnd = _user32.GetForegroundWindow()
    log.debug(f"Captured foreground hwnd: {hwnd:#010x}")
    return hwnd


def get_selected_text() -> str:
    """
    Capture the currently selected text in any application.

    Strategy:
      1. Save whatever is already in the clipboard.
      2. Clear clipboard so we can detect a fresh copy.
      3. Wait briefly for modifier keys to be released (the user is
         still holding Ctrl+Shift+T when this fires).
      4. Simulate Ctrl+C to copy the selection into the clipboard.
      5. Poll the clipboard (up to 500ms) until content appears.
      6. Always restore the original clipboard content afterwards.

    Returns the selected text, or an empty string if nothing was selected.
    """
    t_start = time.monotonic()
    log.info("Clipboard capture — starting")

    original = ""
    try:
        original = pyperclip.paste()
        log.debug(f"Clipboard original saved: {repr(original[:80])}{'...' if len(original) > 80 else ''}")
    except Exception:
        log.warning("Could not read original clipboard content")

    pyperclip.copy("")
    time.sleep(0.05)

    # Wait for physical modifier keys to release before injecting Ctrl+C
    log.debug("Waiting 250ms for modifier keys to settle ...")
    time.sleep(0.25)

    log.debug("Injecting Ctrl+C")
    pyautogui.hotkey("ctrl", "c")

    # Poll up to 500ms
    selected = ""
    for attempt in range(10):
        time.sleep(0.05)
        selected = pyperclip.paste()
        if selected:
            log.debug(f"Clipboard filled on poll attempt {attempt + 1}")
            break
    else:
        log.debug("Clipboard still empty after 10 polls — nothing was selected")

    # FIX BUG 1: restore original clipboard unconditionally
    try:
        pyperclip.copy(original)
        log.debug("Original clipboard restored")
    except Exception:
        log.warning("Could not restore original clipboard")

    elapsed_ms = (time.monotonic() - t_start) * 1000
    log.info(
        f"Clipboard capture done in {elapsed_ms:.0f}ms — "
        f"captured {len(selected)} chars: {repr(selected[:80])}{'...' if len(selected) > 80 else ''}"
    )
    return selected


def paste_text(text: str, target_hwnd: int = 0) -> None:
    """
    Write processed text to the clipboard and simulate Ctrl+V to
    inject it into whatever app currently has focus.

    target_hwnd: HWND of the window to focus before pasting (FIX 5).
                 If 0 or invalid, falls back to whatever has focus.

    Raises ValueError on empty input — prevents silently deleting the
    user's original selection with a blank paste (FIX BUG 2).
    """
    if not text or not text.strip():
        raise ValueError("paste_text() called with empty text — nothing to paste.")

    log.info(f"Paste initiated — {len(text)} chars, target hwnd: {target_hwnd:#010x}")
    log.debug(f"Paste content: {repr(text[:120])}{'...' if len(text) > 120 else ''}")

    # FIX 3 & 5: restore focus to the original app before pasting
    if target_hwnd:
        log.debug(f"Restoring focus to hwnd {target_hwnd:#010x}")
        _user32.SetForegroundWindow(target_hwnd)
        time.sleep(0.1)
        log.debug("Focus restored — waiting 100ms for OS to complete switch")

    pyperclip.copy(text)
    time.sleep(0.05)
    log.debug("Injecting Ctrl+V")
    pyautogui.hotkey("ctrl", "v")
    log.info("Paste complete")
