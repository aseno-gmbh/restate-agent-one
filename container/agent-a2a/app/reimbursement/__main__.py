# pylint: disable=C0116
"""Main entry point for reimbursement agent."""

import logging
import os

from dotenv import load_dotenv

# load_dotenv() must run before any app imports so that os.environ is populated
# when litellm_config.py reads LLM_API_BASE / LLM_API_KEY / LLM_MODEL_NAME at
# module level. In Kubernetes the container env is already set, but local runs
# with a .env file depend on this ordering.
load_dotenv()

import restate
from fastapi import FastAPI
from google.protobuf.json_format import MessageToDict

from a2a.types import AgentCard, AgentCapabilities, AgentInterface, AgentSkill

from app.reimbursement.agent import ReimbursementAgent, reimbursement_service
from app.reimbursement.utils import payment_service
from app.common.a2a.a2a_middleware import RestateA2AMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(process)d] [%(levelname)s] - %(message)s",
)
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    import asyncio
    import hypercorn.asyncio

    try:
        restate_host = os.getenv("RESTATE_HOST", "http://localhost:8080")

        agent_card = AgentCard(
            name="ReimbursementAgent",
            description=(
                "Advanced reimbursement agent with Google ADK intelligence"
                " and Restate durability"
            ),
            version="1.0.0",
            supported_interfaces=[
                AgentInterface(url=restate_host, protocol_binding="JSONRPC")
            ],
            capabilities=AgentCapabilities(
                streaming=False,
                push_notifications=False,
            ),
            skills=[
                AgentSkill(
                    id="process_reimbursement",
                    name="Process Reimbursement",
                    description=(
                        "Handle employee reimbursement requests with workflow management"
                    ),
                    tags=["reimbursement", "finance", "workflow"],
                    examples=[
                        "Can you reimburse me $50 for client lunch on Dec 1st?",
                        "I need to submit a reimbursement for travel expenses",
                        "Process my $200 conference registration fee reimbursement",
                    ],
                ),
                AgentSkill(
                    id="form_management",
                    name="Form Management",
                    description=(
                        "Create and manage reimbursement forms with validation"
                    ),
                    tags=["forms", "validation", "data"],
                    examples=[
                        "Create a new reimbursement form",
                        "Validate submitted expense data",
                    ],
                ),
                AgentSkill(
                    id="approval_workflow",
                    name="Approval Workflow",
                    description="Handle approval workflows for large expenses",
                    tags=["approval", "workflow", "management"],
                    examples=[
                        "Route expense for manager approval",
                        "Check approval status",
                    ],
                ),
            ],
            default_input_modes=["text", "text/plain"],
            default_output_modes=["text", "text/plain"],
        )

        middleware = RestateA2AMiddleware(agent_card, ReimbursementAgent())

        app = FastAPI()

        @app.get("/.well-known/agent.json")
        async def agent_json():
            return MessageToDict(middleware.agent_card)

        app.mount(
            "/restate/v1",
            restate.app([*middleware, reimbursement_service, payment_service]),
        )

        conf = hypercorn.Config()
        host = "0.0.0.0"
        port = os.getenv("AGENT_PORT", "9080")
        conf.bind = [f"{host}:{port}"]
        logger.info("Server running at http://%s:%s", host, port)
        logger.info("  Agent card : http://%s:%s/.well-known/agent.json", host, port)
        logger.info("  Restate    : http://%s:%s/restate/v1", host, port)
        asyncio.run(hypercorn.asyncio.serve(app, conf))

    except Exception as exc:
        logger.error("Startup failed: %s", exc)
        raise SystemExit(1) from exc
