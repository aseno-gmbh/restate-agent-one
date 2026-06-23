"""Async JSON-RPC 2.0 client for the reimbursement A2A agent."""
import asyncio
import logging
import os
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TERMINAL_STATES = frozenset({
    "TASK_STATE_COMPLETED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_FAILED",
    "TASK_STATE_INPUT_REQUIRED",
    "TASK_STATE_REJECTED",
    "TASK_STATE_AUTH_REQUIRED",
})

# 60 s read — long enough for LLM inference, short enough to detect a
# suspended awakeable quickly and fall through to the polling path.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=5.0)


def get_endpoint_url() -> str:
    restate_host = os.environ.get("RESTATE_HOST", "http://localhost:8080")
    return f"{restate_host}/ReimbursementAgentA2AServer/process_request"


class ReimbursementA2AClient:
    def __init__(
        self,
        endpoint_url: str | None = None,
        poll_interval: float = 3.0,
        timeout: httpx.Timeout = _DEFAULT_TIMEOUT,
    ):
        self._url = endpoint_url or get_endpoint_url()
        self._poll_interval = poll_interval
        self._timeout = timeout

    async def _rpc(self, method: str, params: dict, rpc_id: Any | None = None) -> dict:
        if rpc_id is None:
            rpc_id = str(uuid.uuid4())
        payload = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            raise RuntimeError(
                f"A2A RPC error {data['error']['code']}: {data['error']['message']}"
            )
        return data.get("result", {})

    async def send_message(
        self,
        text: str,
        context_id: str,
        task_id: str,
        message_id: str | None = None,
    ) -> dict:
        params = {
            "message": {
                "role": "ROLE_USER",
                "parts": [{"text": text}],
                "messageId": message_id or str(uuid.uuid4()),
                "taskId": task_id,
                "contextId": context_id,
            },
            "metadata": {},
        }
        return await self._rpc("SendMessage", params)

    async def get_task(self, task_id: str) -> dict:
        return await self._rpc("GetTask", {"id": task_id})

    async def cancel_task(self, task_id: str) -> dict:
        return await self._rpc("CancelTask", {"id": task_id})

    async def wait_for_result(self, task_id: str, max_wait_secs: float = 90.0) -> dict:
        """Poll get_task until terminal state or max_wait_secs is exceeded.

        Returns the task dict in whatever state it is when the deadline fires —
        the caller is responsible for handling non-terminal states (e.g. when
        the task is blocked on a human-approval awakeable).
        """
        import time
        deadline = time.monotonic() + max_wait_secs
        task: dict = {}
        while True:
            task = await self.get_task(task_id)
            state = task.get("status", {}).get("state", "")
            logger.info("Polling task %s — state: %s", task_id, state)
            if state in _TERMINAL_STATES:
                return task
            if time.monotonic() >= deadline:
                logger.warning(
                    "Polling deadline exceeded for task %s — last state: %s "
                    "(task is likely awaiting external approval)",
                    task_id, state,
                )
                return task
            await asyncio.sleep(self._poll_interval)

    async def send_and_wait(
        self,
        text: str,
        context_id: str,
        task_id: str,
        message_id: str | None = None,
    ) -> dict:
        """Send a message and block until the task reaches a terminal state.

        SendMessage is synchronous in Restate — the HTTP response already
        contains the final task dict for most cases. Falls back to polling
        if the connection times out (e.g. waiting for human approval).
        """
        try:
            task = await self.send_message(text, context_id, task_id, message_id)
        except httpx.TimeoutException:
            logger.warning(
                "send_message timed out for task %s — falling back to polling", task_id
            )
            return await self.wait_for_result(task_id)

        state = task.get("status", {}).get("state", "")
        if state not in _TERMINAL_STATES:
            return await self.wait_for_result(task_id)
        return task
