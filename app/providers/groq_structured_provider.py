import httpx

from app.core.config import Settings
from app.exceptions.exceptions import AIProviderException
from app.providers.http import post_json, require_key

# Provider for Groq structured output
class GroqStructuredProvider:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

# Extract the query and make the strutured output response
    async def extract(self, query: str, system_prompt: str) -> str:
        key = require_key(self.settings.groq_api_key, self.settings.groq_structured_model)
        payload = await post_json(
            self.client, self.settings, "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": self.settings.groq_structured_model,
                "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": query}],
                "temperature": 0, "max_tokens": 1200,
                "response_format": {"type": "json_object"},
            },
        )
        try:
            choice = payload["choices"][0]
            text = choice["message"]["content"]
            if choice.get("finish_reason") != "stop" or not isinstance(text, str):
                raise ValueError
            return text
        except (KeyError, IndexError, TypeError, ValueError):
            raise AIProviderException("invalid_structured_response") from None
