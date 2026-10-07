"""Process-local MVP store. Run a single worker; restart loses conversations."""
import asyncio
import logging
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import AsyncContextManager, AsyncIterator

from app.schemas.conversation import ConversationState, utcnow

logger = logging.getLogger(__name__)


class ConversationNotFound(Exception):
    pass


class ConversationStore(ABC):
    @abstractmethod
    def transaction(self, conversation_id: str | None) -> AsyncContextManager[ConversationState]:
        """Yield an isolated state, serialize turns, commit only on normal exit."""
        raise NotImplementedError


@dataclass
class Entry:
    state: ConversationState
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class InMemoryConversationStore(ConversationStore):
    def __init__(self, ttl_seconds: int = 1800, clock=utcnow):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._entries: dict[str, Entry] = {}
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock

    def _expire(self) -> None:
        now = self._clock()
        expired = [key for key, entry in self._entries.items()
                   if not entry.users and now - entry.state.updatedAt >= self._ttl]
        for key in expired:
            del self._entries[key]
        if expired:
            logger.info("conversation expired count=%d", len(expired))

    @asynccontextmanager
    async def transaction(self, conversation_id: str | None) -> AsyncIterator[ConversationState]:
        self._expire()
        if conversation_id is None:
            now = self._clock()
            state = ConversationState(createdAt=now, updatedAt=now)
            entry = Entry(state)
            self._entries[state.conversationId] = entry
            logger.info("conversation created")
        else:
            entry = self._entries.get(conversation_id)
            if entry is None:
                raise ConversationNotFound()
            logger.info("conversation found")
        entry.users += 1
        try:
            async with entry.lock:
                working = entry.state.model_copy(deep=True)
                yield working
                validated = ConversationState.model_validate(working.model_dump())
                validated.updatedAt = self._clock()
                entry.state = validated.model_copy(deep=True)
        finally:
            entry.users -= 1
