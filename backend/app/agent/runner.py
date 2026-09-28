"""SupportAgent: one object used by the API, the eval script and answers.json.

It wires RAG + MCP + LangGraph + the model router together and turns each turn
into a structured response: answer text, validated citations (with
customer-friendly titles), and a trace of the documents and tools used.
"""
from __future__ import annotations

import re
from contextlib import AsyncExitStack

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app import config
from app.agent.graph import build_graph
from app.agent.llm import LLMConfigError, ModelRouter
from app.agent.mcp_client import MCPOrdersClient
from app.agent.tools import TOOL_SCHEMAS, ToolExecutor
from app.rag.retriever import PolicyRetriever

CITE_RE = re.compile(r"\[\s*(\d{2}_[\w]+\.md)\s*§\s*([^\]]+?)\s*\]")


def friendly_title(doc_title: str, effective: str) -> str:
    """'ShopKart – Returns Policy Update', '1 March 2026' -> 'Returns Policy Update (2026)'."""
    title = re.sub(r"^ShopKart\s*[–-]\s*", "", doc_title).strip()
    year = re.search(r"(20\d\d)", effective or "")
    return f"{title} ({year.group(1)})" if year and "return" in title.lower() else title


class SupportAgent:
    def __init__(self, llm=None, persist: bool = False, db_path=None):
        """persist=True stores conversation memory in SQLite (used by the API)."""
        self._llm = llm
        self._persist = persist
        self._db_path = db_path or config.CHECKPOINT_DB
        self._stack = AsyncExitStack()
        self.checkpointer = None
        self.retriever = PolicyRetriever()
        self.mcp = MCPOrdersClient()
        self.router: ModelRouter | None = None
        self.graph = None
        self.config_error: LLMConfigError | None = None
        self._sections = {(c.doc, c.section.lower()): c for c in self.retriever.chunks}

    async def start(self) -> "SupportAgent":
        await self.mcp.start()
        if self._persist:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self.checkpointer = await self._stack.enter_async_context(
                AsyncSqliteSaver.from_conn_string(str(self._db_path)))
        executor = ToolExecutor(self.retriever, self.mcp)
        try:
            if self._llm is None:
                self.router = ModelRouter(TOOL_SCHEMAS)
            self.graph = build_graph(executor, checkpointer=self.checkpointer, llm=self._llm, router=self.router)
        except LLMConfigError as exc:  # start anyway; /api/health and /api/chat explain it
            self.config_error = exc
        return self

    async def close(self) -> None:
        await self._stack.aclose()
        await self.mcp.close()

    async def delete_session(self, thread_id: str) -> None:
        """Forget a conversation (when the customer deletes it from history)."""
        if self.checkpointer is not None and hasattr(self.checkpointer, "adelete_thread"):
            await self.checkpointer.adelete_thread(thread_id)

    def llm_status(self) -> dict:
        models = self.router.status() if self.router else [
            {**config.model_info(m), "state": "disabled", "reason": str(self.config_error or "")}
            for m in dict.fromkeys([config.LLM_MODEL, *config.LLM_FALLBACKS])]
        ready = [m for m in models if m["state"] == "ready"]
        return {
            "ready": self.graph is not None and bool(ready or self._llm),
            "active": ready[0] if ready else None,   # the model the next message will use
            "models": models,
            "error": str(self.config_error) if self.config_error else None,
        }

    def _match_section(self, doc: str, section: str):
        section = section.lower().strip()
        for (d, s), chunk in self._sections.items():
            if d == doc and (s == section or s.startswith(section) or section.startswith(s.split(".")[0] + ".")):
                return chunk
        return None

    async def chat(self, thread_id: str, message: str) -> dict:
        if self.graph is None:
            raise self.config_error or LLMConfigError("model not configured")
        cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 20}
        before = await self.graph.aget_state(cfg)
        start = len(before.values.get("messages", [])) if before and before.values else 0

        result = await self.graph.ainvoke({"messages": [HumanMessage(message)]}, cfg)
        new_msgs = result["messages"][start:]
        final = next((m for m in reversed(new_msgs) if isinstance(m, AIMessage) and not m.tool_calls), None)
        answer = (final.content if final else "") or ""
        if isinstance(answer, list):  # some providers return content blocks
            answer = "".join(b.get("text", "") for b in answer if isinstance(b, dict))

        tools, documents, models = [], [], []
        for m in new_msgs:
            if isinstance(m, AIMessage):
                used = (m.response_metadata or {}).get("shopkart_model")
                if used and used not in models:
                    models.append(used)
            if isinstance(m, ToolMessage):
                art = dict(m.artifact or {"tool": m.name})
                auto = art.pop("auto_policy_check", None)
                if auto:  # show the automatic policy check as its own step
                    tools.append({"tool": "search_policy", "via": "rag", "ok": bool(auto["documents"]),
                                  "auto": True, **auto})
                    documents.extend(auto["documents"])
                tools.append(art)
                documents.extend(art.get("documents", []))

        citations, invalid, seen = [], [], set()
        for doc, section in CITE_RE.findall(answer):
            chunk = self._match_section(doc, section)
            if not chunk:
                invalid.append({"doc": doc, "section": section.strip()})
                continue
            if (chunk.doc, chunk.section) in seen:
                continue
            seen.add((chunk.doc, chunk.section))
            citations.append({"doc": chunk.doc, "section": chunk.section,
                              "title": friendly_title(chunk.doc_title, chunk.effective)})

        return {
            "answer": answer,
            "citations": citations,
            "invalid_citations": invalid,
            "tools_used": tools,
            "documents_retrieved": documents,
            "models_used": models,
            "verified_email": result.get("verified_email"),
        }
