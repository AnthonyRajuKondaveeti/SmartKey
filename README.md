# Smart Keyboard

A Windows desktop application that brings AI-powered English grammar correction, multilingual translation (8 Indian languages), and persona-based tone rewriting into any application — triggered by a global hotkey, no browser extension or copy-paste required.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Supported Languages](#supported-languages)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Dependencies](#dependencies)
- [Installation](#installation)
- [Running the App](#running-the-app)
- [Usage](#usage)
- [Configuration](#configuration)
- [Building an Executable](#building-an-executable)
- [Model Setup](#model-setup)
- [Testing & Quality Audit](#testing--quality-audit)
- [Module Reference](#module-reference)
- [Known Limitations](#known-limitations)

---

## Overview

Smart Keyboard runs as a system tray application. When you select text in any app and press the configured hotkey (`Ctrl+Shift+T` by default), a floating popup appears alongside the target window. From there you can:

1. **Refine English** — grammar correction using a T5-based ONNX model
2. **Translate** — English to one of 8 Indian languages via IndicTrans2 ONNX
3. **Apply Tone** — rewrite Hindi output in four relationship personas (Mother, Friend, Partner, Stranger)

Click **Paste to Application** and the processed text is injected back into the original window via clipboard.

---

## Features

- **Global hotkey** — works in any focused window (browser, email, Word, WhatsApp Desktop, etc.)
- **Grammar engine** — normalises slang, corrects grammar sentence-by-sentence; falls back to a secondary model if the primary is unavailable
- **Translation engine** — IndicTrans2 INT8-quantised ONNX encoder-decoder; beam search with repetition penalty
- **Tone engine** — IndicBART persona fine-tuned decoders; 4 relationship personas for Hindi output
- **LRU cache** — 200-entry in-session cache per engine avoids redundant inference
- **Floating popup** — frameless, minimises to a small circle; follows the target window as it moves
- **System tray** — blue (ready), amber (loading), grey (disabled); right-click menu for toggle / hotkey change / quit
- **Hotkey recorder** — live key-capture dialog; validates format before saving
- **Persistent settings** — hotkey and default language saved to `%APPDATA%/SmartKeyboard/settings.json`
- **Rotating log file** — debug-level file log at `%APPDATA%/SmartKeyboard/logs/smart_keyboard.log` (2 MB × 3 backups)
- **Standalone executable** — PyInstaller spec included; ships without Python runtime

---

## Supported Languages

| Code | Language | Script |
|------|----------|--------|
| `hin_Deva` | Hindi | Devanagari |
| `ben_Beng` | Bengali | Bengali |
| `mar_Deva` | Marathi | Devanagari |
| `tel_Telu` | Telugu | Telugu |
| `tam_Taml` | Tamil | Tamil |
| `kan_Knda` | Kannada | Kannada |
| `pan_Guru` | Punjabi | Gurmukhi |
| `mal_Mlym` | Malayalam | Malayalam |

Tone rewriting is currently available for **Hindi only**. The tone UI is hidden automatically for other languages.

---

## Architecture

### Threading Model

| Thread | Responsibility |
|--------|---------------|
| Main (Qt) | UI, signals, focus monitor timer |
| Hotkey thread | pynput global keyboard listener (daemon) |
| Tray thread | pystray event loop (daemon) |
| Model threads | TranslationEngine, GrammarEngine, ToneEngine each load on a separate daemon thread |
| Worker threads | Per-request ThreadPoolExecutor workers inside each engine |

### Signal Flow

```
[User selects text]
        │
        ▼
[Hotkey listener] ──hotkey_fired(text)──▶ [Qt main thread]
        │
        ▼
[SmartKeyboardPopup shown near target window]
        │
[User clicks PROCESS]
        │
        ├──▶ GrammarEngine.correct()  ──bg thread──▶ result via queue
        └──▶ TranslationEngine.translate()  ──bg thread──▶ result via queue
                                │
                        [User clicks PASTE]
                                │
                        paste_text(result, hwnd)  ──▶ Ctrl+V to target window
```

### Model Loading

All three engines load in parallel on daemon threads at startup. The tray icon stays **amber** until all engines are ready. If one engine fails to load, the others continue and the failed feature is gracefully disabled.

### Popup Window Lifecycle

A 500 ms Qt timer polls `GetForegroundWindow()`. The popup hides if the target window loses focus and reappears when focus returns. The popup repositions itself if the target window is moved.

---

## Project Structure

```
smart-keyboard/
├── app/
│   ├── __init__.py
│   ├── main.py              # Entry point; wires all components
│   ├── hotkey_listener.py   # Global hotkey via pynput
│   ├── hotkey_dialog.py     # Settings dialog (hotkey recorder + language selector)
│   ├── popup.py             # Floating UI (PyQt5)
│   ├── clipboard_manager.py # Selection capture & paste injection (Windows API)
│   ├── translation.py       # TranslationEngine (IndicTrans2 ONNX)
│   ├── grammar.py           # GrammarEngine (T5 ONNX, two-model fallback)
│   ├── tone.py              # ToneEngine (IndicBART persona decoders)
│   ├── tray.py              # System tray icon (pystray)
│   ├── cache.py             # LRU cache + appdata path helper
│   └── logger.py            # Rotating file + console logging
├── scripts/
│   └── convert_translation_model.py   # Colab script: export IndicTrans2 to ONNX
├── tests/
│   ├── audit.py             # End-to-end quality audit (grammar / translate / tone)
│   ├── test_week1.py
│   ├── test_week2.py
│   └── test_week3.py
├── models/                  # Place downloaded ONNX models here (see Model Setup)
│   ├── indictrans2/
│   ├── grammar/
│   │   ├── coedit-small_int8/
│   │   └── visheratin-tiny_int8/
│   └── tone/hin/
├── requirements.txt
├── smart_keyboard.spec      # PyInstaller build spec
└── README.md
```

---

## Dependencies

### Week 1 — UI & System

| Package | Version | Purpose |
|---------|---------|---------|
| pynput | 1.8.1 | Global hotkey listener |
| pyperclip | 1.11.0 | Clipboard read/write |
| PyAutoGUI | 0.9.54 | Keyboard/mouse injection |
| PyQt5 | 5.15.11 | Desktop UI framework |
| pystray | 0.19.5 | System tray icon |
| Pillow | 10.3.0 | Tray icon rendering |

### Week 2 — Translation Pipeline

| Package | Version | Purpose |
|---------|---------|---------|
| onnxruntime | 1.20.1 | ONNX model inference (CPU) |
| onnx | 1.17.0 | ONNX model utilities |
| transformers | 4.41.0 | Tokenizers and model helpers |
| sentencepiece | 0.2.0 | Subword tokenisation |
| indic-transliteration | latest | Devanagari → other Indian scripts |
| sacrebleu | 2.4.2 | Colab validation only (not needed at runtime) |

### Week 3 — Grammar & Tone

No additional runtime dependencies. Uses the same `onnxruntime` + `transformers` stack as Week 2.

---

## Installation

**Requirements:** Python 3.10–3.12 on Windows 10/11 (64-bit)

```bash
# Clone or unzip the project
cd smart-keyboard

# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate        # Windows CMD
# or
source venv/Scripts/activate  # Git Bash / WSL

# Install dependencies
pip install -r requirements.txt
```

---

## Running the App

```bash
# From the project root with venv active
python app/main.py
```

A keyboard icon appears in the system tray. The icon is **amber** while models load and turns **blue** when ready.

---

## Usage

1. Open any application (browser, email client, text editor, etc.)
2. Select some English text
3. Press `Ctrl+Shift+T` (or your configured hotkey)
4. The Smart Keyboard popup appears near your window
5. Choose a mode:
   - **English Refiner** — grammar correction only
   - **Translate** — select a target language from the dropdown
6. Click **PROCESS**
7. (Optional) Select a **Tone** persona for Hindi output: Mother, Friend, Partner, Stranger
8. Click **Paste to Application** — the result is injected back into the original window

### Popup Controls

| Control | Action |
|---------|--------|
| `—` (minimize) | Collapses popup to a small floating circle |
| `✕` (close) | Hides popup (app keeps running in tray) |
| `⚙` (settings) | Opens hotkey and language settings dialog |
| Copy icon | Copies output text to clipboard |
| History buttons | Re-loads a recent input/output pair |

---

## Configuration

Settings are stored in `%APPDATA%\SmartKeyboard\settings.json`:

```json
{
  "hotkey": "ctrl+shift+t",
  "default_lang": "hin_Deva"
}
```

### Changing the Hotkey

- Right-click the tray icon → **Change Hotkey**
- Or click the `⚙` gear icon inside the popup
- Press your desired key combination (must include at least one modifier + a letter/digit)
- Valid format: `(ctrl|shift|alt)(+(ctrl|shift|alt))*+[a-z0-9]`
- Examples: `ctrl+shift+t`, `ctrl+alt+k`, `shift+alt+d`

### Changing the Default Language

Open the settings dialog from the tray or popup gear icon and select from the language dropdown.

---

## Building an Executable

```bash
# With venv active, from the project root
pyinstaller smart_keyboard.spec
```

Output: `dist/SmartKeyboard/SmartKeyboard.exe` (directory bundle, no console window)

**After building**, manually copy the model directories into the bundle:

```
dist/SmartKeyboard/models/indictrans2/
dist/SmartKeyboard/models/grammar/coedit-small_int8/
dist/SmartKeyboard/models/grammar/visheratin-tiny_int8/
dist/SmartKeyboard/models/tone/hin/
```

The spec excludes PyTorch, TensorFlow, scipy, sklearn, pandas, and matplotlib — keeping the bundle lean. UPX compression is enabled (~30% size reduction).

---

## Model Setup

Models are **not** included in the repository (combined size ~1.5 GB). Place them in the `models/` directory at the project root before running.

### Translation — IndicTrans2 ONNX

```
models/indictrans2/
├── encoder_model_int8.onnx
├── decoder_model_int8.onnx
└── tokenizer/            # SentencePiece model + config
```

Export script: `scripts/convert_translation_model.py` (designed for Google Colab with GPU).

### Grammar — T5 ONNX (two-model fallback)

```
models/grammar/
├── coedit-small_int8/    # Primary model
│   ├── encoder_model.onnx
│   ├── decoder_model.onnx
│   └── tokenizer/
└── visheratin-tiny_int8/ # Fallback model
    ├── encoder_model.onnx
    ├── decoder_model.onnx
    └── tokenizer/
```

If the primary model directory is missing or fails to load, the engine automatically tries the fallback.

### Tone — IndicBART Persona Decoders

```
models/tone/hin/
├── encoder_shared.onnx
├── decoder_mother.onnx
├── decoder_friend.onnx
├── decoder_gf_wife.onnx
├── decoder_stranger.onnx
├── persona_prefixes.npy   # shape (4, 24, 1024)
└── tokenizer/             # AlbertTokenizer sentencepiece model
```

---

## Testing & Quality Audit

```bash
# From the project root with venv active
python tests/audit.py
```

The audit script exercises the full pipeline across a built-in corpus of short sentences, long paragraphs, and all four tone personas.

### Audit Sections

1. **Grammar Correction** — tests short and long inputs; flags output that matches input unchanged
2. **Translation** — English → Hindi and Telugu for all test inputs
3. **Tone** — applies all four personas to Hindi text; skips inputs over 150 characters
4. **Full Pipeline** — Grammar → Translation → Tone in sequence

### Output Flags

| Flag | Meaning |
|------|---------|
| `OK` | Success |
| `!!` | Warning (unchanged output or possible meaning drift) |
| `--` | Skipped (input too long for tone, etc.) |

---

## Module Reference

### `app/main.py` — `SmartKeyboardApp`

Entry point. Wires PyQt5, pystray, pynput, and model engines.

| Method | Description |
|--------|-------------|
| `run()` | Starts tray, hotkey listener, models; enters Qt event loop |
| `_on_hotkey()` | Fires on hotkey combo; captures selected text |
| `_show_popup(text)` | Creates/updates floating popup |
| `_on_paste(result)` | Pastes processed text back to target app |
| `_on_model_ready(name, error)` | Tracks engine load status |
| `_save_settings(updates)` | Persists hotkey and language to JSON |

---

### `app/hotkey_listener.py` — `start_hotkey_listener(on_trigger, hotkey_str)`

Registers a system-wide hotkey via pynput's raw keyboard listener. Handles Windows modifier-release bugs. Dispatches callbacks on a bounded background queue (max 1 pending event).

---

### `app/clipboard_manager.py`

| Function | Description |
|----------|-------------|
| `get_selected_text() → str` | Saves clipboard, injects Ctrl+C, polls for new content, restores original |
| `paste_text(text, hwnd)` | Focuses target window, copies text, injects Ctrl+V |
| `get_foreground_hwnd() → int` | Returns active window handle |
| `get_window_rect(hwnd)` | Returns `(x, y, w, h)` or `None` if minimised |

---

### `app/translation.py` — `TranslationEngine`

IndicTrans2 INT8-quantised ONNX encoder-decoder.

| Constant | Value |
|----------|-------|
| `MAX_INPUT_LEN` | 256 tokens |
| `MAX_TEXT_SIZE` | 5000 chars |
| `MAX_OUTPUT_LEN` | 384 tokens |
| `NUM_BEAMS` | 4 |
| `REPETITION_PENALTY` | 1.3 |
| `CACHE_SIZE` | 200 entries |

Processing pipeline: Unicode normalisation → language-token injection → SentencePiece tokenisation → ONNX encode → beam-search decode → optional transliteration (Devanagari → target script).

---

### `app/grammar.py` — `GrammarEngine`

T5-based seq2seq ONNX correction with automatic fallback.

| Constant | Value |
|----------|-------|
| `CACHE_SIZE` | 200 entries |
| `MAX_TEXT_SIZE` | 5000 chars |
| `MAX_INPUT_LEN` | 256 tokens per sentence |

Processing pipeline: 85-rule slang normalisation → sentence splitting (with abbreviation protection) → per-sentence T5 correction → rejoined output. Model prefix: `"Fix grammatical errors in this sentence: TEXT"`.

---

### `app/tone.py` — `ToneEngine`

IndicBART shared encoder + persona-specific LoRA-merged decoders.

| Persona chip | Decoder file | Prefix row |
|---|---|---|
| 0 — Mother | `decoder_mother.onnx` | row 0 |
| 1 — Friend | `decoder_friend.onnx` | row 2 |
| 2 — Partner | `decoder_gf_wife.onnx` | row 3 |
| 3 — Stranger | `decoder_stranger.onnx` | row 1 |

| Constant | Value |
|----------|-------|
| `TONE_MAX_CHARS` | 150 (auto-skip if longer) |
| `MAX_INPUT_LEN` | 64 tokens |
| `MAX_OUTPUT_LEN` | 35 tokens |
| `CACHE_SIZE` | 200 entries |

Postprocessing: extracts first sentence, strips tag-questions, rejects dangling clauses, validates ≥20% word overlap with source.

---

### `app/popup.py` — `SmartKeyboardPopup`

Frameless floating window. Minimises to a 48×48 circle (hidden from taskbar via `WS_EX_TOOLWINDOW`). Stores last 5 (input, output, timestamp) entries in a history deque.

**UI layout:**

```
┌──────────────────────────────────────────┐
│  SMART KEYBOARD               ⚙  —  ✕  │
│  ⏳ Loading models...  (status banner)  │
│  [English Refiner]  [Translate]          │
│  [Language: Hindi ▼]                     │
│  [TONE: Mother  Friend  Partner Stranger]│
│  INPUT  ──────────────────────────────  │
│  [                                    ]  │
│  [PROCESS]                               │
│  OUTPUT ──────────────────── [Copy]  ── │
│  [                                    ]  │
│  [Paste to Application]                  │
│  Recent: [entry 1] [entry 2] [entry 3]  │
└──────────────────────────────────────────┘
```

---

### `app/tray.py` — `TrayManager`

Manages pystray icon (daemon thread). Icon states: blue (enabled), amber (loading), grey (disabled). Menu: hotkey reminder, Enable/Disable toggle, Change Hotkey, Quit.

---

### `app/cache.py` — `PersistentLRUCache`

In-memory LRU cache backed by `collections.OrderedDict`. Tracks hit/miss counts. `appdata_dir()` returns the platform-appropriate config directory.

---

### `app/logger.py`

Global `log` object. Console: INFO+. File: DEBUG+, rotating 2 MB × 3 backups at `%APPDATA%/SmartKeyboard/logs/smart_keyboard.log`.

Log format:
```
2025-01-15 14:22:35.042  INFO     SmartKeyboard       Message here
```

---

## Known Limitations

- **Windows only** — uses pyautogui, pynput, and win32api Windows-specific backends; does not run on macOS or Linux.
- **Tone input length** — models were trained on short sentences (≤64 tokens). Inputs longer than 150 characters are automatically skipped by the tone engine.
- **Tone quality** — Partner and Stranger personas may show meaning drift or hallucination on negative-sentiment input due to limited training data diversity.
- **Long-text translation** — very long inputs (3+ sentences, >250 characters) may be truncated; IndicTrans2 was trained on sentence pairs.
- **Telugu tone** — no persona decoder models exist yet; the tone UI is hidden for Telugu and other non-Hindi languages.
- **Grammar on heavy slang** — the correction engine is conservative; heavily abbreviated or non-standard text may be returned unchanged.
- **Models not bundled** — the executable bundle (~1.5 GB of ONNX models) must be placed manually after running PyInstaller.
