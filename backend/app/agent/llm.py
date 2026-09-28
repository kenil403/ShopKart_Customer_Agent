"""Model router: automatic switching between free-tier LLM providers.

Why not LangChain's `with_fallbacks`? It retries every model on every message.
With free tiers that burns quota: a model that is rate-limited for 30 s gets
hit again on the next message, and a retired model (404) is retried forever.

The router keeps a small state machine per model:

    ready ──429──► cooling (for the retry time the provider sends back)
      ▲               │ cooldown ends
      └───────────────┘
    ready ──401 / 404 / missing key──► disabled (until restart)

Each call goes to the first model that is `ready`, in LLM_MODEL, LLM_FALLBACKS
order, so the main model is used again as soon as its cooldown ends. Every
model also has a client-side rate limiter, so we wait a moment instead of
bursting past the provider's per-minute limit.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field

from langchain.chat_models import init_chat_model
from langchain_core.rate_limiters import InMemoryRateLimiter

from app import config

log = logging.getLogger("shopkart.llm")

PACKAGES = {"groq": "langchain-groq", "google_genai": "langchain-google-genai",
            "mistralai": "langchain-mistralai", "openai": "langchain-openai",
            "anthropic": "langchain-anthropic"}


class LLMConfigError(RuntimeError):
    """No usable model at all: missing package or API key for every model."""


class AllModelsUnavailable(RuntimeError):
    """Every configured model is cooling down, disabled, or just failed."""

    def __init__(self, attempts: list[dict], retry_after: float | None):
        self.attempts = attempts
        self.retry_after = retry_after
        summary = "; ".join(f"{a['model']}: {a['reason']}" for a in attempts)
        super().__init__(f"All models unavailable ({summary})")


# ---------------------------------------------------------------- error parsing
RETRY_PATTERNS = [
    re.compile(r"retry in (\d+(?:\.\d+)?)\s*s", re.I),                           # Gemini
    re.compile(r"try again in (?:(\d+)m)?\s*(\d+(?:\.\d+)?)s", re.I),            # Groq "6m11.5s"
    re.compile(r"retrydelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", re.I),          # Gemini details
    re.compile(r"retry-after['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)", re.I),       # header echoes
]
DAILY_QUOTA = re.compile(r"per ?day|perday|\(tpd\)|\(rpd\)|tokens per day|requests per day", re.I)


def parse_retry_after(text: str) -> float | None:
    for pat in RETRY_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        nums = [g for g in m.groups() if g]
        if len(nums) == 2:  # minutes + seconds
            return int(nums[0]) * 60 + float(nums[1])
        return float(nums[0])
    return None


def classify(exc: Exception) -> dict:
    """Map a provider exception to {kind, detail, retry_after, daily}."""
    text = f"{type(exc).__module__}.{type(exc).__name__}: {exc}"
    low = text.lower()
    if isinstance(exc, LLMConfigError):
        kind = "missing_api_key"
    elif any(s in low for s in ("connecterror", "connection", "timed out", "timeout", "getaddrinfo",
                                "allowlist", "proxy", "name resolution")) or re.search(r"\bssl", low):
        kind = "network"
    elif any(s in low for s in ("401", "403", "unauthorized", "invalid api key", "api key not valid",
                                "invalid_api_key", "permission_denied", "permissiondenied", "authentication")):
        kind = "auth_failed"
    elif any(s in low for s in ("429", "rate limit", "rate_limit", "resource_exhausted", "quota",
                                "too many requests")):
        kind = "rate_limited"
    elif any(s in low for s in ("model_not_found", "does not exist", "not found for api version",
                                "decommissioned", "404", "not_found")):
        kind = "model_not_found"
    elif "tool_use_failed" in low or "failed to call a function" in low or "function call" in low:
        kind = "tool_call_failed"
    elif any(s in low for s in ("500", "502", "503", "504", "overloaded", "unavailable", "internal")):
        kind = "provider_error"
    else:
        kind = "unknown"
    return {"kind": kind, "detail": text[:500], "retry_after": parse_retry_after(text),
            "daily": bool(DAILY_QUOTA.search(text))}


# ---------------------------------------------------------------- model slots
@dataclass
class Slot:
    model: str
    info: dict
    runnable: object = None
    state: str = "ready"            # ready | cooling | disabled
    until: float = 0.0              # monotonic time when cooling ends
    reason: str = ""
    strikes: int = 0                # consecutive rate limits (cooldown grows)
    calls: int = 0
    failures: int = 0
    last_error: str = ""
    history: list = field(default_factory=list)

    def available(self) -> bool:
        if self.state == "cooling" and time.monotonic() >= self.until:
            self.state, self.reason = "ready", ""
        return self.state == "ready"

    def cool(self, seconds: float, reason: str) -> None:
        self.state, self.until, self.reason = "cooling", time.monotonic() + seconds, reason

    def disable(self, reason: str) -> None:
        self.state, self.reason = "disabled", reason

    def status(self) -> dict:
        self.available()
        remaining = max(0, math.ceil(self.until - time.monotonic())) if self.state == "cooling" else 0
        return {**self.info, "state": self.state, "reason": self.reason,
                "retry_in_seconds": remaining, "calls": self.calls, "failures": self.failures,
                "last_error": self.last_error}


def _build(model: str, tools) -> object:
    info = config.model_info(model)
    if not info["key_present"]:
        raise LLMConfigError(f"{info['key_var'] or 'API key'} is not set")
    rps = config.PROVIDER_RPS.get(info["provider"])
    kwargs = {"temperature": 0, "max_retries": 0, "timeout": config.LLM_TIMEOUT}
    if rps:
        kwargs["rate_limiter"] = InMemoryRateLimiter(
            requests_per_second=rps, check_every_n_seconds=0.1, max_bucket_size=1)
    try:
        llm = init_chat_model(model, **kwargs)
    except ImportError as exc:
        raise LLMConfigError(f"package missing: uv add {PACKAGES.get(info['provider'], 'provider package')}") from exc
    return llm.bind_tools(tools)


class ModelRouter:
    def __init__(self, tools):
        self.slots: list[Slot] = []
        for model in dict.fromkeys([config.LLM_MODEL, *config.LLM_FALLBACKS]):
            slot = Slot(model=model, info=config.model_info(model))
            try:
                slot.runnable = _build(model, tools)
            except LLMConfigError as exc:
                slot.disable(str(exc))
                log.warning("Model %s disabled: %s", model, exc)
            self.slots.append(slot)
        if not any(s.runnable for s in self.slots):
            raise LLMConfigError("; ".join(f"{s.model}: {s.reason}" for s in self.slots))

    def status(self) -> list[dict]:
        return [s.status() for s in self.slots]

    async def ainvoke(self, messages):
        attempts = []
        for slot in self.slots:
            if not slot.available():
                attempts.append({"model": slot.model, "reason": f"skipped ({slot.state}: {slot.reason})"})
                continue
            for attempt in (1, 2):
                slot.calls += 1
                try:
                    response = await slot.runnable.ainvoke(messages)
                except Exception as exc:  # noqa: BLE001
                    err = classify(exc)
                    slot.failures += 1
                    slot.last_error = f"{err['kind']}: {err['detail'][:200]}"
                    if err["kind"] == "rate_limited":
                        slot.strikes += 1
                        base = err["retry_after"] or 10
                        if err["daily"]:
                            base = max(base, 3600)  # daily quota: a 24 s hint is misleading
                        wait = min(max(base, 5 * 2 ** (slot.strikes - 1)), 6 * 3600)
                        slot.cool(wait, "daily limit reached" if err["daily"] else "rate limited")
                    elif err["kind"] in ("auth_failed", "model_not_found", "missing_api_key"):
                        slot.disable({"auth_failed": "API key rejected",
                                      "model_not_found": "model not found (retired or misspelled)",
                                      "missing_api_key": "API key missing"}[err["kind"]])
                    elif attempt == 1 and err["kind"] in ("tool_call_failed", "provider_error",
                                                          "network", "unknown"):
                        await asyncio.sleep(1.0)
                        continue  # one quick retry on the same model
                    else:
                        slot.cool(20, err["kind"].replace("_", " "))
                    log.warning("Model %s failed (%s) -> %s", slot.model, err["kind"],
                                "trying next model" if slot is not self.slots[-1] else "no more models")
                    attempts.append({"model": slot.model, "reason": slot.reason or err["kind"],
                                     "detail": err["detail"][:300]})
                    break
                else:
                    slot.strikes = 0
                    if slot is not self.slots[0]:
                        log.info("Answered by fallback model %s", slot.model)
                    response.response_metadata = {**(response.response_metadata or {}),
                                                  "shopkart_model": slot.model}
                    return response
        waits = [s.status()["retry_in_seconds"] for s in self.slots if s.state == "cooling"]
        raise AllModelsUnavailable(attempts, retry_after=min(waits) if waits else None)


# ---------------------------------------------------------------- errors for the API/UI
def describe_error(exc: Exception) -> dict:
    """Two audiences: `message` is safe to show a customer; `dev` is for developers."""
    if isinstance(exc, AllModelsUnavailable):
        disabled_only = all("disabled" in a["reason"] or "API key" in a["reason"] or "not found" in a["reason"]
                            for a in exc.attempts)
        wait = exc.retry_after
        if disabled_only or wait is None:
            msg, status, kind = ("Our assistant is temporarily unavailable. Please try again a little later.",
                                 503, "unavailable")
        elif wait <= 90:
            msg, status, kind = (f"Our assistant is busy right now. Please try again in about "
                                 f"{max(10, int(math.ceil(wait / 10.0)) * 10)} seconds.", 429, "busy")
        elif wait <= 30 * 60:
            msg, status, kind = (f"Our assistant is busy right now. Please try again in about "
                                 f"{int(math.ceil(wait / 60))} minutes.", 429, "busy")
        else:
            msg, status, kind = ("Our assistant has reached its limit for now. Please try again later today.",
                                 429, "busy")
        return {"status": status, "type": kind, "message": msg, "retry_after": wait,
                "dev": {"detail": str(exc), "attempts": exc.attempts,
                        "hint": "Check `python -m scripts.check_llm`. Retired models must be replaced in "
                                "LLM_MODEL/LLM_FALLBACKS; rate-limited ones recover by themselves."}}
    if isinstance(exc, LLMConfigError):
        return {"status": 503, "type": "unavailable",
                "message": "Our assistant is temporarily unavailable. Please try again a little later.",
                "retry_after": None,
                "dev": {"detail": str(exc), "hint": "Set the API keys in backend/.env and restart."}}
    err = classify(exc)
    return {"status": 500, "type": "error",
            "message": "Sorry, something went wrong on our side. Please try again.",
            "retry_after": None, "dev": {"detail": err["detail"], "hint": err["kind"]}}
