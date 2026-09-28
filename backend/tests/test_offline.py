"""Offline tests (no API key needed): policy engine, retriever, MCP, and the
LangGraph flow driven by a scripted fake LLM.   Run:  pytest -q"""
import os
import tempfile
from datetime import date
from pathlib import Path

os.environ.setdefault("STORE_TODAY", "2026-09-26")
# tempfile works on Windows too ("/tmp" does not exist there)
os.environ.setdefault("RETURNS_LOG", str(Path(tempfile.gettempdir()) / "shopkart_test_returns.json"))

import pytest  # noqa: E402
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from app import config  # noqa: E402
from app.agent.runner import SupportAgent  # noqa: E402
from app.orders_repo import OrdersRepo  # noqa: E402
from app.policy.return_rules import check_return_eligibility, classify_reason  # noqa: E402
from app.rag.retriever import PolicyRetriever  # noqa: E402

TODAY = date(2026, 9, 26)
repo = OrdersRepo(config.ORDERS_FILE)


# ----------------------------------------------------------- policy engine
@pytest.mark.parametrize("oid,reason,eligible", [
    ("ORD-1001", "size too small", True),          # clothing, 8 days < 15
    ("ORD-1002", "I don't like it", False),        # electronics, change of mind
    ("ORD-1002", "arrived damaged", True),         # electronics, damaged, 4 days < 10
    ("ORD-1003", "changed my mind", False),        # not delivered
    ("ORD-1004", "stopped working", False),        # electronics, 21 days > 10
    ("ORD-1005", "arrived dented", True),          # damaged within 48h
    ("ORD-1006", "changed my mind", False),        # socks, non-returnable
    ("ORD-1007", "too big", False),                # final sale
    ("ORD-1010", "not needed", False),             # home, 24 days > 15
    ("ORD-1011", "changed my mind", False),        # customised
    ("ORD-1012", "no longer needed", False),       # gift card
])
def test_eligibility(oid, reason, eligible):
    assert check_return_eligibility(repo.get(oid), reason, TODAY).eligible is eligible


def test_damage_within_48h_requires_photo():
    d = check_return_eligibility(repo.get("ORD-1005"), "arrived damaged", TODAY)
    assert any("photo" in r.lower() for r in d.requirements)


def test_reason_classifier():
    assert classify_reason("it arrived damaged") == "damaged"
    assert classify_reason("wrong item sent") == "defective_or_wrong"
    assert classify_reason("I changed my mind") == "change_of_mind"


# ----------------------------------------------------------- retriever
def test_retriever_finds_current_return_window():
    hits = PolicyRetriever().search("Can I return jeans after 12 days?")
    assert hits[0].chunk.doc == "03_returns_policy_update_2026.md"


def test_retriever_no_match():
    assert PolicyRetriever().search("loyalty points program") == []


def test_old_policy_is_flagged_superseded():
    old = [c for c in PolicyRetriever().chunks if c.doc.startswith("02") and c.section.startswith("5.")][0]
    assert "SUPERSEDED" in old.render()


# ----------------------------------------------------------- graph flow
class ScriptedLLM(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def call(name, args, i):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}", "type": "tool_call"}])


@pytest.mark.asyncio
async def test_multiturn_return_flow_with_gates():
    email = "priya.sharma@example.com"
    llm = ScriptedLLM(responses=[
        # turn 1
        call("get_customer_orders", {"email": email}, 1),
        AIMessage(content="You have 3 orders: 1. ORD-1001 2. ORD-1002 3. ORD-1003"),
        # turn 2: return directly -> the tool checks the policy automatically
        call("request_return", {"order_id": "ORD-1002", "reason": "arrived damaged"}, 2),
        AIMessage(content="Done! [03_returns_policy_update_2026.md § 1. New Return Window]"),
        # turn 3: asks about someone else's order
        call("get_order_status", {"order_id": "ORD-1004"}, 5),
        AIMessage(content="I couldn't verify that order with your email."),
    ])
    agent = await SupportAgent(llm=llm).start()
    try:
        t1 = await agent.chat("t1", f"What's the status of my orders? My email is {email}")
        assert t1["tools_used"][0]["ok"] and t1["verified_email"] == email

        t2 = await agent.chat("t1", "Return the second one, it arrived damaged.")
        auto, ret = t2["tools_used"]
        assert auto["tool"] == "search_policy" and auto["auto"]
        assert any(d["doc"].startswith("03_") for d in auto["documents"])
        assert ret["ok"] and ret["order_id"] == "ORD-1002"
        assert t2["citations"][0]["title"] == "Returns Policy Update (2026)"

        t3 = await agent.chat("t1", "What about ORD-1004?")
        assert t3["tools_used"][0].get("blocked") == "verification"
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_email_not_typed_by_customer_is_rejected():
    llm = ScriptedLLM(responses=[
        call("get_customer_orders", {"email": "rahul.mehta@example.com"}, 1),  # LLM invents an email
        AIMessage(content="Could you share your registered email?"),
    ])
    agent = await SupportAgent(llm=llm).start()
    try:
        r = await agent.chat("t2", "Show me my orders")
        assert r["tools_used"][0].get("blocked") == "verification"
        assert r["verified_email"] is None
    finally:
        await agent.close()


# ----------------------------------------------------------- model router
class FakeModel:
    """Stands in for a bound chat model; raises the queued errors, then answers."""
    def __init__(self, name, errors=()):
        self.name, self.errors, self.calls = name, list(errors), 0

    async def ainvoke(self, messages):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return AIMessage(content=f"answer from {self.name}")


def make_router(monkeypatch, fakes: dict):
    import app.agent.llm as llm_mod
    monkeypatch.setattr(llm_mod.config, "LLM_MODEL", "groq:openai/gpt-oss-120b")
    monkeypatch.setattr(llm_mod.config, "LLM_FALLBACKS",
                        ["google_genai:gemini-3.1-flash-lite", "mistralai:mistral-small-latest"])
    monkeypatch.setattr(llm_mod, "_build", lambda model, tools: fakes[model])
    return llm_mod.ModelRouter([])


@pytest.mark.asyncio
async def test_router_replays_the_reported_failure(monkeypatch):
    """Groq 404 (retired model), Gemini daily quota, Mistral 1 req/s limit."""
    groq = FakeModel("groq", [Exception("Error code: 404 - model_not_found: llama-3.3-70b-versatile does not exist")])
    gemini = FakeModel("gemini", [Exception("429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProjectPerModel-FreeTier. Please retry in 24.7s.")])
    mistral = FakeModel("mistral", [Exception("Error response 429: Rate limit exceeded")])
    router = make_router(monkeypatch, {"groq:openai/gpt-oss-120b": groq,
                                       "google_genai:gemini-3.1-flash-lite": gemini,
                                       "mistralai:mistral-small-latest": mistral})
    from app.agent.llm import AllModelsUnavailable, describe_error
    with pytest.raises(AllModelsUnavailable) as info:
        await router.ainvoke([])
    states = {s["provider"]: s for s in router.status()}
    assert states["groq"]["state"] == "disabled"                                      # 404 -> never retried
    assert states["google_genai"]["state"] == "cooling" and states["google_genai"]["retry_in_seconds"] > 3000  # daily
    assert states["mistralai"]["state"] == "cooling" and states["mistralai"]["retry_in_seconds"] <= 10
    assert (groq.calls, gemini.calls, mistral.calls) == (1, 1, 1)                     # no double calls
    err = describe_error(info.value)
    assert err["type"] == "busy" and "try again in about" in err["message"]
    assert "Groq" not in err["message"] and "429" not in err["message"]                # customer-safe


@pytest.mark.asyncio
async def test_router_switches_and_returns_to_main_model(monkeypatch):
    groq = FakeModel("groq", [Exception("Error code: 429 - Rate limit reached. Please try again in 7.5s")])
    gemini = FakeModel("gemini")
    router = make_router(monkeypatch, {"groq:openai/gpt-oss-120b": groq,
                                       "google_genai:gemini-3.1-flash-lite": gemini,
                                       "mistralai:mistral-small-latest": FakeModel("mistral")})
    first = await router.ainvoke([])
    assert first.content == "answer from gemini"                      # switched automatically
    assert first.response_metadata["shopkart_model"].startswith("google_genai")
    second = await router.ainvoke([])
    assert second.content == "answer from gemini" and groq.calls == 1  # groq skipped while cooling
    router.slots[0].until = 0                                          # cooldown over
    third = await router.ainvoke([])
    assert third.content == "answer from groq"                         # back to the main model


def test_retry_after_parsing():
    from app.agent.llm import parse_retry_after
    assert parse_retry_after("Please retry in 24.788s.") == pytest.approx(24.788)
    assert parse_retry_after("Please try again in 6m11.52s.") == pytest.approx(371.52)
    assert parse_retry_after("'retryDelay': '33s'") == 33
    assert parse_retry_after("no hint here") is None


# ----------------------------------------------------------- persistent history
@pytest.mark.asyncio
async def test_conversation_memory_survives_restart_and_can_be_deleted(tmp_path):
    db = tmp_path / "conversations.sqlite"
    email = "priya.sharma@example.com"
    first = ScriptedLLM(responses=[
        call("get_customer_orders", {"email": email}, 1),
        AIMessage(content="You have 3 orders."),
    ])
    agent = await SupportAgent(llm=first, persist=True, db_path=db).start()
    await agent.chat("hist-1", f"My email is {email}, list my orders")
    await agent.close()                                    # backend "restarts"

    second = ScriptedLLM(responses=[AIMessage(content="Sure.")])
    agent = await SupportAgent(llm=second, persist=True, db_path=db).start()
    try:
        state = await agent.graph.aget_state({"configurable": {"thread_id": "hist-1"}})
        assert state.values["verified_email"] == email      # still verified after restart
        assert len(state.values["messages"]) == 4           # full history reloaded
        await agent.delete_session("hist-1")
        state = await agent.graph.aget_state({"configurable": {"thread_id": "hist-1"}})
        assert not state.values                             # deleted
    finally:
        await agent.close()
