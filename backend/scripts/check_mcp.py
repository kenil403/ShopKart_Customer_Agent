"""Talk to the MCP server directly (no LLM, no API key) to prove the tools work.
    cd backend && uv run python -m scripts.check_mcp
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("STORE_TODAY", "2026-09-26")
os.environ["RETURNS_LOG"] = str(Path(tempfile.gettempdir()) / "shopkart_check_mcp.json")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.mcp_client import MCPOrdersClient  # noqa: E402


async def main():
    client = await MCPOrdersClient().start()
    print("MCP tools:", client.tool_names, "\n")
    calls = [
        ("get_customer_orders", {"email": "priya.sharma@example.com"}),
        ("get_order_status", {"order_id": "ORD-1003"}),
        ("request_return", {"order_id": "ORD-1004", "reason": "I don't like it"}),
        ("request_return", {"order_id": "ORD-1005", "reason": "arrived damaged"}),
    ]
    for name, args in calls:
        print(f"> {name}({args})")
        print(json.dumps(await client.call(name, **args), indent=2, ensure_ascii=False)[:700], "\n")
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
