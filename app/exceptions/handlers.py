import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.exceptions.exceptions import StructuredOutputException, TranscriptionException

logger = logging.getLogger(__name__)

# Handles errors and return JSON for readable answers to the client to the client
def install_handlers(app: FastAPI) -> None:

# Handler for transcription exception
    @app.exception_handler(TranscriptionException)
    async def transcription_error(request: Request, exc: TranscriptionException) -> JSONResponse:
        return JSONResponse(status_code=503, content={"code": "TRANSCRIPTION_FAILED", "message": "Unable to transcribe audio."})

# Handler for structured output exception
    @app.exception_handler(StructuredOutputException)
    async def structured_error(request: Request, exc: StructuredOutputException) -> JSONResponse:
        return JSONResponse(status_code=503, content={"code": "STRUCTURED_OUTPUT_FAILED", "message": "Unable to extract search criteria."})

# Handler for request validation
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"code": "INVALID_REQUEST", "message": "Request does not match the expected schema."})

 # Handler for HTTP exception
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        messages = {
            400: ("INVALID_AUDIO", "Audio must not be empty."),
            413: ("AUDIO_TOO_LARGE", "Audio exceeds the configured size limit."),
            415: ("UNSUPPORTED_AUDIO_TYPE", "Unsupported audio MIME type."),
            422: ("INVALID_REQUEST", "Transcription must contain 1 to 5000 characters."),
        }
        code, message = messages.get(exc.status_code, ("HTTP_ERROR", "Request could not be processed."))
        return JSONResponse(status_code=exc.status_code, content={"code": code, "message": message}, headers=exc.headers)

# Handler for unexpected exception
    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unexpected_error type=%s", type(exc).__name__)
        return JSONResponse(status_code=500, content={"code": "INTERNAL_ERROR", "message": "Internal service error."})
