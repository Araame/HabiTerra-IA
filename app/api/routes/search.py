from fastapi import APIRouter, HTTPException, Request, UploadFile
from pydantic import ValidationError

from app.schemas.search import AudioSearchResponse, TextSearchRequest, TextSearchResponse

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
    text = await request.app.state.transcriber.transcribe(audio, filename, mime)
    try:
        body = TextSearchRequest(query=text)
    except ValidationError:
        raise HTTPException(422) from None
    criteria = await request.app.state.parser.parse(body.query)
    return AudioSearchResponse(transcription=text, criteria=criteria)
