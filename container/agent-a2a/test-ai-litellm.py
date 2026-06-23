"""Quick smoke-test: call the LLM using the same env vars as production."""
import os

import litellm
from dotenv import load_dotenv

load_dotenv()

response = litellm.completion(
    model=os.environ["LLM_MODEL_NAME"],
    messages=[{"role": "user", "content": "Hello!"}],
    api_base=os.environ["LLM_API_BASE"],
    api_key=os.environ["LLM_API_KEY"],
)

print(response.choices[0].message.content)
