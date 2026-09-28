"""Thin MCP client: spawns the ShopKart MCP server over stdio once and keeps
the session open for the lifetime of the app (FastAPI lifespan / eval run).
The agent reaches order data ONLY through this client."""
from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

BACKEND_DIR = Path(__file__).resolve().parents[2]


class MCPOrdersClient:
    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None
        self.tool_names: list[str] = []

    async def start(self) -> "MCPOrdersClient":
        self._stack = AsyncExitStack()
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server.server"],
            cwd=str(BACKEND_DIR),
            # The MCP SDK passes only a minimal environment by default, so settings
            # like STORE_TODAY or ORDERS_FILE would be lost. Pass everything through,
            # and force UTF-8 so the ₹ sign survives on Windows consoles.
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        self.tool_names = [t.name for t in (await self.session.list_tools()).tools]
        return self

    async def call(self, name: str, **arguments) -> dict:
        if not self.session:
            raise RuntimeError("MCP client not started")
        result = await self.session.call_tool(name, arguments)
        if result.isError:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            return {"error": text or "tool_error"}
        if getattr(result, "structuredContent", None):
            data = result.structuredContent
            return data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data
        text = "".join(getattr(c, "text", "") for c in result.content)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    async def close(self) -> None:
        if self._stack:
            await self._stack.aclose()
            self._stack = None
