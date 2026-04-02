"""
hotkey_listener.py
------------------
Registers a system-wide hotkey (Ctrl+Shift+T) using pynput.
When triggered, it captures the selected text and signals the
popup to open.

Designed to run on a background thread so it never blocks the UI.
"""

from pynput import keyboard
from typing import Callable


HOTKEY_SEQUENCE = "<ctrl>+<shift>+t"


def _normalize(key) -> object:
    """Normalize a key so left/right variants match."""
    # Treat left and right Ctrl/Shift as the same
    if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
        return keyboard.Key.ctrl_l
    if key in (keyboard.Key.shift_l, keyboard.Key.shift_r, keyboard.Key.shift):
        return keyboard.Key.shift
    # Normalize char keys to lowercase
    if hasattr(key, 'char') and key.char:
        return keyboard.KeyCode(char=key.char.lower())
    return key


# The normalized combo we check against
_TARGET = {
    _normalize(keyboard.Key.ctrl_l),
    _normalize(keyboard.Key.shift),
    keyboard.KeyCode(char='t'),
}


def start_hotkey_listener(on_trigger: Callable[[], None]) -> keyboard.GlobalHotKeys:
    """
    Start a background hotkey listener.

    Args:
        on_trigger: Callback with no arguments, called when the hotkey fires.

    Returns:
        The running pynput Listener (daemon thread). Call .stop() to end it.
    """

    def _wrapped_trigger():
        try:
            on_trigger()
        except Exception as exc:
            # Keep the listener alive even if callback logic fails.
            print(f"[Smart Keyboard] Hotkey callback error: {exc}")

    listener = keyboard.GlobalHotKeys({HOTKEY_SEQUENCE: _wrapped_trigger})
    listener.daemon = True
    listener.start()
    return listener
