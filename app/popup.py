import sys
import time
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QFrame, QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QCursor, QColor
from logger import log

# ── Minimalist Sand Palette ──────────────────────────────────────────────────
BG_COLOR        = "#FBFBFB"   # Paper White
SURFACE_COLOR   = "#FFFFFF"   # Pure White
CHIP_COLOR      = "#F4F4F3"   # Soft Sand / Light Tan
ACCENT_BLACK    = "#1A1A1A"   # Deep Charcoal Black
TEXT_MAIN       = "#37352F"   # Warm Dark Gray (Notion Style)
TEXT_MUTED      = "#807D78"   # Muted Warm Gray
BORDER_COLOR    = "#E8E8E8"   # Minimal soft border
SUCCESS_SAND    = "#435B4E"   # Muted Forest Green (matches the warm theme)

class SmartKeyboardPopup(QWidget):
    def __init__(self, selected_text: str = "", on_paste=None):
        super().__init__()
        self._selected_text = selected_text
        self._on_paste = on_paste
        self._translation_enabled = False
        self._current_relationship = 0
        self._target_lang = "hin_Deva"   # default: Hindi
        self._processing = False
        self._translation_engine = None
        self._grammar_engine = None
        self._process_start_time = 0.0

        self._build_ui()
        self._apply_styles()
        self._add_shadow()

    def set_translation_engine(self, engine): self._translation_engine = engine
    def set_grammar_engine(self, engine): self._grammar_engine = engine

    def _build_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(440)

        # Main Outer Container
        self.container = QWidget(self)
        self.container.setObjectName("MainContainer")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.addWidget(self.container)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        # ── Header ────────────────────────────────────────────────────────────
        header = QHBoxLayout()
        title_label = QLabel("SMART KEYBOARD")
        title_label.setFont(QFont("Inter", 9, QFont.Black))
        title_label.setStyleSheet(f"color: {ACCENT_BLACK}; letter-spacing: 2px;")
        
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {TEXT_MUTED}; border: none; font-size: 12px; }} QPushButton:hover {{ color: {ACCENT_BLACK}; }}")

        header.addWidget(title_label)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        # ── Pipeline / Modes ──────────────────────────────────────────────────
        mode_container = QHBoxLayout()
        mode_container.setSpacing(10)

        self._grammar_btn = QPushButton("English Refiner")
        self._grammar_btn.setFixedHeight(36)
        
        self._translation_btn = QPushButton("Translate")
        self._translation_btn.setCheckable(True)
        self._translation_btn.setFixedHeight(36)
        self._translation_btn.setCursor(Qt.PointingHandCursor)
        self._translation_btn.clicked.connect(self._on_translation_toggle)

        mode_container.addWidget(self._grammar_btn, 1)
        mode_container.addWidget(self._translation_btn, 1)
        layout.addLayout(mode_container)

        # ── Translation Options (language + context) ──────────────────────────
        self._rel_group = QWidget()
        self._rel_group.setVisible(False)
        rel_layout = QVBoxLayout(self._rel_group)
        rel_layout.setContentsMargins(0, 0, 0, 0)
        rel_layout.setSpacing(10)

        # Language selector
        rel_layout.addWidget(self._section_label("LANGUAGE"))
        lang_layout = QHBoxLayout()
        lang_layout.setSpacing(8)
        _LANGUAGES = [("Hindi", "hin_Deva"), ("Telugu", "tel_Telu")]
        self._lang_buttons = []
        for i, (label, code) in enumerate(_LANGUAGES):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setFixedHeight(28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, idx=i, c=code: self._set_language(idx, c))
            self._lang_buttons.append(btn)
            lang_layout.addWidget(btn)
        lang_layout.addStretch()
        rel_layout.addLayout(lang_layout)

        # Relationship / tone chips
        rel_layout.addWidget(self._section_label("CONTEXT"))
        chips_layout = QHBoxLayout()
        chips_layout.setSpacing(8)
        self._rel_buttons = []
        for i, name in enumerate(["Mother", "Friend", "Partner", "Stranger"]):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setFixedHeight(28)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, idx=i: self._set_relationship(idx))
            self._rel_buttons.append(btn)
            chips_layout.addWidget(btn)
        rel_layout.addLayout(chips_layout)
        layout.addWidget(self._rel_group)

        # ── Input Area ────────────────────────────────────────────────────────
        layout.addWidget(self._section_label("INPUT"))
        self._input_box = QTextEdit()
        self._input_box.setPlainText(self._selected_text)
        self._input_box.setFixedHeight(80)
        layout.addWidget(self._input_box)

        # ── Action Button ─────────────────────────────────────────────────────
        self._process_btn = QPushButton("PROCESS")
        self._process_btn.setFixedHeight(40)
        self._process_btn.setCursor(Qt.PointingHandCursor)
        self._process_btn.clicked.connect(self._on_process)
        layout.addWidget(self._process_btn)

        # ── Output Area ───────────────────────────────────────────────────────
        layout.addWidget(self._section_label("OUTPUT"))
        self._output_box = QTextEdit()
        self._output_box.setPlaceholderText("Refined text will appear here...")
        self._output_box.setFixedHeight(80)
        self._output_box.setReadOnly(True)
        layout.addWidget(self._output_box)

        # ── Footer Action ─────────────────────────────────────────────────────
        self._paste_btn = QPushButton("Paste to Application")
        self._paste_btn.setFixedHeight(40)
        self._paste_btn.setEnabled(False)
        self._paste_btn.setCursor(Qt.PointingHandCursor)
        self._paste_btn.clicked.connect(self._on_paste_clicked)
        layout.addWidget(self._paste_btn)

        hint = QLabel("Enter · Process   |   Ctrl+Enter · Paste")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 9px; text-transform: uppercase; letter-spacing: 1px;")
        layout.addWidget(hint)

    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 9px; font-weight: 800; letter-spacing: 1.2px;")
        return lbl

    def _add_shadow(self):
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setXOffset(0)
        shadow.setYOffset(8)
        shadow.setColor(QColor(0, 0, 0, 30))
        self.container.setGraphicsEffect(shadow)

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

        # Process Button - Solid Black
        self._process_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_BLACK}; color: {BG_COLOR};
                border: none; border-radius: 4px; font-size: 11px; font-weight: 800; letter-spacing: 1px;
            }}
            QPushButton:hover {{ background-color: #333333; }}
            QPushButton:disabled {{ background-color: {CHIP_COLOR}; color: {TEXT_MUTED}; }}
        """)

        # Paste Button - Muted Forest Green
        self._paste_btn.setStyleSheet(f"""
            QPushButton:enabled {{
                background-color: {SUCCESS_SAND}; color: white;
                border: none; border-radius: 4px; font-weight: 800;
            }}
            QPushButton:disabled {{
                background-color: {SURFACE_COLOR}; border: 1px solid {BORDER_COLOR}; color: {TEXT_MUTED};
            }}
        """)

    # ── Logic (Preserved) ─────────────────────────────────────────────────────
    def show_near_cursor(self):
        pos = QCursor.pos()
        screen = QApplication.primaryScreen().availableGeometry()
        x, y = pos.x() + 20, pos.y() + 20
        if x + self.width() > screen.right(): x = screen.right() - self.width() - 10
        if y + self.height() > screen.bottom(): y = screen.bottom() - self.height() - 10
        self.move(x, y)
        self.show()

    def set_selected_text(self, text):
        self._input_box.setPlainText(text)
        self._output_box.clear()
        self._paste_btn.setEnabled(False)
        self._reset_process_btn()

    def _set_language(self, idx, code):
        self._target_lang = code
        for i, btn in enumerate(self._lang_buttons): btn.setChecked(i == idx)

    def _set_relationship(self, idx):
        self._current_relationship = idx
        for i, btn in enumerate(self._rel_buttons): btn.setChecked(i == idx)

    def _on_translation_toggle(self):
        self._translation_enabled = self._translation_btn.isChecked()
        self._rel_group.setVisible(self._translation_enabled)
        self.adjustSize()

    def _on_process(self):
        if self._processing: return
        text = self._input_box.toPlainText().strip()
        if not text: return
        self._processing = True
        self._process_start_time = time.monotonic()
        self._process_btn.setEnabled(False)
        self._process_btn.setText("WORKING...")
        if self._translation_enabled: self._run_translation(text)
        else: self._run_grammar(text)

    def _run_translation(self, text):
        lang = self._target_lang
        if self._grammar_engine and self._grammar_engine.is_ready:
            self._grammar_engine.correct(text, on_result=lambda c: self._do_translate(c or text, lang), on_error=lambda e: self._do_translate(text, lang))
        else:
            self._do_translate(text, lang)

    def _do_translate(self, text, target_lang="hin_Deva"):
        if self._translation_engine:
            self._translation_engine.translate(text=text, target_lang=target_lang, on_result=self._on_bg_result, on_error=self._on_bg_error)

    def _run_grammar(self, text):
        if self._grammar_engine: self._grammar_engine.correct(text=text, on_result=self._on_bg_result, on_error=self._on_bg_error)

    def _on_bg_result(self, output):
        self._pending_output = output
        QTimer.singleShot(0, self._flush_pending_output)

    def _on_bg_error(self, message):
        self._pending_output = f"Error: {message}"
        QTimer.singleShot(0, self._flush_pending_output)

    def _flush_pending_output(self):
        out = getattr(self, "_pending_output", None)
        if out:
            self._output_box.setPlainText(out)
            self._paste_btn.setEnabled(True)
            self._reset_process_btn()
            self._processing = False

    def _reset_process_btn(self):
        self._process_btn.setEnabled(True)
        self._process_btn.setText("PROCESS")

    def _on_paste_clicked(self):
        res = self._output_box.toPlainText()
        if self._on_paste and res:
            self.hide()
            QTimer.singleShot(50, lambda: self._on_paste(res))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape: self.close()
        elif event.key() == Qt.Key_Return and not (event.modifiers() & Qt.ControlModifier): self._on_process()
        elif event.key() == Qt.Key_Return and (event.modifiers() & Qt.ControlModifier): self._on_paste_clicked()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton: self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and hasattr(self, "_drag_pos"): self.move(event.globalPos() - self._drag_pos)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SmartKeyboardPopup(selected_text="This minimalist sand theme is inspired by Notion.")
    window.show_near_cursor()
    sys.exit(app.exec_())