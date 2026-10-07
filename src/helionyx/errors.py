"""Structured error and warning model (SRS §4.6).

Every failure that reaches a user is an :class:`HelionyxError` carrying a stable
code, a machine name, a message, an actionable hint (NFR-USE-01) and details.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    VALIDATION_FAILED = "HNX-E001"
    NOT_FOUND = "HNX-E002"
    EXTERNAL_SOURCE_UNAVAILABLE = "HNX-E003"
    SEARCH_SPACE_TOO_LARGE = "HNX-E004"
    NO_FEASIBLE_CANDIDATE = "HNX-E005"
    JOB_TIMEOUT = "HNX-E006"
    RATE_LIMITED = "HNX-E007"
    UNSUPPORTED_COMBINATION = "HNX-E008"
    UNAUTHORIZED = "HNX-E009"


class WarningCode(str, Enum):
    DATA_STALE = "HNX-W001"
    SYNTHETIC_INPUT = "HNX-W002"
    GAP_FILLED = "HNX-W003"
    PLAUSIBILITY = "HNX-W004"


class ErrorBody(BaseModel):
    code: str
    name: str
    message: str
    hint: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class Issue(BaseModel):
    """A validation error or warning attached to a field path."""

    code: str
    name: str
    path: str | None = None
    message: str
    hint: str | None = None


class HelionyxError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        hint: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or {}

    def to_body(self) -> ErrorBody:
        return ErrorBody(
            code=self.code.value,
            name=self.code.name,
            message=self.message,
            hint=self.hint,
            details=self.details,
        )


def not_found(kind: str, ident: str) -> HelionyxError:
    return HelionyxError(
        ErrorCode.NOT_FOUND,
        f"Unknown {kind} ID '{ident}'.",
        f"Check the {kind} ID; list or create the {kind} first.",
        {"kind": kind, "id": ident},
    )


def validation(message: str, hint: str, **details: Any) -> HelionyxError:
    return HelionyxError(ErrorCode.VALIDATION_FAILED, message, hint, details)


def warning(code: WarningCode, message: str, path: str | None = None, hint: str | None = None) -> Issue:
    return Issue(code=code.value, name=code.name, path=path, message=message, hint=hint)
