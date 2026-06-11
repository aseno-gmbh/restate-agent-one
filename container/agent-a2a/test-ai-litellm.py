# import openai

# client = openai.OpenAI(
# 	api_key="sk-nyR3TMq7uzO-gCwHyQR0sw",
# 	base_url="https://ai.cloud-doctor.eu"
# )

import litellm

response = litellm.completion(
    model="litellm_proxy/aseno-gemini-model",        # prefix with litellm_proxy/
    messages=[{"role": "user", "content": "Hello!"}],
    api_base="https://ai.cloud-doctor.eu",     # your proxy URL
    api_key="XXX",       # your virtual key
)

print(response.choices[0].message.content)