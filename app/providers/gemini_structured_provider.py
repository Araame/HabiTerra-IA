from urllib.parse import quote

import httpx

from app.core.config import Settings
from app.exceptions.exceptions import AIProviderException
from app.providers.http import post_json, require_key

# Provider for gemini (fallback)
class GeminiStructuredProvider:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

# Extract the query and make the strutured output response
    async def extract(self, query: str, system_prompt: str) -> str:
        key = require_key(self.settings.gemini_api_key, self.settings.gemini_model)
        model = quote(self.settings.gemini_model, safe="")
        payload = await post_json(
            self.client, self.settings, f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key},
            json={
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": query}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 1200, "responseMimeType": "application/json"},
            },
        )
        # Retrieving the LLM response
        try:
            candidate = payload["candidates"][0]
            # Verify if text gen is done
            if candidate.get("finishReason") != "STOP":
                raise ValueError
            # Concanate the parts of LLM response
            text = "".join(part["text"] for part in candidate["content"]["parts"])
            if not text:
                raise ValueError
            return text
        except (KeyError, IndexError, TypeError, ValueError):
            raise AIProviderException("invalid_structured_response") from None
