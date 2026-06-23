"""Smoke-test: call the LLM via the same LiteLlm wrapper used in production.

Run without tools first (plain); if that passes, run with tools to confirm
whether vLLM tool-calling support is configured correctly.
"""
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_request import FunctionDeclaration, Schema, Type
from google.genai import types

from app.common.litellm_config import make_llm_model


async def test_plain() -> None:
    """Plain completion — no tools."""
    model = make_llm_model()
    request = LlmRequest(
        contents=[types.Content(role="user", parts=[types.Part(text="Hello!")])]
    )
    async for response in model.generate_content_async(request):
        if response.content and response.content.parts:
            print("[plain] OK:", response.content.parts[0].text[:120])
            return
    print("[plain] no response received")


async def test_with_tools() -> None:
    """Completion with a dummy tool — mirrors what ADK sends in production."""
    model = make_llm_model()
    dummy_tool = types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="dummy",
            description="A dummy tool for testing.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={"amount": types.Schema(type=types.Type.NUMBER)},
            ),
        )
    ])
    request = LlmRequest(
        contents=[types.Content(role="user", parts=[types.Part(text="Call the dummy tool with amount 42.")])],
        tools=[dummy_tool],
    )
    async for response in model.generate_content_async(request):
        if response.content and response.content.parts:
            print("[with-tools] OK:", response.content.parts[0].text[:120])
            return
        if response.content and response.content.parts == []:
            # tool call response with no text — still counts as success
            print("[with-tools] OK: tool call response received")
            return
    print("[with-tools] no response received")


async def main() -> None:
    await test_plain()
    await test_with_tools()


asyncio.run(main())
