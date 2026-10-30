"""Bounded incremental PII redaction for streamed model text."""

from __future__ import annotations

import re
from dataclasses import dataclass

REDACTION = "[REDACTED]"

EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
    r"(?![A-Za-z0-9.-])"
)
SSN_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")

EMAIL_TOKEN_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.!#$%&'*+/=?^_`{|}~@-"
)
NUMERIC_CANDIDATE_CHARS = frozenset("0123456789 -")


def luhn_valid(digits: str) -> bool:
    """Return whether a 13-19 digit card candidate has a valid Luhn checksum."""
    if not (13 <= len(digits) <= 19) or len(set(digits)) == 1:
        return False
    total = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        value = int(character)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _pii_spans(text: str) -> list[tuple[int, int]]:
    spans = [(match.start(), match.end()) for match in EMAIL_RE.finditer(text)]
    spans.extend((match.start(), match.end()) for match in SSN_RE.finditer(text))
    for match in CARD_RE.finditer(text):
        digits = re.sub(r"[ -]", "", match.group())
        if luhn_valid(digits):
            spans.append((match.start(), match.end()))
    if not spans:
        return []
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def redact_complete_text(text: str) -> str:
    """Redact complete PII values in finalized text."""
    spans = _pii_spans(text)
    if not spans:
        return text
    output: list[str] = []
    cursor = 0
    for start, end in spans:
        output.append(text[cursor:start])
        output.append(REDACTION)
        cursor = end
    output.append(text[cursor:])
    return "".join(output)


def _suffix_start(text: str, allowed: frozenset[str], *, require_digit: bool = False) -> int | None:
    index = len(text)
    while index > 0 and text[index - 1] in allowed:
        index -= 1
    suffix = text[index:]
    if not suffix or (
        require_digit
        and not any(character.isascii() and character.isdigit() for character in suffix)
    ):
        return None
    return index


@dataclass
class IncrementalPIIRedactor:
    """Release safe prefixes while retaining a bounded possible-PII suffix."""

    max_candidate_chars: int = 320
    max_numeric_candidate_chars: int = 96
    pending: str = ""
    suppressed_kind: str | None = None
    peak_buffered_chars: int = 0

    def __post_init__(self) -> None:
        if self.max_candidate_chars < 64:
            raise ValueError("max_candidate_chars must be at least 64")
        if not (32 <= self.max_numeric_candidate_chars <= self.max_candidate_chars):
            raise ValueError("max_numeric_candidate_chars must be between 32 and the maximum")

    @property
    def buffered_chars(self) -> int:
        return len(self.pending)

    def feed(self, text: str) -> str:
        """Consume one parsed text delta and return the safe text now releasable."""
        if not isinstance(text, str):
            raise TypeError("stream delta content must be a string")
        output: list[str] = []
        for character in text:
            if self.suppressed_kind is not None:
                allowed = (
                    EMAIL_TOKEN_CHARS
                    if self.suppressed_kind == "email"
                    else NUMERIC_CANDIDATE_CHARS
                )
                if character in allowed:
                    continue
                self.suppressed_kind = None

            self.pending += character
            email_start = _suffix_start(self.pending, EMAIL_TOKEN_CHARS)
            numeric_start = _suffix_start(
                self.pending,
                NUMERIC_CANDIDATE_CHARS,
                require_digit=True,
            )
            starts = [start for start in (email_start, numeric_start) if start is not None]
            hold_start = min(starts) if starts else len(self.pending)
            if hold_start:
                output.append(redact_complete_text(self.pending[:hold_start]))
                self.pending = self.pending[hold_start:]
                if email_start is not None:
                    email_start -= hold_start
                if numeric_start is not None:
                    numeric_start -= hold_start

            self.peak_buffered_chars = max(self.peak_buffered_chars, len(self.pending))
            if not self.pending:
                continue

            numeric_is_earliest = numeric_start is not None and (
                email_start is None or numeric_start < email_start
            )
            limit = (
                self.max_numeric_candidate_chars
                if numeric_is_earliest
                else self.max_candidate_chars
            )
            if len(self.pending) > limit:
                output.append(REDACTION)
                self.pending = ""
                self.suppressed_kind = "numeric" if numeric_is_earliest else "email"

        return "".join(output)

    def finish(self) -> str:
        """Finalize a cleanly terminated stream without releasing suppressed overflow."""
        if self.suppressed_kind is not None:
            self.pending = ""
            self.suppressed_kind = None
            return ""
        output = redact_complete_text(self.pending)
        self.pending = ""
        return output
