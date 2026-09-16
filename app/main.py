import logging
from contextlib import asynccontextmanager
from time import perf_counter

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.search import router
from app.core.config import Settings
from app.core.logging import configure_logging
from app.exceptions.handlers import install_handlers
from app.providers.gemini_structured_provider import GeminiStructuredProvider
from app.providers.groq_structured_provider import GroqStructuredProvider
from app.providers.groq_transcription_provider import GroqTranscriptionProvider
from app.providers.huggingface_transcription_provider import HuggingFaceTranscriptionProvider
from app.services.search_parser_service import SearchParserService
from app.services.transcription_service import TranscriptionService

settings = Settings()
configure_logging()
logger = logging.getLogger(__name__)

# Config for prep and clean resources
@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(settings.http_read_timeout_seconds, connect=settings.http_connect_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        app.state.settings = settings
        app.state.parser = SearchParserService(
            GroqStructuredProvider(client, settings), GeminiStructuredProvider(client, settings)
        )
        app.state.transcriber = TranscriptionService(
            GroqTranscriptionProvider(client, settings), HuggingFaceTranscriptionProvider(client, settings)
        )
        yield

# App config
app = FastAPI(
    title=settings.app_name,
    description="Audio transcription and real-estate criteria extraction.",
    version="1.0.0",
    lifespan=lifespan,)

# Installing handlers
install_handlers(app)

# Define routing
app.include_router(router)

# CORS setup
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins,
        allow_methods=["POST", "GET"], allow_headers=["Content-Type"],
    )

# Log setup
@app.middleware("http")
async def log_request(request: Request, call_next):
    started = perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    logger.info(
        "endpoint=%s status=%d duration_ms=%.0f",
        getattr(route, "path", "unmatched"), response.status_code, (perf_counter() - started) * 1000,
    )
    return response

# Server check
@app.get("/health", response_model=dict[str, str], tags=["Health"], summary="Check service liveness")
async def health() -> dict[str, str]:
    return {"status": "ok"}
