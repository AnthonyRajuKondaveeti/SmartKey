"""
grammar.py
----------
GrammarEngine — offline English grammar correction via pure ONNX Runtime.

No PyTorch or optimum required — models are loaded directly with
onnxruntime.InferenceSession, same approach as translation.py.

Primary model  : models/grammar/coedit-small_int8/
Fallback model : models/grammar/visheratin-tiny_int8/

Both are T5-based seq2seq models exported to ONNX by optimum.
Encoder I/O  : input_ids, attention_mask  →  last_hidden_state
Decoder I/O  : input_ids, encoder_hidden_states, encoder_attention_mask  →  logits, ...

Generation: greedy autoregressive decode (start=pad_id=0, stop=eos_id=1).
"""

import os
import time
import threading
import numpy as np
from collections import OrderedDict
from typing import Callable, Optional
from logger import log


# ── Constants ─────────────────────────────────────────────────────────────────

PRIMARY_SUBDIR    = "coedit-small_int8"
FALLBACK_SUBDIR   = "visheratin-tiny_int8"

COEDIT_PREFIX     = "Fix grammatical errors in this sentence: "
VISHERATIN_PREFIX = "grammar: "

CACHE_SIZE     = 20
MAX_INPUT_LEN  = 256   # per-sentence limit (model trained on sentences, not paragraphs)
MAX_OUTPUT_LEN = 256
DECODER_START  = 0     # T5 pad_token_id used as decoder start token
EOS_ID         = 1     # T5 eos_token_id
LOAD_TIMEOUT   = 60


# ── LRU Cache ─────────────────────────────────────────────────────────────────

class _LRUCache:
    """Thread-safe LRU cache."""

    def __init__(self, maxsize: int):
        self._cache   = OrderedDict()
        self._maxsize = maxsize
        self._lock    = threading.Lock()
        self.hits     = 0
        self.misses   = 0

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


# ── GrammarEngine ─────────────────────────────────────────────────────────────

class GrammarEngine:
    """
    Manages grammar correction model lifecycle and correction requests.

    Tries coedit-small_int8 first; falls back to visheratin-tiny_int8 if the
    primary model directory is missing or fails to load.

    Uses only onnxruntime + transformers tokenizer — no PyTorch required.
    """

    def __init__(self, model_dir: str = "models/grammar"):
        self._base_dir     = model_dir
        self._tokenizer    = None
        self._encoder      = None
        self._decoder      = None
        self._prefix       = COEDIT_PREFIX
        self._active_model = None
        self._cache        = _LRUCache(CACHE_SIZE)
        self._ready        = threading.Event()
        self._load_called  = False
        self._lock         = threading.Lock()
        log.debug(f"GrammarEngine created | base_dir={model_dir}")

    # ── Loading ───────────────────────────────────────────────────────────────

    def load(
        self,
        on_ready: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Load model on a background thread (primary → fallback)."""
        self._load_called = True
        log.info("GrammarEngine load requested — starting background thread")

        def _load():
            t_start = time.monotonic()
            try:
                self._load_model()
                elapsed = (time.monotonic() - t_start) * 1000
                self._ready.set()
                log.info(
                    f"GrammarEngine ready | model: {self._active_model} | "
                    f"prefix: {repr(self._prefix)} | loaded in {elapsed:.0f}ms"
                )
                if on_ready:
                    on_ready()
            except Exception as e:
                elapsed = (time.monotonic() - t_start) * 1000
                log.error(f"GrammarEngine load failed after {elapsed:.0f}ms: {e}")
                if on_error:
                    on_error(str(e))

        threading.Thread(target=_load, daemon=True, name="GrammarEngine-Load").start()

    def _load_model(self) -> None:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        candidates = [
            (PRIMARY_SUBDIR,  COEDIT_PREFIX),
            (FALLBACK_SUBDIR, VISHERATIN_PREFIX),
        ]

        last_error = None
        for subdir, prefix in candidates:
            path = os.path.join(self._base_dir, subdir)
            if not os.path.isdir(path):
                log.warning(f"GrammarEngine: model dir not found, skipping: {path}")
                last_error = f"Model directory not found: {path}"
                continue

            enc_path = os.path.join(path, "encoder_model.onnx")
            dec_path = os.path.join(path, "decoder_model.onnx")
            if not os.path.isfile(enc_path) or not os.path.isfile(dec_path):
                log.warning(f"GrammarEngine: missing encoder/decoder .onnx in {path}, skipping")
                last_error = f"Missing encoder_model.onnx or decoder_model.onnx in {path}"
                continue

            try:
                log.info(f"GrammarEngine: loading tokenizer from {subdir} ...")
                t0 = time.monotonic()
                self._tokenizer = AutoTokenizer.from_pretrained(path)
                log.info(f"GrammarEngine: tokenizer loaded in {(time.monotonic()-t0)*1000:.0f}ms")

                log.info(f"GrammarEngine: loading encoder from {subdir} ...")
                t0 = time.monotonic()
                self._encoder = ort.InferenceSession(enc_path)
                log.info(f"GrammarEngine: encoder loaded in {(time.monotonic()-t0)*1000:.0f}ms")

                log.info(f"GrammarEngine: loading decoder from {subdir} ...")
                t0 = time.monotonic()
                self._decoder = ort.InferenceSession(dec_path)
                log.info(f"GrammarEngine: decoder loaded in {(time.monotonic()-t0)*1000:.0f}ms")

                self._prefix       = prefix
                self._active_model = subdir
                return
            except Exception as e:
                log.warning(f"GrammarEngine: failed to load {subdir}: {e}")
                last_error = str(e)
                self._tokenizer = None
                self._encoder   = None
                self._decoder   = None

        raise RuntimeError(
            f"GrammarEngine: all models failed to load.\n"
            f"Last error: {last_error}\n"
            f"Ensure models/grammar/coedit-small_int8/ or "
            f"models/grammar/visheratin-tiny_int8/ contains valid .onnx files."
        )

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    @property
    def active_model(self) -> Optional[str]:
        """Subdir name of the loaded model, or None until ready."""
        return self._active_model

    # ── Correction ────────────────────────────────────────────────────────────

    def correct(
        self,
        text: str,
        on_result: Callable[[str], None],
        on_error:  Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Grammar-correct `text` asynchronously.
        on_result(corrected_text) called on a background thread when done.
        on_error(msg) called on failure.
        """
        log.info(
            f"Grammar correct requested | "
            f"{len(text)} chars: {repr(text[:80])}{'...' if len(text) > 80 else ''}"
        )

        def _run():
            try:
                on_result(self._correct_sync(text))
            except Exception as e:
                log.error(f"Grammar correction error: {e}")
                if on_error:
                    on_error(str(e))

        threading.Thread(target=_run, daemon=True, name="GrammarEngine-Infer").start()

    # ── Sentence splitting ────────────────────────────────────────────────────

    @staticmethod
    def _split_sentences(text: str) -> list:
        """
        Split text into correctable chunks:

        Pass 1 — sentence boundaries:
          Split on .  !  ?  followed by any whitespace.
          (No capital-letter requirement — handles casual writing.)

        Pass 2 — long-sentence splitting:
          If a sentence is still estimated to exceed MAX_INPUT_LEN tokens
          (approximated as len(chars) / 4), further split at clause
          boundaries: semicolons, or commas followed by a coordinating
          conjunction (and / but / or / so / yet / because / although /
          since / while).

        Pass 3 — hard chunk:
          If a clause is still too long after pass 2 (e.g. a massive
          compound sentence with no conjunctions), split at the nearest
          comma before the midpoint so each half can be corrected
          independently.
        """
        import re

        # ── Pass 1: sentence boundaries ────────────────────────────────────
        raw_parts = re.split(r'(?<=[.!?])\s+', text.strip())

        sentences: list = []
        for part in raw_parts:
            for line in part.splitlines():
                line = line.strip()
                if line:
                    sentences.append(line)

        # ── Pass 2: clause splitting for long sentences ────────────────────
        _CLAUSE_RE = re.compile(
            r';\s+|'
            r'(?<=,)\s+(?:and|but|or|so|yet|because|although|since|while)\s+',
            flags=re.IGNORECASE,
        )
        # coedit-small works reliably on ~50-token sentences.
        # Prefix (~10 tokens) + sentence should stay under 128 tokens total.
        # 200 chars ≈ 50 tokens — conservative but safe.
        _TOKEN_EST = 200

        expanded: list = []
        for s in sentences:
            if len(s) <= _TOKEN_EST:
                expanded.append(s)
                continue
            clauses = _CLAUSE_RE.split(s)
            if len(clauses) > 1:
                expanded.extend(c.strip() for c in clauses if c.strip())
            else:
                expanded.append(s)   # fall through to pass 3

        # ── Pass 3: hard split at comma near midpoint ──────────────────────
        result: list = []
        for chunk in expanded:
            while len(chunk) > _TOKEN_EST:
                mid   = len(chunk) // 2
                # search left of midpoint for a comma
                cut   = chunk.rfind(", ", 0, mid)
                if cut == -1:
                    cut = mid          # no comma — hard cut at midpoint
                else:
                    cut += 1           # keep the comma on the left half
                result.append(chunk[:cut].strip())
                chunk = chunk[cut:].strip()
            if chunk:
                result.append(chunk)

        return result if result else [text]

    # ── Sync correction ───────────────────────────────────────────────────────

    def _correct_sync(self, text: str) -> str:
        """
        Correct `text`. If it contains multiple sentences, each sentence is
        corrected individually (the model is sentence-level) then rejoined.
        Full-text result is cached so repeated identical inputs are instant.
        """
        text = text.strip()
        if not text:
            return ""

        # Full-text cache check
        cached = self._cache.get(text)
        if cached is not None:
            log.info(
                f"Grammar cache HIT "
                f"({self._cache.hits} hits / {self._cache.hits + self._cache.misses} total)"
            )
            return cached

        log.debug(f"Grammar cache MISS ({self._cache.misses} misses so far)")

        if not self._load_called:
            raise RuntimeError(
                "GrammarEngine.load() was never called. "
                "Call engine.load() at app startup before correcting."
            )
        if not self._ready.wait(timeout=LOAD_TIMEOUT):
            raise RuntimeError(
                f"Grammar model did not finish loading within {LOAD_TIMEOUT}s."
            )

        sentences = self._split_sentences(text)
        t_total = time.monotonic()

        if len(sentences) > 1:
            log.debug(f"Grammar: split into {len(sentences)} sentences")
        corrected_parts = [self._correct_single(s) for s in sentences]
        result = " ".join(corrected_parts)

        total_ms = (time.monotonic() - t_total) * 1000
        log.info(
            f"Grammar done in {total_ms:.0f}ms | {len(sentences)} sentence(s) | "
            f"model: {self._active_model} | "
            f"input: {repr(text[:60])}{'...' if len(text) > 60 else ''} | "
            f"output: {repr(result[:60])}{'...' if len(result) > 60 else ''}"
        )

        self._cache.put(text, result)
        return result

    def _correct_single(self, sentence: str) -> str:
        """Run inference on one sentence. Serialized via _lock; result cached."""
        # Sentence-level cache hit (avoids re-running the same sentence seen in
        # a different paragraph)
        cached = self._cache.get(sentence)
        if cached is not None:
            log.debug(f"Grammar sentence cache HIT: {repr(sentence[:60])}")
            return cached

        with self._lock:
            # Double-check inside lock
            cached = self._cache.get(sentence)
            if cached is not None:
                return cached

            prefixed = self._prefix + sentence
            log.debug(f"Grammar sentence input: {repr(prefixed[:120])}")
            t0 = time.monotonic()

            tok = self._tokenizer(
                prefixed,
                return_tensors = "np",
                padding        = True,
                truncation     = True,
                max_length     = MAX_INPUT_LEN,
            )
            input_ids      = tok["input_ids"].astype(np.int64)
            attention_mask = tok["attention_mask"].astype(np.int64)

            # Encode
            enc_out = self._encoder.run(
                None,
                {"input_ids": input_ids, "attention_mask": attention_mask},
            )
            encoder_hidden = enc_out[0]

            # Greedy autoregressive decode
            decoder_ids = np.array([[DECODER_START]], dtype=np.int64)
            steps = 0

            for _ in range(MAX_OUTPUT_LEN):
                logits = self._decoder.run(
                    None,
                    {
                        "input_ids":              decoder_ids,
                        "encoder_hidden_states":  encoder_hidden,
                        "encoder_attention_mask": attention_mask,
                    },
                )[0]
                next_id = int(np.argmax(logits[0, -1, :]))
                steps += 1
                if next_id == EOS_ID:
                    break
                decoder_ids = np.concatenate(
                    [decoder_ids, np.array([[next_id]], dtype=np.int64)], axis=1
                )

            elapsed = (time.monotonic() - t0) * 1000
            result = self._tokenizer.decode(decoder_ids[0], skip_special_tokens=True)

            # Strip echoed task prefix if present
            if result.startswith(self._prefix):
                result = result[len(self._prefix):].strip()

            # If the model produced nothing (ran to MAX_OUTPUT_LEN on a chunk
            # that was too long or too unusual), pass the original through.
            if not result.strip():
                log.warning(
                    f"Grammar returned empty output for input "
                    f"{repr(sentence[:60])} — passing through unchanged"
                )
                result = sentence

            log.debug(
                f"Grammar sentence done in {elapsed:.0f}ms | {steps} steps | "
                f"output: {repr(result[:60])}"
            )

        self._cache.put(sentence, result)
        return result

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def cache_info(self) -> dict:
        return {
            "size":         len(self._cache),
            "max_size":     CACHE_SIZE,
            "hits":         self._cache.hits,
            "misses":       self._cache.misses,
            "active_model": self._active_model,
            "hit_rate": (
                f"{self._cache.hits / (self._cache.hits + self._cache.misses):.0%}"
                if (self._cache.hits + self._cache.misses) > 0 else "n/a"
            ),
        }
