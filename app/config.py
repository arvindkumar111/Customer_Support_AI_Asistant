import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://support:support_dev_password@localhost:5432/support")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Multilingual model: English, Hindi and Hinglish queries land near English policy text.
DENSE_MODEL = "intfloat/multilingual-e5-large"
DENSE_DIM = 1024

# Chunking (words used as a cheap proxy for tokens)
CHUNK_WORDS = 300
OVERLAP_WORDS = 40

# Retrieval
RERANK_MODEL = "jinaai/jina-reranker-v2-base-multilingual"  # multilingual cross-encoder
RRF_K = 60            # standard constant for Reciprocal Rank Fusion
CANDIDATES = 15       # chunks fetched from each search before fusion/rerank
TOP_N = 6             # chunks sent to the LLM (4 missed the 'automatic reversal' chunk for G007; 6 fixed it in evals/eval_answers)
USE_RERANKER = False   # eval on 13 golden queries: rerank hurt Recall@3 (1.00 -> 0.85) and cost ~1s on CPU

# LLM (Gemini). Tried in order; the next one is used if a model is overloaded (503/429) or retired (404).
# Model names retire often (2.5-flash returned 404 for new users on 2026-10-14): keep them here, never in code.
# Measured 2026-10-14 (3 runs each, free tier): flash-lite 1.7-4.3 s, 3.5-flash 503 on every run, 3.8-flash 24-66 s.
# 3.8-flash is deliberately NOT in the chain: a 24 s wait is worse for a customer than a human handoff.
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.5-flash"]
LLM_TIMEOUT_SECONDS = 15          # max wait for ONE call
LLM_TOTAL_DEADLINE_SECONDS = 20   # max wait for the WHOLE chain (all retries + fallbacks), then hand off
LLM_RETRIES_PER_MODEL = 1     # 1 retry (2 attempts) per model, then fall back to the next model
LLM_BACKOFF_SECONDS = 1.0     # waits 1s, 2s, 4s ... between retries

# Router (Step 5). The LLM's self-reported confidence is NOT calibrated; these are starting guesses, tuned in evals/eval_router.py
ROUTER_HIGH = 0.7   # >= this: act on the classification
ROUTER_LOW = 0.4    # below this: hand off to a human; in between: ask one clarifying question

# Guardrails / logging (Step 6b)
RATE_LIMIT_PER_MINUTE = 20          # messages per customer per minute
LOG_HASH_SECRET = os.getenv("LOG_HASH_SECRET", "dev-secret-change-me")   # MUST be set to a long random value in production
LOG_MESSAGE_TEXT = os.getenv("LOG_MESSAGE_TEXT", "true").lower() == "true"  # redacted text, 200 chars max; set false if policy forbids