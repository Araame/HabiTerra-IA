import logging

from pydantic import ValidationError

from app.exceptions.exceptions import StructuredOutputException
from app.schemas.conversation import ConversationRequest, ConversationResponse, ConversationState
from app.services.conversation_store import ConversationNotFound, ConversationStore
from app.services.criteria_merger import CriteriaMerger
from app.services.search_parser_service import SearchParserService
from app.services.property_type_resolver import PropertyTypeResolver, PropertyTypesUnavailable

logger = logging.getLogger(__name__)


class ConversationService:
    def __init__(self, parser: SearchParserService, store: ConversationStore, property_type_resolver: PropertyTypeResolver):
        self.parser = parser
        self.store = store
        self.merger = CriteriaMerger()
        self.property_type_resolver = property_type_resolver

    @staticmethod
    def response(state: ConversationState, status: str, message: str | None = None) -> ConversationResponse:
        return ConversationResponse(
            conversationId=state.conversationId, criteria=state.criteria.model_copy(deep=True),
            awaitingField=state.awaitingField, unresolvedPropertyType=state.unresolvedPropertyType,
            status=status, message=message,
        )

    async def input_failure(self, conversation_id: str | None, status: str, message: str) -> ConversationResponse:
        """Report a failed STT/input step without discarding existing criteria."""
        if conversation_id is not None:
            try:
                async with self.store.transaction(conversation_id) as state:
                    return self.response(state, status, message)
            except ConversationNotFound:
                return ConversationResponse(
                    conversationId=conversation_id, status="INVALID_INPUT",
                    message="Conversation inconnue ou expirée. Recommencez sans conversationId.",
                )
        return ConversationResponse(status=status, message=message)

    async def process(self, body: ConversationRequest) -> ConversationResponse:
        try:
            async with self.store.transaction(body.conversationId) as state:
                try:
                    update = await self.parser.parse_update(body.message, state)
                    update = await self.property_type_resolver.resolve_update(update)
                    criteria = self.merger.merge(state.criteria, update)
                except PropertyTypesUnavailable:
                    return self.response(state, "ERROR", "Les types de logement sont temporairement indisponibles. Veuillez réessayer.")
                except (StructuredOutputException, ValidationError):
                    logger.warning("conversation structured_output_error")
                    return self.response(state, "ERROR", "Impossible d'interpréter ce message. Veuillez reformuler.")

                if not update.operations and not update.clarification and not update.unresolvedPropertyType:
                    if state.awaitingField:
                        return self.response(state, "NEEDS_CLARIFICATION", state.clarificationMessage)
                    return self.response(state, "INVALID_INPUT", "Aucun critère exploitable dans ce message.")

                # Build and validate a candidate before changing the transaction's state.
                candidate = state.model_copy(deep=True)
                candidate.criteria = criteria
                modified = {op.field for op in update.operations}
                if candidate.awaitingField in modified:
                    candidate.awaitingField = None
                    candidate.clarificationMessage = None
                if "typeId" in modified:
                    candidate.unresolvedPropertyType = None
                if update.unresolvedPropertyType:
                    candidate.unresolvedPropertyType = update.unresolvedPropertyType
                if update.clarification:
                    candidate.awaitingField = update.clarification.field
                    candidate.clarificationMessage = update.clarification.message
                elif candidate.unresolvedPropertyType and candidate.awaitingField is None:
                    candidate.awaitingField = "typeId"
                    candidate.clarificationMessage = "Quel type de logement recherchez-vous ?"
                candidate = ConversationState.model_validate(candidate.model_dump())
                state.criteria = candidate.criteria
                state.awaitingField = candidate.awaitingField
                state.clarificationMessage = candidate.clarificationMessage
                state.unresolvedPropertyType = candidate.unresolvedPropertyType
                logger.info("conversation operations=%d clarification=%s", len(update.operations), bool(state.awaitingField))
                if state.awaitingField:
                    return self.response(state, "NEEDS_CLARIFICATION", state.clarificationMessage)
                return self.response(state, "SEARCH_READY")
        except ConversationNotFound:
            return ConversationResponse(
                conversationId=body.conversationId, status="INVALID_INPUT",
                message="Conversation inconnue ou expirée. Recommencez sans conversationId.",
            )
