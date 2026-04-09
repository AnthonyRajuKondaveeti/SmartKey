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
  - TranslationEngine  (loads on bg thread at startup — Week 2)

Run with:
    python main.py
"""

import sys
import os
import signal

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore    import QObject, pyqtSignal, QTimer
from logger import log

# Restore default SIGINT (Ctrl+C) behaviour — PyQt5's exec_() runs in C and
# blocks Python's signal handling, so without this Ctrl+C is silently ignored.
signal.signal(signal.SIGINT, signal.SIG_DFL)

from popup             import SmartKeyboardPopup
from hotkey_listener   import start_hotkey_listener
from clipboard_manager import get_selected_text, paste_text, get_foreground_hwnd
from tray              import TrayManager
from translation       import TranslationEngine
from grammar           import GrammarEngine

# Force transformers to fully initialise on the main thread before any
# background loading threads start — avoids AutoTokenizer import race.
import transformers  # noqa: F401


# ── Signal bridge ─────────────────────────────────────────────────────────────
class _Bridge(QObject):
    hotkey_fired = pyqtSignal(str)


# ── Main controller ───────────────────────────────────────────────────────────

class SmartKeyboardApp:

    HOTKEY_LABEL  = "Ctrl+Shift+T"
    MODEL_DIR     = os.path.join(os.path.dirname(__file__), "..", "models", "indictrans2")
    GRAMMAR_DIR   = os.path.join(os.path.dirname(__file__), "..", "models", "grammar")

    def __init__(self):
        self._app      = QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)
        self._bridge       = _Bridge()
        self._popup        = None
        self._enabled      = True
        self._listener     = None
        self._target_hwnd  = 0   # window to restore focus to before paste
        self._bridge.hotkey_fired.connect(self._show_popup)
        self._translation_engine = TranslationEngine(model_dir=self.MODEL_DIR)
        self._grammar_engine     = GrammarEngine(model_dir=self.GRAMMAR_DIR)

    def run(self):
        self._tray = TrayManager(
            on_toggle  = self._on_tray_toggle,
            on_quit    = self._quit,
            hotkey_str = self.HOTKEY_LABEL,
        )
        self._tray.start()
        self._listener = start_hotkey_listener(self._on_hotkey)

        self._grammar_engine.load(
            on_ready = lambda: log.info(
                f"Grammar model ready | active: {self._grammar_engine.active_model}"
            ),
            on_error = lambda e: log.warning(
                f"Grammar model load info: {e}\n"
                "  App will show placeholder output until models/grammar/ is populated."
            ),
        )

        self._translation_engine.load(
            on_ready = lambda: log.info("Translation model ready"),
            on_error = lambda e: log.warning(
                f"Translation model load info: {e}\n"
                "  App will use placeholder output until models/indictrans2/ is populated."
            ),
        )

        print(f"[Smart Keyboard] Running — hotkey: {self.HOTKEY_LABEL}")
        sys.exit(self._app.exec_())

    def _quit(self):
        if self._listener:
            self._listener.stop()
        self._app.quit()

    def _on_hotkey(self):
        if not self._enabled:
            log.debug("Hotkey fired but app is disabled — skipped")
            return
        self._target_hwnd = get_foreground_hwnd()
        log.info(f"Hotkey received | target hwnd: {self._target_hwnd:#010x}")
        try:
            selected = get_selected_text()
        except Exception as e:
            log.exception(f"Selection capture failed: {e}")
            selected = ""
        log.info(f"Emitting hotkey_fired signal | text length: {len(selected)}")
        self._bridge.hotkey_fired.emit(selected)

    def _show_popup(self, selected_text: str):
        if self._popup and self._popup.isVisible():
            log.info("Popup already visible — updating text and raising")
            self._popup.set_selected_text(selected_text)
            self._popup.raise_()
            return
        log.info("Creating new popup")
        self._popup = SmartKeyboardPopup(
            selected_text = selected_text,
            on_paste      = self._on_paste,
        )
        self._popup.set_grammar_engine(self._grammar_engine)
        self._popup.set_translation_engine(self._translation_engine)
        self._popup.show_near_cursor()

    def _on_paste(self, result_text: str):
        log.info(f"Paste callback | target hwnd: {self._target_hwnd:#010x} | text length: {len(result_text)}")
        try:
            paste_text(result_text, target_hwnd=self._target_hwnd)
        except Exception as e:
            log.error(f"Paste failed: {e}")
            print("Text is in your clipboard — paste manually with Ctrl+V.")

    def _on_tray_toggle(self, enabled: bool):
        self._enabled = enabled
        log.info(f"Tray toggle — app {'enabled' if enabled else 'disabled'}")


if __name__ == "__main__":
    SmartKeyboardApp().run()
