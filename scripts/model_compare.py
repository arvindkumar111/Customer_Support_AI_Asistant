"""Compare the configured Gemini models on a realistic, RAG-sized prompt, with streaming.
Usage (from project root):  python -m scripts.model_compare

Prints time-to-first-token (TTFT) and total time per run. Failures are shown, not hidden.
"""
import time

from google import genai
from google.genai import errors

from app import config

PROMPT = (
    "Answer in 2-3 sentences using only this policy.\n"
    "Policy: Electronics can be returned within 7 days of delivery if unused and in original packaging. "
    "If an electronic item arrives damaged, report it within 48 hours with photos; pickup is free.\n"
    "Customer: Mera laptop damaged aaya hai, kitne din mein return kar sakta hu?"
)
RUNS = 3
client = genai.Client()

for model in config.GEMINI_MODELS:
    print(f"\n{model}")
    for i in range(RUNS):
        t0 = time.perf_counter()
        first, chars = None, 0
        try:
            for chunk in client.models.generate_content_stream(model=model, contents=PROMPT):
                if chunk.text and first is None:
                    first = (time.perf_counter() - t0) * 1000
                chars += len(chunk.text or "")
            total = (time.perf_counter() - t0) * 1000
            print(f"   run {i+1}: TTFT {first or 0:6.0f} ms | total {total:6.0f} ms | {chars} chars")
        except errors.APIError as e:
            print(f"   run {i+1}: ERROR {e.code} {str(e)[:110]}")
        except Exception as e:
            print(f"   run {i+1}: ERROR {type(e).__name__}")
