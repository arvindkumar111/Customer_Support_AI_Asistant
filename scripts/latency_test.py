"""Find out WHY the LLM call is slow: is the model spending time "thinking"?
Usage (from project root):  python -m scripts.latency_test
Prints latency and token counts for the default setting and for minimal thinking.
"""
import time

from google import genai
from google.genai import errors, types

from app import config

MODEL = config.GEMINI_MODELS[0]
PROMPT = "Reply with the single word: ready"
client = genai.Client()

SETTINGS = {
    "default": None,
    "thinking MINIMAL": types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
}

print(f"model: {MODEL}")
for name, thinking in SETTINGS.items():
    for i in range(3):
        t0 = time.perf_counter()
        try:
            r = client.models.generate_content(
                model=MODEL, contents=PROMPT,
                config=types.GenerateContentConfig(thinking_config=thinking),
            )
            ms = (time.perf_counter() - t0) * 1000
            u = r.usage_metadata
            print(f"{name:17s} run {i+1}: {ms:6.0f} ms | prompt={u.prompt_token_count} "
                  f"thoughts={u.thoughts_token_count} output={u.candidates_token_count}")
        except errors.APIError as e:
            print(f"{name:17s} run {i+1}: ERROR {e.code} {str(e)[:150]}")
            break
