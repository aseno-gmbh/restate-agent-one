import os

import litellm
from google.adk.models.lite_llm import LiteLlm

_API_BASE = os.environ["LLM_API_BASE"]
_API_KEY = os.environ["LLM_API_KEY"]
_MODEL_NAME = os.environ["LLM_MODEL_NAME"]

# Apply globally so ADK's LiteLlm wrapper picks them up.
litellm.api_base = _API_BASE
litellm.api_key = _API_KEY


def make_llm_model() -> LiteLlm:
    return LiteLlm(model=_MODEL_NAME)
