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
import re
import json
import signal
import threading
import ctypes.wintypes
from collections import deque
import time

# Suppress "None of PyTorch / TensorFlow / Flax have been found" from
# transformers — we use ONNX Runtime for inference, not any of these.
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

# In a PyInstaller --onedir bundle sys.frozen is True and sys.executable points
# to the .exe. At dev time __file__ is app/main.py so we go up one level.
def _root_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Hotkey must be: one or more modifiers (ctrl/shift/alt) joined by +, then a letter or digit.
_HOTKEY_RE = re.compile(r'^(ctrl|shift|alt)(\+(ctrl|shift|alt))*\+[a-z0-9]$')

from cache import appdata_dir
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore    import QObject, pyqtSignal, QTimer, Qt, QAbstractNativeEventFilter
from logger import log

# Restore default SIGINT (Ctrl+C) behaviour — PyQt5's exec_() runs in C and
# blocks Python's signal handling, so without this Ctrl+C is silently ignored.
signal.signal(signal.SIGINT, signal.SIG_DFL)

from popup             import SmartKeyboardPopup
from hotkey_listener   import start_hotkey_listener
from clipboard_manager import get_selected_text, paste_text, get_foreground_hwnd, get_window_rect
from tray              import TrayManager
from translation       import TranslationEngine
from grammar           import GrammarEngine

# Pre-import all transformers submodules used by background threads on the main
# thread before any threads start. Without this, Grammar (AutoTokenizer) and
# Tone (AlbertTokenizer) threads deadlock each other via Python's _ModuleLock:
# each holds the lock the other needs to complete its import.
import transformers  # noqa: F401
from transformers import AutoTokenizer, AlbertTokenizer  # noqa: F401

# Pre-import heavy C extensions so their DLL initialisation (which holds the
# GIL) happens here on the main thread, not inside background loading threads.
# Background threads' subsequent `import onnxruntime` calls become instant
# dict-lookups and never block the Qt main thread from dispatching slots.
try:
    import onnxruntime    # noqa: F401
    import sentencepiece  # noqa: F401
except ImportError:
    pass


# ── Sleep / wake recovery ─────────────────────────────────────────────────────

class _PowerFilter(QAbstractNativeEventFilter):
    """
    Listens for WM_POWERBROADCAST / PBT_APMRESUMEAUTOMATIC.
    When Windows resumes from sleep the pynput keyboard hook is sometimes
    silently dropped; calling on_resume() restarts the listener.
    Rate-limited to once per 10 s to ignore duplicate resume events.
    """
    _WM_POWERBROADCAST      = 0x0218
    _PBT_APMRESUMEAUTOMATIC = 0x0012

    def __init__(self, on_resume):
        super().__init__()
        self._on_resume   = on_resume
        self._last_fired  = 0.0

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            try:
                msg = ctypes.wintypes.MSG.from_address(message.__int__())
                if (msg.message == self._WM_POWERBROADCAST
                        and msg.wParam == self._PBT_APMRESUMEAUTOMATIC):
                    now = time.monotonic()
                    if now - self._last_fired > 10.0:
                        self._last_fired = now
                        self._on_resume()
            except Exception:
                pass
        return False, 0


# ── Signal bridge ─────────────────────────────────────────────────────────────
class _Bridge(QObject):
    hotkey_fired   = pyqtSignal(str)
    # Emitted from background threads; connected with Qt.QueuedConnection so
    # the slots always run on the Qt main thread regardless of which thread emits.
    model_ready    = pyqtSignal(bool, str, bool)   # (all_done, status_text, has_failures)
    text_captured  = pyqtSignal(str)         # clipboard result arrives after popup is shown
    show_hk_dialog = pyqtSignal()            # open the Change-Hotkey dialog


# ── Main controller ───────────────────────────────────────────────────────────

_VALID_LANGS = {
    "hin_Deva", "ben_Beng", "mar_Deva", "tel_Telu",
    "tam_Taml", "kan_Knda", "pan_Guru", "mal_Mlym",
}

def _load_settings() -> dict:
    defaults = {"hotkey": "ctrl+alt+k", "default_lang": "hin_Deva", "automate": False, "translate": False}
    path = os.path.join(appdata_dir(), "settings.json")
    if not os.path.isfile(path):
        try:
            with open(path, "w") as f:
                json.dump(defaults, f, indent=2)
        except Exception as e:
            log.warning(f"Could not create settings file: {e}")
        return defaults
    try:
        with open(path) as f:
            data = json.load(f)
        merged = {**defaults, **data}
        hotkey = str(merged.get("hotkey", defaults["hotkey"])).lower().strip()
        if not _HOTKEY_RE.match(hotkey):
            log.warning(f"Invalid hotkey in settings.json ({hotkey!r}) — using default")
            merged["hotkey"] = defaults["hotkey"]
        else:
            merged["hotkey"] = hotkey
        lang = str(merged.get("default_lang", defaults["default_lang"]))
        if lang not in _VALID_LANGS:
            log.warning(f"Invalid default_lang in settings.json ({lang!r}) — using default")
            merged["default_lang"] = defaults["default_lang"]
        merged["automate"]    = bool(merged.get("automate",   defaults["automate"]))
        merged["translate"]   = bool(merged.get("translate",  defaults["translate"]))
        return merged
    except Exception as e:
        log.warning(f"Could not load settings.json: {e} — using defaults")
        return defaults


class SmartKeyboardApp:

    MODEL_DIR     = os.path.join(_root_dir(), "models", "indictrans2")
    GRAMMAR_DIR   = os.path.join(_root_dir(), "models", "grammar")
    TONE_DIR      = os.path.join(_root_dir(), "models", "tone", "hin")

    def __init__(self):
        settings = _load_settings()
        self._hotkey_str  = settings.get("hotkey", "ctrl+alt+k")
        self.HOTKEY_LABEL = self._hotkey_str.replace("+", " + ").title()
        self._default_lang = settings.get("default_lang", "hin_Deva")
        self._automate        = settings.get("automate",  False)
        self._translate_mode  = settings.get("translate", False)

        self._app      = QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)
        self._bridge       = _Bridge()
        self._popup                = None
        self._popup_hidden_for_focus = False
        self._power_filter           = None
        self._enabled              = True
        self._listener             = None
        self._target_hwnd          = 0
        self._bridge.hotkey_fired.connect(self._show_popup, Qt.QueuedConnection)
        self._popup_history = deque(maxlen=5)
        # QueuedConnection: slot is posted to the main event-loop queue, so it
        # always runs on the main thread even when emitted from a bg thread.
        self._bridge.model_ready.connect(self._apply_model_status, Qt.QueuedConnection)
        self._bridge.text_captured.connect(self._on_text_captured, Qt.QueuedConnection)
        self._bridge.show_hk_dialog.connect(self._open_hotkey_dialog, Qt.QueuedConnection)

        # Focus monitor — hide popup when another app takes focus, restore when target returns
        self._focus_timer = QTimer()
        self._focus_timer.timeout.connect(self._check_focus)
        self._focus_timer.start(500)
        self._translation_engine = TranslationEngine(model_dir=self.MODEL_DIR)
        self._grammar_engine     = GrammarEngine(model_dir=self.GRAMMAR_DIR)
        self._tone_engine        = None   # tone fine-tuning in progress — not loaded
        self._models_ready       = {"grammar": False, "translation": False}
        self._model_errors       = {}   # name → error string, populated on load failure
        self._models_lock        = threading.Lock()
        self._last_target_rect   = None

    def run(self):
        self._tray = TrayManager(
            on_toggle        = self._on_tray_toggle,
            on_quit          = self._quit,
            on_change_hotkey = lambda: self._bridge.show_hk_dialog.emit(),
            hotkey_str       = self.HOTKEY_LABEL,
        )
        self._tray.start()
        self._listener = start_hotkey_listener(self._on_hotkey, self._hotkey_str)

        self._power_filter = _PowerFilter(self._on_system_resume)
        self._app.installNativeEventFilter(self._power_filter)

        self._grammar_engine.load(
            on_ready = lambda: self._on_model_ready("grammar"),
            on_error = lambda e: self._on_model_ready("grammar", error=e),
        )
        self._translation_engine.load(
            on_ready = lambda: self._on_model_ready("translation"),
            on_error = lambda e: self._on_model_ready("translation", error=e),
        )

        print(f"[Smart Keyboard] Running — hotkey: {self.HOTKEY_LABEL}")
        print(f"[Smart Keyboard] Settings: {os.path.join(appdata_dir(), 'settings.json')}")
        sys.exit(self._app.exec_())

    def _open_hotkey_dialog(self):
        from hotkey_dialog import SettingsDialog
        from PyQt5.QtWidgets import QDialog
        dlg = SettingsDialog(
            current_hotkey=self._hotkey_str,
            current_lang=self._default_lang,
            current_automate=self._automate,
        )
        if dlg.exec_() != QDialog.Accepted:
            return

        updates = {}

        # ── Language ──────────────────────────────────────────────────────────
        if dlg.result_lang and dlg.result_lang != self._default_lang:
            self._default_lang = dlg.result_lang
            updates["default_lang"] = self._default_lang
            # Apply to any currently-open popup
            if self._popup:
                self._popup.set_default_lang(self._default_lang)
            log.info(f"Default language changed to: {self._default_lang}")

        # ── Hotkey ────────────────────────────────────────────────────────────
        if dlg.result_hotkey:
            new_hotkey = dlg.result_hotkey.lower().strip()
            if not _HOTKEY_RE.match(new_hotkey):
                log.warning(f"SettingsDialog returned invalid hotkey {new_hotkey!r} — ignoring")
            elif new_hotkey != self._hotkey_str:
                if self._listener:
                    self._listener.stop()
                self._hotkey_str  = new_hotkey
                self.HOTKEY_LABEL = new_hotkey.replace("+", " + ").title()
                self._listener    = start_hotkey_listener(self._on_hotkey, new_hotkey)
                updates["hotkey"] = new_hotkey
                self._tray.set_hotkey(self.HOTKEY_LABEL)
                log.info(f"Hotkey changed to: {new_hotkey}")
                if self._popup:
                    self._popup.set_hotkey_label(self.HOTKEY_LABEL)

        # ── Automate ──────────────────────────────────────────────────────────
        if dlg.result_automate is not None and dlg.result_automate != self._automate:
            self._automate = dlg.result_automate
            updates["automate"] = self._automate
            log.info(f"Automate mode {'enabled' if self._automate else 'disabled'}")

        if updates:
            self._save_settings(updates)

    def _save_settings(self, updates: dict):
        path = os.path.join(appdata_dir(), "settings.json")
        try:
            existing = {}
            if os.path.isfile(path):
                with open(path) as f:
                    existing = json.load(f)
            existing.update(updates)
            with open(path, "w") as f:
                json.dump(existing, f, indent=2)
        except Exception as e:
            log.warning(f"Could not save settings: {e}")

    def _on_model_ready(self, name: str, error: str = ""):
        # Called from model-loading background threads.
        # Lock protects the shared dict from concurrent mutation across three threads.
        if error:
            log.warning(f"{name} model unavailable: {error}")
        else:
            log.info(f"{name} model ready")
        with self._models_lock:
            self._models_ready[name] = "error" if error else True
            if error:
                self._model_errors[name] = error
            still    = [k for k, v in self._models_ready.items() if v is False]
            failed   = [k for k, v in self._models_ready.items() if v == "error"]
            done     = len(self._models_ready) - len(still)
            total    = len(self._models_ready)
            all_done = not still

        parts = []
        if still:
            parts.append(f"Loading: {', '.join(still)} ({done}/{total} ready)")
        if failed:
            parts.append(f"Failed: {', '.join(failed)} — check logs")
        status_text = "  |  ".join(parts)

        # Emit across threads — QueuedConnection (set in __init__) guarantees
        # _apply_model_status runs on the Qt main thread.
        self._bridge.model_ready.emit(all_done, status_text, bool(failed))

    def _apply_model_status(self, all_done: bool, status_text: str, has_failures: bool):
        """Runs on Qt main thread — safe to touch tray."""
        if all_done:
            self._tray.set_loading(False)
            if has_failures:
                log.warning("One or more models failed to load — running in degraded mode")
                self._show_model_error_dialog()
            else:
                log.info("All models ready")
        else:
            self._tray.set_loading(True, status_text)

    def _show_model_error_dialog(self):
        """Show a one-time dialog explaining which models failed and where to place them."""
        lines = ["The following AI models could not be loaded:\n"]
        for name, err in self._model_errors.items():
            lines.append(f"  {name.title()}:\n  {err}\n")
        lines.append("Place the missing model files in the paths shown above,\nthen restart Smart Keyboard.")

        dlg = QMessageBox()
        dlg.setWindowTitle("Smart Keyboard — Model Error")
        dlg.setIcon(QMessageBox.Critical)
        dlg.setText("\n".join(lines))
        dlg.setStandardButtons(QMessageBox.Ok)
        dlg.exec_()

    def _quit(self):
        if self._listener:
            self._listener.stop()
        if self._power_filter:
            self._app.removeNativeEventFilter(self._power_filter)
            self._power_filter = None
        for engine in (self._translation_engine, self._grammar_engine, self._tone_engine):
            if engine and hasattr(engine, "_executor"):
                engine._executor.shutdown(wait=False)
        self._app.quit()

    def _own_hwnds(self) -> set:
        """HWNDs of our own windows — never treat these as the target app."""
        hwnds = set()
        try:
            if self._popup:
                hwnds.add(int(self._popup.winId()))
        except Exception:
            pass
        return hwnds

    def _on_hotkey(self):
        if not self._enabled:
            log.debug("Hotkey fired but app is disabled — skipped")
            return
        hwnd = get_foreground_hwnd()
        if hwnd not in self._own_hwnds():
            self._target_hwnd = hwnd   # only update when a foreign window is active
        log.info(f"Hotkey received | target hwnd: {self._target_hwnd:#010x}")
        # Show the popup immediately with empty text so the user sees it at once,
        # then fill in the captured selection when clipboard read completes (~300ms later).
        self._bridge.hotkey_fired.emit("")

        def _capture():
            try:
                selected = get_selected_text()
            except Exception as e:
                log.exception(f"Selection capture failed: {e}")
                selected = ""
            log.info(f"Text captured | length: {len(selected)}")
            self._bridge.text_captured.emit(selected)

        threading.Thread(target=_capture, daemon=True, name="TextCapture").start()

    def _on_mode_change(self, translate_enabled: bool):
        """Called when the user toggles Grammar ↔ Translate in the popup."""
        self._translate_mode = translate_enabled
        self._save_settings({"translate": translate_enabled})

    def _on_text_captured(self, selected_text: str):
        """Runs on Qt main thread — fills input box once clipboard capture completes."""
        if self._popup:
            self._popup.set_selected_text(selected_text)
            if self._automate and self._popup._minimized and selected_text.strip():
                self._popup._on_process()

    def _get_target_rect(self):
        """Screen rect (x, y, w, h) of the target application window, or None."""
        return get_window_rect(self._target_hwnd) if self._target_hwnd else None

    def _show_popup(self, selected_text: str):
        rect = self._get_target_rect()

        if self._popup and (
            self._popup.isVisible()
            or self._popup_hidden_for_focus
            or self._popup._minimized
        ):
            log.info("Popup already open — updating text")
            if self._popup._minimized and not self._automate:
                # Manual mode: restore popup on new hotkey press
                self._popup._on_restore()
            # Automate mode: keep circle, let _on_text_captured re-trigger processing
            self._popup.set_selected_text(selected_text)
            self._popup.set_hotkey_label(self.HOTKEY_LABEL)
            if not self._automate:
                if rect:
                    self._last_target_rect = rect
                    self._popup.position_near_window(*rect)
                else:
                    self._popup.show()
                    self._popup.raise_()
            self._popup_hidden_for_focus = False
            return

        log.info("Creating new popup")
        self._popup = SmartKeyboardPopup(
            selected_text        = selected_text,
            on_paste             = self._on_paste,
            on_change_hotkey     = self._open_hotkey_dialog,
            hotkey_label         = self.HOTKEY_LABEL,
            default_lang         = self._default_lang,
            history              = self._popup_history,
            automate             = self._automate,
            translation_enabled  = self._translate_mode,
            on_mode_change       = self._on_mode_change,
        )
        self._popup.set_grammar_engine(self._grammar_engine)
        self._popup.set_translation_engine(self._translation_engine)
        self._popup.set_tone_engine(self._tone_engine)

        if self._automate:
            if rect:
                self._last_target_rect = rect
                self._popup.show_as_circle(*rect)
            else:
                self._popup.show_as_circle_near_cursor()
        else:
            if rect:
                self._last_target_rect = rect
                self._popup.position_near_window(*rect)
            else:
                self._popup.show_near_cursor()
        self._popup_hidden_for_focus = False

    def _on_paste(self, result_text: str, on_result=None):
        log.info(f"Paste callback | target hwnd: {self._target_hwnd:#010x} | text length: {len(result_text)}")
        hwnd = self._target_hwnd

        def _run():
            try:
                paste_text(result_text, target_hwnd=hwnd)
                if on_result:
                    QTimer.singleShot(0, lambda: on_result(True))
            except Exception as e:
                log.error(f"Paste failed: {e}")
                if on_result:
                    msg = str(e)
                    QTimer.singleShot(0, lambda: on_result(False, msg))
                else:
                    print("Text is in your clipboard — paste manually with Ctrl+V.")

        threading.Thread(target=_run, daemon=True, name="PasteThread").start()

    def _check_focus(self):
        try:
            if not self._popup:
                return
            fg  = get_foreground_hwnd()
            own = self._own_hwnds()

            if fg == self._target_hwnd:
                rect = self._get_target_rect()
                if self._popup_hidden_for_focus:
                    # Target window regained focus — re-attach popup
                    if rect:
                        self._last_target_rect = rect
                        self._popup.position_near_window(*rect)
                    else:
                        self._popup.show()
                        self._popup.raise_()
                    self._popup_hidden_for_focus = False
                elif self._popup.isVisible() and rect and rect != self._last_target_rect:
                    # Target window moved while popup was visible — follow it
                    self._last_target_rect = rect
                    self._popup.position_near_window(*rect)
            elif fg in own:
                pass   # one of our own windows — leave popup alone
            else:
                # Don't hide while minimised (circle is a separate independent window).
                # Don't hide within 2 s of restore — user intentionally clicked the circle
                # and _check_focus must not immediately undo that.
                just_restored = time.monotonic() - self._popup._last_restore_time < 2.0
                if (self._popup.isVisible()
                        and not self._popup._minimized
                        and not just_restored):
                    self._popup.hide()
                    self._popup_hidden_for_focus = True
        except Exception as e:
            log.debug(f"Focus check error: {e}")

    def _on_system_resume(self):
        """Called when Windows resumes from sleep — restart the hotkey hook."""
        log.info("System resume detected — restarting hotkey listener")
        if self._listener:
            self._listener.stop()
        self._listener = start_hotkey_listener(self._on_hotkey, self._hotkey_str)
        log.info("Hotkey listener restarted after resume")

    def _on_tray_toggle(self, enabled: bool):
        self._enabled = enabled
        log.info(f"Tray toggle — app {'enabled' if enabled else 'disabled'}")


_instance_mutex = None   # module-level keeps the Win32 handle alive


def _acquire_instance_lock() -> bool:
    """
    Create a named Windows mutex. Returns True if this is the first instance.
    The handle is held in _instance_mutex for the process lifetime — Windows
    releases it automatically on exit (clean or crash).
    """
    global _instance_mutex
    handle = ctypes.windll.kernel32.CreateMutexW(None, True, "SmartKeyboard_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
        ctypes.windll.kernel32.CloseHandle(handle)
        return False
    _instance_mutex = handle
    return True


if __name__ == "__main__":
    if not _acquire_instance_lock():
        ctypes.windll.user32.MessageBoxW(
            0,
            "Smart Keyboard is already running.\nCheck the system tray.",
            "Smart Keyboard",
            0x40,   # MB_ICONINFORMATION
        )
        sys.exit(0)
    SmartKeyboardApp().run()
