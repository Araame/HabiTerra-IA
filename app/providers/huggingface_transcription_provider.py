from urllib.parse import quote

import httpx

from app.core.config import Settings
from app.providers.http import post_json, require_key, transcription_text

# Provider for Hugging face transcription (fallback)
class HuggingFaceTranscriptionProvider:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def transcribe(self, audio: bytes, filename: str, mime_type: str) -> str:
        key = require_key(self.settings.huggingface_api_key, self.settings.huggingface_transcription_model)
        model = quote(self.settings.huggingface_transcription_model, safe="/")
        url = self.settings.huggingface_transcription_url or f"https://router.huggingface.co/hf-inference/models/{model}"
        payload = await post_json(
            self.client, self.settings, url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": mime_type}, content=audio,
        )
        return transcription_text(payload)
