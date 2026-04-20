"""
translation.py
--------------
TranslationEngine — offline English → Indian language translation using the
IndicTrans2 ONNX INT8 model.

Supported target languages (pass as `target_lang`):
  "hin_Deva"  — Hindi (Devanagari)   [default]
  "tel_Telu"  — Telugu

Features:
  - Loads model once at startup, keeps in memory
  - Runs inference on a background thread (never blocks the UI)
  - LRU cache of last 20 translations (keyed by text + target language)
  - Falls back gracefully if model files are not yet downloaded
  - Detects "never loaded" vs "still loading" — no 30s silent hang

Usage:
    engine = TranslationEngine(model_dir="models/indictrans2")
    engine.load()   # call once at app start — runs on a bg thread

    engine.translate(
        text        = "I will be late today.",
        target_lang = "hin_Deva",          # or "tel_Telu" for Telugu
        on_result   = lambda out: print(out),
        on_error    = lambda err: print(f"Error: {err}"),
    )
"""

import os
import time
import threading
import numpy as np
from collections import OrderedDict
from typing import Callable, Optional
from logger import log

# IndicTrans2 outputs ALL Indic languages in Devanagari script internally.
# postprocess_batch transliterates Devanagari → actual target script.
# We use indic-transliteration (pure Python, no C++ required) for this.
import unicodedata

# Flores-200 lang code → indic_transliteration script constant
_FLORES_TO_SCRIPT = {
    "tel_Telu": "telugu",
    "kan_Knda": "kannada",
    "mal_Mlym": "malayalam",
    "tam_Taml": "tamil",
    "guj_Gujr": "gujarati",
    "pan_Guru": "gurmukhi",
    "ben_Beng": "bengali",
    "ory_Orya": "oriya",
}

try:
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate as _transliterate
    _TRANSLITERATION_AVAILABLE = True
except ImportError:
    _TRANSLITERATION_AVAILABLE = False


def _preprocess(text: str, src_lang: str, tgt_lang: str) -> str:
    """Normalise + inject IndicTrans2 language prefix."""
    text = unicodedata.normalize("NFC", text.strip())
    return f"{src_lang} {tgt_lang} {text}"


_TRAILING_PUNCT = {".", "?", "!", "।", "…"}

def _postprocess(text: str, tgt_lang: str, source: str = "") -> str:
    """Transliterate Devanagari model output → target script (no-op for hin_Deva).
    Also restores trailing punctuation that the model drops in greedy decoding."""
    if _TRANSLITERATION_AVAILABLE and tgt_lang != "hin_Deva":
        script = _FLORES_TO_SCRIPT.get(tgt_lang)
        if script:
            text = _transliterate(text, sanscript.DEVANAGARI, script)

    # Restore terminal punctuation dropped by greedy decoding.
    if source:
        src_end = source.rstrip()[-1] if source.rstrip() else ""
        out_end = text.rstrip()[-1]   if text.rstrip()   else ""
        if src_end in _TRAILING_PUNCT and out_end not in _TRAILING_PUNCT:
            text = text.rstrip() + src_end

    return text


# ── Constants ────────────────────────────────────────────────────────────────

CACHE_SIZE     = 20    # Number of translations to keep in memory
MAX_INPUT_LEN  = 256   # Token limit — matches IndicTrans2 training
MAX_OUTPUT_LEN = 256
NUM_BEAMS      = 4     # Beam search width (quality vs speed)
LOAD_TIMEOUT   = 60    # Seconds to wait for model load before giving up


# ── LRU Cache ────────────────────────────────────────────────────────────────

class _LRUCache:
    """Thread-safe LRU cache backed by an OrderedDict."""

    def __init__(self, maxsize: int):
        self._cache:   OrderedDict = OrderedDict()
        self._maxsize: int         = maxsize
        self._lock:    threading.Lock = threading.Lock()
        self.hits:   int = 0
        self.misses: int = 0

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if key not in self._cache:
                self.misses += 1
                return None
            self._cache.move_to_end(key)
            self.hits += 1
            return self._cache[key]

    def put(self, key: str, value: str) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def __len__(self) -> int:
        return len(self._cache)


# ── TranslationEngine ─────────────────────────────────────────────────────────

class TranslationEngine:
    """
    Manages the IndicTrans2 ONNX model lifecycle and translation requests.

    Thread safety:
      - load()           → runs on a dedicated daemon thread
      - translate()      → dispatches each request to its own daemon thread
      - _translate_sync  → serialized via _lock; cache read/write is also locked
    """

    def __init__(self, model_dir: str = "models/indictrans2"):
        self._model_dir   = model_dir
        self._tokenizer   = None
        self._encoder     = None
        self._decoder     = None
        self._cache       = _LRUCache(CACHE_SIZE)
        self._ready       = threading.Event()
        self._load_called = False
        self._lock        = threading.Lock()
        log.debug(f"TranslationEngine created | model_dir={model_dir}")

    # ── Loading ───────────────────────────────────────────────────────────────

    def load(
        self,
        on_ready: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Load the model on a background thread.
        on_ready() called when loading succeeds.
        on_error(msg) called on failure.
        """
        self._load_called = True
        log.info("Model load requested — starting background thread")

        def _load():
            t_start = time.monotonic()
            try:
                self._load_model()
                elapsed = (time.monotonic() - t_start) * 1000
                self._ready.set()
                log.info(f"Model loaded and ready in {elapsed:.0f}ms")
                if on_ready:
                    on_ready()
            except Exception as e:
                elapsed = (time.monotonic() - t_start) * 1000
                log.error(f"Model load failed after {elapsed:.0f}ms: {e}")
                if on_error:
                    on_error(str(e))

        t = threading.Thread(target=_load, daemon=True, name="TranslationEngine-Load")
        t.start()

    def _load_model(self) -> None:
        if not os.path.isdir(self._model_dir):
            raise FileNotFoundError(
                f"Model directory not found: {self._model_dir}\n"
                "Run scripts/SmartKeyboard_IndicTrans2_Convert.ipynb in Colab "
                "and place the downloaded files in models/indictrans2/"
            )

        enc_path = os.path.join(self._model_dir, "encoder_model.onnx")
        dec_path = os.path.join(self._model_dir, "decoder_model.onnx")
        if not os.path.isfile(enc_path) or not os.path.isfile(dec_path):
            raise FileNotFoundError(
                f"Expected encoder_model.onnx and decoder_model.onnx in {self._model_dir}.\n"
                "Run the Colab conversion notebook first."
            )

        import onnxruntime as ort
        from transformers import AutoTokenizer

        log.info("Loading tokenizer ...")
        t0 = time.monotonic()
        self._tokenizer = AutoTokenizer.from_pretrained(
            self._model_dir, trust_remote_code=True
        )
        log.info(f"Tokenizer loaded in {(time.monotonic()-t0)*1000:.0f}ms")

        log.info(f"Loading ONNX encoder ({enc_path}) ...")
        t0 = time.monotonic()
        self._encoder = ort.InferenceSession(enc_path)
        log.info(f"Encoder loaded in {(time.monotonic()-t0)*1000:.0f}ms")

        log.info(f"Loading ONNX decoder ({dec_path}) ...")
        t0 = time.monotonic()
        self._decoder = ort.InferenceSession(dec_path)
        log.info(f"Decoder loaded in {(time.monotonic()-t0)*1000:.0f}ms")

        if _TRANSLITERATION_AVAILABLE:
            log.info("indic-transliteration ready — Telugu/other scripts enabled")
        else:
            log.warning(
                "indic-transliteration not installed — Telugu will output Devanagari. "
                "Run: pip install indic-transliteration"
            )

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    # ── Translation ───────────────────────────────────────────────────────────

    def translate(
        self,
        text: str,
        on_result:   Callable[[str], None],
        on_error:    Optional[Callable[[str], None]] = None,
        target_lang: str = "hin_Deva",
    ) -> None:
        """
        Translate `text` from English to `target_lang` asynchronously.
        on_result(text) called on a background thread when done.
        on_error(msg)   called if something fails.
        Cache hit returns instantly without model inference.

        target_lang: "hin_Deva" (Hindi) | "tel_Telu" (Telugu)
        """
        log.info(
            f"Translate requested | lang={target_lang} | "
            f"input ({len(text)} chars): {repr(text[:100])}{'...' if len(text) > 100 else ''}"
        )

        def _run():
            try:
                result = self._translate_sync(text, target_lang)
                on_result(result)
            except Exception as e:
                log.error(f"Translation error: {e}")
                if on_error:
                    on_error(str(e))

        t = threading.Thread(target=_run, daemon=True, name="TranslationEngine-Infer")
        t.start()

    def _translate_sync(self, text: str, target_lang: str = "hin_Deva") -> str:
        text = text.strip()
        if not text:
            log.debug("Empty input — returning empty string")
            return ""

        # Cache key includes target language so Hindi/Telugu don't collide
        cache_key = f"{target_lang}:{text}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            log.info(
                f"Cache HIT ({self._cache.hits} hits / {self._cache.hits + self._cache.misses} total) | "
                f"result: {repr(cached[:80])}{'...' if len(cached) > 80 else ''}"
            )
            return cached

        log.debug(f"Cache MISS ({self._cache.misses} misses so far)")

        # FIX BUG 9: Distinguish "load() never called" from "still loading"
        if not self._load_called:
            raise RuntimeError(
                "TranslationEngine.load() was never called. "
                "Call engine.load() at app startup before translating."
            )

        # Wait for model to finish loading
        log.debug("Waiting for model to be ready ...")
        t_wait = time.monotonic()
        if not self._ready.wait(timeout=LOAD_TIMEOUT):
            raise RuntimeError(
                f"Translation model did not finish loading within {LOAD_TIMEOUT}s. "
                "Check that the model files in models/indictrans2/ are complete."
            )
        waited_ms = (time.monotonic() - t_wait) * 1000
        if waited_ms > 100:
            log.debug(f"Waited {waited_ms:.0f}ms for model to be ready")

        # Serialize inference
        with self._lock:
            # Double-check cache
            cached = self._cache.get(cache_key)
            if cached is not None:
                log.debug("Cache hit inside lock (another thread translated while waiting)")
                return cached

            # Pre-process: normalise + inject IndicTrans2 language prefix.
            processed = _preprocess(text, "eng_Latn", target_lang)

            log.debug(f"Tokenising: {repr(processed[:120])}")
            t0 = time.monotonic()
            tok_out = self._tokenizer(
                processed,
                return_tensors = "np",
                padding        = True,
                truncation     = True,
                max_length     = MAX_INPUT_LEN,
            )
            input_ids      = tok_out["input_ids"].astype(np.int64)
            attention_mask = tok_out["attention_mask"].astype(np.int64)
            log.debug(f"Tokenisation done in {(time.monotonic()-t0)*1000:.0f}ms | input_ids shape: {input_ids.shape}")

            # Encode
            t0 = time.monotonic()
            enc_out = self._encoder.run(
                None,
                {"input_ids": input_ids, "attention_mask": attention_mask},
            )
            encoder_hidden = enc_out[0]
            log.debug(f"Encoder done in {(time.monotonic()-t0)*1000:.0f}ms | hidden shape: {encoder_hidden.shape}")

            # Greedy autoregressive decode
            eos_id = self._tokenizer.eos_token_id or 2
            decoder_ids = np.array([[eos_id]], dtype=np.int64)
            t0 = time.monotonic()
            steps = 0

            for _ in range(MAX_OUTPUT_LEN):
                logits = self._decoder.run(
                    None,
                    {
                        "decoder_input_ids":     decoder_ids,
                        "encoder_hidden_states": encoder_hidden,
                        "encoder_attention_mask": attention_mask,
                    },
                )[0]
                next_id = int(np.argmax(logits[0, -1, :]))
                steps += 1
                if next_id == eos_id:
                    break
                decoder_ids = np.concatenate(
                    [decoder_ids, np.array([[next_id]], dtype=np.int64)], axis=1
                )

            decode_ms = (time.monotonic() - t0) * 1000
            log.debug(f"Decoder done in {decode_ms:.0f}ms | {steps} steps | output tokens: {decoder_ids.shape[1]}")

            raw = self._tokenizer.decode(decoder_ids[0], skip_special_tokens=True)
            # Post-process: transliterate + restore dropped terminal punctuation.
            result = _postprocess(raw, target_lang, source=text)

        self._cache.put(cache_key, result)

        log.info(
            f"Translation complete | lang={target_lang} | "
            f"input: {repr(text[:60])}{'...' if len(text) > 60 else ''} | "
            f"output: {repr(result[:80])}{'...' if len(result) > 80 else ''} | "
            f"decoder steps: {steps} | decode time: {decode_ms:.0f}ms"
            + (f" | raw: {repr(raw[:60])}" if raw != result else "")
        )
        return result

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def cache_info(self) -> dict:
        """IMPROVE 10: expose hit/miss counters for debugging."""
        info = {
            "size":     len(self._cache),
            "max_size": CACHE_SIZE,
            "hits":     self._cache.hits,
            "misses":   self._cache.misses,
            "hit_rate": (
                f"{self._cache.hits / (self._cache.hits + self._cache.misses):.0%}"
                if (self._cache.hits + self._cache.misses) > 0 else "n/a"
            ),
        }
        log.debug(f"Cache info: {info}")
        return info
