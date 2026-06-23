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

_STATE_COLORS = {
    "TASK_STATE_COMPLETED": "green",
    "TASK_STATE_INPUT_REQUIRED": "orange",
    "TASK_STATE_WORKING": "blue",
    "TASK_STATE_SUBMITTED": "blue",
    "TASK_STATE_CANCELED": "gray",
    "TASK_STATE_FAILED": "red",
    "TASK_STATE_REJECTED": "red",
    "TASK_STATE_AUTH_REQUIRED": "red",
}

_STATE_LABELS = {
    "TASK_STATE_COMPLETED": "Completed",
    "TASK_STATE_INPUT_REQUIRED": "Input Required",
    "TASK_STATE_WORKING": "Waiting for Approval",
    "TASK_STATE_SUBMITTED": "Waiting for Approval",
    "TASK_STATE_CANCELED": "Canceled",
    "TASK_STATE_FAILED": "Failed",
    "TASK_STATE_REJECTED": "Rejected",
    "TASK_STATE_AUTH_REQUIRED": "Auth Required",
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
    state_before = st.session_state.graph.get_state(_lg_config())
    n_before = len((state_before.values or {}).get("a2a_exchanges", []))

    result = await st.session_state.graph.ainvoke(
        {"messages": [HumanMessage(content=user_input)]},
        config=_lg_config(),
    )

    all_exchanges: list[dict] = result.get("a2a_exchanges", [])
    latest_exchange = all_exchanges[n_before] if len(all_exchanges) > n_before else None

    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            return text, latest_exchange

    return "I was unable to process your request.", latest_exchange


async def _cancel_current_task(task_id: str) -> None:
    client = ReimbursementA2AClient()
    await client.cancel_task(task_id)


# ── A2A exchange renderers ────────────────────────────────────────────────────

def _state_badge(state: str) -> str:
    color = _STATE_COLORS.get(state, "gray")
    label = _STATE_LABELS.get(state, state.replace("TASK_STATE_", "").replace("_", " ").title())
    return f":{color}[**{label}**]"


def _render_a2a_exchange_inline(exchange: dict) -> None:
    """Compact expander shown below an assistant message in the Chat tab."""
    state = exchange.get("task_state", "unknown")
    label = _STATE_LABELS.get(state, state)
    color = _STATE_COLORS.get(state, "gray")

    with st.expander(f"A2A — :{color}[{label}]", expanded=False):
        col_req, col_res = st.columns(2)
        with col_req:
            st.markdown("**→ Sent**")
            st.code(exchange.get("query", ""), language=None)
            st.caption(f"Context: `{exchange.get('context_id', '')}`")
            st.caption(f"Task:    `{exchange.get('task_id', '')}`")
        with col_res:
            st.markdown("**← Received**")
            st.markdown(_state_badge(state))
            st.markdown(exchange.get("response_text", ""))
            with st.expander("Raw payload", expanded=False):
                st.json(exchange.get("task_raw", {}))


def _render_a2a_exchange_full(idx: int, exchange: dict) -> None:
    """Full-detail card used in the A2A Log tab."""
    state = exchange.get("task_state", "unknown")
    label = _STATE_LABELS.get(state, state)
    color = _STATE_COLORS.get(state, "gray")
    ts = exchange.get("timestamp", "")
    ts_display = ts[:19].replace("T", " ") + " UTC" if ts else "—"

    with st.expander(
        f"Exchange {idx} · :{color}[{label}] · {ts_display}",
        expanded=(idx == 1),
    ):
        st.markdown(f"**Context ID:** `{exchange.get('context_id', '')}`")
        st.markdown(f"**Task ID:** `{exchange.get('task_id', '')}`")

        if exchange.get("approval_pending"):
            st.warning(
                "⏳ This task is blocked on **manual approval**. "
                "Resolve the awakeable in the Restate system to unblock it.",
                icon="⏳",
            )

        st.divider()
        st.markdown("##### Request sent to ReimbursementAgent")
        st.code(exchange.get("query", ""), language=None)

        st.divider()
        st.markdown(f"##### Response — {_state_badge(state)}")
        st.markdown(exchange.get("response_text", ""))

        st.divider()
        st.markdown("##### Raw task payload (JSON)")
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
                st.caption("A2A context")
                st.code(ctx_id, language=None)
                st.caption(f"{len(exchanges)} exchange(s) this session")

            if task_id:
                st.caption("Active task")
                st.code(task_id, language=None)
                if exchanges and exchanges[-1].get("approval_pending"):
                    st.warning("Waiting for manual approval", icon="⏳")
                    st.caption(
                        "Resolve the awakeable via Restate ingress (port 8080) to unblock."
                    )
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

    chat_tab, approval_tab, log_tab = st.tabs(["💬 Chat", "🔐 Approval", "🔗 A2A Log"])

    # ── Chat tab ─────────────────────────────────────────────────────────────
    with chat_tab:
        a2a = _current_a2a_state()
        if a2a:
            exchanges = a2a.get("a2a_exchanges", [])
            if exchanges and exchanges[-1].get("approval_pending"):
                task_id_short = exchanges[-1].get("task_id", "")[:8]
                st.warning(
                    f"⏳ **Waiting for manual approval** — task `{task_id_short}…` is on hold. "
                    f"A manager must resolve the awakeable in Restate to continue. "
                    f"Check the agent logs or the Restate UI (port 9070) for the approval command.",
                    icon="⏳",
                )

        for entry in st.session_state.chat_history:
            with st.chat_message(entry["role"]):
                st.markdown(entry["content"])
                if entry.get("a2a_exchange"):
                    _render_a2a_exchange_inline(entry["a2a_exchange"])

    # ── Approval tab ─────────────────────────────────────────────────────────
    with approval_tab:
        st.subheader("Human-in-the-Loop Approval")

        a2a_approval = _current_a2a_state()
        pending_exchange = None
        if a2a_approval:
            exchanges = a2a_approval.get("a2a_exchanges", [])
            if exchanges and exchanges[-1].get("approval_pending"):
                pending_exchange = exchanges[-1]

        if pending_exchange:
            task_id = pending_exchange.get("task_id", "")
            st.info(
                f"Task **`{task_id}`** is awaiting manual approval.\n\n"
                "Copy the **awakeable ID** from the agent logs "
                "(search for *'Awaiting human approval'*) and paste it below.",
                icon="⏳",
            )

            awakeable_id = st.text_input(
                "Awakeable ID",
                placeholder="sign_...",
                help="Found in agent-a2a logs: 'curl ... /restate/awakeables/<ID>/resolve'",
                key="awakeable_id_input",
            )

            col_approve, col_reject = st.columns(2)
            with col_approve:
                approve_clicked = st.button(
                    "✅ Approve", type="primary", use_container_width=True,
                    disabled=not awakeable_id,
                )
            with col_reject:
                reject_clicked = st.button(
                    "❌ Reject", type="secondary", use_container_width=True,
                    disabled=not awakeable_id,
                )

            if approve_clicked and awakeable_id:
                with st.spinner("Sending approval…"):
                    try:
                        asyncio.run(ReimbursementA2AClient().resolve_awakeable(awakeable_id, True))
                        st.success("Approval sent — the reimbursement workflow will continue.", icon="✅")
                    except Exception as exc:
                        st.error(f"Failed to resolve awakeable: {exc}", icon="🚨")

            if reject_clicked and awakeable_id:
                with st.spinner("Sending rejection…"):
                    try:
                        asyncio.run(ReimbursementA2AClient().resolve_awakeable(awakeable_id, False))
                        st.warning("Rejection sent — the request will be denied.", icon="❌")
                    except Exception as exc:
                        st.error(f"Failed to resolve awakeable: {exc}", icon="🚨")
        else:
            st.success("No pending approvals at the moment.", icon="✅")

    # ── A2A Log tab ──────────────────────────────────────────────────────────
    with log_tab:
        st.subheader("A2A Communication Log")
        a2a_for_log = _current_a2a_state()
        all_exchanges = (a2a_for_log or {}).get("a2a_exchanges", [])

        if not all_exchanges:
            st.info(
                "No A2A exchanges yet. Start a reimbursement request in the Chat tab "
                "to see the full protocol communication here.",
                icon="ℹ️",
            )
        else:
            st.caption(
                f"{len(all_exchanges)} exchange(s) · "
                f"Context: `{all_exchanges[0].get('context_id', '—')}`"
            )
            # Most recent first
            for i, ex in enumerate(reversed(all_exchanges), start=1):
                _render_a2a_exchange_full(i, ex)

    # ── Chat input (page-level — always visible regardless of active tab) ─────
    if user_input := st.chat_input("e.g. I need to reimburse a hotel stay of $850"):
        st.session_state.chat_history.append(
            {"role": "user", "content": user_input, "a2a_exchange": None}
        )

        a2a_now = _current_a2a_state()
        last_exchange = ((a2a_now or {}).get("a2a_exchanges") or [{}])[-1]
        approval_waiting = bool(last_exchange.get("approval_pending"))
        status_label = (
            "Checking approval status…"
            if approval_waiting
            else "Continuing reimbursement request…"
            if a2a_now and a2a_now.get("a2a_task_id")
            else "Processing…"
        )

        with st.spinner(status_label):
            try:
                response, exchange = asyncio.run(_run_agent(user_input))
            except Exception as exc:
                response, exchange = f"An error occurred: {exc}", None
                logging.exception("Agent error")

        st.session_state.chat_history.append(
            {"role": "assistant", "content": response, "a2a_exchange": exchange}
        )
        st.rerun()


if __name__ == "__main__":
    main()
