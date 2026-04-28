"""
hotkey_dialog.py
----------------
SettingsDialog — combined language default + hotkey recorder.

Opens from the ⚙ button in the popup header or via the tray menu.
Both settings are saved together when the user clicks Save.
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QWidget, QComboBox, QFrame
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QIcon, QPixmap, QPainter
from version import __version__


def _keyboard_icon(size: int = 32) -> QIcon:
    """⌨ icon — same character and font as the minimise circle in popup.py."""
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.TextAntialiasing)
    f = QFont("Segoe UI Emoji, Apple Color Emoji, Noto Color Emoji", int(size * 0.72))
    p.setFont(f)
    p.drawText(px.rect(), Qt.AlignCenter, "⌨")
    p.end()
    return QIcon(px)


# ── Palette (matches popup.py) ────────────────────────────────────────────────
BG          = "#FBFBFB"
CHIP_COLOR  = "#F4F4F3"
ACCENT      = "#1A1A1A"
TEXT_MAIN   = "#37352F"
TEXT_MUTED  = "#807D78"
BORDER      = "#E8E8E8"
SUCCESS     = "#435B4E"
ERROR_BG    = "#FFF3F3"
ERROR_BORD  = "#E8C0C0"
ERROR_TEXT  = "#C0392B"

_LANGUAGES = [
    ("Hindi",     "hin_Deva"),
    ("Bengali",   "ben_Beng"),
    ("Marathi",   "mar_Deva"),
    ("Telugu",    "tel_Telu"),
    ("Tamil",     "tam_Taml"),
    ("Kannada",   "kan_Knda"),
    ("Punjabi",   "pan_Guru"),
    ("Malayalam", "mal_Mlym"),
]


def _make_key_chip(label: str) -> QLabel:
    lbl = QLabel(label)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setFont(QFont("Consolas", 11, QFont.Bold))
    lbl.setStyleSheet(
        f"background: {ACCENT}; color: white;"
        "border-radius: 5px; padding: 5px 12px;"
        "border-bottom: 3px solid #404040;"
    )
    return lbl


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {TEXT_MUTED}; font-size: 9px; font-weight: 800; letter-spacing: 1.2px;"
    )
    return lbl


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"color: {BORDER};")
    return line


class SettingsDialog(QDialog):
    """
    Combined settings dialog: default translation language + global hotkey.
    result_hotkey  — new hotkey string, e.g. "ctrl+shift+k" (None = unchanged)
    result_lang    — selected language code, e.g. "hin_Deva"
    """

    def __init__(
        self,
        current_hotkey:   str  = "ctrl+alt+k",
        current_lang:     str  = "hin_Deva",
        current_automate: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.result_hotkey   = None
        self.result_lang     = current_lang
        self.result_automate = None    # None = unchanged
        self._captured       = None
        self._current        = current_hotkey
        self._current_lang   = current_lang
        self._current_auto   = current_automate
        self._build_ui()

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle(f"Smart Keyboard v{__version__} — Settings")
        self.setWindowIcon(_keyboard_icon(32))
        self.setWindowFlags(
            Qt.Window | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint
        )
        self.setFixedWidth(380)
        self.setWindowModality(Qt.ApplicationModal)
        self.setStyleSheet(f"background: {BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # ── Default language ──────────────────────────────────────────────────
        layout.addWidget(_section_label("DEFAULT LANGUAGE"))

        self._lang_combo = QComboBox()
        self._lang_combo.setFixedHeight(34)
        self._lang_combo.setStyleSheet(f"""
            QComboBox {{
                background: {CHIP_COLOR}; color: {TEXT_MAIN};
                border: 1px solid {BORDER}; border-radius: 4px;
                padding: 4px 8px; font-size: 12px; font-weight: 600;
            }}
            QComboBox:hover {{ background: #EDECEB; }}
            QComboBox::drop-down {{ border: none; width: 24px; }}
            QComboBox QAbstractItemView {{
                background: white; color: {TEXT_MAIN};
                border: 1px solid {BORDER};
                selection-background-color: {CHIP_COLOR};
                selection-color: {ACCENT};
            }}
        """)
        for label, code in _LANGUAGES:
            self._lang_combo.addItem(label, userData=code)

        # Pre-select the current default
        for i, (_, code) in enumerate(_LANGUAGES):
            if code == self._current_lang:
                self._lang_combo.setCurrentIndex(i)
                break

        layout.addWidget(self._lang_combo)

        layout.addWidget(_divider())

        # ── Hotkey ────────────────────────────────────────────────────────────
        layout.addWidget(_section_label("GLOBAL HOTKEY"))

        instr = QLabel(
            "Press a new hotkey combination.\n"
            "Needs at least one modifier (Ctrl / Shift / Alt) plus a letter."
        )
        instr.setWordWrap(True)
        instr.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(instr)

        # Current hotkey chips
        cur_row = QHBoxLayout()
        cur_row.setSpacing(4)
        cur_row.setAlignment(Qt.AlignLeft)
        cur_label = QLabel("Current:")
        cur_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
        cur_row.addWidget(cur_label)
        for i, part in enumerate(self._current.split("+")):
            if i > 0:
                sep = QLabel("+")
                sep.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 10px;")
                cur_row.addWidget(sep)
            chip = QLabel(part.capitalize())
            chip.setFont(QFont("Consolas", 9))
            chip.setStyleSheet(
                f"background: {CHIP_COLOR}; color: {TEXT_MAIN};"
                f"border: 1px solid {BORDER}; border-radius: 3px; padding: 2px 7px;"
            )
            cur_row.addWidget(chip)
        cur_row.addStretch()
        layout.addLayout(cur_row)

        # Key-capture area
        self._capture_box = QWidget()
        self._capture_box.setFixedHeight(64)
        self._capture_box.setStyleSheet(
            f"background: {CHIP_COLOR}; border: 2px solid {BORDER}; border-radius: 6px;"
        )
        self._capture_layout = QHBoxLayout(self._capture_box)
        self._capture_layout.setContentsMargins(16, 8, 16, 8)
        self._capture_layout.setSpacing(8)
        self._capture_layout.setAlignment(Qt.AlignCenter)

        self._placeholder = QLabel("Press a new combination here (optional)…")
        self._placeholder.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        self._capture_layout.addWidget(self._placeholder)

        layout.addWidget(self._capture_box)

        hint = QLabel("Click this window first so it receives your keystrokes")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #C0BDB9; font-size: 9px;")
        layout.addWidget(hint)

        # ── Automate ──────────────────────────────────────────────────────────
        layout.addWidget(_divider())
        layout.addWidget(_section_label("AUTOMATE MODE"))

        auto_desc = QLabel(
            "Process and paste in-place automatically on hotkey.\n"
            "No popup — only the circle appears. Click it to review."
        )
        auto_desc.setWordWrap(True)
        auto_desc.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(auto_desc)

        auto_row = QHBoxLayout()
        auto_row.setContentsMargins(0, 4, 0, 0)
        off_lbl = QLabel("Off")
        off_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        self._auto_btn = QPushButton("On" if self._current_auto else "Off")
        self._auto_btn.setCheckable(True)
        self._auto_btn.setChecked(self._current_auto)
        self._auto_btn.setFixedSize(56, 28)
        self._auto_btn.clicked.connect(self._on_auto_toggle)
        self._auto_btn.setStyleSheet(f"""
            QPushButton {{
                background: {CHIP_COLOR}; color: {TEXT_MAIN};
                border: 1px solid {BORDER}; border-radius: 4px; font-weight: 600;
            }}
            QPushButton:checked {{
                background: {SUCCESS}; color: white; border: none;
            }}
        """)
        auto_row.addStretch()
        auto_row.addWidget(self._auto_btn)
        layout.addLayout(auto_row)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {CHIP_COLOR}; color: {TEXT_MAIN};"
            f"border: 1px solid {BORDER}; border-radius: 4px; font-weight: 600; }}"
            "QPushButton:hover { background: #EDECEB; }"
        )

        # Save is always enabled — language can be saved even without a new hotkey
        self._save_btn = QPushButton("Save")
        self._save_btn.setFixedHeight(36)
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background: {SUCCESS}; color: white;"
            "border: none; border-radius: 4px; font-weight: 700; }"
            "QPushButton:hover { background: #3a5043; }"
        )
        self._save_btn.clicked.connect(self._on_save)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._save_btn)
        layout.addLayout(btn_row)

        self.setFocusPolicy(Qt.StrongFocus)

    # ── Chip rendering ────────────────────────────────────────────────────────

    def _clear_capture_area(self):
        while self._capture_layout.count():
            item = self._capture_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_chips(self, parts: list):
        self._clear_capture_area()
        for i, part in enumerate(parts):
            if i > 0:
                sep = QLabel("+")
                sep.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px; font-weight: 600;")
                self._capture_layout.addWidget(sep)
            self._capture_layout.addWidget(_make_key_chip(part.capitalize()))
        self._capture_box.setStyleSheet(
            f"background: {CHIP_COLOR}; border: 2px solid {SUCCESS}; border-radius: 6px;"
        )

    def _show_error(self, message: str):
        self._clear_capture_area()
        lbl = QLabel(message)
        lbl.setStyleSheet(f"color: {ERROR_TEXT}; font-size: 11px;")
        self._capture_layout.addWidget(lbl)
        self._capture_box.setStyleSheet(
            f"background: {ERROR_BG}; border: 2px solid {ERROR_BORD}; border-radius: 6px;"
        )

    # ── Events ────────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)

    def keyPressEvent(self, event):
        key = event.key()

        if key == Qt.Key_Escape:
            self.reject()
            return

        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            return

        mods = event.modifiers()
        parts = []
        if mods & Qt.ControlModifier: parts.append("ctrl")
        if mods & Qt.ShiftModifier:   parts.append("shift")
        if mods & Qt.AltModifier:     parts.append("alt")

        k = event.key()
        if Qt.Key_A <= k <= Qt.Key_Z:
            char = chr(k).lower()
        elif Qt.Key_0 <= k <= Qt.Key_9:
            char = chr(k)
        else:
            self._show_error("Use a letter (A–Z) or digit (0–9) as the trigger key")
            self._captured = None
            return

        if not parts:
            self._show_error("Add at least one modifier: Ctrl, Shift, or Alt")
            self._captured = None
            return

        self._captured = "+".join(parts + [char])
        self._show_chips(parts + [char])

    def _on_auto_toggle(self):
        self._auto_btn.setText("On" if self._auto_btn.isChecked() else "Off")

    def _on_save(self):
        self.result_lang     = self._lang_combo.currentData()
        if self._captured:
            self.result_hotkey = self._captured
        self.result_automate = self._auto_btn.isChecked()
        self.accept()


# Keep the old name as an alias so any other imports don't break
HotkeyDialog = SettingsDialog
