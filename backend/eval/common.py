"""Shared helper for the eval scripts: ask a question, and if every model is
rate-limited, wait for the shortest cooldown and ask again (free tiers)."""
from __future__ import annotations

import asyncio

from app import config
from app.agent.llm import AllModelsUnavailable


async def ask_with_patience(agent, thread: str, message: str) -> dict:
    waited = 0.0
    while True:
        try:
            return await agent.chat(thread, message)
        except AllModelsUnavailable as exc:
            wait = exc.retry_after
            if wait is None or waited + wait > config.EVAL_MAX_WAIT_SECONDS:
                raise
            wait = max(wait, 5) + 1
            print(f"      all models busy; waiting {wait:.0f}s then retrying…", flush=True)
            await asyncio.sleep(wait)
            waited += wait
