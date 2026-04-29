"""
setup_wizard.py
---------------
First-run model setup check.

Called once at startup before engines are loaded. If any required ONNX model
files are missing, a blocking dialog is shown explaining exactly what to place
where. The app does not proceed to engine loading until the check passes or the
user explicitly chooses to continue in degraded mode.

Usage (from main.py):
    from setup_wizard import run_setup_if_needed
    if not run_setup_if_needed(model_dir, grammar_dir):
        sys.exit(0)
"""

import os
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFrame, QScrollArea, QWidget,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui  import QFont

# ── Palette (matches popup.py / hotkey_dialog.py) ────────────────────────────
_BG         = "#FBFBFB"
_BORDER     = "#E8E8E8"
_ACCENT     = "#1A1A1A"
_TEXT_MAIN  = "#37352F"
_TEXT_MUTED = "#807D78"
_CHIP       = "#F4F4F3"
_SUCCESS    = "#435B4E"
_WARN_BG    = "#FFF8DC"
_WARN_BORD  = "#E8D87A"
_WARN_TEXT  = "#7C6A00"
_ERR_BG     = "#FFF3F3"
_ERR_BORD   = "#E8C0C0"
_ERR_TEXT   = "#C0392B"


# ── Model file requirements ───────────────────────────────────────────────────

def _required_files(model_dir: str, grammar_dir: str) -> list:
    """
    Returns a list of (label, path, required) tuples describing every file
    the app needs. required=True means the engine cannot start without it.
    """
    return [
        # Translation — both files required
        (
            "Translation encoder",
            os.path.join(model_dir, "encoder_model.onnx"),
            True,
        ),
        (
            "Translation decoder",
            os.path.join(model_dir, "decoder_model.onnx"),
            True,
        ),
        # Grammar primary — preferred model
        (
            "Grammar encoder (primary)",
            os.path.join(grammar_dir, "coedit-small_int8", "encoder_model.onnx"),
            False,
        ),
        (
            "Grammar decoder (primary)",
            os.path.join(grammar_dir, "coedit-small_int8", "decoder_model.onnx"),
            False,
        ),
        # Grammar fallback — used if primary is absent
        (
            "Grammar encoder (fallback)",
            os.path.join(grammar_dir, "visheratin-tiny_int8", "encoder_model.onnx"),
            False,
        ),
        (
            "Grammar decoder (fallback)",
            os.path.join(grammar_dir, "visheratin-tiny_int8", "decoder_model.onnx"),
            False,
        ),
    ]


def _check(model_dir: str, grammar_dir: str) -> dict:
    """
    Returns a dict with:
      missing_required  — list of (label, path) for required missing files
      missing_optional  — list of (label, path) for optional missing files
      grammar_ok        — True if at least one full grammar variant is present
    """
    files = _required_files(model_dir, grammar_dir)
    missing_required = []
    missing_optional = []

    for label, path, required in files:
        if not os.path.isfile(path):
            (missing_required if required else missing_optional).append((label, path))

    # Grammar is OK if at least one complete variant exists
    primary_ok  = (
        os.path.isfile(os.path.join(grammar_dir, "coedit-small_int8",    "encoder_model.onnx")) and
        os.path.isfile(os.path.join(grammar_dir, "coedit-small_int8",    "decoder_model.onnx"))
    )
    fallback_ok = (
        os.path.isfile(os.path.join(grammar_dir, "visheratin-tiny_int8", "encoder_model.onnx")) and
        os.path.isfile(os.path.join(grammar_dir, "visheratin-tiny_int8", "decoder_model.onnx"))
    )

    # If neither grammar variant is fully present, treat it as a required gap
    if not primary_ok and not fallback_ok:
        grammar_missing = [
            (l, p) for l, p, req in files
            if not req and not os.path.isfile(p)
        ]
        # Move all grammar misses to required so the UI flags them clearly
        missing_required.extend(grammar_missing)
        missing_optional = [m for m in missing_optional if m not in grammar_missing]

    return {
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "grammar_ok":       primary_ok or fallback_ok,
    }


def models_ready(model_dir: str, grammar_dir: str) -> bool:
    """Quick check — True only when every required file exists and grammar is ready."""
    result = _check(model_dir, grammar_dir)
    return not result["missing_required"] and result["grammar_ok"]


# ── Dialog ────────────────────────────────────────────────────────────────────

class _SetupDialog(QDialog):

    def __init__(self, model_dir: str, grammar_dir: str, parent=None):
        super().__init__(parent)
        self._model_dir   = model_dir
        self._grammar_dir = grammar_dir
        self._result      = False   # True = proceed, False = exit
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        self.setWindowTitle("Smart Keyboard — Model Setup")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.setWindowModality(Qt.ApplicationModal)
        self.setFixedWidth(520)
        self.setStyleSheet(f"background: {_BG};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 24)
        outer.setSpacing(16)

        # Header
        title = QLabel("Model files required")
        title.setFont(QFont("Inter", 13, QFont.Bold))
        title.setStyleSheet(f"color: {_ACCENT};")
        outer.addWidget(title)

        subtitle = QLabel(
            "Smart Keyboard needs ONNX model files to work.\n"
            "Place them in the paths shown below, then click Check Again."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px;")
        outer.addWidget(subtitle)

        outer.addWidget(self._divider())

        # Status banner (updated by _refresh)
        self._banner = QLabel()
        self._banner.setWordWrap(True)
        self._banner.setAlignment(Qt.AlignCenter)
        self._banner.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 0.3px;"
            f"border-radius: 4px; padding: 8px 12px;"
        )
        outer.addWidget(self._banner)

        # Scrollable file list
        self._scroll_widget  = QWidget()
        self._scroll_layout  = QVBoxLayout(self._scroll_widget)
        self._scroll_layout.setContentsMargins(0, 0, 0, 0)
        self._scroll_layout.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidget(self._scroll_widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMaximumHeight(260)
        scroll.setStyleSheet("background: transparent;")
        outer.addWidget(scroll)

        outer.addWidget(self._divider())

        # Model paths reference
        paths_label = QLabel("Where to place the files:")
        paths_label.setStyleSheet(
            f"color: {_TEXT_MUTED}; font-size: 9px; font-weight: 800; letter-spacing: 1px;"
        )
        outer.addWidget(paths_label)

        paths_info = QLabel(
            f"  Translation:  {self._model_dir}\\\n"
            f"  Grammar:      {self._grammar_dir}\\coedit-small_int8\\\n"
            f"                {self._grammar_dir}\\visheratin-tiny_int8\\"
        )
        paths_info.setFont(QFont("Consolas", 8))
        paths_info.setWordWrap(True)
        paths_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        paths_info.setStyleSheet(
            f"color: {_ACCENT}; background: {_CHIP}; border: 1px solid {_BORDER};"
            f"border-radius: 4px; padding: 8px 10px;"
        )
        outer.addWidget(paths_info)

        source_label = QLabel(
            "Run scripts/convert_translation_model.py in Google Colab to export the models."
        )
        source_label.setWordWrap(True)
        source_label.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 10px;")
        outer.addWidget(source_label)

        outer.addWidget(self._divider())

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._continue_btn = QPushButton("Continue Anyway")
        self._continue_btn.setFixedHeight(34)
        self._continue_btn.setStyleSheet(
            f"QPushButton {{ background: {_CHIP}; color: {_TEXT_MUTED};"
            f"border: 1px solid {_BORDER}; border-radius: 4px;"
            f"font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ color: {_ACCENT}; }}"
        )
        self._continue_btn.setToolTip(
            "Start the app without models — processing will fail until models are placed."
        )
        self._continue_btn.clicked.connect(self._on_continue)

        self._exit_btn = QPushButton("Exit")
        self._exit_btn.setFixedHeight(34)
        self._exit_btn.setStyleSheet(
            f"QPushButton {{ background: {_CHIP}; color: {_TEXT_MAIN};"
            f"border: 1px solid {_BORDER}; border-radius: 4px;"
            f"font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: #EDECEB; }}"
        )
        self._exit_btn.clicked.connect(self._on_exit)

        self._check_btn = QPushButton("Check Again")
        self._check_btn.setFixedHeight(34)
        self._check_btn.setStyleSheet(
            f"QPushButton {{ background: {_SUCCESS}; color: white;"
            f"border: none; border-radius: 4px;"
            f"font-size: 11px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: #3a5043; }}"
        )
        self._check_btn.clicked.connect(self._refresh)

        btn_row.addWidget(self._continue_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._exit_btn)
        btn_row.addWidget(self._check_btn)
        outer.addLayout(btn_row)

    def _divider(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {_BORDER};")
        return line

    def _refresh(self):
        result = _check(self._model_dir, self._grammar_dir)

        # Clear existing file rows
        while self._scroll_layout.count():
            item = self._scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_missing = result["missing_required"] + result["missing_optional"]

        if not all_missing:
            # All good — auto-accept
            self._result = True
            self.accept()
            return

        # Render a row per missing file
        for label, path in all_missing:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 6, 8, 6)
            row_layout.setSpacing(8)

            dot = QLabel("✗")
            dot.setStyleSheet(f"color: {_ERR_TEXT}; font-size: 12px; font-weight: bold;")
            dot.setFixedWidth(16)

            info = QLabel(f"<b>{label}</b><br>"
                          f"<span style='font-family:Consolas;font-size:9px;color:{_TEXT_MUTED};'>"
                          f"{path}</span>")
            info.setWordWrap(True)
            info.setTextInteractionFlags(Qt.TextSelectableByMouse)

            row_layout.addWidget(dot, 0, Qt.AlignTop)
            row_layout.addWidget(info, 1)

            row.setStyleSheet(
                f"background: {_ERR_BG}; border: 1px solid {_ERR_BORD}; border-radius: 4px;"
            )
            self._scroll_layout.addWidget(row)

        # Update banner
        n = len(all_missing)
        self._banner.setText(f"{n} file{'s' if n > 1 else ''} missing")
        self._banner.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 0.3px;"
            f"background: {_ERR_BG}; border: 1px solid {_ERR_BORD};"
            f"color: {_ERR_TEXT}; border-radius: 4px; padding: 8px 12px;"
        )

        self.adjustSize()

    def _on_continue(self):
        self._result = True
        self.accept()

    def _on_exit(self):
        self._result = False
        self.reject()

    def closeEvent(self, event):
        self._result = False
        super().closeEvent(event)

    @property
    def should_proceed(self) -> bool:
        return self._result


# ── Public entry point ────────────────────────────────────────────────────────

def run_setup_if_needed(model_dir: str, grammar_dir: str) -> bool:
    """
    Show the setup wizard if any model files are missing.

    Returns True  → proceed with engine loading (all files found, or user clicked
                    'Continue Anyway' to start in degraded mode).
    Returns False → user clicked Exit; caller should sys.exit(0).

    Must be called after QApplication has been created but before exec_() starts.
    """
    if models_ready(model_dir, grammar_dir):
        return True

    # Show blocking dialog — dialog.exec_() runs its own event loop so the
    # Qt main thread is not blocked from processing paint events.
    dlg = _SetupDialog(model_dir, grammar_dir)
    dlg.exec_()
    return dlg.should_proceed
