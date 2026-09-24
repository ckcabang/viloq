"""The single error shape the contract defines: `{code, message}`."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """Raised anywhere in the app; rendered as `{code, message, ...extra}`."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


def validation(message: str) -> ApiError:
    return ApiError(400, "validation", message)


def unauthorized(message: str = "Please sign in again.") -> ApiError:
    return ApiError(401, "unauthorized", message)


def forbidden(message: str) -> ApiError:
    return ApiError(403, "forbidden", message)


def not_found(message: str) -> ApiError:
    return ApiError(404, "not_found", message)


def version_conflict(current: dict[str, Any]) -> ApiError:
    return ApiError(
        409,
        "version_conflict",
        "This record was changed by someone else. Reloading the latest version.",
        current=current,
    )


def currency_locked() -> ApiError:
    return ApiError(
        409, "currency_locked", "Currency is locked once the group has expenses."
    )


def precondition_required() -> ApiError:
    return ApiError(
        412, "precondition_required", "Provide the record version via If-Match."
    )
