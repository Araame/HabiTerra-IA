import httpx

from app.core.config import Settings
from app.providers.http import post_json, require_key, transcription_text

# Provider for Groq transcription
class GroqTranscriptionProvider:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

# Transcribe audio
    async def transcribe(self, audio: bytes, filename: str, mime_type: str) -> str:
        key = require_key(self.settings.groq_api_key, self.settings.groq_transcription_model)
        payload = await post_json(
            self.client, self.settings, "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            data={"model": self.settings.groq_transcription_model, "response_format": "json"},
            files={"file": (filename, audio, mime_type)},
        )
        return transcription_text(payload)
