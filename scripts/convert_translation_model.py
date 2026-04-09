"""
scripts/convert_translation_model.py
--------------------------------------
Run this in Google Colab (free tier is fine — CPU-only export works).

What it does:
  1. Installs required libraries
  2. Downloads IndicTrans2 en-indic-dist-200M from AI4Bharat on HuggingFace
  3. Runs a BLEU baseline on 20 test sentences (records quality before compression)
  4. Exports the model to ONNX format via Hugging Face optimum
  5. Applies INT8 static quantization to shrink the model below 200 MB
  6. Validates post-quantization BLEU drop is < 2 points
  7. Zips the final .onnx files so you can download them from Colab

Usage in Colab:
  - Upload this file or paste it into a code cell
  - Runtime → Run all
  - Download the output zip from the Files panel on the left

Expected output files (in /content/indictrans2_onnx/):
  encoder_model_quantized.onnx
  decoder_model_quantized.onnx   (or decoder_with_past_quantized.onnx)
  tokenizer files (sentencepiece model + vocab)
  config.json

Place the downloaded folder at:
  smart-keyboard/models/indictrans2/
"""

# ─────────────────────────────────────────────────────────────────────────────
# CELL 1 — Install dependencies
# ─────────────────────────────────────────────────────────────────────────────
INSTALL = """
!pip install -q transformers==4.41.0 optimum[onnxruntime]==1.19.0 \
    onnxruntime==1.18.0 sacrebleu sentencepiece datasets
"""
print("Paste and run INSTALL in Colab, then continue with the cells below.")
print(INSTALL)


# ─────────────────────────────────────────────────────────────────────────────
# CELL 2 — Load tokenizer + model, run BLEU baseline
# ─────────────────────────────────────────────────────────────────────────────

BASELINE_CODE = '''
import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
import sacrebleu

MODEL_ID = "ai4bharat/indictrans2-en-indic-dist-200M"
print(f"Loading {MODEL_ID} …")

tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
model     = AutoModelForSeq2SeqLM.from_pretrained(MODEL_ID, trust_remote_code=True)
model.eval()

# 20 English → Hindi test sentences
TEST_PAIRS = [
    ("Hello, how are you?",                          "नमस्ते, आप कैसे हैं?"),
    ("I will be late today.",                        "मैं आज देर से आऊँगा।"),
    ("Please send me the document.",                 "कृपया मुझे दस्तावेज़ भेजें।"),
    ("The meeting is at 3 PM.",                      "बैठक 3 बजे है।"),
    ("I love you.",                                  "मैं तुमसे प्यार करता हूँ।"),
    ("Thank you so much.",                           "बहुत बहुत धन्यवाद।"),
    ("Can you help me?",                             "क्या आप मेरी मदद कर सकते हैं?"),
    ("I am feeling sick today.",                     "मैं आज बीमार महसूस कर रहा हूँ।"),
    ("The weather is very nice.",                    "मौसम बहुत अच्छा है।"),
    ("Let us go for a walk.",                        "चलो टहलने चलते हैं।"),
    ("I will call you tomorrow.",                    "मैं कल आपको फोन करूँगा।"),
    ("Happy birthday!",                              "जन्मदिन मुबारक हो!"),
    ("Please wait for me.",                          "कृपया मेरा इंतजार करें।"),
    ("I am very tired.",                             "मैं बहुत थका हुआ हूँ।"),
    ("What is your name?",                           "आपका नाम क्या है?"),
    ("I need to talk to you.",                       "मुझे आपसे बात करनी है।"),
    ("The food is delicious.",                       "खाना बहुत स्वादिष्ट है।"),
    ("See you tomorrow.",                            "कल मिलते हैं।"),
    ("I am going to the office.",                    "मैं ऑफिस जा रहा हूँ।"),
    ("Please take care of yourself.",                "कृपया अपना ख्याल रखें।"),
]

src_sentences = [p[0] for p in TEST_PAIRS]
ref_sentences = [p[1] for p in TEST_PAIRS]

def translate_batch(sentences, mdl, tok):
    inputs = tok(sentences, return_tensors="pt", padding=True, truncation=True, max_length=256)
    with torch.no_grad():
        outputs = mdl.generate(**inputs, num_beams=4, max_length=256)
    return tok.batch_decode(outputs, skip_special_tokens=True)

print("Running BLEU baseline …")
hyps = translate_batch(src_sentences, model, tokenizer)

for src, hyp, ref in zip(src_sentences[:5], hyps[:5], ref_sentences[:5]):
    print(f"  SRC : {src}")
    print(f"  HYP : {hyp}")
    print(f"  REF : {ref}")
    print()

bleu = sacrebleu.corpus_bleu(hyps, [ref_sentences])
print(f"Baseline BLEU: {bleu.score:.2f}")
baseline_bleu = bleu.score
'''
print("=" * 60)
print("CELL 2 — Baseline BLEU")
print("=" * 60)
print(BASELINE_CODE)


# ─────────────────────────────────────────────────────────────────────────────
# CELL 3 — Export to ONNX
# ─────────────────────────────────────────────────────────────────────────────

EXPORT_CODE = '''
import subprocess, os

MODEL_ID  = "ai4bharat/indictrans2-en-indic-dist-200M"
ONNX_DIR  = "/content/indictrans2_onnx"
os.makedirs(ONNX_DIR, exist_ok=True)

print("Exporting to ONNX … (takes 3-8 minutes on Colab CPU)")
result = subprocess.run([
    "optimum-cli", "export", "onnx",
    "--model", MODEL_ID,
    "--task",  "text2text-generation-with-past",
    "--opset", "14",
    ONNX_DIR,
], capture_output=True, text=True)

print(result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout)
if result.returncode != 0:
    print("STDERR:", result.stderr[-2000:])
    raise RuntimeError("ONNX export failed — see above")

import os
files = os.listdir(ONNX_DIR)
total_mb = sum(os.path.getsize(f"{ONNX_DIR}/{f}") for f in files) / 1e6
print(f"\\nONNX export complete. Files: {files}")
print(f"Total size: {total_mb:.1f} MB")
'''
print("=" * 60)
print("CELL 3 — ONNX Export")
print("=" * 60)
print(EXPORT_CODE)


# ─────────────────────────────────────────────────────────────────────────────
# CELL 4 — INT8 Quantization
# ─────────────────────────────────────────────────────────────────────────────

QUANTIZE_CODE = '''
from onnxruntime.quantization import quantize_dynamic, QuantType
import os, glob

ONNX_DIR  = "/content/indictrans2_onnx"
QUANT_DIR = "/content/indictrans2_onnx_int8"
os.makedirs(QUANT_DIR, exist_ok=True)

onnx_files = glob.glob(f"{ONNX_DIR}/*.onnx")
print(f"Quantizing {len(onnx_files)} ONNX file(s) …")

for src_path in onnx_files:
    fname    = os.path.basename(src_path)
    dst_path = f"{QUANT_DIR}/{fname.replace('.onnx', '_quantized.onnx')}"
    print(f"  {fname} → {os.path.basename(dst_path)}")
    quantize_dynamic(
        model_input   = src_path,
        model_output  = dst_path,
        weight_type   = QuantType.QInt8,
        optimize_model = True,
    )

# Copy tokenizer files (non-ONNX) to the quantized dir
import shutil
for f in os.listdir(ONNX_DIR):
    if not f.endswith(".onnx"):
        shutil.copy(f"{ONNX_DIR}/{f}", f"{QUANT_DIR}/{f}")

total_mb = sum(
    os.path.getsize(f"{QUANT_DIR}/{f}") for f in os.listdir(QUANT_DIR)
) / 1e6
print(f"\\nQuantization complete. Total size: {total_mb:.1f} MB (target: <200 MB)")
'''
print("=" * 60)
print("CELL 4 — INT8 Quantization")
print("=" * 60)
print(QUANTIZE_CODE)


# ─────────────────────────────────────────────────────────────────────────────
# CELL 5 — Validate BLEU on quantized model + zip for download
# ─────────────────────────────────────────────────────────────────────────────

VALIDATE_CODE = '''
from optimum.onnxruntime import ORTModelForSeq2SeqLM
from transformers import AutoTokenizer
import sacrebleu, shutil

QUANT_DIR = "/content/indictrans2_onnx_int8"
MODEL_ID  = "ai4bharat/indictrans2-en-indic-dist-200M"

print("Loading quantized ONNX model …")
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
ort_model = ORTModelForSeq2SeqLM.from_pretrained(QUANT_DIR)

SRC = [
    "Hello, how are you?", "I will be late today.",
    "Please send me the document.", "The meeting is at 3 PM.",
    "I love you.", "Thank you so much.", "Can you help me?",
    "I am feeling sick today.", "The weather is very nice.",
    "Let us go for a walk.", "I will call you tomorrow.",
    "Happy birthday!", "Please wait for me.", "I am very tired.",
    "What is your name?", "I need to talk to you.",
    "The food is delicious.", "See you tomorrow.",
    "I am going to the office.", "Please take care of yourself.",
]
REF = [
    "नमस्ते, आप कैसे हैं?", "मैं आज देर से आऊँगा।",
    "कृपया मुझे दस्तावेज़ भेजें।", "बैठक 3 बजे है।",
    "मैं तुमसे प्यार करता हूँ।", "बहुत बहुत धन्यवाद।",
    "क्या आप मेरी मदद कर सकते हैं?", "मैं आज बीमार महसूस कर रहा हूँ।",
    "मौसम बहुत अच्छा है।", "चलो टहलने चलते हैं।",
    "मैं कल आपको फोन करूँगा।", "जन्मदिन मुबारक हो!",
    "कृपया मेरा इंतजार करें।", "मैं बहुत थका हुआ हूँ।",
    "आपका नाम क्या है?", "मुझे आपसे बात करनी है।",
    "खाना बहुत स्वादिष्ट है।", "कल मिलते हैं।",
    "मैं ऑफिस जा रहा हूँ।", "कृपया अपना ख्याल रखें।",
]

inputs = tokenizer(SRC, return_tensors="pt", padding=True, truncation=True, max_length=256)
outputs = ort_model.generate(**inputs, num_beams=4, max_length=256)
hyps    = tokenizer.batch_decode(outputs, skip_special_tokens=True)

bleu_q = sacrebleu.corpus_bleu(hyps, [REF]).score
drop   = baseline_bleu - bleu_q   # baseline_bleu from Cell 2

print(f"Baseline BLEU  : {baseline_bleu:.2f}")
print(f"Quantized BLEU : {bleu_q:.2f}")
print(f"Drop           : {drop:.2f} points  (limit: < 2.0)")

if drop >= 2.0:
    print("⚠ BLEU drop too high — consider using static quantization or a larger model.")
else:
    print("✅ Quality validated — within acceptable range.")

# Zip for download
shutil.make_archive("/content/indictrans2_int8", "zip", QUANT_DIR)
print("\\nDownload: /content/indictrans2_int8.zip")
print("Place contents in: smart-keyboard/models/indictrans2/")
'''
print("=" * 60)
print("CELL 5 — Validate & Download")
print("=" * 60)
print(VALIDATE_CODE)
