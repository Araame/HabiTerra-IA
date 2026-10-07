from app.schemas.conversation import CriteriaUpdate, PropertyFilterRequest


class CriteriaMerger:
    """Apply a validated batch atomically, leaving the original untouched."""

    def merge(self, criteria: PropertyFilterRequest, update: CriteriaUpdate) -> PropertyFilterRequest:
        # Revalidate even objects constructed without normal Pydantic validation.
        update = CriteriaUpdate.model_validate(update.model_dump())
        values = criteria.model_dump()
        for operation in update.operations:
            values[operation.field] = operation.value if operation.operation == "SET" else None
        # Turning shared housing off also removes its now-inapplicable capacity.
        # An explicitly supplied incompatible capacity is rejected instead.
        capacity_set = any(
            op.field == "minSharedHousingCapacity" and op.operation == "SET"
            for op in update.operations
        )
        if values["sharedHousingAllowed"] is not True and not capacity_set:
            values["minSharedHousingCapacity"] = None
        return PropertyFilterRequest.model_validate(values)
