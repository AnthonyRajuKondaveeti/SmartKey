import sys
import time
import queue
import ctypes
import textwrap
from collections import deque
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QGraphicsDropShadowEffect, QComboBox
)
from PyQt5.QtCore import Qt, QTimer, QEvent, QPoint
from PyQt5.QtGui import QFont, QCursor, QColor, QRegion
from logger import log
from tone import TONE_MAX_CHARS
from utils import LANGUAGES as _LANGUAGES, is_english_input

# ── Palette ───────────────────────────────────────────────────────────────────
BG_COLOR      = "#FBFBFB"
SURFACE_COLOR = "#FFFFFF"
CHIP_COLOR    = "#F4F4F3"
ACCENT_BLACK  = "#1A1A1A"
TEXT_MAIN     = "#37352F"
TEXT_MUTED    = "#807D78"
BORDER_COLOR  = "#E8E8E8"
SUCCESS_SAND  = "#435B4E"

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"



# ── Floating circle (separate top-level window, no setWindowFlags changes) ────

class _CircleWindow(QWidget):
    """
    Independent 48×48 circle shown when the popup is minimised.

    WindowStaysOnTopHint keeps it visible when other windows are clicked.
    WS_EX_TOOLWINDOW (set via Win32 after first show) hides it from the
    taskbar without using Qt.Tool, which would hide it when the app loses focus.
    """

    def __init__(self, on_restore):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(48, 48)
        self.setMask(QRegion(0, 0, 48, 48, QRegion.Ellipse))

        self._drag_pos = None
        self._dragging = False
        self._closing  = False

        btn = QPushButton("⌨", self)
        btn.setFixedSize(48, 48)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip("Click to restore")
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {BG_COLOR};
                color: {ACCENT_BLACK};
                border-radius: 24px;
                font-size: 22px;
                font-family: 'Segoe UI Emoji', 'Apple Color Emoji', 'Noto Color Emoji', sans-serif;
                border: 1.5px solid {BORDER_COLOR};
            }}
            QPushButton:hover {{ background: {CHIP_COLOR}; border: 1.5px solid {ACCENT_BLACK}; }}
        """)
        shadow = QGraphicsDropShadowEffect(btn)
        shadow.setBlurRadius(16)
        shadow.setXOffset(0)
        shadow.setYOffset(4)
        shadow.setColor(QColor(0, 0, 0, 40))
        btn.setGraphicsEffect(shadow)
        btn.installEventFilter(self)
        btn.clicked.connect(on_restore)

    def showEvent(self, event):
        super().showEvent(event)
        # Hide from taskbar via Win32 extended style so no Python icon appears.
        # WS_EX_TOOLWINDOW suppresses the taskbar button; WS_EX_APPWINDOW would
        # force one — clearing it ensures the suppress takes effect.
        try:
            GWL_EXSTYLE      = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW  = 0x00040000
            hwnd  = int(self.winId())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    def close_permanently(self):
        self._closing = True
        self.close()

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange:
            if self.windowState() & Qt.WindowMinimized:
                self.setWindowState(Qt.WindowNoState)
        super().changeEvent(event)

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            self._dragging = False
        elif t == QEvent.MouseMove and event.buttons() & Qt.LeftButton:
            if self._drag_pos is not None:
                delta = event.globalPos() - (self.frameGeometry().topLeft() + self._drag_pos)
                if not self._dragging and delta.manhattanLength() > 6:
                    self._dragging = True
                if self._dragging:
                    self.move(event.globalPos() - self._drag_pos)
                    return True
        elif t == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            was = self._dragging
            self._drag_pos = None
            self._dragging = False
            if was:
                return True   # swallow release — don't fire clicked after a drag
        return super().eventFilter(obj, event)


# ── Main popup ────────────────────────────────────────────────────────────────

class SmartKeyboardPopup(QWidget):

    def __init__(self, selected_text: str = "", on_paste=None,
                 on_change_hotkey=None, hotkey_label: str = "Ctrl+Shift+T",
                 default_lang: str = "hin_Deva", history: deque = None,
                 automate: bool = False, translation_enabled: bool = False,
                 on_mode_change=None):
        super().__init__()
        self._selected_text         = selected_text
        self._on_paste              = on_paste
        self._on_change_hotkey      = on_change_hotkey
        self._on_mode_change        = on_mode_change
        self._hotkey_label          = hotkey_label
        self._translation_enabled   = translation_enabled
        self._current_relationship  = None
        self._target_lang           = default_lang
        self._default_lang          = default_lang
        self._processing            = False
        self._job_seq               = 0      # incremented on each new job; stale results are discarded
        self._spinner_idx           = 0
        self._minimized             = False
        self._circle_win            = None   # _CircleWindow instance while minimised
        self._last_restore_time     = 0.0    # monotonic timestamp of last restore
        self._translation_engine    = None
        self._grammar_engine        = None
        self._tone_engine           = None
        self._process_start_time    = 0.0
        self._result_queue              = queue.Queue()
        self._pending_grammar_future    = None   # Future for in-flight grammar job
        self._pending_translation_future = None  # Future for in-flight translation job
        self._drag_pos                  = None
        self._history                   = history if history is not None else deque(maxlen=5)
        self._automate                  = automate

        self._build_ui()
        self._apply_styles()
        self._add_shadow()

    def set_translation_engine(self, engine):
        self._translation_engine = engine
        self._update_model_status()

    def set_grammar_engine(self, engine):
        self._grammar_engine = engine
        self._update_model_status()

    def set_tone_engine(self, engine):
        self._tone_engine = engine

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(440)

        self.container = QWidget(self)
        self.container.setObjectName("MainContainer")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.addWidget(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        # ── Header ────────────────────────────────────────────────────────────
        header = QHBoxLayout()
        title_label = QLabel("SMART KEYBOARD")
        title_label.setFont(QFont("Inter", 9, QFont.Black))
        title_label.setStyleSheet(f"color: {ACCENT_BLACK}; letter-spacing: 2px;")
        _btn_style = (
            f"QPushButton {{ background: transparent; color: {TEXT_MUTED}; border: none; font-size: 12px; }}"
            f"QPushButton:hover {{ color: {ACCENT_BLACK}; }}"
        )
        settings_btn = QPushButton("⚙")
        settings_btn.setFixedSize(20, 20)
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.setToolTip("Change hotkey")
        settings_btn.clicked.connect(self._on_settings_clicked)
        settings_btn.setStyleSheet(_btn_style)

        minimize_btn = QPushButton("—")
        minimize_btn.setFixedSize(20, 20)
        minimize_btn.setCursor(Qt.PointingHandCursor)
        minimize_btn.setToolTip("Minimise to circle")
        minimize_btn.clicked.connect(self._on_minimize)
        minimize_btn.setStyleSheet(_btn_style)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet(_btn_style)

        header.addWidget(title_label)
        header.addStretch()
        header.addWidget(settings_btn)
        header.addWidget(minimize_btn)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # ── Model loading status ──────────────────────────────────────────────
        self._model_status = QLabel("")
        self._model_status.setAlignment(Qt.AlignCenter)
        self._model_status.setStyleSheet(
            f"color: #7C6A00; font-size: 10px; font-weight: 600; letter-spacing: 0.3px;"
            f"background: #FFF8DC; border: 1px solid #E8D87A;"
            f"border-radius: 4px; padding: 5px 10px;"
        )
        self._model_status.setVisible(False)
        layout.addWidget(self._model_status)

        # ── Mode buttons ──────────────────────────────────────────────────────
        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        self._grammar_btn = QPushButton("English Refiner")
        self._grammar_btn.setFixedHeight(36)
        self._translation_btn = QPushButton("Translate")
        self._translation_btn.setCheckable(True)
        self._translation_btn.setFixedHeight(36)
        self._translation_btn.setCursor(Qt.PointingHandCursor)
        self._translation_btn.setChecked(self._translation_enabled)
        self._translation_btn.clicked.connect(self._on_translation_toggle)
        mode_row.addWidget(self._grammar_btn, 1)
        mode_row.addWidget(self._translation_btn, 1)
        layout.addLayout(mode_row)

        # ── Translation options ───────────────────────────────────────────────
        self._rel_group = QWidget()
        self._rel_group.setVisible(self._translation_enabled)
        rel_layout = QVBoxLayout(self._rel_group)
        rel_layout.setContentsMargins(0, 0, 0, 0)
        rel_layout.setSpacing(10)

        rel_layout.addWidget(self._section_label("LANGUAGE"))
        self._lang_combo = QComboBox()
        self._lang_combo.setFixedHeight(30)
        self._lang_combo.setCursor(Qt.PointingHandCursor)
        for label, code in _LANGUAGES:
            self._lang_combo.addItem(label, userData=code)
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
        # Pre-select saved default — block signals so _on_language_changed
        # doesn't fire before _tone_group is created further down in _build_ui.
        self._lang_combo.blockSignals(True)
        for i, (_, code) in enumerate(_LANGUAGES):
            if code == self._target_lang:
                self._lang_combo.setCurrentIndex(i)
                break
        self._lang_combo.blockSignals(False)
        rel_layout.addWidget(self._lang_combo)

        self._tone_group = QWidget()
        tone_layout = QVBoxLayout(self._tone_group)
        tone_layout.setContentsMargins(0, 0, 0, 0)
        tone_layout.setSpacing(6)
        tone_layout.addWidget(self._section_label("TONE"))
        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        self._rel_buttons = []
        for i, name in enumerate(["Mother", "Friend", "Partner", "Stranger"]):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(False)
            btn.setFixedHeight(28)
            btn.setEnabled(False)
            btn.setCursor(Qt.ArrowCursor)
            btn.clicked.connect(lambda _, idx=i: self._set_relationship(idx))
            self._rel_buttons.append(btn)
            chips_row.addWidget(btn)
        tone_layout.addLayout(chips_row)

        self._tone_coming_soon = QLabel("Persona fine-tuning in progress — coming soon")
        self._tone_coming_soon.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 9px; letter-spacing: 0.3px;"
        )
        tone_layout.addWidget(self._tone_coming_soon)
        rel_layout.addWidget(self._tone_group)
        layout.addWidget(self._rel_group)

        # ── Input ─────────────────────────────────────────────────────────────
        layout.addWidget(self._section_label("INPUT"))
        self._input_box = QTextEdit()
        self._input_box.setPlaceholderText("Capturing selected text…")
        self._input_box.setPlainText(self._selected_text)
        self._input_box.setFixedHeight(80)
        self._input_box.installEventFilter(self)
        layout.addWidget(self._input_box)

        # ── Process button ────────────────────────────────────────────────────
        self._process_btn = QPushButton("PROCESS")
        self._process_btn.setFixedHeight(40)
        self._process_btn.setCursor(Qt.PointingHandCursor)
        self._process_btn.clicked.connect(self._on_process)
        layout.addWidget(self._process_btn)

        # ── Output ────────────────────────────────────────────────────────────
        out_header = QHBoxLayout()
        out_header.addWidget(self._section_label("OUTPUT"))
        out_header.addStretch()

        self._tone_status_label = QLabel("")
        self._tone_status_label.setStyleSheet("font-size: 9px; font-weight: 700; letter-spacing: 0.5px;")
        self._tone_status_label.setVisible(False)
        out_header.addWidget(self._tone_status_label)

        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setFixedSize(46, 20)
        self._copy_btn.setCursor(Qt.PointingHandCursor)
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._on_copy_output)
        out_header.addWidget(self._copy_btn)
        layout.addLayout(out_header)

        self._output_box = QTextEdit()
        self._output_box.setPlaceholderText("Refined text will appear here...")
        self._output_box.setFixedHeight(80)
        self._output_box.setReadOnly(True)
        # Nirmala UI ships with Windows 8+ and covers all 8 supported Indic scripts,
        # preventing the "OpenType support missing for script 17" Qt warnings.
        self._output_box.setFont(QFont("Nirmala UI", 13))
        layout.addWidget(self._output_box)

        # ── Paste button (manual mode only) ──────────────────────────────────────
        self._paste_btn = QPushButton("Paste to Application")
        self._paste_btn.setFixedHeight(40)
        self._paste_btn.setEnabled(False)
        self._paste_btn.setCursor(Qt.PointingHandCursor)
        self._paste_btn.clicked.connect(self._on_paste_clicked)
        self._paste_btn.setVisible(not self._automate)
        layout.addWidget(self._paste_btn)

        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        self._status_label.setVisible(False)
        layout.addWidget(self._status_label)

        self._hint = QLabel(self._hint_text())
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 9px; letter-spacing: 1px;"
        )
        layout.addWidget(self._hint)

        # ── History ───────────────────────────────────────────────────────────
        self._history_group = QWidget()
        self._history_group.setVisible(False)
        hist_outer = QVBoxLayout(self._history_group)
        hist_outer.setContentsMargins(0, 6, 0, 0)
        hist_outer.setSpacing(4)
        hist_outer.addWidget(self._section_label("RECENT"))
        self._history_list_layout = QVBoxLayout()
        self._history_list_layout.setSpacing(2)
        hist_outer.addLayout(self._history_list_layout)
        layout.addWidget(self._history_group)

        self._refresh_history_ui()

        self._ready_timer = QTimer(self)
        self._ready_timer.timeout.connect(self._update_model_status)
        self._ready_timer.start(500)

    # ── Section label helper ──────────────────────────────────────────────────

    def _hint_text(self) -> str:
        if self._automate:
            return f"Automate mode  |  Hotkey: {self._hotkey_label}"
        return f"Enter · Process  |  Ctrl+Enter · Paste  |  Hotkey: {self._hotkey_label}"

    def set_hotkey_label(self, label: str):
        self._hotkey_label = label
        if hasattr(self, "_hint"):
            self._hint.setText(self._hint_text())

    def set_default_lang(self, lang: str):
        """Apply a newly saved default language to the combo box."""
        self._default_lang = lang
        self._target_lang  = lang
        for i, (_, code) in enumerate(_LANGUAGES):
            if code == lang:
                self._lang_combo.setCurrentIndex(i)
                break

    def _on_settings_clicked(self):
        if self._on_change_hotkey:
            self._on_change_hotkey()

    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 9px; font-weight: 800; letter-spacing: 1.2px;"
        )
        return lbl

    # ── Shadow ────────────────────────────────────────────────────────────────

    def _add_shadow(self):
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setXOffset(0)
        shadow.setYOffset(8)
        shadow.setColor(QColor(0, 0, 0, 30))
        self.container.setGraphicsEffect(shadow)

    # ── Styles ────────────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.container.setStyleSheet(f"""
            #MainContainer {{
                background-color: {BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 8px;
            }}
            QTextEdit {{
                background-color: {SURFACE_COLOR};
                color: {TEXT_MAIN};
                border: 1px solid {BORDER_COLOR};
                border-radius: 4px;
                padding: 10px;
                font-size: 13px;
                selection-background-color: {CHIP_COLOR};
                selection-color: {ACCENT_BLACK};
            }}
            QTextEdit:focus {{
                border: 1px solid {ACCENT_BLACK};
            }}
            QPushButton {{
                background-color: {CHIP_COLOR};
                color: {TEXT_MAIN};
                border: 1px solid {BORDER_COLOR};
                border-radius: 4px;
                font-weight: 600;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #EDECEB;
            }}
            QPushButton:checked {{
                background-color: {ACCENT_BLACK};
                color: {BG_COLOR};
                border: 1px solid {ACCENT_BLACK};
            }}
        """)

        self._process_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_BLACK}; color: {BG_COLOR};
                border: none; border-radius: 4px;
                font-size: 11px; font-weight: 800; letter-spacing: 1px;
            }}
            QPushButton:hover {{ background-color: #333333; }}
            QPushButton:disabled {{ background-color: {CHIP_COLOR}; color: {TEXT_MUTED}; }}
        """)

        self._paste_btn.setStyleSheet(f"""
            QPushButton:enabled {{
                background-color: {SUCCESS_SAND}; color: white;
                border: none; border-radius: 4px; font-weight: 800;
            }}
            QPushButton:disabled {{
                background-color: {SURFACE_COLOR};
                border: 1px solid {BORDER_COLOR}; color: {TEXT_MUTED};
            }}
        """)

        self._copy_btn.setStyleSheet(f"""
            QPushButton {{
                background: {CHIP_COLOR}; color: {TEXT_MUTED};
                border: 1px solid {BORDER_COLOR}; border-radius: 3px;
                font-size: 9px; font-weight: 600;
            }}
            QPushButton:hover {{ color: {ACCENT_BLACK}; }}
            QPushButton:disabled {{ color: {BORDER_COLOR}; border-color: {BORDER_COLOR}; }}
        """)

        self._lang_combo.setStyleSheet(f"""
            QComboBox {{
                background: {CHIP_COLOR}; color: {TEXT_MAIN};
                border: 1px solid {BORDER_COLOR}; border-radius: 4px;
                padding: 4px 8px; font-size: 11px; font-weight: 600;
            }}
            QComboBox:hover {{ background: #EDECEB; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{
                background: {SURFACE_COLOR}; color: {TEXT_MAIN};
                border: 1px solid {BORDER_COLOR};
                selection-background-color: {CHIP_COLOR};
                selection-color: {ACCENT_BLACK};
            }}
        """)

    # ── Model status ──────────────────────────────────────────────────────────

    def _update_model_status(self):
        engines = [
            ("Grammar",     self._grammar_engine),
            ("Translation", self._translation_engine),
        ]
        # Wait until at least one engine has been attached before evaluating state.
        # The timer starts inside __init__ but engines are set by the caller
        # immediately after construction — guard prevents premature stop.
        if not any(eng is not None for _, eng in engines):
            return

        loading = [
            name for name, eng in engines
            if eng and not eng.is_ready and not getattr(eng, "is_failed", False)
        ]

        if loading:
            self._spinner_idx = (self._spinner_idx + 1) % len(_SPINNER)
            spin = _SPINNER[self._spinner_idx]
            noun = "models" if len(loading) > 1 else "model"
            self._model_status.setText(f"{spin}  Loading {noun}: {', '.join(loading)}")
            self._model_status.setVisible(True)
        else:
            self._model_status.setVisible(False)
            self._ready_timer.stop()

        # PROCESS button tracks the engine for the currently selected mode only
        active = self._translation_engine if self._translation_enabled else self._grammar_engine
        active_loading = (
            active
            and not active.is_ready
            and not getattr(active, "is_failed", False)
        )
        if not self._processing:
            if active_loading:
                spin = _SPINNER[self._spinner_idx]
                self._process_btn.setEnabled(False)
                self._process_btn.setText(f"{spin}  Loading...")
            else:
                self._reset_process_btn()

    # ── History ───────────────────────────────────────────────────────────────

    def _refresh_history_ui(self):
        while self._history_list_layout.count():
            item = self._history_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        entries = list(self._history)[:3]
        self._history_group.setVisible(bool(entries))
        for entry in entries:
            preview = textwrap.shorten(entry["output"], 42, placeholder="…")
            btn = QPushButton(preview)
            btn.setFixedHeight(22)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(
                f"Input: {entry['input'][:80]}\nOutput: {entry['output'][:80]}"
            )
            btn.clicked.connect(lambda _, e=entry: self._load_history_entry(e))
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {TEXT_MUTED};
                    border: none; text-align: left;
                    font-size: 10px; padding-left: 4px;
                }}
                QPushButton:hover {{
                    color: {ACCENT_BLACK}; background: {CHIP_COLOR}; border-radius: 3px;
                }}
            """)
            self._history_list_layout.addWidget(btn)

        self.adjustSize()

    def _load_history_entry(self, entry):
        self._input_box.setPlainText(entry["input"])
        self._output_box.setPlainText(entry["output"])
        self._copy_btn.setEnabled(True)
        if not self._automate:
            self._paste_btn.setEnabled(True)

    # ── Copy output ───────────────────────────────────────────────────────────

    def _on_copy_output(self):
        text = self._output_box.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self._copy_btn.setText("Copied!")
            QTimer.singleShot(1200, lambda: self._copy_btn.setText("Copy"))

    # ── Minimise / restore ────────────────────────────────────────────────────

    def _on_minimize(self):
        if self._processing:
            return
        # Place the circle at the popup's bottom-right corner before hiding.
        cx = self.x() + self.width()  - 48 - 12
        cy = self.y() + self.height() - 48 - 12
        self._minimized = True
        self._circle_win = _CircleWindow(on_restore=self._on_restore)
        self._circle_win.move(cx, cy)
        self._circle_win.show()
        self.hide()   # hide popup — no setWindowFlags, no native window recreation

    def _on_restore(self):
        # Read circle position before destroying it.
        if self._circle_win is not None:
            cx, cy = self._circle_win.x(), self._circle_win.y()
            self._circle_win.close_permanently()
            self._circle_win = None
        else:
            cx = self.x()
            cy = self.y()
        self._minimized = False
        self._last_restore_time = time.monotonic()
        self.adjustSize()
        screen = (QApplication.screenAt(QPoint(cx, cy))
                  or QApplication.primaryScreen()).availableGeometry()
        new_x = cx - (self.width()  - 48) - 12
        new_y = cy - (self.height() - 48) - 12
        new_x = max(screen.left() + 8, min(new_x, screen.right()  - self.width()  - 8))
        new_y = max(screen.top()  + 8, min(new_y, screen.bottom() - self.height() - 8))
        self.move(new_x, new_y)
        self.show()
        self.raise_()

    # ── Positioning ───────────────────────────────────────────────────────────

    def position_near_window(self, win_x: int, win_y: int, win_w: int, win_h: int):
        if self._minimized:
            return   # circle is a separate window; user may have dragged it
        screen = (QApplication.screenAt(QPoint(win_x, win_y))
                  or QApplication.primaryScreen()).availableGeometry()
        self.adjustSize()
        pw, ph = self.width(), self.height()

        x = win_x + win_w - pw - 12
        y = win_y + win_h - ph - 12

        x = max(screen.left() + 8, min(x, screen.right()  - pw - 8))
        y = max(screen.top()  + 8, min(y, screen.bottom() - ph - 8))

        self.move(x, y)
        self.show()
        self.raise_()

    def show_near_cursor(self):
        pos    = QCursor.pos()
        screen = (QApplication.screenAt(pos)
                  or QApplication.primaryScreen()).availableGeometry()
        x, y   = pos.x() + 20, pos.y() + 20
        if x + self.width()  > screen.right():  x = screen.right()  - self.width()  - 10
        if y + self.height() > screen.bottom(): y = screen.bottom() - self.height() - 10
        self.move(x, y)
        self.show()

    # ── Automate-mode positioning (circle only, no popup) ─────────────────────

    def show_as_circle(self, win_x: int, win_y: int, win_w: int, win_h: int):
        """Automate mode: position popup at target window but show only the circle."""
        screen = (QApplication.screenAt(QPoint(win_x, win_y))
                  or QApplication.primaryScreen()).availableGeometry()
        self.adjustSize()
        pw, ph = self.width(), self.height()
        x = win_x + win_w - pw - 12
        y = win_y + win_h - ph - 12
        x = max(screen.left() + 8, min(x, screen.right()  - pw - 8))
        y = max(screen.top()  + 8, min(y, screen.bottom() - ph - 8))
        self.move(x, y)   # position without showing — needed for correct restore later
        cx = x + pw - 48 - 12
        cy = y + ph - 48 - 12
        self._minimized  = True
        self._circle_win = _CircleWindow(on_restore=self._on_restore)
        self._circle_win.move(cx, cy)
        self._circle_win.show()

    def show_as_circle_near_cursor(self):
        """Automate mode fallback when no target window rect is available."""
        pos    = QCursor.pos()
        screen = (QApplication.screenAt(pos)
                  or QApplication.primaryScreen()).availableGeometry()
        self.adjustSize()
        pw, ph = self.width(), self.height()
        # Place popup off-screen so _on_restore can compute a sane position
        self.move(screen.left() + 8, screen.bottom() - ph - 8)
        cx = max(screen.left() + 8, min(pos.x() + 10, screen.right()  - 56))
        cy = max(screen.top()  + 8, min(pos.y() + 10, screen.bottom() - 56))
        self._minimized  = True
        self._circle_win = _CircleWindow(on_restore=self._on_restore)
        self._circle_win.move(cx, cy)
        self._circle_win.show()

    def set_selected_text(self, text):
        self._processing = False
        self._job_seq += 1   # invalidate any in-flight job
        self._cancel_pending_futures()
        self._input_box.setPlainText(text)
        self._output_box.clear()
        self._copy_btn.setEnabled(False)
        if not self._automate:
            self._paste_btn.setEnabled(False)
        self._tone_status_label.setVisible(False)
        self._status_label.setVisible(False)
        self._reset_process_btn()

    # ── Language / tone selection ─────────────────────────────────────────────

    def _on_language_changed(self, _index):
        code = self._lang_combo.currentData()
        self._target_lang = code
        is_hindi = (code == "hin_Deva")
        self._tone_group.setVisible(is_hindi)
        if not is_hindi:
            self._current_relationship = None
            for btn in self._rel_buttons:
                btn.setChecked(False)
        self.adjustSize()

    def _set_relationship(self, idx):
        if self._current_relationship == idx:
            self._current_relationship = None
            for btn in self._rel_buttons:
                btn.setChecked(False)
        else:
            self._current_relationship = idx
            for i, btn in enumerate(self._rel_buttons):
                btn.setChecked(i == idx)

    def _on_translation_toggle(self):
        self._translation_enabled = self._translation_btn.isChecked()
        self._rel_group.setVisible(self._translation_enabled)
        self.adjustSize()
        self._update_model_status()
        if self._on_mode_change:
            self._on_mode_change(self._translation_enabled)

    # ── Processing ────────────────────────────────────────────────────────────

    def _on_process(self):
        if self._processing:
            return
        text = self._input_box.toPlainText().strip()
        if not text:
            return

        if not is_english_input(text):
            self._output_box.setPlainText(
                "Input must be in English.\n"
                "This pipeline only processes Latin-script text."
            )
            return

        if self._translation_enabled:
            if not self._translation_engine:
                self._output_box.setPlainText("Translation engine not initialised — restart the app.")
                return
            if getattr(self._translation_engine, "is_failed", False):
                self._output_box.setPlainText(
                    "Translation model failed to load.\n"
                    "Check that models/indictrans2/ contains valid .onnx files, then restart."
                )
                return
            if not self._translation_engine.is_ready:
                self._output_box.setPlainText(
                    "Translation model is still loading — this takes 30–60 s on first start.\n"
                    "Please wait a moment and try again."
                )
                return
        else:
            if not self._grammar_engine:
                self._output_box.setPlainText("Grammar engine not initialised — restart the app.")
                return
            if getattr(self._grammar_engine, "is_failed", False):
                self._output_box.setPlainText(
                    "Grammar model failed to load.\n"
                    "Check that models/grammar/ contains valid .onnx files, then restart."
                )
                return
            if not self._grammar_engine.is_ready:
                self._output_box.setPlainText(
                    "Grammar model is still loading — please wait a moment."
                )
                return

        self._cancel_pending_futures()
        self._processing = True
        self._job_seq += 1
        self._process_start_time = time.monotonic()
        self._process_btn.setEnabled(False)
        self._process_btn.setText("WORKING...")
        self._tone_status_label.setVisible(False)
        mode = "translate" if self._translation_enabled else "grammar"
        log.info(f"Pipeline start | mode={mode} | lang={self._target_lang} | {len(text)} chars")
        if self._translation_enabled:
            self._run_translation(text)
        else:
            self._run_grammar(text)

    def _cancel_pending_futures(self):
        """Cancel any queued (not yet running) grammar or translation futures."""
        for f in (self._pending_grammar_future, self._pending_translation_future):
            if f is not None and not f.done():
                f.cancel()
        self._pending_grammar_future    = None
        self._pending_translation_future = None

    def _run_translation(self, text):
        lang = self._target_lang
        t0   = self._process_start_time
        if self._grammar_engine and self._grammar_engine.is_ready:
            log.info(f"Pipeline | grammar stage starting | {len(text)} chars")
            def _on_grammar_done(corrected):
                log.info(
                    f"Pipeline | grammar done | "
                    f"{(time.monotonic()-t0)*1000:.0f}ms cumulative | "
                    f"translation starting"
                )
                self._do_translate(corrected or text, lang, t0)
            self._pending_grammar_future = self._grammar_engine.correct(
                text,
                on_result=_on_grammar_done,
                on_error=lambda _: self._do_translate(text, lang, t0),
            )
        else:
            log.info("Pipeline | grammar skipped (not ready) | translation starting")
            self._do_translate(text, lang, t0)

    def _do_translate(self, text, target_lang="hin_Deva", t_pipeline=None):
        if not self._translation_engine:
            return

        t0 = t_pipeline or self._process_start_time

        def _on_translated(translated_text):
            log.info(
                f"Pipeline | translation done | "
                f"{(time.monotonic()-t0)*1000:.0f}ms cumulative | "
                f"{len(translated_text)} chars out"
            )
            persona = self._current_relationship
            if (
                target_lang == "hin_Deva"
                and persona is not None
                and self._tone_engine
                and self._tone_engine.is_ready
            ):
                persona_name = ["Mother", "Friend", "Partner", "Stranger"][persona]
                max_line = max(
                    (len(l) for l in translated_text.splitlines() if l.strip()),
                    default=len(translated_text),
                )
                if max_line > TONE_MAX_CHARS:
                    notice = ("TEXT TOO LONG — TONE SKIPPED", "warn")
                else:
                    notice = (f"TONE: {persona_name.upper()}", "ok")
                log.info(f"Pipeline | tone stage starting | persona={persona_name}")
                self._tone_engine.apply(
                    translated_text,
                    persona_idx=persona,
                    on_result=lambda t, n=notice: self._on_bg_result(t, n),
                    on_error=lambda _, n=notice: self._on_bg_result(translated_text, None),
                )
            else:
                self._on_bg_result(translated_text, None)

        self._pending_translation_future = self._translation_engine.translate(
            text=text,
            target_lang=target_lang,
            on_result=_on_translated,
            on_error=lambda msg: self._on_bg_result(f"Error: {msg}", None),
        )

    def _run_grammar(self, text):
        if not self._grammar_engine:
            self._on_bg_error("Grammar engine not available")
            return
        self._pending_grammar_future = self._grammar_engine.correct(
            text=text,
            on_result=lambda t: self._on_bg_result(t, None),
            on_error=self._on_bg_error,
        )

    # ── Background result callbacks ───────────────────────────────────────────

    def _on_bg_result(self, output, notice):
        self._result_queue.put((output, notice, self._job_seq))
        QTimer.singleShot(0, self._flush_pending_output)

    def _on_bg_error(self, message):
        self._result_queue.put((f"Error: {message}", None, self._job_seq))
        QTimer.singleShot(0, self._flush_pending_output)

    def _flush_pending_output(self):
        try:
            out, notice, seq = self._result_queue.get_nowait()
        except queue.Empty:
            return
        # Discard results from superseded jobs (user loaded new text mid-flight)
        if seq != self._job_seq:
            return
        if not out:
            return

        total_ms = (time.monotonic() - self._process_start_time) * 1000
        status   = "error" if out.startswith("Error:") else "ok"
        log.info(
            f"Pipeline done | {total_ms:.0f}ms total | "
            f"status={status} | {len(out)} chars out"
        )
        self._output_box.setPlainText(out)
        self._copy_btn.setEnabled(True)
        self._reset_process_btn()
        self._processing = False

        if notice:
            msg, level = notice
            color = "#C0392B" if level == "warn" else SUCCESS_SAND
            self._tone_status_label.setText(msg)
            self._tone_status_label.setStyleSheet(
                f"color: {color}; font-size: 9px; font-weight: 700; letter-spacing: 0.5px;"
            )
            self._tone_status_label.setVisible(True)
        else:
            self._tone_status_label.setVisible(False)

        input_text = self._input_box.toPlainText().strip()
        if input_text and not out.startswith("Error:"):
            entry = {"input": input_text, "output": out}
            dup = next((e for e in self._history if e["input"] == input_text), None)
            if dup:
                self._history.remove(dup)
            self._history.appendleft(entry)
            self._refresh_history_ui()

        if len(input_text) > 5000:
            self._status_label.setText("⚠  Input trimmed to 5,000 chars — result may be incomplete")
            self._status_label.setStyleSheet("color: #B7791F; font-size: 10px;")
            self._status_label.setVisible(True)

        if not out.startswith("Error:") and self._on_paste:
            if self._automate:
                # Automate mode: paste immediately, no button needed.
                # Uses `out` directly so the truncation notice is never included.
                QTimer.singleShot(30, lambda: self._on_paste(out, self._on_auto_paste_result))
            else:
                # Manual mode: let the user decide when to paste.
                self._paste_btn.setEnabled(True)

        self.adjustSize()

    def _reset_process_btn(self):
        self._process_btn.setEnabled(True)
        self._process_btn.setText("PROCESS")

    # ── Paste results ─────────────────────────────────────────────────────────

    def _on_auto_paste_result(self, success: bool, error_msg: str = ""):
        """Called after auto-paste in automate mode."""
        if success:
            if self._minimized:
                # Silent success — close circle, nothing more for the user to see
                if self._circle_win:
                    self._circle_win.close_permanently()
                    self._circle_win = None
                self._minimized = False
            else:
                self._status_label.setText("Pasted!")
                self._status_label.setStyleSheet(
                    f"color: {SUCCESS_SAND}; font-size: 10px; font-weight: 600;"
                )
                self._status_label.setVisible(True)
                QTimer.singleShot(2000, lambda: self._status_label.setVisible(False))
        else:
            if self._minimized:
                self._on_restore()   # surface the popup so the user sees the error
            self._status_label.setText(
                "Auto-paste failed — text is in clipboard, press Ctrl+V manually"
            )
            self._status_label.setStyleSheet("color: #C0392B; font-size: 10px;")
            self._status_label.setVisible(True)
            self.adjustSize()

    # ── Manual paste (non-automate mode) ─────────────────────────────────────

    def _on_paste_clicked(self):
        res = self._output_box.toPlainText()
        if self._on_paste and res:
            self._paste_btn.setEnabled(False)
            self._paste_btn.setText("Sending...")
            self._status_label.setVisible(False)
            QTimer.singleShot(50, lambda: self._on_paste(res, self._on_paste_result))

    def _on_paste_result(self, success: bool, error_msg: str = ""):
        if success:
            self._paste_btn.setText("Pasted!")
            QTimer.singleShot(1500, self._reset_paste_btn)
        else:
            self._paste_btn.setEnabled(True)
            self._paste_btn.setText("Paste to Application")
            self._status_label.setText(f"Send failed: {error_msg}")
            self._status_label.setStyleSheet("color: #C0392B; font-size: 10px;")
            self._status_label.setVisible(True)
            self.adjustSize()

    def _reset_paste_btn(self):
        self._paste_btn.setEnabled(True)
        self._paste_btn.setText("Paste to Application")

    # ── Events ────────────────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        if obj is self._input_box and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if not self._automate and event.modifiers() & Qt.ControlModifier:
                    self._on_paste_clicked()
                else:
                    self._on_process()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() == Qt.Key_Return:
            if not self._automate and event.modifiers() & Qt.ControlModifier:
                self._on_paste_clicked()
            else:
                self._on_process()

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange:
            if self.windowState() & Qt.WindowMinimized:
                self.setWindowState(Qt.WindowNoState)
        super().changeEvent(event)

    def closeEvent(self, event):
        if self._circle_win is not None:
            self._circle_win.close_permanently()
            self._circle_win = None
        super().closeEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos is not None:
            self.move(event.globalPos() - self._drag_pos)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SmartKeyboardPopup(selected_text="This minimalist sand theme is inspired by Notion.")
    window.show_near_cursor()
    sys.exit(app.exec_())
