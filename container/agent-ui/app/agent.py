"""LangGraph agent that routes reimbursement requests to the A2A agent."""
import logging
import os
import uuid
from typing import Annotated, Any

from langchain_community.chat_models import ChatLiteLLM
from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from app.a2a_client import ReimbursementA2AClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful assistant that supports employees with expense reimbursement.

When a user wants to file a reimbursement request or is responding to a follow-up question
from the reimbursement process (e.g. providing a date, amount, or approval confirmation),
call the `submit_reimbursement` tool with their message verbatim.

When the reimbursement agent asks for missing information (you will see "MISSING_INFO:" in
the tool response), relay the question naturally to the user — do not expose the raw prefix.

For general questions unrelated to reimbursement, answer directly without using any tools."""


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    a2a_context_id: str | None   # persists for the lifetime of an A2A conversation
    a2a_task_id: str | None      # persists only while a task is in input-required state


def _make_llm() -> Any:
    return ChatLiteLLM(
        model=os.environ["LLM_MODEL_NAME"],
        api_base=os.environ["LLM_API_BASE"],
        api_key=os.environ["LLM_API_KEY"],
    )


def _extract_text(task: dict) -> str:
    """Pull the most relevant text out of a completed or input-required task dict."""
    state = task.get("status", {}).get("state", "unknown")

    if state == "input-required":
        msg = task.get("status", {}).get("message", {})
        parts = msg.get("parts", [])
        return parts[0].get("text", "I need more information.") if parts else "I need more information."

    if state == "completed":
        artifacts = task.get("artifacts", [])
        if artifacts:
            parts = artifacts[0].get("parts", [])
            return parts[0].get("text", "Request completed.") if parts else "Request completed."
        return "Your reimbursement request has been processed."

    if state == "canceled":
        return "The reimbursement request was canceled."

    return f"The request ended with an unexpected status: {state}."


def build_graph() -> Any:
    """Build and compile the LangGraph agent. Returns a compiled graph."""

    # Bind the tool schema to the LLM so it can emit tool-call messages.
    # The actual execution happens in the custom call_a2a node, not via ToolNode,
    # so that we can read and write a2a_context_id / a2a_task_id in graph state.
    @tool
    def submit_reimbursement(query: str) -> str:
        """Submit a reimbursement request or continue an ongoing one.
        Use this for any message related to filing, continuing, or confirming a reimbursement.
        """
        raise NotImplementedError("Intercepted by call_a2a node before execution")

    llm = _make_llm().bind_tools([submit_reimbursement])

    # ── Graph nodes ──────────────────────────────────────────────────────────

    async def call_model(state: AgentState) -> dict:
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(state["messages"])
        response = await llm.ainvoke(messages)
        return {"messages": [response]}

    async def call_a2a(state: AgentState) -> dict:
        last = state["messages"][-1]
        tool_call = last.tool_calls[0]
        query: str = tool_call["args"]["query"]

        # Maintain A2A conversation context across LangGraph turns.
        context_id: str = state.get("a2a_context_id") or str(uuid.uuid4())
        # Reuse the same task_id when the previous turn left the task in
        # input-required state; otherwise start a fresh task.
        task_id: str = state.get("a2a_task_id") or str(uuid.uuid4())

        logger.info(
            "A2A call — context=%s task=%s query=%r", context_id[:8], task_id[:8], query[:60]
        )

        client = ReimbursementA2AClient()
        task = await client.send_and_wait(query, context_id, task_id)

        task_state = task.get("status", {}).get("state", "unknown")
        response_text = _extract_text(task)

        # Keep task_id alive only while the task is still waiting for user input.
        next_task_id = task_id if task_state == "input-required" else None

        logger.info("A2A task %s finished with state: %s", task_id[:8], task_state)

        return {
            "messages": [ToolMessage(content=response_text, tool_call_id=tool_call["id"])],
            "a2a_context_id": context_id,
            "a2a_task_id": next_task_id,
        }

    # ── Routing ──────────────────────────────────────────────────────────────

    def route_after_model(state: AgentState) -> str:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "call_a2a"
        return END

    # ── Graph assembly ───────────────────────────────────────────────────────

    graph = StateGraph(AgentState)
    graph.add_node("call_model", call_model)
    graph.add_node("call_a2a", call_a2a)

    graph.add_edge(START, "call_model")
    graph.add_conditional_edges("call_model", route_after_model, {"call_a2a": "call_a2a", END: END})
    graph.add_edge("call_a2a", "call_model")

    return graph.compile(checkpointer=MemorySaver())
