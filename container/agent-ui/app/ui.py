"""Streamlit chat UI for the reimbursement assistant."""
import asyncio
import json
import logging
import uuid

import streamlit as st
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage

from app.a2a_client import ReimbursementA2AClient
from app.agent import AgentState, build_graph

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] - %(message)s",
)

_STATE_COLORS = {
    "TASK_STATE_COMPLETED": "green",
    "TASK_STATE_INPUT_REQUIRED": "orange",
    "TASK_STATE_CANCELED": "gray",
    "TASK_STATE_FAILED": "red",
    "TASK_STATE_REJECTED": "red",
    "TASK_STATE_AUTH_REQUIRED": "red",
}


# ── Session bootstrap ────────────────────────────────────────────────────────

def _init_session() -> None:
    if "graph" not in st.session_state:
        st.session_state.graph = build_graph()
    if "thread_id" not in st.session_state:
        st.session_state.thread_id = str(uuid.uuid4())
    if "chat_history" not in st.session_state:
        # Each entry: {"role": str, "content": str, "a2a_exchange": dict | None}
        st.session_state.chat_history: list[dict] = []


def _lg_config() -> dict:
    return {"configurable": {"thread_id": st.session_state.thread_id}}


def _current_a2a_state() -> AgentState | None:
    snapshot = st.session_state.graph.get_state(_lg_config())
    return snapshot.values if snapshot and snapshot.values else None


# ── Async processing ─────────────────────────────────────────────────────────

async def _run_agent(user_input: str) -> tuple[str, dict | None]:
    """Invoke the graph and return (response_text, latest_a2a_exchange | None)."""
    # Count exchanges before so we can identify what was added this turn.
    state_before = st.session_state.graph.get_state(_lg_config())
    n_before = len((state_before.values or {}).get("a2a_exchanges", []))

    result = await st.session_state.graph.ainvoke(
        {"messages": [HumanMessage(content=user_input)]},
        config=_lg_config(),
    )

    # Pick up any exchange added during this turn.
    all_exchanges: list[dict] = result.get("a2a_exchanges", [])
    new_exchanges = all_exchanges[n_before:]
    latest_exchange = new_exchanges[-1] if new_exchanges else None

    # Return the last non-tool-calling AI message.
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            return text, latest_exchange

    return "I was unable to process your request.", latest_exchange


async def _cancel_current_task(task_id: str) -> None:
    client = ReimbursementA2AClient()
    await client.cancel_task(task_id)


# ── A2A exchange renderer ────────────────────────────────────────────────────

def _render_a2a_exchange(exchange: dict) -> None:
    state = exchange.get("task_state", "unknown")
    color = _STATE_COLORS.get(state, "gray")
    label = state.replace("TASK_STATE_", "").replace("_", " ").title()

    with st.expander(f"A2A — :{color}[{label}]", expanded=False):
        col_req, col_res = st.columns(2)

        with col_req:
            st.markdown("**Request →**")
            st.markdown("*Sent to ReimbursementAgentA2AServer*")
            st.code(exchange.get("query", ""), language=None)
            st.caption(f"Context ID: `{exchange.get('context_id', '')}`")
            st.caption(f"Task ID:    `{exchange.get('task_id', '')}`")

        with col_res:
            st.markdown("**Response ←**")
            st.markdown(f"Status: :{color}[**{label}**]")
            st.markdown(exchange.get("response_text", ""))
            st.markdown("*Raw task payload*")
            st.json(exchange.get("task_raw", {}))


# ── Sidebar ──────────────────────────────────────────────────────────────────

def _render_sidebar() -> None:
    with st.sidebar:
        st.header("Session")
        st.caption("Thread ID")
        st.code(st.session_state.thread_id, language=None)

        if st.button("New conversation", use_container_width=True, type="secondary"):
            st.session_state.graph = build_graph()
            st.session_state.thread_id = str(uuid.uuid4())
            st.session_state.chat_history = []
            st.rerun()

        a2a = _current_a2a_state()
        if a2a:
            ctx_id = a2a.get("a2a_context_id")
            task_id = a2a.get("a2a_task_id")
            exchanges = a2a.get("a2a_exchanges", [])
            if ctx_id:
                st.divider()
                st.caption("A2A conversation context")
                st.code(ctx_id, language=None)
                st.caption(f"{len(exchanges)} A2A exchange(s) this session")
            if task_id:
                st.caption("Active task (awaiting input)")
                st.code(task_id, language=None)
                if st.button("Cancel task", use_container_width=True, type="primary"):
                    with st.spinner("Cancelling…"):
                        asyncio.run(_cancel_current_task(task_id))
                    st.toast("Task cancelled.", icon="🛑")
                    st.rerun()

        st.divider()
        st.caption("Reimbursement Agent")
        st.write("`ReimbursementAgentA2AServer`")
        st.caption("Protocol: A2A / JSON-RPC 2.0")
        st.caption("Transport: async HTTP")


# ── Main UI ──────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="Reimbursement Assistant",
        page_icon="💸",
        layout="centered",
    )

    _init_session()
    _render_sidebar()

    st.title("💸 Reimbursement Assistant")
    st.caption("Ask me to process expense reimbursements. Powered by LangGraph + Restate A2A.")

    # Render chat history (including any A2A exchange details)
    for entry in st.session_state.chat_history:
        with st.chat_message(entry["role"]):
            st.markdown(entry["content"])
            if entry.get("a2a_exchange"):
                _render_a2a_exchange(entry["a2a_exchange"])

    # Handle new user input
    if user_input := st.chat_input("e.g. I need to reimburse a hotel stay of $850"):
        st.session_state.chat_history.append(
            {"role": "user", "content": user_input, "a2a_exchange": None}
        )
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            a2a = _current_a2a_state()
            status_label = (
                "Continuing reimbursement request…"
                if a2a and a2a.get("a2a_task_id")
                else "Processing…"
            )
            with st.spinner(status_label):
                try:
                    response, exchange = asyncio.run(_run_agent(user_input))
                except Exception as exc:
                    response, exchange = f"An error occurred: {exc}", None
                    logging.exception("Agent error")

            st.markdown(response)
            if exchange:
                _render_a2a_exchange(exchange)

        st.session_state.chat_history.append(
            {"role": "assistant", "content": response, "a2a_exchange": exchange}
        )
        st.rerun()


if __name__ == "__main__":
    main()
