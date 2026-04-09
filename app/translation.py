"""
translation.py
--------------
TranslationEngine — offline English → Hindi translation using the
IndicTrans2 ONNX INT8 model.

Features:
  - Loads model once at startup, keeps in memory
  - Runs inference on a background thread (never blocks the UI)
  - LRU cache of last 20 translations with hit/miss counters
  - Falls back gracefully if model files are not yet downloaded
  - Detects "never loaded" vs "still loading" — no 30s silent hang

Usage:
    engine = TranslationEngine(model_dir="models/indictrans2")
    engine.load()   # call once at app start — runs on a bg thread

    engine.translate(
        text      = "I will be late today.",
        on_result = lambda hindi: print(hindi),
        on_error  = lambda err:   print(f"Error: {err}"),
    )
"""

import os
import time
import threading
import numpy as np
from collections import OrderedDict
from typing import Callable, Optional
from logger import log


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

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    # ── Translation ───────────────────────────────────────────────────────────

    def translate(
        self,
        text: str,
        on_result: Callable[[str], None],
        on_error:  Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Translate `text` from English to Hindi asynchronously.
        on_result(hindi) called on a background thread when done.
        on_error(msg)    called if something fails.
        Cache hit returns instantly without model inference.
        """
        log.info(f"Translate requested | input ({len(text)} chars): {repr(text[:100])}{'...' if len(text) > 100 else ''}")

        def _run():
            try:
                result = self._translate_sync(text)
                on_result(result)
            except Exception as e:
                log.error(f"Translation error: {e}")
                if on_error:
                    on_error(str(e))

        t = threading.Thread(target=_run, daemon=True, name="TranslationEngine-Infer")
        t.start()

    def _translate_sync(self, text: str) -> str:
        text = text.strip()
        if not text:
            log.debug("Empty input — returning empty string")
            return ""

        # Cache hit
        cached = self._cache.get(text)
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
            cached = self._cache.get(text)
            if cached is not None:
                log.debug("Cache hit inside lock (another thread translated while waiting)")
                return cached

            # Tokenise
            tagged_text = f"eng_Latn hin_Deva {text}"
            log.debug(f"Tokenising: {repr(tagged_text[:120])}")
            t0 = time.monotonic()
            tok_out = self._tokenizer(
                tagged_text,
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

            result = self._tokenizer.decode(decoder_ids[0], skip_special_tokens=True)

        self._cache.put(text, result)

        log.info(
            f"Translation complete | "
            f"input: {repr(text[:60])}{'...' if len(text) > 60 else ''} | "
            f"output: {repr(result[:80])}{'...' if len(result) > 80 else ''} | "
            f"decoder steps: {steps} | decode time: {decode_ms:.0f}ms"
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
