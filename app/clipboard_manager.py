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

_user32   = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_VK_CONTROL = 0x11
_VK_SHIFT   = 0x10
_VK_ALT     = 0x12

# ── Timing constants ──────────────────────────────────────────────────────────
_MODIFIER_WAIT_MS        = 400   # max ms to wait for Ctrl/Shift/Alt release
_CLIPBOARD_SETTLE_MS     = 50    # ms to wait after clearing clipboard
_CLIPBOARD_POLL_ATTEMPTS = 15    # poll iterations after Ctrl+C injection
_CLIPBOARD_POLL_MS       = 20    # ms between clipboard poll attempts
_PASTE_SETTLE_MS         = 50    # ms to wait after copying text before injecting Ctrl+V
_FOCUS_CONFIRM_MS        = 20    # ms to wait after AttachThreadInput before confirming focus


def _transfer_focus(target_hwnd: int) -> bool:
    """
    Reliably transfer keyboard focus to target_hwnd using AttachThreadInput.

    Plain SetForegroundWindow fails silently when the calling process does not
    own the foreground lock (FOREGROUNDLOCKTIMEOUT, UIPI, elevated target).
    AttachThreadInput temporarily merges our thread's input queue with the
    target window's thread, making SetForegroundWindow succeed unconditionally.

    The attachment is held only for the duration of the focus transfer and
    released in a finally block — threads are never left permanently attached.

    Returns True if target_hwnd is the foreground window after the call.
    """
    our_tid    = _kernel32.GetCurrentThreadId()
    target_tid = _user32.GetWindowThreadProcessId(target_hwnd, None)

    if our_tid == target_tid:
        # Same thread — AttachThreadInput with equal IDs is a no-op / error.
        # Fall back to bare SetForegroundWindow (this path is rare in practice).
        _user32.SetForegroundWindow(target_hwnd)
        time.sleep(0.020)
        return _user32.GetForegroundWindow() == target_hwnd

    attached = False
    try:
        attached = bool(_user32.AttachThreadInput(our_tid, target_tid, True))
        if attached:
            _user32.SetForegroundWindow(target_hwnd)
            _user32.BringWindowToTop(target_hwnd)
        else:
            # AttachThreadInput failed (e.g. target thread exited between the
            # hwnd validity check and here) — fall back gracefully.
            log.debug("AttachThreadInput failed — falling back to SetForegroundWindow")
            _user32.SetForegroundWindow(target_hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(our_tid, target_tid, False)

    # Brief sleep lets the OS propagate the focus change before we inject Ctrl+V.
    time.sleep(_FOCUS_CONFIRM_MS / 1000)
    return _user32.GetForegroundWindow() == target_hwnd


def _wait_modifiers_released(max_ms: int = 400) -> None:
    """Return as soon as Ctrl/Shift/Alt are physically released, or after max_ms."""
    deadline = time.monotonic() + max_ms / 1000
    while time.monotonic() < deadline:
        held = (
            _user32.GetAsyncKeyState(_VK_CONTROL) & 0x8000
            or _user32.GetAsyncKeyState(_VK_SHIFT) & 0x8000
            or _user32.GetAsyncKeyState(_VK_ALT)   & 0x8000
        )
        if not held:
            return
        time.sleep(0.010)
    log.debug("Modifier wait timed out — keys still held after 400ms")


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
      5. Poll the clipboard (up to 300ms at 20ms intervals) until content appears.
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
    time.sleep(_CLIPBOARD_SETTLE_MS / 1000)

    selected = ""
    try:
        t_mod = time.monotonic()
        _wait_modifiers_released(max_ms=_MODIFIER_WAIT_MS)
        log.debug(f"Modifier wait: {(time.monotonic() - t_mod) * 1000:.0f}ms")

        for _key in ("ctrl", "alt", "shift"):
            pyautogui.keyUp(_key)

        fg = _user32.GetForegroundWindow()
        log.debug(f"Injecting Ctrl+C | foreground hwnd: {fg:#010x}")
        pyautogui.hotkey("ctrl", "c")

        for attempt in range(_CLIPBOARD_POLL_ATTEMPTS):
            time.sleep(_CLIPBOARD_POLL_MS / 1000)
            selected = pyperclip.paste()
            if selected:
                log.debug(f"Clipboard filled on poll attempt {attempt + 1}")
                break
        else:
            log.debug(f"Clipboard still empty after {_CLIPBOARD_POLL_ATTEMPTS} polls — nothing selected")

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
    time.sleep(_PASTE_SETTLE_MS / 1000)

    if target_hwnd:
        if not _is_valid_hwnd(target_hwnd):
            log.warning(f"Target hwnd {target_hwnd:#010x} is no longer valid")
            raise RuntimeError("Target window is no longer open — text is in your clipboard.")
        elif _user32.GetForegroundWindow() != target_hwnd:
            log.debug(f"Transferring focus to hwnd {target_hwnd:#010x} via AttachThreadInput")
            if not _transfer_focus(target_hwnd):
                fg = _user32.GetForegroundWindow()
                log.warning(
                    f"Focus transfer failed | target: {target_hwnd:#010x} "
                    f"| actual foreground: {fg:#010x}"
                )
                raise RuntimeError(
                    "Could not focus target window — text is in your clipboard, press Ctrl+V."
                )
            log.debug("Focus confirmed via AttachThreadInput")
        else:
            log.debug(f"Target hwnd {target_hwnd:#010x} already in foreground")

    log.debug("Injecting Ctrl+V")
    pyautogui.hotkey("ctrl", "v")
    log.info("Paste complete")
