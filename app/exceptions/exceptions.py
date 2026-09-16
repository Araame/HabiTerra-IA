class AIProviderException(Exception):
    """Technical failure; contains only a safe error category."""

# Transcription exception
class TranscriptionException(Exception):
    pass

# Structured output exception
class StructuredOutputException(Exception):
    pass
