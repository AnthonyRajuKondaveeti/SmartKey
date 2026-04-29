"""
tone.py
-------
ToneEngine — applies persona-specific tone to Hindi text using ONNX models
from models/tone/hin/.

Model layout (IndicBART fine-tuned with prefix + LoRA, exported per persona):
  encoder_shared.onnx      — shared encoder (prefix_embeds steers style)
  decoder_<name>.onnx      — LoRA-merged persona decoder (4 files)
  persona_prefixes.npy     — shape (4, 24, 1024); row order from training:
                               0=mother, 1=stranger, 2=friend, 3=gf_wife
  spiece.model + friends   — AlbertTokenizer

UI chip → persona mapping (chip index matches _PERSONA_DEFS order):
  0  Mother   → decoder_mother.onnx,   prefix row 0
  1  Friend   → decoder_friend.onnx,   prefix row 2
  2  Partner  → decoder_gf_wife.onnx,  prefix row 3
  3  Stranger → decoder_stranger.onnx, prefix row 1
"""

import os
import re
import time
import numpy as np
from typing import Callable, Optional
from logger import log
from engine_base import BaseEngine

# UI chip order → (label, decoder_file, prefix_row_in_npy)
# Row order in .npy fixed by training: {0:mother, 1:stranger, 2:friend, 3:gf_wife}
_PERSONA_DEFS = [
    ("Mother",   "decoder_mother.onnx",   0),
    ("Friend",   "decoder_friend.onnx",   2),
    ("Partner",  "decoder_gf_wife.onnx",  3),
    ("Stranger", "decoder_stranger.onnx", 1),
]

_HIN_LANG_TOKEN = "<2hi>"

_ORT_THREADS = min(max(1, (os.cpu_count() or 4)), 8)

CACHE_SIZE     = 200
MAX_INPUT_LEN  = 64   # matches training MAX_SRC_LEN
MAX_OUTPUT_LEN = 35   # matches training adaptive max (upper bound)
LOAD_TIMEOUT   = 60

# Tone model was trained on short sentences (~1-2 sentences, ≤64 tokens).
# Inputs longer than this in characters are out-of-distribution — skip tone.
TONE_MAX_CHARS = 150

# Devanagari Unicode block + allowed punctuation
_DEVA_RE = re.compile(r'[^ऀ-ॿ \t।?!,]')

# Tag-question suffixes the model adds to statements (e.g. "ना?", "है ना?")
_TAG_Q_RE = re.compile(r'\s*[ऀ-ॿ]*\s*ना\s*\??\s*$')

# Strip trailing conjunctions that are clearly dangling (comma before them means
# the decoder started a new clause but didn't finish it).  Pronouns (आप, वो, वह)
# excluded — they can legitimately end a statement.
_DANGLE_RE = re.compile(
    r'[,،]\s*(पर|और|लेकिन|मगर|क्योंकि|तो|जो|कि)\s*$'
)

# ॊ (U+094A short-o vowel sign) at word-start is a strong garble indicator in Hindi
_GARBLE_RE = re.compile(r'(?<!\S)[ी-ॏॢॣ]')


def _is_garbled(text: str) -> bool:
    """True if text contains tokens that look like corrupted Devanagari (vowel signs
    appearing at word boundaries, which never happen in well-formed Hindi)."""
    return bool(_GARBLE_RE.search(text))


def _postprocess(text: str, fallback: str, source: str = "") -> str:
    """Filter Devanagari, extract first sentence, strip model artefacts."""
    cleaned = _DEVA_RE.sub("", text).strip()

    # Reject garbled output (misplaced vowel-sign tokens)
    if _is_garbled(cleaned):
        return fallback

    m = re.search(r'[।?!]', cleaned)
    result = cleaned[:m.end()].strip() if m else cleaned

    # Strip trailing dangling clause left incomplete by the decoder
    result = _DANGLE_RE.sub("", result).rstrip()

    # Restore statement type if model added a tag-question
    if source:
        src_terminal = source.rstrip()[-1] if source.rstrip() else ""
        out_terminal = result[-1] if result else ""
        if src_terminal != "?" and out_terminal == "?":
            result = _TAG_Q_RE.sub("", result).rstrip()
            if src_terminal == "।" and not result.endswith("।"):
                result += "।"

    return result or fallback


class ToneEngine(BaseEngine):
    """Applies persona tone to Hindi text using shared encoder + persona decoders."""

    def __init__(self, model_dir: str = "models/tone/hin"):
        super().__init__(
            cache_namespace = "tone",
            cache_size      = CACHE_SIZE,
            thread_prefix   = "ToneEngine",
            load_timeout    = LOAD_TIMEOUT,
        )
        self._model_dir     = model_dir
        self._tokenizer     = None
        self._encoder       = None
        self._decoders      = {}     # persona_idx → ort.InferenceSession
        self._prefixes      = None   # np.ndarray shape (4, 24, 1024)
        self._bos_id        = None
        self._stop_ids      = set()
        self._prefix_slices = None
        log.debug(f"ToneEngine created | model_dir={model_dir}")

    def _load_model(self) -> None:
        if not os.path.isdir(self._model_dir):
            raise FileNotFoundError(f"Tone model dir not found: {self._model_dir}")

        import onnxruntime as ort
        from transformers import AlbertTokenizer

        log.info("Loading tone tokenizer ...")
        t0 = time.monotonic()
        self._tokenizer = AlbertTokenizer.from_pretrained(self._model_dir)
        log.info(f"Tone tokenizer loaded in {(time.monotonic()-t0)*1000:.0f}ms")

        # BOS = <2hi> token (signals "generate Hindi"); stop on eos / <2hi> / pad
        self._bos_id = self._tokenizer.convert_tokens_to_ids(_HIN_LANG_TOKEN)
        self._stop_ids = {
            self._tokenizer.eos_token_id,
            self._bos_id,
            self._tokenizer.pad_token_id,
        }
        log.debug(f"bos_id={self._bos_id} stop_ids={self._stop_ids}")

        prefix_path    = os.path.join(self._model_dir, "persona_prefixes.npy")
        self._prefixes = np.load(prefix_path).astype(np.float32)
        log.info(f"Persona prefixes loaded | shape: {self._prefixes.shape}")
        # Pre-slice once per persona so inference never re-slices the array.
        self._prefix_slices = [
            self._prefixes[row:row + 1]          # (1, 24, 1024)
            for _label, _dec_file, row in _PERSONA_DEFS
        ]

        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_opts.intra_op_num_threads = _ORT_THREADS

        enc_path = os.path.join(self._model_dir, "encoder_shared.onnx")
        log.info("Loading shared encoder ...")
        t0 = time.monotonic()
        self._encoder = ort.InferenceSession(enc_path, sess_options=sess_opts)
        log.info(f"Encoder loaded in {(time.monotonic()-t0)*1000:.0f}ms")

        for idx, (label, dec_file, _prefix_row) in enumerate(_PERSONA_DEFS):
            dec_path = os.path.join(self._model_dir, dec_file)
            log.info(f"Loading {label} decoder ...")
            t0 = time.monotonic()
            self._decoders[idx] = ort.InferenceSession(dec_path, sess_options=sess_opts)
            log.info(f"{label} decoder loaded in {(time.monotonic()-t0)*1000:.0f}ms")

    def apply(
        self,
        text:        str,
        persona_idx: int,
        on_result:   Callable[[str], None],
        on_error:    Optional[Callable[[str], None]] = None,
    ) -> None:
        """Apply tone to Hindi `text` using persona at `persona_idx` (0–3)."""
        persona_idx = max(0, min(persona_idx, len(_PERSONA_DEFS) - 1))
        label = _PERSONA_DEFS[persona_idx][0]

        max_line = max((len(l) for l in text.splitlines() if l.strip()), default=len(text))
        if max_line > TONE_MAX_CHARS:
            log.info(
                f"ToneEngine.apply | persona={label} | longest line too long "
                f"({max_line} chars > {TONE_MAX_CHARS}) — skipping tone"
            )
            on_result(text)
            return

        log.info(f"ToneEngine.apply | persona={label} | input: {len(text)} chars")

        def _run():
            try:
                result = self._apply_sync(text, persona_idx)
                on_result(result)
            except Exception as e:
                log.error(f"ToneEngine error: {e}")
                if on_error:
                    on_error(str(e))
                else:
                    on_result(text)  # fall back to untoned text

        self._executor.submit(_run)

    def _apply_sync(self, text: str, persona_idx: int) -> str:
        text = text.strip()
        if not text:
            return ""

        # Preserve newline structure — tone each line independently
        lines = text.splitlines()
        if len(lines) > 1:
            toned = [self._apply_single_line(l, persona_idx) for l in lines]
            return "\n".join(toned)
        return self._apply_single_line(text, persona_idx)

    def _apply_single_line(self, text: str, persona_idx: int) -> str:
        text = text.strip()
        if not text:
            return ""

        cache_key = f"{persona_idx}:{text}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            log.info(f"ToneEngine cache HIT | persona={_PERSONA_DEFS[persona_idx][0]}")
            return cached

        self._wait_ready()

        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

            label, _dec_file, prefix_row = _PERSONA_DEFS[persona_idx]
            decoder = self._decoders[persona_idx]

            # Tokenize — prepend <2hi> language token (required by IndicBART)
            processed = f"{_HIN_LANG_TOKEN} {text}"
            t0 = time.monotonic()
            tok_out = self._tokenizer(
                processed,
                return_tensors="np",
                padding=True,
                truncation=True,
                max_length=MAX_INPUT_LEN,
            )
            input_ids      = tok_out["input_ids"].astype(np.int64)
            attention_mask = tok_out["attention_mask"].astype(np.int64)
            log.debug(f"Tone tokenised in {(time.monotonic()-t0)*1000:.0f}ms | ids shape: {input_ids.shape}")

            # Encode with persona prefix (pre-sliced at load time)
            prefix = self._prefix_slices[persona_idx]            # (1, 24, 1024)
            t0 = time.monotonic()
            enc_out = self._encoder.run(
                None,
                {
                    "input_ids":      input_ids,
                    "attention_mask": attention_mask,
                    "prefix_embeds":  prefix,
                },
            )
            encoder_hidden = enc_out[0]                       # (1, src_len+24, 1024)
            enc_attn_mask  = enc_out[1].astype(np.int64)     # (1, src_len+24)
            log.debug(f"Tone encoder {(time.monotonic()-t0)*1000:.0f}ms | hidden: {encoder_hidden.shape}")

            # Adaptive cap: mother/gf_wife (persona 0/2) tend to be longer
            src_len = int((input_ids != self._tokenizer.pad_token_id).sum())
            mult = 2.2 if persona_idx in (0, 2) else 2.0
            max_steps = max(15, min(MAX_OUTPUT_LEN, int(src_len * mult) + 5))

            # Greedy decode — start with <2hi> BOS token
            decoder_ids = np.array([[self._bos_id]], dtype=np.int64)
            t0 = time.monotonic()
            steps = 0

            for _ in range(max_steps):
                logits = decoder.run(
                    None,
                    {
                        "encoder_hidden_states":  encoder_hidden,
                        "encoder_attention_mask": enc_attn_mask,
                        "decoder_input_ids":      decoder_ids,
                    },
                )[0]
                next_id = int(np.argmax(logits[0, -1, :]))
                steps += 1
                if next_id in self._stop_ids:
                    break
                decoder_ids = np.concatenate(
                    [decoder_ids, np.array([[next_id]], dtype=np.int64)], axis=1
                )

            decode_ms = (time.monotonic() - t0) * 1000
            raw = self._tokenizer.decode(decoder_ids[0], skip_special_tokens=True)
            result = _postprocess(raw, fallback=text, source=text)
            log.info(
                f"Tone done | persona={label} | {steps} steps | {decode_ms:.0f}ms | "
                f"output: {len(result)} chars"
            )

        self._cache.put(cache_key, result)
        return result
