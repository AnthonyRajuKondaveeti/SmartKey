"""
clipboard_manager.py
--------------------
Handles reading the currently selected text and writing processed
text back to the clipboard, then auto-pasting into the active app.
"""

import time
import ctypes
import ctypes.wintypes
import pyperclip
import pyautogui
from logger import log

_user32 = ctypes.windll.user32


def _is_valid_hwnd(hwnd: int) -> bool:
    """Return True if hwnd is a valid, existing window."""
    return bool(hwnd and _user32.IsWindow(hwnd))


def get_foreground_hwnd() -> int:
    """Return the HWND of the currently active window."""
    hwnd = _user32.GetForegroundWindow()
    log.debug(f"Captured foreground hwnd: {hwnd:#010x}")
    return hwnd


def get_window_rect(hwnd: int):
    """Return (x, y, width, height) of the window in screen coordinates.
    Returns None if the hwnd is invalid or the window is minimised."""
    try:
        rect = ctypes.wintypes.RECT()
        if _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            w = rect.right  - rect.left
            h = rect.bottom - rect.top
            if w > 10 and h > 10:          # skip minimised / zero-size windows
                return rect.left, rect.top, w, h
    except Exception:
        pass
    return None


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
        log.debug(f"Clipboard original saved: {len(original)} chars")
    except Exception:
        log.warning("Could not read original clipboard content")

    pyperclip.copy("")
    time.sleep(0.05)

    selected = ""
    try:
        # Wait for physical modifier keys to release before injecting Ctrl+C
        log.debug("Waiting 250ms for modifier keys to settle ...")
        time.sleep(0.25)

        log.debug("Injecting Ctrl+C")
        pyautogui.hotkey("ctrl", "c")

        # Poll up to 500ms
        for attempt in range(10):
            time.sleep(0.05)
            selected = pyperclip.paste()
            if selected:
                log.debug(f"Clipboard filled on poll attempt {attempt + 1}")
                break
        else:
            log.debug("Clipboard still empty after 10 polls — nothing was selected")

    except Exception as e:
        log.warning(f"Clipboard capture error: {e}")

    finally:
        # Always restore original clipboard — even if Ctrl+C injection threw
        try:
            pyperclip.copy(original)
            log.debug("Original clipboard restored")
        except Exception:
            log.warning("Could not restore original clipboard")

    elapsed_ms = (time.monotonic() - t_start) * 1000
    log.info(
        f"Clipboard capture done in {elapsed_ms:.0f}ms — "
        f"captured {len(selected)} chars"
    )
    return selected


def paste_text(text: str, target_hwnd: int = 0) -> None:
    """
    Write processed text to the clipboard and simulate Ctrl+V to
    inject it into whatever app currently has focus.

    target_hwnd: HWND of the window to focus before pasting.
                 If 0 or invalid, falls back to whatever has focus.

    Raises ValueError on empty input — prevents silently pasting blank
    text over the user's original selection.
    """
    if not text or not text.strip():
        raise ValueError("paste_text() called with empty text — nothing to paste.")

    log.info(f"Paste initiated — {len(text)} chars, target hwnd: {target_hwnd:#010x}")
    log.debug(f"Paste content: {len(text)} chars")

    # Copy to clipboard before the focus check so the text is always available
    # for manual Ctrl+V even if the focus transfer fails below.
    pyperclip.copy(text)
    time.sleep(0.05)

    if target_hwnd:
        if not _is_valid_hwnd(target_hwnd):
            log.warning(f"Target hwnd {target_hwnd:#010x} is no longer valid")
            raise RuntimeError("Target window is no longer open — text is in your clipboard.")
        elif _user32.GetForegroundWindow() != target_hwnd:
            # Only call SetForegroundWindow when actually needed — avoids a
            # brief flicker/deselect if the target is already in the foreground
            # (which it should be when the popup uses WA_ShowWithoutActivating).
            log.debug(f"Restoring focus to hwnd {target_hwnd:#010x}")
            _user32.SetForegroundWindow(target_hwnd)
            for _ in range(6):
                time.sleep(0.05)
                if _user32.GetForegroundWindow() == target_hwnd:
                    log.debug("Focus confirmed")
                    break
            else:
                log.warning(f"Focus did not transfer to {target_hwnd:#010x} after 300ms")
                raise RuntimeError(
                    "Could not focus target window — text is in your clipboard, press Ctrl+V."
                )
        else:
            log.debug(f"Target hwnd {target_hwnd:#010x} already in foreground")

    log.debug("Injecting Ctrl+V")
    pyautogui.hotkey("ctrl", "v")
    log.info("Paste complete")
