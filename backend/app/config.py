"""Central configuration. Everything is read from backend/.env (or real env vars)."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

DATA_DIR = BACKEND_DIR / "data"
POLICY_DIR = Path(os.getenv("POLICY_DIR", DATA_DIR / "policy_docs"))
ORDERS_FILE = Path(os.getenv("ORDERS_FILE", DATA_DIR / "orders.json"))
RETURNS_LOG = Path(os.getenv("RETURNS_LOG", DATA_DIR / "returns_log.json"))
INDEX_DIR = Path(os.getenv("INDEX_DIR", DATA_DIR / "index"))
# Conversation memory for the API (survives restarts, so chats reopened from the
# history sidebar keep their context). Tests and eval scripts use RAM instead.
CHECKPOINT_DB = Path(os.getenv("CHECKPOINT_DB", DATA_DIR / "conversations.sqlite"))

# ---------------------------------------------------------------- LLM
# Format "provider:model". Free-tier options (checked September 2026):
#   groq:openai/gpt-oss-120b            (GROQ_API_KEY)    best tool use; 8K tokens/min
#   google_genai:gemini-3.1-flash-lite  (GOOGLE_API_KEY)  ~500 requests/day
#   mistralai:mistral-small-latest      (MISTRAL_API_KEY) 1 request/second
# Note: groq:llama-3.3-70b-versatile was shut down by Groq on 16 Aug 2026 (404).
LLM_MODEL = os.getenv("LLM_MODEL", "groq:openai/gpt-oss-120b").strip()
# Tried in order when the main model is rate-limited or broken. The router then
# returns to the main model automatically once its cooldown ends.
LLM_FALLBACKS = [m.strip() for m in os.getenv(
    "LLM_FALLBACKS", "google_genai:gemini-3.1-flash-lite,mistralai:mistral-small-latest").split(",") if m.strip()]

# Client-side pacing (requests per second) so we never burst past free-tier limits.
# The request waits briefly instead of failing with 429.
PROVIDER_RPS = {
    "groq": float(os.getenv("GROQ_RPS", "0.4")),            # 30 req/min limit
    "google_genai": float(os.getenv("GEMINI_RPS", "0.2")),  # 15 req/min limit
    "mistralai": float(os.getenv("MISTRAL_RPS", "0.8")),    # 1 req/s limit
}
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "45"))

PROVIDER_KEYS = {
    "groq": "GROQ_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "mistralai": "MISTRAL_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
PROVIDER_NAMES = {
    "groq": "Groq", "google_genai": "Gemini", "mistralai": "Mistral",
    "openai": "OpenAI", "anthropic": "Anthropic",
}


def provider_of(model: str) -> str:
    return model.split(":", 1)[0] if ":" in model else "unknown"


def model_info(model: str) -> dict:
    provider = provider_of(model)
    key_var = PROVIDER_KEYS.get(provider)
    # Gemini also accepts GEMINI_API_KEY; copy it so langchain finds it.
    if provider == "google_genai" and not os.getenv("GOOGLE_API_KEY") and os.getenv("GEMINI_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
    return {
        "model": model,
        "provider": provider,
        "provider_name": PROVIDER_NAMES.get(provider, provider),
        "model_name": model.split(":", 1)[-1],
        "key_var": key_var,
        "key_present": bool(key_var and os.getenv(key_var, "").strip()),
    }


# ---------------------------------------------------------------- retrieval
# BM25 is always on. Set EMBEDDING_MODEL to add dense retrieval (hybrid), e.g.
#   google_genai:models/text-embedding-004   or   mistralai:mistral-embed
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "").strip()
USE_EMBEDDINGS = bool(EMBEDDING_MODEL)
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "3"))
RETRIEVAL_MIN_SCORE = float(os.getenv("RETRIEVAL_MIN_SCORE", "1.5"))

# ---------------------------------------------------------------- evaluation
# Pause between eval cases so free-tier rate limits (requests/minute) aren't hit.
EVAL_DELAY_SECONDS = float(os.getenv("EVAL_DELAY_SECONDS", "4"))
# When every model is busy, eval scripts wait up to this long and retry the same question.
EVAL_MAX_WAIT_SECONDS = float(os.getenv("EVAL_MAX_WAIT_SECONDS", "180"))


def today() -> date:
    """The store's 'today'. STORE_TODAY pins the date so evaluations are reproducible."""
    pinned = os.getenv("STORE_TODAY", "").strip()
    return date.fromisoformat(pinned) if pinned else date.today()
