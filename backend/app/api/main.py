"""FastAPI backend.

Run (recommended, works on Windows too):   python run.py
Or:                                        uvicorn app.api.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config
from app.agent.llm import describe_error
from app.agent.runner import SupportAgent

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
log = logging.getLogger("shopkart")


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent = await SupportAgent(persist=True).start()
    app.state.agent = agent
    status = agent.llm_status()
    log.info("MCP tools: %s", agent.mcp.tool_names)
    for m in status["models"]:
        log.info("Model %-40s %s %s", m["model"], m["state"], f"({m['reason']})" if m["reason"] else "")
    if not status["ready"]:
        log.error("NO MODEL READY: %s  ->  set API keys in backend/.env and restart.", status["error"])
    yield
    await agent.close()


app = FastAPI(title="ShopKart Support Agent", version="1.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


def _health_payload() -> dict:
    agent: SupportAgent = app.state.agent
    return {
        "status": "ok",
        "llm": agent.llm_status(),
        "mcp_tools": agent.mcp.tool_names,
        "policy_chunks": len(agent.retriever.chunks),
        "embeddings": config.EMBEDDING_MODEL or None,
        "store_today": config.today().isoformat(),
    }


@app.get("/api")
async def api_root():
    """Opening the API root in a browser shows this instead of a 404."""
    return {
        "service": "ShopKart Support Agent API",
        "endpoints": {"health": "GET /api/health", "chat": "POST /api/chat", "docs": "GET /docs"},
    }


@app.get("/api/health")
@app.get("/health", include_in_schema=False)
async def health():
    return _health_payload()


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Called when a conversation is deleted from the history sidebar."""
    await app.state.agent.delete_session(session_id)
    return {"deleted": session_id}


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())
    try:
        result = await app.state.agent.chat(session_id, req.message)
    except Exception as exc:  # noqa: BLE001
        err = describe_error(exc)
        log.error("chat failed [%s]: %s", err["type"], err["dev"]["detail"][:400])
        headers = {"Retry-After": str(int(err["retry_after"]))} if err.get("retry_after") else None
        return JSONResponse(status_code=err.pop("status"), headers=headers,
                            content={"session_id": session_id, "error": err})
    return {"session_id": session_id, **result}


# ---------------------------------------------------------------- the website
# In development the React app runs on Vite (port 5173) and proxies /api here.
# In production one service serves both: the built frontend is copied to
# backend/static, so the site and the API share an origin (no CORS needed).
STATIC_DIR = config.BACKEND_DIR / "static"

if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        """Serve index.html for every non-API path (single-page app routing)."""
        candidate = (STATIC_DIR / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(STATIC_DIR.resolve()):
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
else:
    @app.get("/", include_in_schema=False)
    async def dev_root():
        return {"service": "ShopKart Support Agent API", "ui": "http://127.0.0.1:5173",
                "note": "No built frontend found; run 'npm run dev' in frontend/."}
