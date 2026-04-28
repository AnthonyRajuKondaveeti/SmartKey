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
import re
import time
import hashlib
import threading
import concurrent.futures
import numpy as np
from typing import Callable, Optional
from logger import log
from cache import LRUCache
from utils import ABBREV_RE

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

# Devanagari Unicode block — U+0900 to U+097F
_DEVANAGARI_RE = re.compile(r'[ऀ-ॿ]')


def _has_devanagari(text: str) -> bool:
    """True if the model output is in Devanagari and needs script conversion.

    The real IndicTrans2 ONNX model generates output in the target script
    directly (Telugu in Telugu Unicode, etc.).  Only call transliterate()
    when the output is actually Devanagari — otherwise transliterate()
    treats native-script characters as invalid Devanagari and drops them,
    producing empty / truncated text.
    """
    return bool(_DEVANAGARI_RE.search(text))


def _preprocess(text: str, src_lang: str, tgt_lang: str) -> str:
    """Normalise + inject IndicTrans2 language prefix."""
    text = unicodedata.normalize("NFC", text.strip())
    return f"{src_lang} {tgt_lang} {text}"


_TRAILING_PUNCT = {".", "?", "!", "।", "…"}

def _postprocess(text: str, tgt_lang: str, source: str = "") -> str:
    """Optionally transliterate Devanagari model output → target script.

    Transliteration is skipped when the model has already produced output
    in the correct target script (which the ONNX model does for Telugu, etc.).
    """
    if _TRANSLITERATION_AVAILABLE and tgt_lang != "hin_Deva":
        script = _FLORES_TO_SCRIPT.get(tgt_lang)
        if script and _has_devanagari(text):
            text = _transliterate(text, sanscript.DEVANAGARI, script)

    # IndicTrans2 consistently outputs a space before । — strip it.
    text = re.sub(r"\s+।", "।", text)

    # Restore terminal punctuation dropped by greedy/beam decoding.
    if source:
        src_end = source.rstrip()[-1] if source.rstrip() else ""
        out_end = text.rstrip()[-1]   if text.rstrip()   else ""
        if src_end in _TRAILING_PUNCT and out_end not in _TRAILING_PUNCT:
            text = text.rstrip() + src_end

    return text


# ── Constants ────────────────────────────────────────────────────────────────

_ORT_THREADS = min(max(1, (os.cpu_count() or 4)), 8)

CACHE_SIZE          = 200
MAX_INPUT_LEN       = 256   # Token limit — matches IndicTrans2 training
MAX_TEXT_SIZE       = 5_000  # Char limit — reject before tokenisation to avoid CPU spike
MAX_OUTPUT_LEN      = 384
NUM_BEAMS           = 4     # Beam search width (quality vs speed)
REPETITION_PENALTY  = 1.3   # Penalise repeated tokens during decoding
LOAD_TIMEOUT        = 60    # Seconds to wait for model load before giving up

# Split long inputs into per-sentence chunks above this char count.
# Each sentence is translated independently then rejoined — avoids truncation
# and keeps attention focused on one thought at a time.
_SENTENCE_SPLIT_THRESHOLD = 180  # ~45 tokens; typical 2-sentence message


def _split_for_translation(text: str) -> list:
    """Split text on sentence boundaries, preserving abbreviation periods."""
    protected = ABBREV_RE.sub(lambda m: m.group(0)[:-1] + "\x00", text.strip())
    parts = re.split(r'(?<=[.!?।])\s+', protected)
    return [p.replace("\x00", ".").strip() for p in parts if p.strip()] or [text]


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
        self._cache       = LRUCache("translation", CACHE_SIZE)
        self._ready       = threading.Event()
        self._failed      = False
        self._load_called = False
        self._lock        = threading.Lock()
        self._executor    = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="TranslationEngine"
        )
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
                self._failed = True
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
        # trust_remote_code is required — IndicTrans2 ships a custom tokenizer
        # class inside the model directory that transformers must execute.
        # Risk is contained: the model directory is local and user-controlled.
        self._tokenizer = AutoTokenizer.from_pretrained(
            self._model_dir, trust_remote_code=True
        )
        log.info(f"Tokenizer loaded in {(time.monotonic()-t0)*1000:.0f}ms")
        time.sleep(0)   # yield GIL so Qt can dispatch any pending slots

        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_opts.intra_op_num_threads = _ORT_THREADS

        log.info(f"Loading ONNX encoder ({enc_path}) ...")
        t0 = time.monotonic()
        self._encoder = ort.InferenceSession(enc_path, sess_options=sess_opts)
        log.info(f"Encoder loaded in {(time.monotonic()-t0)*1000:.0f}ms")
        time.sleep(0)   # yield GIL

        log.info(f"Loading ONNX decoder ({dec_path}) ...")
        t0 = time.monotonic()
        self._decoder = ort.InferenceSession(dec_path, sess_options=sess_opts)
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

    @property
    def is_failed(self) -> bool:
        return self._failed

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
        log.info(f"Translate requested | lang={target_lang} | input: {len(text)} chars")

        def _run():
            try:
                result = self._translate_sync(text, target_lang)
                on_result(result)
            except Exception as e:
                log.error(f"Translation error: {e}")
                if on_error:
                    on_error(str(e))

        self._executor.submit(_run)

    def _translate_sync(self, text: str, target_lang: str = "hin_Deva") -> str:
        text = text.strip()
        if not text:
            log.debug("Empty input — returning empty string")
            return ""

        if len(text) > MAX_TEXT_SIZE:
            log.warning(f"Input too long ({len(text)} chars > {MAX_TEXT_SIZE}) — truncating")
            text = text[:MAX_TEXT_SIZE]

        cache_key = f"{target_lang}:{hashlib.md5(text.encode()).hexdigest()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            log.info(
                f"Cache HIT ({self._cache.hits} hits / {self._cache.hits + self._cache.misses} total) | "
                f"result: {len(cached)} chars"
            )
            return cached

        log.debug(f"Cache MISS ({self._cache.misses} misses so far)")

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
            cached = self._cache.get(cache_key)
            if cached is not None:
                log.debug("Cache hit inside lock")
                return cached

            t0 = time.monotonic()

            # Preserve newline structure — translate each line independently
            # so multi-line messages (e.g. WhatsApp) map line-for-line.
            raw_lines = text.splitlines()
            translated_lines = []
            for line in raw_lines:
                line = line.strip()
                if not line:
                    translated_lines.append("")
                    continue
                # Within each line, split long sentences to avoid truncation.
                sentences = (
                    _split_for_translation(line)
                    if len(line) > _SENTENCE_SPLIT_THRESHOLD
                    else [line]
                )
                parts = [self._translate_sentence(s, target_lang) for s in sentences]
                translated_lines.append(" ".join(parts))

            result    = "\n".join(translated_lines)
            total_ms  = (time.monotonic() - t0) * 1000
            n_lines   = len(raw_lines)
            self._cache.put(cache_key, result)

        log.info(
            f"Translation complete | lang={target_lang} | {n_lines} line(s) | "
            f"{total_ms:.0f}ms | input: {len(text)} chars | output: {len(result)} chars"
        )
        return result

    def _translate_sentence(self, sentence: str, target_lang: str) -> str:
        """Translate a single sentence using beam search with repetition penalty."""
        processed = _preprocess(sentence, "eng_Latn", target_lang)
        tok_out = self._tokenizer(
            processed,
            return_tensors="np",
            padding=True,
            truncation=True,
            max_length=MAX_INPUT_LEN,
        )
        input_ids      = tok_out["input_ids"].astype(np.int64)
        attention_mask = tok_out["attention_mask"].astype(np.int64)

        enc_out = self._encoder.run(
            None, {"input_ids": input_ids, "attention_mask": attention_mask}
        )
        encoder_hidden = enc_out[0]

        eos_id     = self._tokenizer.eos_token_id or 2
        output_ids = self._beam_decode(encoder_hidden, attention_mask, eos_id)

        raw    = self._tokenizer.decode(output_ids, skip_special_tokens=True)
        result = _postprocess(raw, target_lang, source=sentence)
        return result

    def _beam_decode(self, encoder_hidden: np.ndarray,
                     attention_mask: np.ndarray, eos_id: int) -> list:
        """Beam search with repetition penalty. Returns best token id list."""
        # Each beam: [score (higher=better), token_ids]
        beams     = [(0.0, [eos_id])]
        completed = []

        for _ in range(MAX_OUTPUT_LEN):
            if not beams:
                break
            candidates = []
            for score, ids in beams:
                dec_ids = np.array([ids], dtype=np.int64)
                logits  = self._decoder.run(None, {
                    "decoder_input_ids":      dec_ids,
                    "encoder_hidden_states":  encoder_hidden,
                    "encoder_attention_mask": attention_mask,
                })[0][0, -1, :].copy()       # (vocab_size,)

                # Repetition penalty — downscale logits for already-seen tokens
                for prev_id in set(ids[1:]):  # skip BOS
                    if logits[prev_id] > 0:
                        logits[prev_id] /= REPETITION_PENALTY
                    else:
                        logits[prev_id] *= REPETITION_PENALTY

                # Stable log-softmax
                logits -= logits.max()
                log_probs = logits - np.log(np.exp(logits).sum())

                # Expand with top NUM_BEAMS next tokens
                top_ids = np.argpartition(log_probs, -NUM_BEAMS)[-NUM_BEAMS:]
                for nid in top_ids:
                    new_score = score + float(log_probs[nid])
                    new_ids   = ids + [int(nid)]
                    if int(nid) == eos_id:
                        # Length-normalise so shorter beams don't dominate
                        length        = max(len(new_ids) - 1, 1)
                        normed_score  = new_score / (length ** 0.6)
                        completed.append((normed_score, new_ids))
                    else:
                        candidates.append((new_score, new_ids))

            # Prune to top NUM_BEAMS
            candidates.sort(key=lambda x: -x[0])
            beams = candidates[:NUM_BEAMS]

            if len(completed) >= NUM_BEAMS:
                break

        if completed:
            completed.sort(key=lambda x: -x[0])
            return completed[0][1]
        return beams[0][1] if beams else [eos_id]

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def cache_info(self) -> dict:
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
