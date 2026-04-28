# smart_keyboard.spec
# -------------------
# PyInstaller spec for Smart Keyboard desktop app.
#
# Build command (run from project root with venv active):
#   pyinstaller smart_keyboard.spec
#
# Output: dist/SmartKeyboard/SmartKeyboard.exe  (--onedir bundle)
#
# MODELS (not bundled — too large at ~1.5 GB):
#   After building, copy your models folder next to the exe:
#     dist/SmartKeyboard/models/indictrans2/
#     dist/SmartKeyboard/models/grammar/
#     dist/SmartKeyboard/models/tone/hin/
#
# UPDATING:
#   Bump APP_VERSION below, rebuild, re-package MSIX, submit to Partner Center.

import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

APP_NAME    = "SmartKeyboard"
APP_VERSION = "1.0.0"

# ── Collect packages that PyInstaller misses with static analysis ─────────────
trans_datas, trans_binaries, trans_hiddenimports = collect_all("transformers")
sent_datas,  sent_binaries,  sent_hiddenimports  = collect_all("sentencepiece")
ort_datas,   ort_binaries,   ort_hiddenimports   = collect_all("onnxruntime")
indic_datas, indic_binaries, indic_hiddenimports = collect_all("indic_transliteration")
pyqt_datas,  pyqt_binaries,  pyqt_hiddenimports  = collect_all("PyQt5")

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ["app/main.py"],
    pathex=["app"],
    binaries=(
        ort_binaries +
        sent_binaries +
        trans_binaries +
        indic_binaries +
        pyqt_binaries
    ),
    datas=(
        ort_datas +
        sent_datas +
        trans_datas +
        indic_datas +
        pyqt_datas
    ),
    hiddenimports=[
        # onnxruntime
        "onnxruntime",
        "onnxruntime.capi._pybind_state",
        *ort_hiddenimports,
        # transformers
        "transformers",
        "transformers.models.albert",
        "transformers.models.albert.tokenization_albert",
        "transformers.models.auto.tokenization_auto",
        *trans_hiddenimports,
        # sentencepiece
        "sentencepiece",
        *sent_hiddenimports,
        # indic-transliteration
        *indic_hiddenimports,
        # PyQt5 — collect_all handles sip; list explicit submodules as fallback
        *pyqt_hiddenimports,
        "PyQt5.QtWidgets",
        "PyQt5.QtCore",
        "PyQt5.QtGui",
        "PyQt5.sip",
        "sip",                      # older PyQt5 versions expose bare sip
        # pynput Windows backend
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        # pystray Windows backend (dynamically loaded at runtime — missed by analysis)
        "pystray",
        "pystray._win32",
        # pywin32 — used by pyautogui and pyperclip Windows backends
        "win32api",
        "win32con",
        "win32gui",
        "win32clipboard",
        "pywintypes",
        # PIL (tray icon)
        "PIL.Image",
        "PIL.ImageDraw",
        # stdlib
        "ctypes",
        "ctypes.wintypes",
        "unicodedata",
        "numpy",
    ],
    # Tell PyInstaller not to follow optional imports it cannot satisfy.
    # selenium: imported optionally by transformers test utilities.
    # torch / tf / flax: transformers checks for them at runtime but we use ONNX.
    excludes=[
        "selenium", "torch", "torchvision", "tensorflow", "flax",
        "jax", "sklearn", "scipy", "pandas", "matplotlib",
        "IPython", "notebook", "jupyter", "sacrebleu", "sacremoses",
    ],
    hookspath=[],
    noarchive=False,
    optimize=1,
)

# ── Remove unused large packages to shrink bundle size ───────────────────────
EXCLUDES = {
    "matplotlib", "scipy", "pandas", "sklearn", "tensorflow", "torch",
    "torchvision", "jinja2", "IPython", "notebook", "jupyter",
    "sacrebleu", "sacremoses",
}
a.binaries  = [x for x in a.binaries  if x[0].split(".")[0] not in EXCLUDES]
a.datas     = [x for x in a.datas     if x[0].split(".")[0] not in EXCLUDES]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # compress binaries — reduces size ~30%
    console=False,      # no terminal window (windowed app)
    icon=None,          # replace with "assets/icon.ico" when you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)
