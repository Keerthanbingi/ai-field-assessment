"""Documented tiktoken-based input accounting."""

from __future__ import annotations

import tiktoken

from ai_field.task4_router.models import CompletionRequest


class TokenCounter:
    """Count prompt/chat tokens using the stable cl100k_base encoding."""

    def __init__(self) -> None:
        self._encoding = tiktoken.get_encoding("cl100k_base")

    def count_input_tokens(self, request: CompletionRequest) -> int:
        if request.prompt is not None:
            return len(self._encoding.encode(request.prompt))
        assert request.messages is not None
        # A documented conservative chat envelope: three request tokens and
        # four structural tokens per message, plus encoded role and content.
        total = 3
        for message in request.messages:
            total += 4
            total += len(self._encoding.encode(message.role))
            total += len(self._encoding.encode(message.content))
        return total
