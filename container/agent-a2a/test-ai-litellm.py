"""Smoke-test: verify LLM connectivity with and without tool calling.

Run without tools first (plain); if that passes, run with tools to confirm
whether vLLM tool-calling support is configured correctly.
"""
import asyncio
import os

import litellm
from dotenv import load_dotenv

load_dotenv()

from google.adk.models.llm_request import LlmRequest
from google.genai import types

from app.common.litellm_config import make_llm_model


async def test_plain() -> None:
    """Plain completion via ADK LiteLlm wrapper — no tools."""
    model = make_llm_model()
    request = LlmRequest(
        contents=[types.Content(role="user", parts=[types.Part(text="Hello!")])]
    )
    async for response in model.generate_content_async(request):
        if response.content and response.content.parts:
            print("[plain] OK:", response.content.parts[0].text[:120])
            return
    print("[plain] no response received")


def test_with_tools() -> None:
    """Completion with a dummy tool via litellm directly — mirrors what ADK sends."""
    response = litellm.completion(
        model=os.environ["LLM_MODEL_NAME"],
        messages=[{"role": "user", "content": "Call the dummy tool with amount 42."}],
        api_base=os.environ["LLM_API_BASE"],
        api_key=os.environ["LLM_API_KEY"],
        tools=[{
            "type": "function",
            "function": {
                "name": "dummy",
                "description": "A dummy tool for testing.",
                "parameters": {
                    "type": "object",
                    "properties": {"amount": {"type": "number"}},
                    "required": ["amount"],
                },
            },
        }],
    )
    msg = response.choices[0].message
    if msg.tool_calls:
        print("[with-tools] OK: tool call received →", msg.tool_calls[0].function.name)
    else:
        print("[with-tools] OK (text):", (msg.content or "")[:120])


async def main() -> None:
    await test_plain()
    test_with_tools()


asyncio.run(main())
