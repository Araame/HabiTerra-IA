from datetime import date
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Money = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Area = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
Count = Annotated[int, Field(ge=0, strict=True)]


# JSON response model for the structured output LLM
class PropertySearchCriteria(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    country: Label | None = None
    city: Label | None = None
    municipality: Label | None = None
    neighborhood: Label | None = None
    property_type: Label | None = None
    min_rent: Money | None = None
    max_rent: Money | None = None
    min_area: Area | None = None
    max_area: Area | None = None
    rooms: Count | None = None
    bedrooms: Count | None = None
    bathrooms: Count | None = None
    furnished: Annotated[bool, Field(strict=True)] | None = None
    shared_housing_allowed: Annotated[bool, Field(strict=True)] | None = None
    min_shared_housing_capacity: Annotated[int, Field(ge=1, strict=True)] | None = None
    available_before: date | None = None


#Validation after Pydantic one
    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if self.min_rent is not None and self.max_rent is not None and self.min_rent > self.max_rent:
            raise ValueError("minRent must be <= maxRent")
        if self.min_area is not None and self.max_area is not None and self.min_area > self.max_area:
            raise ValueError("minArea must be <= maxArea")
        return self

#Text search request model
class TextSearchRequest(BaseModel):
#Avoid extra attributes
    model_config = ConfigDict(extra="forbid")
    query: Query

# Text search response model
class TextSearchResponse(BaseModel):
    query: str
    criteria: PropertySearchCriteria


# Audio search response model
class AudioSearchResponse(BaseModel):
    transcription: str
    criteria: PropertySearchCriteria
