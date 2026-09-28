"""Start the backend:   python run.py

Why a script instead of plain `uvicorn ...`:
  * Binds to 127.0.0.1:8000, the exact address the frontend proxy uses.
  * On Windows, forces the Proactor event loop. The agent starts the MCP server
    as a subprocess, and Windows' Selector loop (which uvicorn may pick) cannot
    start subprocesses, failing with NotImplementedError.
"""
import asyncio
import os
import sys

import uvicorn

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

if __name__ == "__main__":
    uvicorn.run(
        "app.api.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,  # reload spawns a Selector-loop worker on Windows; keep it off
        loop="asyncio",
    )
