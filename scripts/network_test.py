"""Separate NETWORK delay from MODEL delay.
Usage (from project root):  python -m scripts.network_test

1. raw HTTPS request to Google's API host (no model involved)
2. models.get() = authenticated API call that does NOT run the model
Compare both with the ~4-8 s of generate_content.
"""
import time

import httpx
from google import genai

from app import config

HOST = "https://generativelanguage.googleapis.com/"
client = genai.Client()

print("1) raw HTTPS round trip to the API host (error status is fine, we only time it)")
with httpx.Client(timeout=30) as h:
    for i in range(3):
        t0 = time.perf_counter()
        try:
            r = h.get(HOST)
            status = r.status_code
        except Exception as e:
            status = type(e).__name__
        print(f"   run {i+1}: {(time.perf_counter() - t0) * 1000:6.0f} ms  status={status}")

print(f"2) models.get('{config.GEMINI_MODELS[0]}') - authenticated call, model not run")
for i in range(3):
    t0 = time.perf_counter()
    try:
        client.models.get(model=config.GEMINI_MODELS[0])
        status = "ok"
    except Exception as e:
        status = f"{type(e).__name__}"
    print(f"   run {i+1}: {(time.perf_counter() - t0) * 1000:6.0f} ms  {status}")
