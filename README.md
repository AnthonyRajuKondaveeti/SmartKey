# Smart Keyboard

A Windows desktop application that brings AI-powered English grammar correction, multilingual translation (8 Indian languages), and persona-based Hindi tone rewriting into any application — triggered by a global hotkey, with no browser extension or copy-paste required.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Supported Languages](#supported-languages)
- [Architecture](#architecture)
- [Data Flow](#data-flow)
- [Project Structure](#project-structure)
- [Performance](#performance)
- [Dependencies](#dependencies)
- [Installation](#installation)
- [Running the App](#running-the-app)
- [Usage](#usage)
- [Configuration](#configuration)
- [Building an Executable](#building-an-executable)
- [Model Setup](#model-setup)
- [Module Reference](#module-reference)
- [Known Limitations](#known-limitations)

---

## Overview

Smart Keyboard runs as a system tray application. Select text in any app, press `Ctrl+Alt+K`, and a floating popup appears next to the target window. From there you can:

1. **Refine English** — grammar correction using a T5-based ONNX model
2. **Translate** — English to one of 8 Indian languages via IndicTrans2 ONNX
3. **Apply Tone** — rewrite Hindi output in four relationship personas

Click **Paste to Application** and the processed text is injected back into the original window via clipboard. **Automate mode** skips the popup entirely: the pipeline runs silently and pastes the result in-place, with a small floating circle as the only UI.

---

## Features

- **Global hotkey** — works in any focused window (browser, email, Word, WhatsApp Desktop, etc.)
- **Two operating modes** — Manual (popup with review step) and Automate (silent in-place replacement)
- **Grammar engine** — 85-rule slang normaliser + T5 seq2seq correction; short-sentence bypass (<20 chars) skips inference for fragments; automatic fallback to secondary model if primary fails
- **Translation engine** — IndicTrans2 INT8 ONNX encoder-decoder; beam search with repetition penalty and dynamic output-length cap
- **Tone engine** — IndicBART shared encoder + persona LoRA-merged decoders; 4 relationship personas for Hindi; auto-skips inputs over 150 chars
- **LRU cache** — 200-entry thread-safe in-session cache per engine; repeated identical inputs return instantly
- **Floating popup** — frameless, follows the target window as it moves, minimises to a 48×48 draggable circle hidden from the taskbar
- **System tray** — blue (ready), amber (loading), grey (disabled); right-click menu for toggle / hotkey change / quit
- **Settings dialog** — live key-capture hotkey recorder; language dropdown; automate toggle
- **Persistent settings** — hotkey, default language, and mode flags saved to `%APPDATA%\SmartKeyboard\settings.json`
- **Adaptive clipboard capture** — polls `GetAsyncKeyState` to detect modifier release; force-releases residual keys before injecting Ctrl+C to prevent Ctrl+Alt+C corruption
- **Sleep/wake recovery** — `WM_POWERBROADCAST` native event filter restarts the pynput hook after Windows resumes from sleep
- **Single-instance lock** — Win32 named mutex prevents duplicate processes
- **Rotating log file** — DEBUG-level file log at `%APPDATA%\SmartKeyboard\logs\smart_keyboard.log` (2 MB × 3 backups)
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

Tone rewriting is available for **Hindi only**. The tone UI is automatically hidden for other languages.

---

## Architecture

### Thread Model

Five long-lived threads run for the lifetime of the application:

| Thread | Owner | Responsibility |
|--------|-------|----------------|
| **Qt main** | `QApplication.exec_()` | UI rendering, signal dispatch, 500ms focus monitor timer |
| **HotkeyListener** | pynput `Listener` | Raw keyboard events; modifier tracking; bounded trigger queue (max 1) |
| **HotkeyWorker** | `threading.Thread` | Dequeues trigger events; calls `_on_hotkey`; starts TextCapture threads |
| **Tray** | pystray | System tray event loop and icon redraws |
| **ModelLoad × N** | `threading.Thread` per engine | Parallel ONNX session loading at startup |

Short-lived threads are spawned per-request by each engine's `ThreadPoolExecutor` (2 workers each).

### Cross-Thread Communication

All background → UI communication uses `pyqtSignal` with `Qt.QueuedConnection`, which posts slots to the Qt event queue so they always execute on the main thread:

| Signal | Direction | Carries |
|--------|-----------|---------|
| `hotkey_fired` | HotkeyWorker → Qt main | Empty string (popup shown immediately) |
| `text_captured` | TextCapture thread → Qt main | Captured selection text |
| `model_ready` | ModelLoad thread → Qt main | `(all_done, status_text, has_failures)` |
| `show_hk_dialog` | Any → Qt main | (opens settings dialog) |

Engine result callbacks communicate via a `queue.Queue` + `QTimer.singleShot(0, flush)` pattern. The notice tuple `(message, level)` travels through the queue alongside the output text and job sequence number, eliminating any cross-thread shared mutable state.

### Startup Sequence

```
main.py
  │
  ├─ Pre-import transformers + onnxruntime on main thread
  │   (prevents _ModuleLock deadlock between Grammar and Tone loading threads)
  │
  ├─ QApplication created
  ├─ SmartKeyboardApp.__init__()
  │   ├─ Load settings.json (validated against regex + allowlist)
  │   ├─ Create TranslationEngine + GrammarEngine (not yet loaded)
  │   └─ Wire Qt signals with QueuedConnection
  │
  ├─ run()
  │   ├─ TrayManager.start()           → Tray thread (daemon)
  │   ├─ start_hotkey_listener()       → HotkeyListener + HotkeyWorker threads (daemon)
  │   ├─ Install _PowerFilter          → listens for WM_POWERBROADCAST on main thread
  │   ├─ GrammarEngine.load()          → ModelLoad-Grammar thread (daemon)
  │   ├─ TranslationEngine.load()      → ModelLoad-Translation thread (daemon)
  │   └─ QApplication.exec_()          → Qt event loop (blocks here)
```

Tray icon is **amber** until both engines signal ready. If either engine fails, a one-time error dialog lists missing model files and paths.

---

## Data Flow

### Hotkey → Text Capture

```
User holds Ctrl+Alt+K
        │
        ▼
pynput _on_press / _on_release
  - tracks pressed modifier set
  - on trigger key: required_mods ⊆ pressed_mods → put(1) on bounded queue
        │
        ▼
HotkeyWorker dequeues → _on_hotkey() [on HotkeyWorker thread]
  ├─ record hotkey_time (monotonic)
  ├─ capture foreground HWND → self._target_hwnd
  ├─ emit hotkey_fired("") via QueuedConnection → _show_popup() on Qt main thread
  └─ spawn TextCapture daemon thread
        │
        ▼
TextCapture thread → get_selected_text()
  1. Save original clipboard content
  2. Clear clipboard to ""
  3. sleep(50ms) — settle
  4. Poll GetAsyncKeyState until Ctrl/Shift/Alt released (max 400ms)
  5. Force-release any still-held modifiers via synthetic keyUp events
     (prevents Ctrl+Alt being held → Ctrl+C becoming Ctrl+Alt+C)
  6. Inject Ctrl+C via pyautogui
  7. Poll clipboard at 20ms intervals (max 15 attempts = 300ms)
  8. Restore original clipboard content
  9. emit text_captured(selected) → _on_text_captured() on Qt main thread
```

### Processing Pipeline

```
_on_text_captured() [Qt main thread]
  └─ popup.set_selected_text(text)
        │
        ▼
[User clicks PROCESS — or automate mode triggers automatically]
        │
        ├─ mode = Grammar only
        │     └─ GrammarEngine.correct(text)
        │           ├─ Short-sentence bypass: sentences < 20 chars → pass through unchanged
        │           ├─ Slang normalisation (85 rules, case-insensitive)
        │           ├─ Sentence split (abbreviation-aware, clause-aware)
        │           ├─ Per-sentence T5 ONNX encode + greedy decode
        │           └─ Rejoin → on_result(corrected_text) [bg thread]
        │
        └─ mode = Translate
              ├─ GrammarEngine.correct(text)    [bg thread]
              │       (pipeline logs: "grammar done | Xms cumulative")
              │
              └─ TranslationEngine.translate(corrected, lang)   [bg thread]
                    ├─ MD5 cache key lookup → return instantly on HIT
                    ├─ Split multi-line input → translate each line
                    ├─ Per-sentence: split if > 180 chars
                    ├─ NFC normalise → prepend lang tokens → SentencePiece tokenise
                    ├─ ONNX encode
                    ├─ Beam search decode (NUM_BEAMS=2, dynamic step cap)
                    ├─ Optional transliteration Devanagari → target script
                    └─ on_result(translated) [bg thread]
                            │
                            └─ (if Hindi + persona selected + ToneEngine ready)
                                  ToneEngine.apply(translated, persona_idx)
                                    ├─ AlbertTokenizer + prepend <2hi>
                                    ├─ ONNX shared encoder + persona_prefix slice
                                    ├─ Persona decoder greedy decode
                                    └─ Postprocess: extract first sentence,
                                       strip tag-questions, reject garbled output
```

### Result → Paste

```
on_result(output) [bg thread]
  └─ _on_bg_result(output, notice)
        ├─ result_queue.put((output, notice, job_seq))
        └─ QTimer.singleShot(0, _flush_pending_output)  ← posts to Qt event loop
              │
              ▼
_flush_pending_output() [Qt main thread]
  ├─ Discard if seq ≠ _job_seq (stale result from superseded job)
  ├─ Show output in output box
  ├─ Log: "Pipeline done | Xms total"
  │
  ├─ Automate mode → QTimer.singleShot(30ms) → paste_text(output, target_hwnd)
  └─ Manual mode  → enable "Paste to Application" button
        │
        ▼
paste_text(text, hwnd)  [PasteThread daemon]
  1. pyperclip.copy(text)
  2. sleep(50ms)
  3. If hwnd valid + not foreground: SetForegroundWindow, poll 20×15ms
  4. pyautogui.hotkey("ctrl", "v")
```

### Popup Window Lifecycle

```
hotkey_fired → _show_popup()
  ├─ If popup exists and visible/minimized → update text, restore if minimized
  └─ Else → create SmartKeyboardPopup
       ├─ Manual mode:  position_near_window() → show() + raise_()
       └─ Automate mode: show_as_circle() → circle only, popup hidden

500ms QTimer → _check_focus()
  ├─ Foreground = target window  → show/reposition popup if hidden
  ├─ Foreground = our own window → no-op
  └─ Foreground = other window   → hide popup (if not minimized, not just restored)

popup.closeEvent()
  ├─ Destroy circle window if present
  └─ popup.destroyed signal → self._popup = None  (releases reference)
```

---

## Project Structure

```
smart-keyboard/
├── app/
│   ├── main.py              # Entry point; wires all components; single-instance lock
│   ├── hotkey_listener.py   # Global hotkey via pynput; bounded trigger queue
│   ├── hotkey_dialog.py     # Settings dialog (hotkey recorder + language + automate)
│   ├── popup.py             # Floating UI (PyQt5); circle minimise; history deque
│   ├── clipboard_manager.py # Adaptive capture (GetAsyncKeyState) + paste injection
│   ├── translation.py       # TranslationEngine — IndicTrans2 ONNX beam search
│   ├── grammar.py           # GrammarEngine — T5 ONNX; slang normaliser; fallback
│   ├── tone.py              # ToneEngine — IndicBART persona decoders
│   ├── tray.py              # System tray icon (pystray)
│   ├── cache.py             # Thread-safe LRU cache + appdata path helper
│   ├── utils.py             # Shared regex (ABBREV_RE)
│   ├── version.py           # __version__ string
│   └── logger.py            # Rotating file + console logging
├── scripts/
│   └── convert_translation_model.py   # Export IndicTrans2 → ONNX (Colab)
├── models/                  # Not included — download separately (see Model Setup)
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

## Performance

Measured on a mid-range Windows 11 laptop (Intel Core i5, no GPU). All inference uses ONNX Runtime CPU.

### Startup (model load)

| Engine | Load time |
|--------|-----------|
| Grammar tokenizer | ~110ms |
| Grammar encoder + decoder | ~900ms |
| Translation tokenizer | ~1400ms |
| Translation encoder | ~900ms |
| Translation decoder | ~3000ms |
| **Total (parallel)** | **~5400ms** |

### Per-request latency (warm, second run)

| Stage | Typical | Notes |
|-------|---------|-------|
| Clipboard capture | 400–700ms | Modifier wait + 1–3 clipboard polls |
| Grammar (1 sentence) | ~230ms | ~0ms for sentences < 20 chars (bypass) |
| Grammar (3 sentences) | ~810ms | 2 inferred + 1 bypassed |
| Translation (1 line) | ~900ms | 2-beam decode, ~60 steps |
| Translation (3 lines) | ~1800ms | Sequential per-line |
| Paste injection | ~165ms | Focus transfer + Ctrl+V |
| **Pipeline total (1 sentence, translate)** | **~1200ms** | Grammar + translation |
| **Pipeline total (3 sentences, translate)** | **~2700ms** | |

### Key tuning constants

| Parameter | Value | Effect |
|-----------|-------|--------|
| `NUM_BEAMS` | 2 | 2× faster than 4 beams; marginal quality trade-off |
| Dynamic output cap | `min(384, max(32, input_len×2+20))` | Prevents 384-step decode for short inputs |
| Grammar short bypass | 20 chars | Skips model for greetings and fragments |
| LRU cache size | 200 entries/engine | Repeated inputs return in <1ms |

---

## Dependencies

### UI & System

| Package | Version | Purpose |
|---------|---------|---------|
| PyQt5 | 5.15.11 | Desktop UI framework |
| pynput | 1.8.1 | Global hotkey listener |
| pyperclip | 1.11.0 | Clipboard read/write |
| PyAutoGUI | 0.9.54 | Keyboard injection (Ctrl+C, Ctrl+V) |
| pystray | 0.19.5 | System tray icon |
| Pillow | 10.3.0 | Tray icon rendering |

### AI Inference

| Package | Version | Purpose |
|---------|---------|---------|
| onnxruntime | 1.20.1 | ONNX model inference (CPU) |
| onnx | 1.17.0 | ONNX model utilities |
| transformers | 4.41.0 | AutoTokenizer, AlbertTokenizer |
| sentencepiece | 0.2.0 | Subword tokenisation |
| indic-transliteration | latest | Devanagari → other Indian scripts |
| numpy | 2.4.x | Tensor operations |

---

## Installation

**Requirements:** Python 3.10–3.12, Windows 10/11 (64-bit)

```bash
# Clone or unzip the project
cd smart-keyboard

# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate        # Windows CMD / PowerShell
# or
source venv/Scripts/activate  # Git Bash

# Install dependencies
pip install -r requirements.txt
```

---

## Running the App

```bash
# From the project root with venv active
python app/main.py
```

A keyboard icon appears in the system tray. It is **amber** while models load (~5s) and turns **blue** when ready. If a model fails to load, a dialog lists the missing files and the app continues in degraded mode.

---

## Usage

### Manual Mode (default)

1. Open any application (browser, email, text editor, etc.)
2. Select some English text
3. Press `Ctrl+Alt+K` (or your configured hotkey)
4. The popup appears next to your window with the captured text pre-filled
5. Choose a mode:
   - **English Refiner** — grammar correction only, no translation
   - **Translate** — select a target language from the dropdown
6. For Hindi translation, optionally pick a **Tone** persona (Mother / Friend / Partner / Stranger)
7. Click **PROCESS** (or press Enter)
8. Review the output, then click **Paste to Application** (or press Ctrl+Enter)

### Automate Mode

Enable via the settings dialog (`⚙`) or tray menu → Change Hotkey → Automate toggle.

1. Select text in any app
2. Press the hotkey — a small circle appears; no popup shown
3. The pipeline runs silently (grammar → translate → paste) and injects the result in-place
4. Click the circle to surface the popup and review the result
5. If paste fails, the popup opens automatically with the output ready to copy manually

### Popup Controls

| Control | Action |
|---------|--------|
| `—` (minimise) | Collapses popup to a 48×48 draggable circle |
| `✕` (close) | Closes popup; app keeps running in tray |
| `⚙` (settings) | Opens settings dialog |
| Copy | Copies output text to clipboard |
| History row | Re-loads a recent input/output pair into the boxes |
| Enter | Trigger PROCESS |
| Ctrl+Enter | Trigger Paste (manual mode) |
| Escape | Close popup |

---

## Configuration

Settings are stored in `%APPDATA%\SmartKeyboard\settings.json` and written automatically by the app:

```json
{
  "hotkey": "ctrl+alt+k",
  "default_lang": "hin_Deva",
  "automate": false,
  "translate": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `hotkey` | string | Key combo: `(ctrl\|shift\|alt)(+modifier)*+[a-z0-9]` |
| `default_lang` | string | Flores-200 language code (see Supported Languages table) |
| `automate` | bool | Enable silent in-place replacement mode |
| `translate` | bool | Whether Translate mode is the last-used selection |

### Changing the Hotkey

- Right-click the tray icon → **Change Hotkey**  
- Or click `⚙` in the popup header
- Press the desired combination (at least one modifier + a letter or digit)
- Valid examples: `ctrl+alt+k`, `ctrl+shift+h`, `shift+alt+d`

---

## Building an Executable

```bash
# With venv active, from the project root
pyinstaller smart_keyboard.spec
```

Output: `dist/SmartKeyboard/SmartKeyboard.exe` (directory bundle, no console window)

After building, copy model directories into the bundle:

```
dist/SmartKeyboard/models/indictrans2/
dist/SmartKeyboard/models/grammar/coedit-small_int8/
dist/SmartKeyboard/models/grammar/visheratin-tiny_int8/
dist/SmartKeyboard/models/tone/hin/
```

The spec excludes PyTorch, TensorFlow, scipy, sklearn, pandas, and matplotlib. UPX compression is enabled (~30% size reduction on the Python internals).

---

## Model Setup

Models are not included in the repository (~1.5 GB combined). Place them in the `models/` directory before running.

### Translation — IndicTrans2 ONNX INT8

```
models/indictrans2/
├── encoder_model.onnx
├── decoder_model.onnx
├── config.json
├── tokenizer_config.json
├── special_tokens_map.json
├── dict.SRC.json
└── dict.TGT.json
```

Export script: `scripts/convert_translation_model.py` (designed for Google Colab with GPU).
The tokenizer requires `trust_remote_code=True` because IndicTrans2 ships a custom tokenizer class inside the model directory.

### Grammar — T5 ONNX (two-model fallback)

The engine tries `coedit-small_int8` first. If the directory is missing or ONNX loading fails, it automatically falls back to `visheratin-tiny_int8`.

```
models/grammar/
├── coedit-small_int8/          # Primary — higher quality
│   ├── encoder_model.onnx
│   ├── decoder_model.onnx
│   ├── tokenizer.json
│   └── tokenizer_config.json
└── visheratin-tiny_int8/       # Fallback — faster, lower quality
    ├── encoder_model.onnx
    ├── decoder_model.onnx
    ├── tokenizer.json
    └── tokenizer_config.json
```

Task prefix used at inference:
- coedit-small: `"Fix grammatical errors in this sentence: "`
- visheratin-tiny: `"grammar: "`

### Tone — IndicBART Persona Decoders

```
models/tone/hin/
├── encoder_shared.onnx
├── decoder_mother.onnx
├── decoder_friend.onnx
├── decoder_gf_wife.onnx
├── decoder_stranger.onnx
├── persona_prefixes.npy        # shape (4, 24, 1024); row order: 0=mother, 1=stranger, 2=friend, 3=gf_wife
├── tokenizer.json
└── tokenizer_config.json
```

UI chip → model mapping:

| Chip index | Label | Decoder | prefix row |
|---|---|---|---|
| 0 | Mother | `decoder_mother.onnx` | 0 |
| 1 | Friend | `decoder_friend.onnx` | 2 |
| 2 | Partner | `decoder_gf_wife.onnx` | 3 |
| 3 | Stranger | `decoder_stranger.onnx` | 1 |

The tone engine is currently **not loaded at startup** (models are still in training). The UI shows a "coming soon" notice and persona chips are disabled. The engine code is fully implemented; loading is enabled by uncommenting the ToneEngine instantiation in `main.py`.

---

## Module Reference

### `app/main.py` — `SmartKeyboardApp`

Entry point. Wires all components. Holds the single-instance Win32 mutex.

| Method | Thread | Description |
|--------|--------|-------------|
| `run()` | Main | Starts tray, listener, model loads; enters Qt event loop |
| `_on_hotkey()` | HotkeyWorker | Records timestamp; emits hotkey_fired; spawns TextCapture |
| `_show_popup(text)` | Qt main | Creates or updates floating popup |
| `_on_text_captured(text)` | Qt main | Fills popup input; triggers automate pipeline |
| `_on_paste(result, cb)` | Qt main | Spawns PasteThread |
| `_check_focus()` | Qt main | 500ms timer; hides/shows popup based on foreground window |
| `_on_system_resume()` | Qt main | Restarts pynput listener after Windows sleep/wake |
| `_on_model_ready(name, error)` | ModelLoad | Updates shared ready-state dict; emits model_ready signal |
| `_save_settings(updates)` | Qt main | Merges and writes settings.json |

---

### `app/hotkey_listener.py` — `start_hotkey_listener()`

Parses `"ctrl+alt+k"` → `(required_mods, trigger_char)`. Tracks modifier state via `_on_press` / `_on_release`. On trigger: `put_nowait(1)` on a `Queue(maxsize=1)` — rapid re-presses beyond one pending event are silently dropped.

`stop()` drains any queued trigger before placing the `None` sentinel, guaranteeing the worker thread always exits cleanly (prevents zombie on hotkey restart after sleep/resume).

---

### `app/clipboard_manager.py`

| Function | Description |
|----------|-------------|
| `get_selected_text()` | Adaptive capture: polls `GetAsyncKeyState` until modifiers released (max 400ms), force-releases via synthetic keyUp, injects Ctrl+C, polls clipboard at 20ms intervals |
| `paste_text(text, hwnd)` | Focuses target HWND (20×15ms polling), copies text, injects Ctrl+V |
| `get_foreground_hwnd()` | Returns `GetForegroundWindow()` HWND |
| `get_window_rect(hwnd)` | Returns `(x, y, w, h)` or `None` if minimised/invalid |

---

### `app/translation.py` — `TranslationEngine`

| Constant | Value | Description |
|----------|-------|-------------|
| `MAX_INPUT_LEN` | 256 | Token limit per sentence |
| `MAX_TEXT_SIZE` | 5000 | Char hard-reject before tokenisation |
| `MAX_OUTPUT_LEN` | 384 | Absolute beam decode step ceiling |
| `NUM_BEAMS` | 2 | Beam width; raise to 3–4 for higher quality at cost of speed |
| `REPETITION_PENALTY` | 1.3 | Downscales logits for already-seen tokens |
| `CACHE_SIZE` | 200 | LRU entries keyed by `lang:md5(text)` |
| `_SENTENCE_SPLIT_THRESHOLD` | 180 chars | Split long lines before translation |

Dynamic step cap per sentence: `min(MAX_OUTPUT_LEN, max(32, input_tokens × 2 + 20))`.

Fail-fast: if the model failed to load at startup, `_translate_sync` raises immediately rather than blocking for `LOAD_TIMEOUT` seconds.

---

### `app/grammar.py` — `GrammarEngine`

| Constant | Value | Description |
|----------|-------|-------------|
| `CACHE_SIZE` | 200 | LRU entries keyed by full corrected text |
| `MAX_TEXT_SIZE` | 5000 | Char hard-reject |
| `MAX_INPUT_LEN` | 256 | Token limit per sentence |
| `_SHORT_BYPASS_CHARS` | 20 | Sentences shorter than this skip the model entirely |

Processing pipeline: slang normalise (85 rules) → sentence split (abbreviation-aware; clause-split pass for long sentences; hard midpoint-split pass) → per-sentence T5 ONNX encode + greedy decode → rejoin.

Short-sentence bypass: fragments under 20 chars (greetings, one-word sentences) pass through unchanged — each bypassed sentence saves ~300ms of inference.

Fail-fast on `_failed=True` before any model wait.

---

### `app/tone.py` — `ToneEngine`

| Constant | Value | Description |
|----------|-------|-------------|
| `TONE_MAX_CHARS` | 150 | Skip tone for lines over this length |
| `MAX_INPUT_LEN` | 64 | Matches training `MAX_SRC_LEN` |
| `MAX_OUTPUT_LEN` | 35 | Adaptive upper bound |
| `CACHE_SIZE` | 200 | LRU keyed by `persona_idx:text` |

Adaptive cap per persona: `max(15, min(35, src_len × mult + 5))` where `mult=2.2` for Mother/Partner, `2.0` for Friend/Stranger.

Postprocessing: filter non-Devanagari chars → extract first sentence → strip dangling conjunctions → strip tag-questions (`ना?`) if source was not a question → reject garbled output (vowel signs at word boundaries).

---

### `app/popup.py` — `SmartKeyboardPopup`

Frameless `Qt.Tool | Qt.WindowStaysOnTopHint` window with `WA_ShowWithoutActivating` to avoid stealing focus from the target app.

**Pipeline result flow:**
- Background callbacks call `_on_bg_result(output, notice)` which puts `(output, notice, job_seq)` on `self._result_queue`
- `QTimer.singleShot(0, _flush_pending_output)` posts to Qt event loop
- `_flush_pending_output` discards stale results (`seq ≠ _job_seq`) and renders the output

**Job sequencing:** `_job_seq` is incremented on every new PROCESS click or `set_selected_text()` call. Any in-flight result from the previous job is discarded when it arrives.

**UI layout:**

```
┌──────────────────────────────────────────┐
│  SMART KEYBOARD               ⚙  —  ✕  │
│  ⏳ Loading models...  (amber banner)   │
│  [English Refiner]    [Translate ✓]      │
│  LANGUAGE                                │
│  [Telugu              ▼]                 │
│  TONE  (Hindi only)                      │
│  [Mother] [Friend] [Partner] [Stranger]  │
│  INPUT ──────────────────────────────── │
│  [selected text appears here          ]  │
│  [PROCESS]                               │
│  OUTPUT ─────────────────── TONE: MOTHER │
│  [translated / corrected text         ]  │  ← Nirmala UI font (Indic support)
│  [Paste to Application]                  │
│  RECENT                                  │
│  [prev output 1]  [prev output 2]        │
└──────────────────────────────────────────┘
```

The output box uses **Nirmala UI** font (ships with Windows 8+) to correctly render all 8 supported Indic scripts without OpenType fallback warnings.

---

### `app/tray.py` — `TrayManager`

| Icon state | Colour | Condition |
|---|---|---|
| Enabled + ready | Blue | All models loaded |
| Loading | Amber | Any model still loading |
| Disabled | Grey | User toggled off via tray menu |

---

### `app/cache.py` — `LRUCache`

Thread-safe in-memory LRU cache backed by `collections.OrderedDict`. All `get` and `put` operations hold an internal `threading.Lock` for the full multi-step dict operation, preventing race conditions across the engine `ThreadPoolExecutor` workers.

`appdata_dir()` returns `%APPDATA%\SmartKeyboard` on Windows.

---

### `app/logger.py`

Global `log` singleton. Two handlers:

| Handler | Level | Destination |
|---------|-------|-------------|
| StreamHandler | INFO+ | Console (stdout) |
| RotatingFileHandler | DEBUG+ | `%APPDATA%\SmartKeyboard\logs\smart_keyboard.log` (2 MB × 3 backups) |

Key log lines for latency analysis:

```
INFO  Text captured | N chars | Xms since hotkey
INFO  Pipeline start | mode=translate | lang=tel_Telu | N chars
INFO  Pipeline | grammar done | Xms cumulative | translation starting
INFO  Translation complete | lang=tel_Telu | N line(s) | Xms | ...
INFO  Pipeline done | Xms total | status=ok | N chars out
DEBUG Modifier wait: Xms
DEBUG Injecting Ctrl+C | foreground hwnd: 0x????????
DEBUG Beam decode | X/Y steps | Xms | X.Xms/step
INFO  Grammar done in Xms | N sentence(s) (N inferred, N bypassed) | ...
```

---

## Known Limitations

- **Windows only** — uses `ctypes.windll`, pyautogui Windows backend, and Win32 API directly; does not run on macOS or Linux
- **Clipboard capture latency** — first capture after startup can be slow (1–2s) due to Windows clipboard initialisation or contention from clipboard-monitoring software (password managers, Office, etc.); subsequent captures are typically 400–700ms
- **Modifier hold time** — if the user holds the hotkey modifiers for more than 400ms, the adaptive wait times out; synthetic keyUp events fire before Ctrl+C injection as a fallback, but unusually long key holds may still occasionally interfere
- **Translation speed** — 3-line input takes ~1.8s at 2 beams on CPU; no GPU support in the current ONNX session setup
- **Tone not yet active** — ToneEngine is fully implemented but models are still in training; UI shows "coming soon" and persona chips are disabled
- **Tone input length** — when enabled, inputs over 150 chars per line are automatically skipped by the tone engine (out-of-distribution for the training data)
- **Grammar on heavy slang** — the model is conservative; very informal or fragmented text may be returned unchanged (fallback behaviour is explicit and logged)
- **Grammar prefix hallucination** — on unusual long inputs, the T5 model occasionally echoes the task prefix mid-output; the engine detects this and passes the original sentence through unchanged (logged as WARNING)
- **Models not bundled** — ~1.5 GB of ONNX models must be placed manually before running or after PyInstaller build
- **Telugu/other Indic rendering** — requires Nirmala UI (bundled with Windows 8+); older Windows builds may show square boxes for Indic characters
