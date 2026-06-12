from abc import ABC, abstractmethod

import restate
from pydantic import BaseModel


class A2AAgent(ABC):
    @abstractmethod
    async def invoke(
        self, ctx: restate.ObjectContext, query: str, session_id: str
    ) -> "AgentInvokeResult":
        pass


class AgentInvokeResult(BaseModel):
    parts: list[dict]
    require_user_input: bool = False
    is_task_complete: bool = True
