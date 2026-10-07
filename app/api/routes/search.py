from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from pydantic import ValidationError

from app.schemas.search import AudioSearchResponse, TextSearchRequest, TextSearchResponse
from app.schemas.conversation import ConversationRequest, ConversationResponse
from app.exceptions.exceptions import TranscriptionException

router = APIRouter(prefix="/api/v1/search", tags=["Search"])


AUDIO_TYPES = {
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mp4": "m4a",
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
}



@router.post("/text", response_model=TextSearchResponse, summary="Extract real-estate criteria")
async def search_text(body: TextSearchRequest, request: Request) -> TextSearchResponse:
    criteria = await request.app.state.parser.parse(body.query)
    return TextSearchResponse(query=body.query, criteria=criteria)


@router.post("/audio", response_model=AudioSearchResponse, summary="Transcribe audio and extract criteria")
async def search_audio(file: UploadFile, request: Request) -> AudioSearchResponse:
    text = await transcribe_upload(file, request)
    try:
        body = TextSearchRequest(query=text)
    except ValidationError:
        raise HTTPException(422) from None
    criteria = await request.app.state.parser.parse(body.query)
    return AudioSearchResponse(transcription=text, criteria=criteria)


async def transcribe_upload(file: UploadFile, request: Request) -> str:
    """Shared upload checks and existing STT for both audio endpoints."""
    try:
        mime = (file.content_type or "").split(";", 1)[0].strip().lower()
        if mime not in AUDIO_TYPES:
            raise HTTPException(415)
        limit = request.app.state.settings.max_audio_size_mb * 1024 * 1024
        audio = await file.read(limit + 1)
        if len(audio) > limit:
            raise HTTPException(413)
        if not audio:
            raise HTTPException(400)
    finally:
        await file.close()

    filename = f"audio.{AUDIO_TYPES[mime]}"
    return await request.app.state.transcriber.transcribe(audio, filename, mime)


@router.post("/conversation", response_model=ConversationResponse, summary="Update conversational search criteria")
async def search_conversation(body: ConversationRequest, request: Request) -> ConversationResponse:
    return await request.app.state.conversation.process(body)


@router.post("/conversation/audio", response_model=ConversationResponse, summary="Transcribe and update conversational search")
async def search_conversation_audio(
    file: UploadFile, request: Request,
    conversationId: Annotated[str | None, Form(max_length=128, pattern=r"^[A-Za-z0-9_-]+$")] = None,
) -> ConversationResponse:
    try:
        text = await transcribe_upload(file, request)
    except TranscriptionException:
        return await request.app.state.conversation.input_failure(
            conversationId, "ERROR", "Impossible de transcrire cet audio."
        )
    try:
        body = ConversationRequest(conversationId=conversationId, message=text)
    except ValidationError:
        response = await request.app.state.conversation.input_failure(
            conversationId, "INVALID_INPUT", "La transcription doit contenir entre 1 et 5000 caractères."
        )
        response.transcription = text
        return response
    response = await request.app.state.conversation.process(body)
    response.transcription = text
    return response
