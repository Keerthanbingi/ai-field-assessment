"""Strict Pydantic models used by the Task 1 MCP tools."""

from __future__ import annotations

import re
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

CustomerId = Annotated[str, StringConstraints(strict=True, pattern=r"^CUST-[0-9]{5}$")]
PositiveFiniteAmount = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]

_CUSTOMER_ID_PATTERN = re.compile(r"CUST-[0-9]{5}", flags=re.ASCII)


class StrictToolInput(BaseModel):
    """Base model that rejects coercion and undeclared fields."""

    model_config = ConfigDict(strict=True, extra="forbid")


class GetCustomerRecordInput(StrictToolInput):
    """Arguments for ``get_customer_record``."""

    customer_id: CustomerId = Field(
        description="Customer ID in the exact form CUST- followed by five ASCII digits."
    )

    @model_validator(mode="after")
    def require_exact_ascii_customer_id(self) -> Self:
        """Use fullmatch so a trailing newline can never pass an end anchor."""
        if _CUSTOMER_ID_PATTERN.fullmatch(self.customer_id) is None:
            raise ValueError("customer_id must match CUST-[0-9]{5} exactly")
        return self


class TriggerRefundInput(StrictToolInput):
    """Arguments for ``trigger_refund``."""

    customer_id: CustomerId = Field(
        description="Customer ID in the exact form CUST- followed by five ASCII digits."
    )
    amount: PositiveFiniteAmount = Field(
        description="Positive, finite numeric refund amount; strings and booleans are invalid."
    )
    reason: str = Field(
        strict=True,
        min_length=10,
        description="Reason containing at least ten non-whitespace characters after trimming.",
    )

    @model_validator(mode="after")
    def enforce_semantics(self) -> Self:
        """Apply constraints that JSON Schema alone cannot express clearly."""
        if _CUSTOMER_ID_PATTERN.fullmatch(self.customer_id) is None:
            raise ValueError("customer_id must match CUST-[0-9]{5} exactly")

        trimmed_reason = self.reason.strip()
        if sum(not character.isspace() for character in trimmed_reason) < 10:
            raise ValueError("reason must contain at least 10 non-whitespace characters")

        self.reason = trimmed_reason
        return self


def input_schema(model: type[BaseModel]) -> dict[str, object]:
    """Return the Pydantic-generated JSON Schema advertised by ``tools/list``."""
    return model.model_json_schema(mode="validation")
