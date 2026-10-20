"""One place that talks to the LLM. Everything else calls generate().

Failure handling (Stage 6 design):
  503 / 429 / timeout  -> retry with exponential backoff, then fall back to the next model
  404 (model retired)  -> skip to the next model immediately
  400 / 401 / 403      -> our bug or a bad key: fail loudly, never retry
  every model failed   -> raise LLMUnavailable so the caller can hand off to a human
  deadline exceeded    -> same: a slow answer is treated like no answer (customer is never kept waiting 60 s)

Test:  python -m app.llm "Reply with the single word: ready"
"""
import logging
import sys
import time
from dataclasses import dataclass

from google import genai
from google.genai import errors, types

from app import config

log = logging.getLogger("llm")


class LLMUnavailable(Exception):
    """Every configured model failed. Caller must degrade gracefully (ticket + human agent)."""


@dataclass
class LLMResult:
    text: str
    model: str
    latency_ms: float
    attempts: int


_client = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        # Reads GEMINI_API_KEY from the environment (.env is loaded by app.config).
        _client = genai.Client(http_options=types.HttpOptions(timeout=config.LLM_TIMEOUT_SECONDS * 1000))
    return _client


def _call(client, model: str, prompt: str, system: str | None, temperature: float, max_tokens: int,
          timeout_s: float, json_mode: bool = False):
    return client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system, temperature=temperature, max_output_tokens=max_tokens,
            response_mime_type="application/json" if json_mode else None,
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000))),
    )


def generate(prompt: str, system: str | None = None, temperature: float = 0.0,
             max_tokens: int = 1024, client=None, sleep=time.sleep, json_mode: bool = False) -> LLMResult:
    """json_mode=True asks the API itself for valid JSON (use it whenever code will parse the answer)."""
    client = client or _get_client()
    attempts = 0
    start = time.perf_counter()
    for model in config.GEMINI_MODELS:
        for attempt in range(config.LLM_RETRIES_PER_MODEL + 1):
            remaining = config.LLM_TOTAL_DEADLINE_SECONDS - (time.perf_counter() - start)
            if remaining <= 1:
                raise LLMUnavailable(f"deadline of {config.LLM_TOTAL_DEADLINE_SECONDS}s exceeded after {attempts} attempts")
            attempts += 1
            t0 = time.perf_counter()
            try:
                resp = _call(client, model, prompt, system, temperature, max_tokens,
                             timeout_s=min(config.LLM_TIMEOUT_SECONDS, remaining), json_mode=json_mode)
                text = (resp.text or "").strip()
                if not text:                      # blocked or empty answer: treat as a failure of this model
                    raise errors.ServerError(503, {"error": {"message": "empty response"}}, None)
                return LLMResult(text, model, (time.perf_counter() - t0) * 1000, attempts)
            except errors.APIError as e:
                code = getattr(e, "code", None)
                if code == 404:
                    log.warning("model %s not available (404), trying next model", model)
                    break                         # next model, no retry
                if code in (429, 500, 502, 503, 504) or code is None:
                    log.warning("model %s attempt %d failed with %s", model, attempt + 1, code)
                    if attempt < config.LLM_RETRIES_PER_MODEL:
                        sleep(config.LLM_BACKOFF_SECONDS * (2 ** attempt))
                        continue
                    break                         # retries used up: next model
                raise                             # 400/401/403: do not hide real bugs
            except Exception as e:                # timeouts / network errors
                log.warning("model %s attempt %d network error: %s", model, attempt + 1, type(e).__name__)
                if attempt < config.LLM_RETRIES_PER_MODEL:
                    sleep(config.LLM_BACKOFF_SECONDS * (2 ** attempt))
                    continue
                break
    raise LLMUnavailable(f"all models failed after {attempts} attempts: {config.GEMINI_MODELS}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    q = " ".join(sys.argv[1:]) or "Reply with the single word: ready"
    r = generate(q)
    print(f"[{r.model}, {r.latency_ms:.0f} ms, {r.attempts} attempt(s)]\n{r.text}")