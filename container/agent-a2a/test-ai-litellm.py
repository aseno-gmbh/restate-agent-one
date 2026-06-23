
import litellm

response = litellm.completion(
    model="openai/aseno-gemini-model",        # prefix with litellm_proxy/
    messages=[{"role": "user", "content": "Hello!"}],
    api_base="https://ai.domain.eu",     # your proxy URL
    api_key="XXX",       # your virtual key
)

print(response.choices[0].message.content)