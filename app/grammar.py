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
import re
import time
import threading
import unicodedata
import concurrent.futures
import numpy as np
from typing import Callable, List, Optional, Tuple
from logger import log
from cache import LRUCache
from utils import ABBREV_RE


# ── Slang / abbreviation normalization ───────────────────────────────────────
# Applied before grammar correction so the model sees standard English.
# Ordered: longer/more-specific patterns first to avoid partial matches.
_SLANG: List[Tuple[str, str]] = [
    # Contractions & informal spellings
    (r"\bgonna\b",      "going to"),
    (r"\bwanna\b",      "want to"),
    (r"\bgotta\b",      "got to"),
    (r"\bkinda\b",      "kind of"),
    (r"\bsorta\b",      "sort of"),
    (r"\boutta\b",      "out of"),
    (r"\blotta\b",      "a lot of"),
    (r"\bcuz\b",        "because"),
    (r"\bcause\b",      "because"),
    (r"\bcos\b",        "because"),
    (r"\btho\b",        "though"),
    (r"\bthru\b",       "through"),
    (r"\bwud\b",        "would"),
    (r"\bcud\b",        "could"),
    (r"\bsud\b",        "should"),
    # Abbreviations
    (r"\bidk\b",        "I don't know"),
    (r"\bimo\b",        "in my opinion"),
    (r"\bimho\b",       "in my honest opinion"),
    (r"\bngl\b",        "not going to lie"),
    (r"\btbh\b",        "to be honest"),
    (r"\bbtw\b",        "by the way"),
    (r"\bfyi\b",        "for your information"),
    (r"\bomg\b",        "oh my god"),
    (r"\bsmh\b",        "shaking my head"),
    (r"\bik\b",         "I know"),
    (r"\brn\b",         "right now"),
    (r"\babt\b",        "about"),
    (r"\bpls\b",        "please"),
    (r"\bplz\b",        "please"),
    (r"\bthx\b",        "thanks"),
    (r"\bthnx\b",       "thanks"),
    (r"\bthanku\b",     "thank you"),
    (r"\bty\b",         "thank you"),
    (r"\bttyl\b",       "talk to you later"),
    (r"\btyl\b",        "talk to you later"),
    (r"\bb4\b",         "before"),
    (r"\b2day\b",       "today"),
    (r"\b2morrow\b",    "tomorrow"),
    (r"\b2nite\b",      "tonight"),
    # Single-letter informal substitutions (conservative — only unambiguous ones)
    (r"\bu\b",          "you"),
    (r"\bur\b",         "your"),
    # Fillers to remove
    (r"\blol\b",        ""),
    (r"\blmao\b",       ""),
    (r"\bhaha\b",       ""),
]

_SLANG_RE: List[Tuple] = [
    (re.compile(pat, re.IGNORECASE), repl) for pat, repl in _SLANG
]


def _normalize_slang(text: str) -> str:
    for pattern, replacement in _SLANG_RE:
        text = pattern.sub(replacement, text)
    # Collapse multiple spaces left by filler removal
    text = re.sub(r"  +", " ", text).strip()
    return text


# ── Constants ─────────────────────────────────────────────────────────────────

PRIMARY_SUBDIR    = "coedit-small_int8"
FALLBACK_SUBDIR   = "visheratin-tiny_int8"

COEDIT_PREFIX     = "Fix grammatical errors in this sentence: "
VISHERATIN_PREFIX = "grammar: "

CACHE_SIZE     = 200
MAX_TEXT_SIZE  = 5_000  # char limit — reject before splitting to avoid long executor queues
MAX_INPUT_LEN  = 256    # per-sentence limit (model trained on sentences, not paragraphs)
MAX_OUTPUT_LEN = 256
DECODER_START  = 0      # T5 pad_token_id used as decoder start token
EOS_ID         = 1      # T5 eos_token_id
LOAD_TIMEOUT   = 60

# Compiled once at import time — used by _split_sentences on every call.
_CLAUSE_RE = re.compile(
    r';\s+|(?<=,)\s+(?:and|but|or|so|yet|because|although|since|while)\s+',
    flags=re.IGNORECASE,
)

# Use half the logical cores, capped at 8 — avoids over-subscribing on
# 2-core laptops while taking advantage of more cores when available.
_ORT_THREADS = min(max(1, (os.cpu_count() or 4)), 8)


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
        self._cache            = LRUCache("grammar", CACHE_SIZE)
        self._ready            = threading.Event()
        self._failed           = False
        self._load_called      = False
        self._lock             = threading.Lock()
        self._prefix_token_len = 0   # set after tokenizer loads; used to tighten max_length
        self._executor         = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="GrammarEngine"
        )
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
                self._failed = True
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
                time.sleep(0)   # yield GIL so Qt can dispatch any pending slots

                sess_opts = ort.SessionOptions()
                sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                sess_opts.intra_op_num_threads = _ORT_THREADS

                log.info(f"GrammarEngine: loading encoder from {subdir} ...")
                t0 = time.monotonic()
                self._encoder = ort.InferenceSession(enc_path, sess_options=sess_opts)
                log.info(f"GrammarEngine: encoder loaded in {(time.monotonic()-t0)*1000:.0f}ms")
                time.sleep(0)   # yield GIL

                log.info(f"GrammarEngine: loading decoder from {subdir} ...")
                t0 = time.monotonic()
                self._decoder = ort.InferenceSession(dec_path, sess_options=sess_opts)
                log.info(f"GrammarEngine: decoder loaded in {(time.monotonic()-t0)*1000:.0f}ms")

                self._prefix           = prefix
                self._active_model     = subdir
                self._prefix_token_len = len(self._tokenizer.encode(prefix))
                log.debug(f"GrammarEngine: prefix token length = {self._prefix_token_len}")
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
    def is_failed(self) -> bool:
        return self._failed

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
        log.info(f"Grammar correct requested | {len(text)} chars")

        def _run():
            try:
                on_result(self._correct_sync(text))
            except Exception as e:
                log.error(f"Grammar correction error: {e}")
                if on_error:
                    on_error(str(e))

        self._executor.submit(_run)

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
        # ── Pass 1: sentence boundaries ────────────────────────────────────
        # Temporarily replace abbreviation periods (Dr., Mr., etc.) with a
        # null-byte placeholder so they don't trigger sentence splits.
        protected = ABBREV_RE.sub(lambda m: m.group(0)[:-1] + "\x00", text.strip())
        raw_parts = re.split(r'(?<=[.!?])\s+', protected)
        raw_parts = [p.replace("\x00", ".") for p in raw_parts]

        sentences: list = []
        for part in raw_parts:
            for line in part.splitlines():
                line = line.strip()
                if line:
                    sentences.append(line)

        # ── Pass 2: clause splitting for long sentences ────────────────────
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
        text = _normalize_slang(unicodedata.normalize("NFC", text.strip()))
        if not text:
            return ""

        if len(text) > MAX_TEXT_SIZE:
            log.warning(f"Grammar input too long ({len(text)} chars > {MAX_TEXT_SIZE}) — truncating")
            text = text[:MAX_TEXT_SIZE]

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

        # Correct line-by-line so \n boundaries are preserved in the output.
        # Within each line, _split_sentences handles long clauses as before.
        t_total = time.monotonic()
        raw_lines = text.splitlines()
        corrected_lines = []
        total_sentences = 0
        for line in raw_lines:
            if not line.strip():
                corrected_lines.append(line)
                continue
            sentences = self._split_sentences(line)
            total_sentences += len(sentences)
            corrected_lines.append(" ".join(self._correct_single(s) for s in sentences))
        result = "\n".join(corrected_lines)

        total_ms = (time.monotonic() - t_total) * 1000
        log.info(
            f"Grammar done in {total_ms:.0f}ms | {total_sentences} sentence(s) | "
            f"model: {self._active_model} | input: {len(text)} chars | output: {len(result)} chars"
        )

        self._cache.put(text, result)
        return result

    def _correct_single(self, sentence: str) -> str:
        """Run inference on one sentence. Serialized via _lock; result cached."""
        # Sentence-level cache hit (avoids re-running the same sentence seen in
        # a different paragraph)
        cached = self._cache.get(sentence)
        if cached is not None:
            log.debug(f"Grammar sentence cache HIT: {len(sentence)} chars")
            return cached

        with self._lock:
            # Double-check inside lock
            cached = self._cache.get(sentence)
            if cached is not None:
                return cached

            prefixed = self._prefix + sentence
            log.debug(f"Grammar sentence input: {len(prefixed)} chars")
            t0 = time.monotonic()

            # Reserve space for the prefix tokens so the sentence itself is not truncated.
            effective_max = max(32, MAX_INPUT_LEN - self._prefix_token_len)
            tok = self._tokenizer(
                prefixed,
                return_tensors = "np",
                padding        = True,
                truncation     = True,
                max_length     = effective_max,
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

            # Strip echoed task prefix if the model regurgitated it.
            if result.startswith(self._prefix):
                result = result[len(self._prefix):].strip()
            elif result.startswith('"' + self._prefix) or result.startswith("'" + self._prefix):
                result = result[1 + len(self._prefix):].strip()
            # Mid-output prefix embedding (model went off the rails on this chunk)
            # — fall back to original rather than truncating mid-sentence.
            elif self._prefix in result:
                log.warning(f"Model embedded prefix mid-output — passing sentence through unchanged")
                result = sentence

            # If the model produced nothing (ran to MAX_OUTPUT_LEN on a chunk
            # that was too long or too unusual), pass the original through.
            if not result.strip():
                log.warning(
                    f"Grammar returned empty output for input "
                    f"({len(sentence)} chars) — passing through unchanged"
                )
                result = sentence

            log.debug(
                f"Grammar sentence done in {elapsed:.0f}ms | {steps} steps | "
                f"output: {len(result)} chars"
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
