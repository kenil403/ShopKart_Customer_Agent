"""Tools as the LLM sees them, plus the guarded executor that runs them.

Security is enforced HERE, in code, not in the prompt:
  1. Email verification gate: order tools only run with an email the customer
     actually typed in this conversation, and results are released to the LLM
     only if that email matches the order's email.
  2. Policy-check gate: request_return is refused until the agent has read the
     current returns policy in this conversation (search_policy).
  3. The MCP server re-checks eligibility itself (defence in depth).
"""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import BaseModel, Field

from app.agent.mcp_client import MCPOrdersClient
from app.rag.retriever import PolicyRetriever

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
RETURNS_DOCS = {"03_returns_policy_update_2026.md", "02_returns_policy_2024.md"}


# ---------------------------------------------------------------- schemas
class search_policy(BaseModel):
    """Search ShopKart policies: shipping, returns, warranty, payments, refunds, account."""
    query: str = Field(description="Short search query, e.g. 'return window clothing'.")


class get_customer_orders(BaseModel):
    """List the customer's orders."""
    email: str = Field(description="Email the customer typed.")


class get_order_status(BaseModel):
    """Status and details of one order."""
    order_id: str = Field(description="e.g. ORD-1001")
    email: str | None = Field(default=None, description="Omit if already verified.")


class request_return(BaseModel):
    """File a return; eligibility is checked against the current policy."""
    order_id: str = Field(description="e.g. ORD-1001")
    reason: str = Field(description="Customer's reason in their words.")
    email: str | None = Field(default=None, description="Omit if already verified.")


TOOL_SCHEMAS = [search_policy, get_customer_orders, get_order_status, request_return]


# ---------------------------------------------------------------- helpers
def customer_emails(messages: list) -> set[str]:
    """Emails the customer has typed themselves in this conversation."""
    found: set[str] = set()
    for m in messages:
        if isinstance(m, HumanMessage):
            found.update(e.lower().rstrip(".") for e in EMAIL_RE.findall(str(m.content)))
    return found


def _resolve_email(args: dict, state: dict) -> tuple[str | None, str | None]:
    """Returns (email, error_message)."""
    typed = customer_emails(state["messages"])
    email = (args.get("email") or state.get("verified_email") or "").strip().lower()
    if not email:
        return None, ("VERIFICATION_REQUIRED: The customer has not provided their registered email. "
                      "Ask them for it. Do not share any order information.")
    if email not in typed:
        return None, ("VERIFICATION_REQUIRED: This email was not provided by the customer in this "
                      "conversation. Ask the customer to type their registered email.")
    return email, None


def _msg(call: dict, content: Any, artifact: dict) -> ToolMessage:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return ToolMessage(content=text, tool_call_id=call["id"], name=call["name"], artifact=artifact)


# ---------------------------------------------------------------- executor
class ToolExecutor:
    def __init__(self, retriever: PolicyRetriever, mcp: MCPOrdersClient):
        self.retriever = retriever
        self.mcp = mcp

    async def run(self, call: dict, state: dict) -> tuple[ToolMessage, dict]:
        """Execute one tool call. Returns (ToolMessage, state_updates)."""
        name, args = call["name"], call.get("args", {})
        try:
            handler = getattr(self, f"_{name}")
        except AttributeError:
            return _msg(call, f"Unknown tool {name}", {"tool": name, "ok": False}), {}
        try:
            return await handler(call, args, state)
        except Exception as exc:  # never crash the conversation on a tool error
            return _msg(call, f"TOOL_ERROR: {exc}", {"tool": name, "ok": False, "error": str(exc)}), {}

    async def _search_policy(self, call, args, state):
        hits = self.retriever.search(args.get("query", ""))
        docs = [{"doc": h.chunk.doc, "section": h.chunk.section, "score": round(h.score, 2)} for h in hits]
        updates = {}
        if any(h.chunk.doc in RETURNS_DOCS for h in hits):
            updates["policy_checked"] = True
        artifact = {"tool": "search_policy", "via": "rag", "ok": bool(hits),
                    "query": args.get("query"), "documents": docs}
        return _msg(call, self.retriever.format_hits(hits), artifact), updates

    async def _get_customer_orders(self, call, args, state):
        email, err = _resolve_email(args, state)
        if err:
            return _msg(call, err, {"tool": "get_customer_orders", "via": "mcp", "ok": False,
                                    "blocked": "verification"}), {}
        data = await self.mcp.call("get_customer_orders", email=email)
        if not data.get("count"):
            return _msg(call, "No orders were found for that email address. Ask the customer to "
                              "double-check the email they registered with.",
                        {"tool": "get_customer_orders", "via": "mcp", "ok": False}), {}
        artifact = {"tool": "get_customer_orders", "via": "mcp", "ok": True,
                    "orders": [o["order_id"] for o in data["orders"]]}
        return _msg(call, {"verified": True, **data}, artifact), {"verified_email": email}

    async def _verify_order(self, order_id: str, email: str) -> dict | None:
        data = await self.mcp.call("get_order_status", order_id=order_id)
        if data.get("found") and data.get("customer_email", "").lower() == email:
            return data
        return None

    async def _get_order_status(self, call, args, state):
        email, err = _resolve_email(args, state)
        if err:
            return _msg(call, err, {"tool": "get_order_status", "via": "mcp", "ok": False,
                                    "blocked": "verification"}), {}
        data = await self._verify_order(args["order_id"], email)
        if not data:
            return _msg(call, "VERIFICATION_FAILED: No order with that ID matches the email provided. "
                              "Do not reveal whether the order exists or any of its details.",
                        {"tool": "get_order_status", "via": "mcp", "ok": False, "blocked": "verification"}), {}
        return (_msg(call, {"verified": True, "order": data["order"]},
                     {"tool": "get_order_status", "via": "mcp", "ok": True, "order_id": data["order"]["order_id"]}),
                {"verified_email": email})

    async def _request_return(self, call, args, state):
        email, err = _resolve_email(args, state)
        if err:
            return _msg(call, err, {"tool": "request_return", "via": "mcp", "ok": False,
                                    "blocked": "verification"}), {}
        verified = await self._verify_order(args["order_id"], email)
        if not verified:
            return _msg(call, "VERIFICATION_FAILED: No order with that ID matches the email provided. "
                              "Do not reveal whether the order exists or any of its details.",
                        {"tool": "request_return", "via": "mcp", "ok": False, "blocked": "verification"}), {}

        # Policy check: if the agent hasn't read the returns policy in this chat, read it now,
        # targeted at this order's item categories and the customer's reason.
        auto_check = None
        if not state.get("policy_checked"):
            cats = " ".join(sorted({i.get("category", "") for i in verified["order"].get("items", [])}))
            query = f"return window {cats} {args.get('reason', '')} damaged defective non-returnable"
            hits = self.retriever.search(query)
            auto_check = {"query": query.strip(), "documents": [
                {"doc": h.chunk.doc, "section": h.chunk.section, "score": round(h.score, 2)} for h in hits]}

        data = await self.mcp.call("request_return", order_id=args["order_id"], reason=args.get("reason", ""))
        data.pop("customer_email", None)
        artifact = {"tool": "request_return", "via": "mcp", "ok": bool(data.get("success")),
                    "outcome": data.get("outcome"), "order_id": args["order_id"],
                    "citations": data.get("citations", [])}
        if auto_check:
            artifact["auto_policy_check"] = auto_check
            data["policy_checked"] = [f"{d['doc']} § {d['section']}" for d in auto_check["documents"]]
        return _msg(call, data, artifact), {"verified_email": email, "policy_checked": True}
