"""Strict incremental UTF-8 and Server-Sent Events parsing."""

from __future__ import annotations

import codecs
from dataclasses import dataclass


class ProviderStreamError(Exception):
    """Raised when downstream bytes are not a well-formed supported SSE stream."""


@dataclass(frozen=True)
class SSEEvent:
    data: str
    event: str | None = None
    event_id: str | None = None
    retry: str | None = None

    def encode(self) -> bytes:
        lines: list[str] = []
        if self.event is not None:
            lines.append(f"event: {self.event}")
        if self.event_id is not None:
            lines.append(f"id: {self.event_id}")
        if self.retry is not None:
            lines.append(f"retry: {self.retry}")
        data_lines = self.data.split("\n")
        lines.extend(f"data: {line}" for line in data_lines)
        return ("\n".join(lines) + "\n\n").encode()


class IncrementalSSEParser:
    """Parse arbitrary byte chunks without exposing undecoded or unframed data."""

    def __init__(self, *, max_event_chars: int = 65_536) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        self._line_buffer = ""
        self._event_lines: list[str] = []
        self._event_chars = 0
        self._max_event_chars = max_event_chars

    def _dispatch(self) -> SSEEvent | None:
        if not self._event_lines:
            return None
        data: list[str] = []
        event = event_id = retry = None
        for line in self._event_lines:
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if field == "data":
                data.append(value)
            elif field == "event":
                event = value
            elif field == "id" and "\x00" not in value:
                event_id = value
            elif field == "retry" and value.isdigit():
                retry = value
        self._event_lines.clear()
        self._event_chars = 0
        if not data:
            return None
        return SSEEvent(data="\n".join(data), event=event, event_id=event_id, retry=retry)

    def _consume_text(self, text: str) -> list[SSEEvent]:
        self._line_buffer += text
        events: list[SSEEvent] = []
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            if line.endswith("\r"):
                line = line[:-1]
            if line == "":
                event = self._dispatch()
                if event is not None:
                    events.append(event)
                continue
            self._event_chars += len(line)
            if self._event_chars > self._max_event_chars:
                raise ProviderStreamError("provider SSE event exceeded the configured limit")
            self._event_lines.append(line)
        return events

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        try:
            text = self._decoder.decode(chunk, final=False)
        except UnicodeDecodeError as error:
            raise ProviderStreamError("provider stream contained invalid UTF-8") from error
        return self._consume_text(text)

    def finish(self) -> list[SSEEvent]:
        try:
            tail = self._decoder.decode(b"", final=True)
        except UnicodeDecodeError as error:
            raise ProviderStreamError("provider stream ended within a UTF-8 character") from error
        events = self._consume_text(tail)
        if self._line_buffer or self._event_lines:
            raise ProviderStreamError("provider stream ended within an SSE event")
        return events
