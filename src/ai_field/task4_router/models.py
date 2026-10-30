"""Strict request models and safe error helpers for Task 4."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


class ChatMessage(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: StrictStr = Field(min_length=1)


class CompletionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    model: StrictStr = Field(min_length=1)
    prompt: StrictStr | None = Field(default=None, min_length=1)
    messages: list[ChatMessage] | None = Field(default=None, min_length=1)
    max_tokens: StrictInt = Field(gt=0, le=1_000_000)

    @model_validator(mode="after")
    def exactly_one_input_form(self) -> CompletionRequest:
        if (self.prompt is None) == (self.messages is None):
            raise ValueError("exactly one of prompt or messages is required")
        return self

    def provider_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class GatewayError(BaseModel):
    code: str
    message: str
    request_id: str


class GatewayErrorEnvelope(BaseModel):
    error: GatewayError
