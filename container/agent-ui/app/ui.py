"""Streamlit chat UI for the reimbursement assistant."""
import asyncio
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


# ── Session bootstrap ────────────────────────────────────────────────────────

def _init_session() -> None:
    if "graph" not in st.session_state:
        st.session_state.graph = build_graph()
    if "thread_id" not in st.session_state:
        st.session_state.thread_id = str(uuid.uuid4())
    if "chat_history" not in st.session_state:
        st.session_state.chat_history: list[dict] = []


def _lg_config() -> dict:
    return {"configurable": {"thread_id": st.session_state.thread_id}}


def _current_a2a_state() -> AgentState | None:
    snapshot = st.session_state.graph.get_state(_lg_config())
    return snapshot.values if snapshot and snapshot.values else None


# ── Async processing ─────────────────────────────────────────────────────────

async def _run_agent(user_input: str) -> str:
    result = await st.session_state.graph.ainvoke(
        {"messages": [HumanMessage(content=user_input)]},
        config=_lg_config(),
    )
    # Return the last non-tool-calling AI message.
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return "I was unable to process your request."


async def _cancel_current_task(task_id: str) -> None:
    client = ReimbursementA2AClient()
    await client.cancel_task(task_id)


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
            if ctx_id:
                st.divider()
                st.caption("A2A conversation context")
                st.code(ctx_id, language=None)
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
        st.caption("Transport: async HTTP polling")


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

    # Render chat history
    for entry in st.session_state.chat_history:
        with st.chat_message(entry["role"]):
            st.markdown(entry["content"])

    # Handle new user input
    if user_input := st.chat_input("e.g. I need to reimburse a hotel stay of $850"):
        st.session_state.chat_history.append({"role": "user", "content": user_input})
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
                    response = asyncio.run(_run_agent(user_input))
                except Exception as exc:
                    response = f"An error occurred: {exc}"
                    logging.exception("Agent error")

            st.markdown(response)

        st.session_state.chat_history.append({"role": "assistant", "content": response})
        st.rerun()


if __name__ == "__main__":
    main()
