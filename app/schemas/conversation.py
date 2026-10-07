"""Conversation DTOs; criteria use the exact Spring Boot wire names."""
from datetime import date, datetime, timezone
from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.schemas.search import Area, Count, Label, Query

PositiveInt = Annotated[int, Field(strict=True, ge=1)]
StrictBool = Annotated[bool, Field(strict=True)]
ISODate = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
CriteriaField = Literal[
    "country", "city", "municipality", "neighborhood", "typeId", "minRent",
    "maxRent", "rooms", "bedrooms", "bathrooms", "minArea", "maxArea",
    "furnished", "sharedHousingAllowed", "minSharedHousingCapacity", "availableBefore",
]
ConversationId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Criteria
class PropertyFilterRequest(StrictModel):
    country: Label | None = None
    city: Label | None = None
    municipality: Label | None = None
    neighborhood: Label | None = None
    typeId: PositiveInt | None = None
    minRent: Count | None = None
    maxRent: Count | None = None
    rooms: Count | None = None
    bedrooms: Count | None = None
    bathrooms: Count | None = None
    minArea: Area | None = None
    maxArea: Area | None = None
    furnished: StrictBool | None = None
    sharedHousingAllowed: StrictBool | None = None
    minSharedHousingCapacity: PositiveInt | None = None
    availableBefore: ISODate | None = None

    #Validate specific criteria
    @model_validator(mode="after")
    def validate_criteria(self) -> Self:
        if self.minRent is not None and self.maxRent is not None and self.minRent > self.maxRent:
            raise ValueError("minRent must be <= maxRent")
        if self.minArea is not None and self.maxArea is not None and self.minArea > self.maxArea:
            raise ValueError("minArea must be <= maxArea")
        if self.minSharedHousingCapacity is not None and self.sharedHousingAllowed is not True:
            raise ValueError("capacity requires sharedHousingAllowed=true")
        if self.availableBefore is not None:
            date.fromisoformat(self.availableBefore)
        return self


    # Operation model
class CriteriaOperation(StrictModel):
    field: CriteriaField
    operation: Literal["SET", "REMOVE"]
    value: str | int | float | bool | None = None

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        if self.operation == "REMOVE":
            if self.value is not None:
                raise ValueError("REMOVE cannot carry a value")
        else:
            if self.value is None:
                raise ValueError("SET requires a non-null value")
            field = PropertyFilterRequest.model_fields[self.field]
            TypeAdapter(field.annotation).validate_python(self.value)
        return self

# Query clarification model
class Clarification(StrictModel):
    field: CriteriaField
    message: Query


# Criteria update model
class CriteriaUpdate(StrictModel):
    operations: list[CriteriaOperation] = Field(max_length=32)
    clarification: Clarification | None = None
    unresolvedPropertyType: Label | None = None

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        fields = [operation.field for operation in self.operations]
        if len(fields) != len(set(fields)):
            raise ValueError("only one operation per field per turn")
        if self.clarification and self.clarification.field in fields:
            raise ValueError("cannot modify and clarify the same field")
        if self.unresolvedPropertyType and "typeId" in fields:
            raise ValueError("unresolved type conflicts with typeId operation")
        return self


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConversationState(StrictModel):
    conversationId: ConversationId = Field(default_factory=lambda: uuid4().hex)
    criteria: PropertyFilterRequest = Field(default_factory=PropertyFilterRequest)
    awaitingField: CriteriaField | None = None
    clarificationMessage: str | None = None
    unresolvedPropertyType: Label | None = None
    createdAt: datetime = Field(default_factory=utcnow)
    updatedAt: datetime = Field(default_factory=utcnow)


class ConversationRequest(StrictModel):
    conversationId: ConversationId | None = None
    message: Query

# Conversational criteria model
class ConversationResponse(StrictModel):
    conversationId: str | None = None
    status: Literal["SEARCH_READY", "NEEDS_CLARIFICATION", "INVALID_INPUT", "ERROR"]
    message: str | None = None
    criteria: PropertyFilterRequest = Field(default_factory=PropertyFilterRequest)
    awaitingField: CriteriaField | None = None
    unresolvedPropertyType: Label | None = None
    transcription: str | None = None
