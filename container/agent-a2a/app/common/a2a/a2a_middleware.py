import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

import restate
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.timestamp_pb2 import Timestamp as PBTimestamp
from pydantic import BaseModel

from a2a.types import (
    AgentCard,
    Artifact,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    Message,
    Part,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskStatus,
)
from a2a.types.a2a_pb2 import (
    ROLE_AGENT,
    TASK_STATE_CANCELED,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_INPUT_REQUIRED,
    TASK_STATE_SUBMITTED,
)

from .models import A2AAgent

logger = logging.getLogger(__name__)

TASK = "task"
INVOCATION_ID = "invocation-id"

# v1.0 JSON-RPC method name → protobuf request class
METHOD_TO_MODEL: dict[str, type] = {
    "SendMessage": SendMessageRequest,
    "GetTask": GetTaskRequest,
    "CancelTask": CancelTaskRequest,
    "SubscribeToTask": SubscribeToTaskRequest,
    "CreateTaskPushNotificationConfig": TaskPushNotificationConfig,
    "GetTaskPushNotificationConfig": GetTaskPushNotificationConfigRequest,
    "ListTaskPushNotificationConfigs": ListTaskPushNotificationConfigsRequest,
    "DeleteTaskPushNotificationConfig": DeleteTaskPushNotificationConfigRequest,
    "GetExtendedAgentCard": GetExtendedAgentCardRequest,
}


class _JSONRPCEnvelope(BaseModel):
    jsonrpc: str = "2.0"
    id: Any = None
    method: str = ""
    params: dict = {}


def _ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _now_ts() -> PBTimestamp:
    ts = PBTimestamp()
    ts.FromDatetime(datetime.now(timezone.utc))
    return ts


def _dict_to_part(part_dict: dict) -> Part:
    """Convert agent-returned {"type": "text", "text": "..."} dict to a proto Part."""
    if part_dict.get("type") == "text" or "text" in part_dict:
        return Part(text=part_dict.get("text", ""))
    return ParseDict(part_dict, Part())


def _get_user_query(message: Message) -> str:
    if not message.parts:
        raise restate.exceptions.TerminalError("Message has no parts")
    part = message.parts[0]
    if not part.text:
        raise restate.exceptions.TerminalError("Only text parts are supported")
    return part.text


class RestateA2AMiddleware(Iterable[restate.Service | restate.VirtualObject]):

    def __init__(self, agent_card: AgentCard, agent: A2AAgent):
        # Copy the proto message so we don't mutate the caller's object
        self.agent_card = type(agent_card)()
        self.agent_card.CopyFrom(agent_card)
        self.agent = agent
        self.a2a_server_name = f"{self.agent_card.name}A2AServer"
        self.task_object_name = f"{self.agent_card.name}TaskObject"

        # Point the first interface URL at the Restate process_request handler
        if self.agent_card.supported_interfaces:
            base_url = self.agent_card.supported_interfaces[0].url
            self.agent_card.supported_interfaces[0].url = (
                f"{base_url}/{self.a2a_server_name}/process_request"
            )

        self.restate_services: list[restate.Service | restate.VirtualObject] = []
        self._build_services()

    def __iter__(self):
        return iter(self.restate_services)

    @property
    def agent_card_json(self) -> dict:
        return MessageToDict(self.agent_card)

    def _build_services(self) -> None:
        a2a_service = restate.Service(
            self.a2a_server_name,
            description=self.agent_card.description,
            metadata={
                "agent": self.agent_card.name,
                "version": self.agent_card.version,
            },
        )
        self.restate_services.append(a2a_service)

        task_object = restate.VirtualObject(self.task_object_name)
        self.restate_services.append(task_object)

        agent = self.agent

        # ── Inner TaskObject ──────────────────────────────────────────────────

        class TaskObject:

            @staticmethod
            @task_object.handler(kind="shared")
            async def get_invocation_id(
                ctx: restate.ObjectSharedContext,
            ) -> str | None:
                return await ctx.get(INVOCATION_ID) or None

            @staticmethod
            @task_object.handler(kind="shared")
            async def get_task(
                ctx: restate.ObjectSharedContext,
            ) -> dict | None:
                return await ctx.get(TASK, type_hint=dict) or None

            @staticmethod
            @task_object.handler()
            async def cancel_task(
                ctx: restate.ObjectContext, _: None
            ) -> dict:
                return await TaskObject._update_store(ctx, state=TASK_STATE_CANCELED)

            @staticmethod
            @task_object.handler()
            async def handle_send_message_request(
                ctx: restate.ObjectContext, request_dict: dict
            ) -> dict:
                request = ParseDict(request_dict, SendMessageRequest())

                if not request.message.context_id:
                    request.message.context_id = str(ctx.uuid())

                await TaskObject._set_invocation_id(ctx, ctx.request().id)
                await TaskObject._upsert_task(ctx, request)

                try:
                    result = await agent.invoke(
                        ctx,
                        query=_get_user_query(request.message),
                        session_id=request.message.context_id,
                    )
                    parts = [_dict_to_part(p) for p in result.parts]

                    if result.require_user_input:
                        msg = Message(message_id=str(ctx.uuid()), role=ROLE_AGENT)
                        msg.parts.extend(parts)
                        task_dict = await TaskObject._update_store(
                            ctx, state=TASK_STATE_INPUT_REQUIRED, status_message=msg
                        )
                    else:
                        artifact = Artifact(artifact_id=str(ctx.uuid()))
                        artifact.parts.extend(parts)
                        task_dict = await TaskObject._update_store(
                            ctx, state=TASK_STATE_COMPLETED, artifacts=[artifact]
                        )
                    ctx.clear(INVOCATION_ID)
                    return task_dict

                except restate.exceptions.TerminalError as err:
                    # Cancellation (409) or other terminal failure — always return, never re-raise,
                    # so that ctx.attach_invocation in the cancel flow can retrieve this result.
                    state = TASK_STATE_CANCELED if err.status_code == 409 else TASK_STATE_FAILED
                    task_dict = await TaskObject._update_store(ctx, state=state)
                    ctx.clear(INVOCATION_ID)
                    return task_dict

            # ── Private helpers ───────────────────────────────────────────────

            @staticmethod
            async def _update_store(
                ctx: restate.ObjectContext,
                state: int,
                status_message: Message | None = None,
                artifacts: list[Artifact] | None = None,
            ) -> dict:
                task_dict = await ctx.get(TASK, type_hint=dict)
                if task_dict is None:
                    raise restate.exceptions.TerminalError("Task not found in store")

                task = ParseDict(task_dict, Task())

                def _make_status_dict() -> dict:
                    new_status = TaskStatus(state=state, timestamp=_now_ts())
                    if status_message is not None:
                        new_status.message.CopyFrom(status_message)
                    return MessageToDict(new_status)

                status_dict = await ctx.run_typed(
                    "task status",
                    _make_status_dict,
                    restate.RunOptions(type_hint=dict),
                )
                new_status = ParseDict(status_dict, TaskStatus())

                # Move previous status message into history before overwriting
                if task.status.HasField("message"):
                    task.history.extend([task.status.message])
                task.status.CopyFrom(new_status)

                if artifacts:
                    task.artifacts.extend(artifacts)

                updated_dict = MessageToDict(task)
                ctx.set(TASK, updated_dict)
                return updated_dict

            @staticmethod
            async def _set_invocation_id(
                ctx: restate.ObjectContext, invocation_id: str
            ) -> None:
                current = await ctx.get(INVOCATION_ID)
                if current is not None:
                    raise restate.exceptions.TerminalError(
                        "There is an ongoing invocation."
                    )
                ctx.set(INVOCATION_ID, invocation_id)

            @staticmethod
            async def _upsert_task(
                ctx: restate.ObjectContext, request: SendMessageRequest
            ) -> None:
                task_dict = await ctx.get(TASK, type_hint=dict)

                if task_dict is None:
                    def _create_task_dict() -> dict:
                        task = Task(
                            id=request.message.message_id,
                            context_id=request.message.context_id,
                        )
                        task.status.CopyFrom(
                            TaskStatus(state=TASK_STATE_SUBMITTED, timestamp=_now_ts())
                        )
                        task.history.extend([request.message])
                        return MessageToDict(task)

                    task_dict = await ctx.run_typed(
                        "Create task",
                        _create_task_dict,
                        restate.RunOptions(type_hint=dict),
                    )
                else:
                    task = ParseDict(task_dict, Task())
                    task.history.extend([request.message])
                    task_dict = MessageToDict(task)

                ctx.set(TASK, task_dict)

        # ── Inner A2aService ──────────────────────────────────────────────────

        class A2aService:

            @staticmethod
            @a2a_service.handler()
            async def process_request(
                ctx: restate.Context, req: _JSONRPCEnvelope
            ) -> dict:
                model_class = METHOD_TO_MODEL.get(req.method)
                if not model_class:
                    return _err(req.id, -32601, f"Method not found: {req.method}")

                try:
                    specific_request = ParseDict(req.params, model_class())
                except Exception as exc:
                    return _err(req.id, -32602, f"Invalid params: {exc}")

                try:
                    result = await A2aService._dispatch(
                        ctx, req, specific_request, req.method
                    )
                    return _ok(req.id, result)
                except restate.exceptions.TerminalError as err:
                    return _err(req.id, err.status_code, err.message)

            @staticmethod
            async def _dispatch(
                ctx: restate.Context,
                req: _JSONRPCEnvelope,
                specific_request: Any,
                method: str,
            ) -> dict | None:
                match method:
                    case "SendMessage":
                        return await A2aService._on_send_message(
                            ctx, req, specific_request
                        )
                    case "GetTask":
                        return await A2aService._on_get_task(ctx, specific_request)
                    case "CancelTask":
                        return await A2aService._on_cancel_task(ctx, specific_request)
                    case _:
                        raise restate.exceptions.TerminalError(
                            f"Method not supported: {method}", status_code=400
                        )

            @staticmethod
            async def _on_send_message(
                ctx: restate.Context,
                req: _JSONRPCEnvelope,
                request: SendMessageRequest,
            ) -> dict:
                task_id = request.message.task_id or str(ctx.uuid())
                return await ctx.object_call(
                    TaskObject.handle_send_message_request,
                    key=task_id,
                    arg=MessageToDict(request),
                    idempotency_key=str(req.id),
                )

            @staticmethod
            async def _on_get_task(
                ctx: restate.Context, request: GetTaskRequest
            ) -> dict:
                task_dict = await ctx.object_call(
                    TaskObject.get_task, key=request.id, arg=None
                )
                if task_dict is None:
                    raise restate.exceptions.TerminalError(
                        "Task not found", status_code=404
                    )
                return task_dict

            @staticmethod
            async def _on_cancel_task(
                ctx: restate.Context, request: CancelTaskRequest
            ) -> dict:
                task_dict = await ctx.object_call(
                    TaskObject.get_task, key=request.id, arg=None
                )
                if task_dict is None:
                    raise restate.exceptions.TerminalError(
                        "Task not found", status_code=404
                    )

                invocation_id = await ctx.object_call(
                    TaskObject.get_invocation_id, key=request.id, arg=None
                )
                if invocation_id is None:
                    # Task is already finished — mark it cancelled in the store
                    return await ctx.object_call(
                        TaskObject.cancel_task, key=request.id, arg=None
                    )

                # Cancel the running invocation; handle_send_message_request catches
                # the TerminalError internally and returns the cancelled task dict.
                ctx.cancel_invocation(invocation_id)
                return await ctx.attach_invocation(invocation_id, type_hint=dict)
