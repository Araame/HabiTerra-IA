import asyncio
from typing import Any

import httpx
from pydantic import SecretStr

from app.core.config import Settings
from app.exceptions.exceptions import AIProviderException

# Verify API key and model
def require_key(key: SecretStr, model: str) -> str:
    if not key.get_secret_value() or not model.strip():
        raise AIProviderException("missing_configuration")
    return key.get_secret_value()


async def post_json(client: httpx.AsyncClient, settings: Settings, url: str, **kwargs: Any) -> Any:
    """Call the provider with a total deadline and safe errors."""
    try:
        async with asyncio.timeout(settings.http_timeout_seconds):
            response = await client.post(url, **kwargs)
            if not response.is_success:
                raise AIProviderException(f"http_{response.status_code}")
            return response.json()
    except TimeoutError:
        raise AIProviderException("global_timeout") from None
    except httpx.RequestError as exc:
        raise AIProviderException(type(exc).__name__) from None
    except ValueError:
        raise AIProviderException("invalid_response_json") from None

# Retrieving correct transcription text
def transcription_text(payload: Any) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise AIProviderException("invalid_transcription_response")
    # An empty or poor transcription is not a technical reason to switch providers.
    return payload["text"].strip()
