"""
hotkey_listener.py
------------------
Registers a system-wide hotkey using pynput.

The hotkey string is read from settings.json (default: "ctrl+alt+k").
Format: modifier keys joined by "+" then the trigger key.
  Examples: "ctrl+alt+k", "ctrl+alt+k", "ctrl+shift+h"

Uses a raw Listener with explicit modifier tracking to avoid the Windows
bug where missed modifier-release events cause the hotkey to fire on a
bare keypress.
"""

import queue
import threading
from pynput import keyboard
from typing import Callable
from logger import log


def _parse_hotkey(hotkey_str: str) -> tuple:
    """
    Parse "ctrl+alt+k" → ({"ctrl", "shift"}, "t").
    Modifier names: ctrl, shift, alt.
    """
    _MODIFIERS = {"ctrl", "shift", "alt"}
    parts      = [p.strip().lower() for p in hotkey_str.split("+")]
    modifiers  = {p for p in parts if p in _MODIFIERS}
    triggers   = [p for p in parts if p not in _MODIFIERS]
    trigger    = triggers[0] if triggers else "t"
    return modifiers, trigger


def start_hotkey_listener(
    on_trigger:  Callable[[], None],
    hotkey_str:  str = "ctrl+alt+k",
) -> keyboard.Listener:
    """
    Start a background hotkey listener.

    Args:
        on_trigger:  Callback with no arguments called when the hotkey fires.
        hotkey_str:  Hotkey string, e.g. "ctrl+alt+k" or "ctrl+alt+k".

    Returns:
        The running pynput Listener. Call .stop() to end it.
    """
    required_mods, trigger_char = _parse_hotkey(hotkey_str)

    # Ctrl+<key> sends a control character instead of the letter.
    # Compute it so we match both the plain char and the Ctrl-char.
    ctrl_char = None
    if len(trigger_char) == 1 and "a" <= trigger_char <= "z":
        ctrl_char = chr(ord(trigger_char) - ord("a") + 1)

    log.info(
        f"Hotkey listener arming: {hotkey_str!r} "
        f"| mods={required_mods} trigger={trigger_char!r} ctrl_char={ctrl_char!r}"
    )

    # Windows VK code for the trigger key — used when key.char is None
    # (e.g. Ctrl+Alt+K suppresses char generation on Windows).
    # VK codes for A-Z and 0-9 match their ASCII upper-case values exactly.
    _trigger_vk = ord(trigger_char.upper())

    pressed_modifiers: set = set()
    fired = False

    # Single persistent worker thread — no new thread is spawned per keypress.
    # Queue is bounded to 1 so rapid re-presses don't queue up stale events.
    # Sentinel None tells the worker to exit (sent when listener is stopped).
    _trigger_queue: queue.Queue = queue.Queue(maxsize=1)

    def _worker():
        while True:
            item = _trigger_queue.get()
            if item is None:   # shutdown signal
                break
            log.info("Hotkey fired — dispatching on worker thread")
            try:
                on_trigger()
            except Exception as exc:
                log.exception(f"Hotkey callback error: {exc}")

    threading.Thread(target=_worker, daemon=True, name="HotkeyWorker").start()

    def _on_press(key):
        nonlocal fired

        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            pressed_modifiers.add("ctrl")
        elif key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
            pressed_modifiers.add("shift")
        elif key in (keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt):
            pressed_modifiers.add("alt")
        elif _is_trigger(key):
            log.debug(f"Trigger key pressed | held={pressed_modifiers} | fired={fired}")
            if required_mods <= pressed_modifiers and not fired:
                fired = True
                log.debug(f"Hotkey combo detected ({hotkey_str}) — queuing trigger")
                try:
                    _trigger_queue.put_nowait(1)
                except queue.Full:
                    log.debug("Trigger queue full — rapid re-press ignored")

    def _on_release(key):
        nonlocal fired

        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            pressed_modifiers.discard("ctrl")
        elif key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
            pressed_modifiers.discard("shift")
        elif key in (keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt):
            pressed_modifiers.discard("alt")

        if _is_trigger(key):
            fired = False

    def _is_trigger(key) -> bool:
        # Primary: match by character (plain press or ctrl-char variant)
        if hasattr(key, "char") and key.char is not None:
            c = key.char.lower()
            if c == trigger_char or (ctrl_char is not None and key.char == ctrl_char):
                return True
        # Fallback: match by virtual key code — necessary when Ctrl+Alt suppresses
        # char generation (common on Windows for Ctrl+Alt+<letter> combos).
        if hasattr(key, "vk") and key.vk is not None:
            return key.vk == _trigger_vk
        return False

    class _Listener:
        """Thin wrapper that shuts down the worker thread on stop()."""
        def __init__(self, inner, q):
            self._inner = inner
            self._q     = q
        def stop(self):
            # Drain any pending trigger so the sentinel always lands.
            # If the queue is full (a trigger is waiting), discard it —
            # the hotkey is being torn down so that trigger should not fire.
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            self._q.put(None)   # blocking put; worker will exit after current task
            self._inner.stop()

    inner = keyboard.Listener(on_press=_on_press, on_release=_on_release)
    inner.daemon = True
    inner.start()
    return _Listener(inner, _trigger_queue)
