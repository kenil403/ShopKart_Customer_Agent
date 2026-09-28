"""Check your API keys and model names before running the app.

    cd backend
    uv run python -m scripts.check_llm            # test every configured model
    uv run python -m scripts.check_llm --list     # also list the models each key can use

For LLM_MODEL and every LLM_FALLBACKS entry it sends one tiny request that must
produce a tool call (the agent depends on tool calling). If a model name is
wrong or retired, it lists the models your key can use instead.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402

from app import config  # noqa: E402
from app.agent.llm import LLMConfigError, _build, classify  # noqa: E402
from app.agent.tools import TOOL_SCHEMAS  # noqa: E402

FIX = {
    "auth_failed": "The key was rejected. Re-copy it into backend/.env (no quotes, no spaces).",
    "model_not_found": "This model name is wrong or retired. Pick one from the list below.",
    "rate_limited": "Rate limit or daily quota reached. It recovers on its own; the app will use another model meanwhile.",
    "network": "Couldn't reach the provider. Check internet, VPN or firewall.",
}


def list_models(provider: str) -> list[str]:
    """Ask the provider which chat models this key can use."""
    try:
        if provider == "groq":
            r = httpx.get("https://api.groq.com/openai/v1/models", timeout=15,
                          headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
            ids = [m["id"] for m in r.json().get("data", []) if m.get("active", True)]
            return sorted(i for i in ids if not any(x in i for x in ("whisper", "tts", "guard", "orpheus")))
        if provider == "google_genai":
            r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", timeout=15,
                          params={"key": os.environ["GOOGLE_API_KEY"], "pageSize": 200})
            return sorted(m["name"].removeprefix("models/") for m in r.json().get("models", [])
                          if "generateContent" in m.get("supportedGenerationMethods", [])
                          and "gemini" in m["name"] and "tts" not in m["name"] and "image" not in m["name"])
        if provider == "mistralai":
            r = httpx.get("https://api.mistral.ai/v1/models", timeout=15,
                          headers={"Authorization": f"Bearer {os.environ['MISTRAL_API_KEY']}"})
            return sorted(m["id"] for m in r.json().get("data", [])
                          if m.get("capabilities", {}).get("function_calling"))
    except Exception as exc:  # noqa: BLE001
        return [f"(could not list models: {type(exc).__name__})"]
    return []


async def check(model: str, show_list: bool) -> bool:
    info = config.model_info(model)
    label = f"{info['provider_name']:8} {info['model_name']}"
    if not info["key_present"]:
        print(f"  --    {label}\n        skipped: {info['key_var']} is not set in backend/.env")
        return False
    ok, kind = False, None
    try:
        msg = await _build(model, TOOL_SCHEMAS).ainvoke(
            [HumanMessage("Use the search_policy tool to look up the return window.")])
        if msg.tool_calls:
            print(f"  OK    {label}  (tool call works)")
        else:
            print(f"  WARN  {label}  replied without calling a tool; tool use may be unreliable.")
        ok = True
    except LLMConfigError as exc:
        print(f"  FAIL  {label}\n        {exc}")
    except Exception as exc:  # noqa: BLE001
        err = classify(exc)
        kind = err["kind"]
        print(f"  FAIL  {label}\n        {FIX.get(kind, err['detail'][:200])}")
    if show_list or kind == "model_not_found":
        models = list_models(info["provider"])
        if models:
            print(f"        Models your {info['provider_name']} key can use:")
            for m in models[:40]:
                print(f"          {info['provider']}:{m}")
    return ok


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list available models for each provider")
    args = ap.parse_args()
    models = list(dict.fromkeys([config.LLM_MODEL, *config.LLM_FALLBACKS]))
    print(f"Checking {len(models)} model(s) from backend/.env (in fallback order)\n")
    results = [await check(m, args.list) for m in models]
    working = sum(results)
    print(f"\n{working} of {len(models)} model(s) working.",
          "The app switches between them automatically." if working else
          "No model works yet: fix the keys or model names above.")


if __name__ == "__main__":
    asyncio.run(main())
