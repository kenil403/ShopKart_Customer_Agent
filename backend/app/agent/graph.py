"""The LangGraph agent.

    START ──► agent ──(tool calls?)──► tools ──► agent ... ──► END

* `agent`  : the LLM (through the ModelRouter) decides whether to search
             policies, call an order tool, or ask the customer a question.
* `tools`  : custom executor so every call passes through the verification
             gate and the automatic policy check, and can update graph state.
* Memory   : a checkpointer keyed by thread_id keeps the whole conversation, so
             "return the second one" can be resolved in a later turn.
* Tokens   : only the last few turns are sent in full; older tool results are
             shortened. Free tiers limit tokens per minute, so this matters.
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app import config
from app.agent.llm import ModelRouter
from app.agent.prompts import SYSTEM_PROMPT, verification_note
from app.agent.tools import TOOL_SCHEMAS, ToolExecutor

FULL_TURNS = 3          # recent customer turns sent in full
OLD_TOOL_CHARS = 240    # older tool results are cut to this length


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    verified_email: str | None
    policy_checked: bool


def sanitize(messages: list) -> list:
    """Repair history left behind by a failed turn: a customer message directly
    after a tool result is rejected by Mistral and Gemini."""
    fixed = []
    for m in messages:
        if isinstance(m, HumanMessage) and fixed and isinstance(fixed[-1], ToolMessage):
            fixed.append(AIMessage(content="(The previous request could not be completed.)"))
        fixed.append(m)
    return fixed


def compact(messages: list) -> list:
    """Shorten tool results older than the last FULL_TURNS customer turns.
    Messages are never dropped, so tool-call/tool-result pairs stay valid."""
    human_idx = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    if len(human_idx) <= FULL_TURNS:
        return messages
    cutoff = human_idx[-FULL_TURNS]
    out = []
    for i, m in enumerate(messages):
        if i < cutoff and isinstance(m, ToolMessage) and len(str(m.content)) > OLD_TOOL_CHARS:
            m = ToolMessage(content=str(m.content)[:OLD_TOOL_CHARS] + " …[older result shortened]",
                            tool_call_id=m.tool_call_id, name=m.name)
        out.append(m)
    return out


def build_graph(executor: ToolExecutor, checkpointer=None, llm=None, router: ModelRouter | None = None):
    """llm: optional pre-built chat model (offline tests). Otherwise the router is used."""
    model = llm.bind_tools(TOOL_SCHEMAS) if llm is not None else (router or ModelRouter(TOOL_SCHEMAS))

    async def agent_node(state: AgentState) -> dict:
        system = SystemMessage(SYSTEM_PROMPT.format(
            today=config.today().strftime("%d %B %Y"),
            verification_note=verification_note(state.get("verified_email")),
        ))
        response = await model.ainvoke([system, *compact(sanitize(state["messages"]))])
        return {"messages": [response]}

    async def tools_node(state: AgentState) -> dict:
        last: AIMessage = state["messages"][-1]
        messages, updates = [], {}
        working = dict(state)
        for call in last.tool_calls:
            msg, upd = await executor.run(call, working)
            messages.append(msg)
            updates.update(upd)
            working.update(upd)  # later calls in the same step see earlier verification
        return {"messages": messages, **updates}

    def route(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer or MemorySaver())
