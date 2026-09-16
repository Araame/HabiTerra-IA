import logging
from time import perf_counter

from app.exceptions.exceptions import AIProviderException, TranscriptionException
from app.providers.groq_transcription_provider import GroqTranscriptionProvider
from app.providers.huggingface_transcription_provider import HuggingFaceTranscriptionProvider

logger = logging.getLogger(__name__)

# Transcription service
class TranscriptionService:
    def __init__(self, primary: GroqTranscriptionProvider, fallback: HuggingFaceTranscriptionProvider):
        self.providers = (primary, fallback)

# Transcribe process function
    async def transcribe(self, audio: bytes, filename: str, mime_type: str) -> str:
        for index, provider in enumerate(self.providers):
            started = perf_counter()
            name = type(provider).__name__
            try:
                text = await provider.transcribe(audio, filename, mime_type)
                logger.info("transcription provider=%s duration_ms=%.0f", name, (perf_counter() - started) * 1000)
                return text
            except AIProviderException as exc:
                logger.warning("transcription provider=%s error=%s fallback=%s duration_ms=%.0f", name, str(exc), index == 0, (perf_counter() - started) * 1000)
        raise TranscriptionException() from None
