"""Quick smoke-test: call the LLM via the same LiteLlm wrapper used in production."""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from google.adk.models.llm_request import LlmRequest
from google.genai import types

from app.common.litellm_config import make_llm_model


async def main() -> None:
    model = make_llm_model()
    request = LlmRequest(
        contents=[types.Content(role="user", parts=[types.Part(text="Hello!")])]
    )
    async for response in model.generate_content_async(request):
        if response.content and response.content.parts:
            print(response.content.parts[0].text)
            break


asyncio.run(main())
