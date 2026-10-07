"""Resolve catalog references using Spring Boot's public PropertyTypeResponse DTO."""
import asyncio
import logging
import unicodedata
from time import monotonic

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from app.core.config import Settings
from app.schemas.conversation import Clarification, CriteriaOperation, CriteriaUpdate, PositiveInt
from app.schemas.search import Label

logger = logging.getLogger(__name__)


class PropertyTypesUnavailable(Exception):
    """Catalog unavailable or invalid; contains no backend response or user data."""


class PropertyType(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    id: PositiveInt
    label: Label
    description: str | None = None


CATALOG = TypeAdapter(list[PropertyType])


def normalize_label(label: str) -> str:
    decomposed = unicodedata.normalize("NFKD", label.casefold())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


class PropertyTypeResolver:
    def __init__(self, client: httpx.AsyncClient, settings: Settings, clock=monotonic):
        self.client = client
        self.url = str(settings.spring_boot_base_url).rstrip("/") + "/api/v1/property-types"
        self.ttl = settings.property_types_cache_ttl_seconds
        self.timeout = settings.property_types_timeout_seconds
        self._clock = clock
        self._cache: tuple[PropertyType, ...] | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _catalog(self) -> tuple[PropertyType, ...]:
        if self._cache is not None and self._clock() < self._expires_at:
            logger.info("property_types cache_hit")
            return self._cache
        # Concurrent conversations share one refresh, including at TTL expiry.
        async with self._lock:
            if self._cache is not None and self._clock() < self._expires_at:
                return self._cache
            try:
                async with asyncio.timeout(self.timeout):
                    response = await self.client.get(self.url, timeout=self.timeout)
                    if not response.is_success:
                        raise PropertyTypesUnavailable("http_error")
                    catalog = CATALOG.validate_json(response.content)
                    if len({item.id for item in catalog}) != len(catalog):
                        raise PropertyTypesUnavailable("duplicate_ids")
            except (httpx.RequestError, TimeoutError, ValidationError, PropertyTypesUnavailable) as exc:
                logger.warning("property_types unavailable error=%s", type(exc).__name__)
                raise PropertyTypesUnavailable() from None
            self._cache = tuple(catalog)
            self._expires_at = self._clock() + self.ttl
            logger.info("property_types cache_refreshed count=%d", len(catalog))
            return self._cache

    async def resolve_update(self, update: CriteriaUpdate) -> CriteriaUpdate:
        """Convert a label to a SET, or preserve criteria and ask about the type.

        Explicit numeric SETs also have to exist in the catalog. REMOVE and turns
        unrelated to types never fetch the catalog.
        """
        type_set = next((op for op in update.operations if op.field == "typeId" and op.operation == "SET"), None)
        label = update.unresolvedPropertyType
        if label is None and type_set is None:
            return update
        catalog = await self._catalog()
        if label is not None:
            normalized = normalize_label(label)
            matches = [item for item in catalog if normalize_label(item.label) == normalized]
            # The real catalog uses "Villas". Permit a simple terminal plural s
            # only after exact normalization fails; collisions still clarify.
            if not matches:
                def singular(text: str) -> str:
                    return text[:-1] if len(text) > 3 and text.endswith("s") else text
                matches = [item for item in catalog
                           if singular(normalize_label(item.label)) == singular(normalized)]
        else:
            matches = [item for item in catalog if item.id == type_set.value]

        candidate = update.model_copy(deep=True)
        candidate.operations = [op for op in candidate.operations if op.field != "typeId"]
        if len(matches) == 1:
            candidate.operations.append(CriteriaOperation(field="typeId", operation="SET", value=matches[0].id))
            candidate.unresolvedPropertyType = None
            if candidate.clarification and candidate.clarification.field == "typeId":
                candidate.clarification = None
            logger.info("property_types resolved")
        else:
            labels = list(dict.fromkeys(item.label for item in (matches or catalog)))[:5]
            message = (
                "Plusieurs types de logement correspondent à votre demande. Quel type recherchez-vous ?"
                if matches else "Je n'ai pas trouvé ce type de logement. Quel type recherchez-vous ?"
            )
            if labels:
                message += " Types proposés : " + ", ".join(labels) + "."
            # Prioritize this clarification; no unsupported type is ever committed.
            candidate.clarification = Clarification(field="typeId", message=message)
            logger.info("property_types clarification reason=%s", "ambiguous" if matches else "unknown")
        return CriteriaUpdate.model_validate(candidate.model_dump())
