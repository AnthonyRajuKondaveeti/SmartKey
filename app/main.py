"""
main.py
-------
Entry point for Smart Desktop Keyboard.

Wires together:
  - PyQt5 QApplication (UI thread)
  - System tray icon   (daemon thread via pystray)
  - Global hotkey      (daemon thread via pynput)
  - Popup window       (shown on UI thread via Qt signal)
  - Clipboard capture  (called when hotkey fires)
  - Auto-paste         (called when user clicks Paste)

Run with:
    python main.py
"""

import sys
import threading

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore    import QObject, pyqtSignal, QTimer

from popup             import SmartKeyboardPopup
from hotkey_listener   import start_hotkey_listener
from clipboard_manager import get_selected_text, paste_text
from tray              import TrayManager


# ── Signal bridge ─────────────────────────────────────────────────────────────
# pynput fires on a background thread; Qt requires UI work on the main thread.
# We use a QObject signal to safely cross that boundary.

class _Bridge(QObject):
    hotkey_fired = pyqtSignal(str)   # carries the captured selected text


# ── Main controller ───────────────────────────────────────────────────────────

class SmartKeyboardApp:

    HOTKEY_LABEL = "Ctrl+Shift+T"

    def __init__(self):
        self._app     = QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)   # Keep running when popup closes

        self._bridge  = _Bridge()
        self._popup   = None
        self._enabled = True
        self._listener = None

        self._bridge.hotkey_fired.connect(self._show_popup)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def run(self):
        # Start tray icon
        self._tray = TrayManager(
            on_toggle  = self._on_tray_toggle,
            on_quit    = self._quit,
            hotkey_str = self.HOTKEY_LABEL,
        )
        self._tray.start()

        # Start hotkey listener
        self._listener = start_hotkey_listener(self._on_hotkey)

        print(f"[Smart Keyboard] Running — hotkey: {self.HOTKEY_LABEL}")
        print("[Smart Keyboard] Look for the tray icon to enable/disable or quit.")

        sys.exit(self._app.exec_())

    def _quit(self):
        if self._listener:
            self._listener.stop()
        self._app.quit()

    # ── Hotkey handler (background thread) ────────────────────────────────────

    def _on_hotkey(self):
        if not self._enabled:
            return

        print("[Smart Keyboard] Hotkey triggered")

        # Capture selected text — must happen immediately on the hotkey thread
        # before focus shifts away
        selected = get_selected_text()
        if not selected:
            print("[Smart Keyboard] No selected text detected")

        # Cross to the Qt main thread via signal
        self._bridge.hotkey_fired.emit(selected)

    # ── Popup (main / Qt thread) ───────────────────────────────────────────────

    def _show_popup(self, selected_text: str):
        # If popup is already open, just update the text
        if self._popup and self._popup.isVisible():
            self._popup.set_selected_text(selected_text)
            self._popup.raise_()
            return

        self._popup = SmartKeyboardPopup(
            selected_text = selected_text,
            on_paste      = self._on_paste,
            on_close      = None,
        )
        self._popup.show_near_cursor()

    # ── Paste handler (main thread, but popup is already hidden) ──────────────

    def _on_paste(self, result_text: str):
        """
        Called after the user clicks Paste.
        The popup hides itself first (150ms delay) then calls this.
        We do the actual paste here so the active app has focus back.
        """
        try:
            paste_text(result_text)
        except Exception as e:
            print(f"[Smart Keyboard] Paste failed: {e}")
            print("[Smart Keyboard] Text copied to clipboard — paste manually with Ctrl+V.")

    # ── Tray callbacks ────────────────────────────────────────────────────────

    def _on_tray_toggle(self, enabled: bool):
        self._enabled = enabled
        state = "enabled" if enabled else "disabled"
        print(f"[Smart Keyboard] {state.capitalize()}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    SmartKeyboardApp().run()
