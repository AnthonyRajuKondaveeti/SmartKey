"""
popup.py
--------
Floating always-on-top popup window for the Smart Desktop Keyboard.

Features:
  - Mode toggle: Hindi Translation / English Grammar
  - Relationship chips: Mother / Friend / Partner / Stranger
  - Input preview (the captured selected text)
  - Process button → calls AI engine (placeholder in Week 1)
  - Output preview
  - Paste button → injects result into active app
  - Escape or click-outside to close
  - Appears near the mouse cursor
"""

import sys
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QButtonGroup,
    QFrame, QSizePolicy
)
from PyQt5.QtCore import Qt, QPoint, QTimer
from PyQt5.QtGui import QFont, QColor, QPalette, QCursor


# ── Colour palette ────────────────────────────────────────────────────────────
BG_COLOR       = "#1E1E2E"   # Dark background
SURFACE_COLOR  = "#2A2A3E"   # Card / section backgrounds
ACCENT_COLOR   = "#7C6AF7"   # Purple accent (active state)
TEXT_PRIMARY   = "#E0E0F0"   # Main text
TEXT_SECONDARY = "#9090B0"   # Dimmed text
SUCCESS_COLOR  = "#4CAF88"   # Paste button green
CHIP_BG        = "#3A3A52"   # Inactive relationship chip
BORDER_COLOR   = "#3E3E58"   # Subtle borders

RELATIONSHIPS = ["Mother", "Friend", "Partner", "Stranger"]
MODES         = ["🇮🇳  Hindi Translation", "📝  Grammar Polish"]


class SmartKeyboardPopup(QWidget):
    """
    The main floating popup.

    Usage:
        popup = SmartKeyboardPopup(selected_text="Hello world", on_paste=my_fn)
        popup.show_near_cursor()
    """

    def __init__(self, selected_text: str = "", on_paste=None, on_close=None):
        super().__init__()

        self._selected_text = selected_text
        self._on_paste       = on_paste    # Callable[[str], None] — called when Paste is clicked
        self._on_close       = on_close    # Callable[]  — called on close

        self._current_mode         = 0    # 0 = Hindi Translation, 1 = Grammar Polish
        self._current_relationship = 0    # index into RELATIONSHIPS

        self._build_ui()
        self._apply_styles()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.setWindowTitle("Smart Keyboard")
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool               # Keeps it out of the taskbar
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # ── Title bar ────────────────────────────────────────────────────────
        title_bar = QHBoxLayout()
        title_lbl = QLabel("⌨  Smart Keyboard")
        title_lbl.setFont(QFont("Segoe UI", 11, QFont.Bold))
        title_lbl.setStyleSheet(f"color: {TEXT_PRIMARY};")

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {TEXT_SECONDARY};
                border: none;
                font-size: 13px;
            }}
            QPushButton:hover {{ color: #FF6B6B; }}
        """)
        close_btn.clicked.connect(self.close)

        title_bar.addWidget(title_lbl)
        title_bar.addStretch()
        title_bar.addWidget(close_btn)
        root.addLayout(title_bar)

        root.addWidget(self._hline())

        # ── Mode toggle ───────────────────────────────────────────────────────
        mode_lbl = QLabel("Mode")
        mode_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10px;")
        root.addWidget(mode_lbl)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self._mode_buttons = []
        self._mode_group   = QButtonGroup(self)

        for i, label in enumerate(MODES):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setFixedHeight(34)
            btn.clicked.connect(lambda _, idx=i: self._set_mode(idx))
            self._mode_group.addButton(btn, i)
            self._mode_buttons.append(btn)
            mode_row.addWidget(btn)

        root.addLayout(mode_row)

        # ── Relationship chips ────────────────────────────────────────────────
        rel_lbl = QLabel("Tone / Relationship")
        rel_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10px;")
        root.addWidget(rel_lbl)

        rel_row = QHBoxLayout()
        rel_row.setSpacing(6)
        self._rel_buttons = []

        for i, name in enumerate(RELATIONSHIPS):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setFixedHeight(30)
            btn.clicked.connect(lambda _, idx=i: self._set_relationship(idx))
            self._rel_buttons.append(btn)
            rel_row.addWidget(btn)

        root.addLayout(rel_row)

        root.addWidget(self._hline())

        # ── Input preview ─────────────────────────────────────────────────────
        in_lbl = QLabel("Selected text")
        in_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10px;")
        root.addWidget(in_lbl)

        self._input_box = QTextEdit()
        self._input_box.setPlainText(self._selected_text)
        self._input_box.setFixedHeight(72)
        self._input_box.setReadOnly(False)   # User can edit before processing
        root.addWidget(self._input_box)

        # ── Process button ────────────────────────────────────────────────────
        self._process_btn = QPushButton("⚡  Process")
        self._process_btn.setFixedHeight(38)
        self._process_btn.clicked.connect(self._on_process)
        root.addWidget(self._process_btn)

        # ── Output preview ────────────────────────────────────────────────────
        out_lbl = QLabel("Result")
        out_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10px;")
        root.addWidget(out_lbl)

        self._output_box = QTextEdit()
        self._output_box.setPlaceholderText("Processed output will appear here…")
        self._output_box.setFixedHeight(72)
        self._output_box.setReadOnly(True)
        root.addWidget(self._output_box)

        # ── Paste button ──────────────────────────────────────────────────────
        self._paste_btn = QPushButton("📋  Paste into App")
        self._paste_btn.setFixedHeight(38)
        self._paste_btn.setEnabled(False)
        self._paste_btn.clicked.connect(self._on_paste_clicked)
        root.addWidget(self._paste_btn)

        # ── Keyboard shortcut hints ───────────────────────────────────────────
        hint = QLabel("Enter: Process  •  Ctrl+Enter: Paste  •  Esc: Close")
        hint.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        hint.setAlignment(Qt.AlignCenter)
        root.addWidget(hint)

        self.adjustSize()

    def _hline(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {BORDER_COLOR};")
        return line

    # ── Styles ────────────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {BG_COLOR};
                color: {TEXT_PRIMARY};
                font-family: 'Segoe UI', 'Inter', sans-serif;
                font-size: 12px;
            }}
            QTextEdit {{
                background-color: {SURFACE_COLOR};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER_COLOR};
                border-radius: 6px;
                padding: 6px;
                font-size: 12px;
            }}
            QPushButton {{
                background-color: {CHIP_BG};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER_COLOR};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {SURFACE_COLOR};
                border-color: {ACCENT_COLOR};
            }}
            QPushButton:checked {{
                background-color: {ACCENT_COLOR};
                border-color: {ACCENT_COLOR};
                color: white;
                font-weight: bold;
            }}
        """)

        # Process button — accent style
        self._process_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_COLOR};
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #9580FF; }}
            QPushButton:pressed {{ background-color: #6655DD; }}
        """)

        # Paste button — green style (disabled until output is ready)
        self._paste_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #2A3E35;
                color: {TEXT_SECONDARY};
                border: 1px solid #3E5248;
                border-radius: 6px;
                font-size: 13px;
            }}
            QPushButton:enabled {{
                background-color: {SUCCESS_COLOR};
                color: white;
                font-weight: bold;
                border: none;
            }}
            QPushButton:enabled:hover {{ background-color: #5DBF9A; }}
        """)

    # ── Behaviour ─────────────────────────────────────────────────────────────

    def show_near_cursor(self):
        """Position the popup close to the current mouse cursor, on screen."""
        cursor_pos = QCursor.pos()
        screen = QApplication.primaryScreen().availableGeometry()

        x = cursor_pos.x() + 20
        y = cursor_pos.y() + 20

        # Keep it on screen
        if x + self.width()  > screen.right():
            x = screen.right()  - self.width()  - 10
        if y + self.height() > screen.bottom():
            y = screen.bottom() - self.height() - 10

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def set_selected_text(self, text: str):
        self._selected_text = text
        self._input_box.setPlainText(text)
        self._output_box.clear()
        self._paste_btn.setEnabled(False)

    def _set_mode(self, idx: int):
        self._current_mode = idx
        for i, btn in enumerate(self._mode_buttons):
            btn.setChecked(i == idx)
        # Show/hide relationship row — only relevant for Translation mode
        visible = idx == 0
        for btn in self._rel_buttons:
            btn.setVisible(visible)

    def _set_relationship(self, idx: int):
        self._current_relationship = idx
        for i, btn in enumerate(self._rel_buttons):
            btn.setChecked(i == idx)

    def _on_process(self):
        """
        Week 1: Return a placeholder so the full paste flow can be tested.
        Week 2+: This will call TranslationEngine / GrammarEngine.
        """
        input_text = self._input_box.toPlainText().strip()
        if not input_text:
            self._output_box.setPlainText("⚠ Nothing to process — select some text first.")
            return

        mode = MODES[self._current_mode]
        rel  = RELATIONSHIPS[self._current_relationship]

        # ── Placeholder output (replace in Week 2) ──
        if self._current_mode == 0:
            placeholder = (
                f"[Hindi translation of: '{input_text[:40]}{'…' if len(input_text)>40 else ''}']\n"
                f"Tone: {rel} — AI model not loaded yet (Week 2)"
            )
        else:
            placeholder = (
                f"[Grammar-polished: '{input_text[:40]}{'…' if len(input_text)>40 else ''}']\n"
                f"AI model not loaded yet (Week 2)"
            )

        self._output_box.setPlainText(placeholder)
        self._paste_btn.setEnabled(True)

    def _on_paste_clicked(self):
        result = self._output_box.toPlainText()
        if self._on_paste and result:
            self.hide()                       # Hide popup before pasting
            QTimer.singleShot(150, lambda: self._on_paste(result))
        else:
            self.close()

    # ── Keyboard shortcuts ────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() == Qt.Key_Return and not (event.modifiers() & Qt.ControlModifier):
            self._on_process()
        elif event.key() == Qt.Key_Return and event.modifiers() & Qt.ControlModifier:
            if self._paste_btn.isEnabled():
                self._on_paste_clicked()

    # ── Allow window dragging ─────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and hasattr(self, '_drag_pos'):
            self.move(event.globalPos() - self._drag_pos)

    def closeEvent(self, event):
        if self._on_close:
            self._on_close()
        super().closeEvent(event)


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)

    def on_paste(text):
        print(f"\n[PASTE TRIGGERED]\n{text}\n")

    popup = SmartKeyboardPopup(
        selected_text="Hello, I wanted to let you know that I will be late today.",
        on_paste=on_paste,
    )
    popup.show_near_cursor()
    sys.exit(app.exec_())
