"""
hotkey_listener.py
------------------
Registers a system-wide hotkey (Ctrl+Shift+T) using pynput.
When triggered, captures selected text and signals the popup to open.

Runs on a background daemon thread — never blocks the UI.

Uses a manual Listener instead of GlobalHotKeys to avoid a known
Windows bug where missed modifier-release events cause the hotkey
to fire on a bare 't' keypress.
"""

import threading
from pynput import keyboard
from typing import Callable
from logger import log


HOTKEY_SEQUENCE = "<ctrl>+<shift>+t"


def start_hotkey_listener(on_trigger: Callable[[], None]) -> keyboard.Listener:
    """
    Start a background hotkey listener for Ctrl+Shift+T.

    Uses a raw Listener with explicit modifier tracking. After each
    activation the state is fully cleared, preventing the stale-modifier
    bug present in pynput's GlobalHotKeys on Windows.

    Args:
        on_trigger: Callback with no arguments, called when the hotkey fires.

    Returns:
        The running pynput Listener. Call .stop() to end it.
    """
    pressed_modifiers = set()
    fired = False

    def _safe_trigger():
        log.info("Hotkey fired — dispatching trigger on worker thread")
        try:
            on_trigger()
        except Exception as exc:
            log.exception(f"Hotkey callback error: {exc}")

    def _on_press(key):
        nonlocal fired

        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            pressed_modifiers.add("ctrl")
            log.debug(f"Modifier pressed: ctrl  | held={pressed_modifiers}")
        elif key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
            pressed_modifiers.add("shift")
            log.debug(f"Modifier pressed: shift | held={pressed_modifiers}")
        elif _is_t(key):
            log.debug(f"'T' pressed | held={pressed_modifiers} | fired={fired}")
            if "ctrl" in pressed_modifiers and "shift" in pressed_modifiers and not fired:
                fired = True
                log.debug("Ctrl+Shift+T combo detected — spawning trigger thread")
                t = threading.Thread(target=_safe_trigger, daemon=True)
                t.start()
            else:
                log.debug("'T' pressed but combo incomplete or already fired — ignored")

    def _on_release(key):
        nonlocal fired

        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            pressed_modifiers.discard("ctrl")
            log.debug(f"Modifier released: ctrl  | held={pressed_modifiers}")
        elif key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
            pressed_modifiers.discard("shift")
            log.debug(f"Modifier released: shift | held={pressed_modifiers}")

        if _is_t(key) or key in (
            keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
            keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r,
        ):
            if fired:
                log.debug("Fired flag reset on key release")
            fired = False

    def _is_t(key):
        # char-only matching avoids vk false-triggers.
        # When Ctrl is held, pynput sets key.char to the control character
        # \x14 (ASCII 20 = Ctrl+T) instead of "t" — match both.
        if not (hasattr(key, "char") and key.char is not None):
            return False
        return key.char.lower() == "t" or key.char == "\x14"

    listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
    listener.daemon = True
    listener.start()
    log.info(f"Hotkey listener armed: {HOTKEY_SEQUENCE}")
    return listener
