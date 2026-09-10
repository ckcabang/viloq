"""FastAPI application implementing `openapi.yaml`.

Run it with: `uv run uvicorn app.main:app --reload`
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import API_PREFIX
from app.errors import ApiError
from app.routers import auth, expenses, groups, invites, payments, settlement

DESCRIPTION = """
Backend for the viloq expense-splitting app, implemented against the
hand-written contract in `openapi.yaml`.

Money is always integer minor units. Mutations of existing records require an
`If-Match` header carrying the version the client last saw. Auth is an opaque
bearer session token from `POST /auth/magic-links/verify`.

Storage is an in-process mock; state is lost on restart.
"""

# Status codes whose default FastAPI/Starlette body must be rewritten into the
# contract's `{code, message}` shape.
_DEFAULT_ERROR_CODES = {
    401: ("unauthorized", "Please sign in again."),
    403: ("forbidden", "You do not have access to that."),
    404: ("not_found", "Not found."),
    405: ("not_found", "Not found."),
}


def create_app() -> FastAPI:
    app = FastAPI(
        title="viloq API",
        version="0.1.0",
        summary="HTTP contract for the viloq expense-splitting app.",
        description=DESCRIPTION,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )

    for router in (
        auth.router,
        groups.router,
        invites.router,
        expenses.router,
        payments.router,
        settlement.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.body())

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # The contract has no 422: a malformed body is a 400 `validation`.
        return JSONResponse(
            status_code=400,
            content={"code": "validation", "message": _first_message(exc)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code, message = _DEFAULT_ERROR_CODES.get(
            exc.status_code, ("internal", "Something went wrong.")
        )
        detail = exc.detail if isinstance(exc.detail, str) else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": code, "message": detail or message},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"code": "internal", "message": "Something went wrong."},
        )

    return app


def _first_message(exc: RequestValidationError) -> str:
    """Turn pydantic's first error into one user-facing sentence."""
    errors = exc.errors()
    if not errors:
        return "Request could not be understood."
    first = errors[0]
    location = [str(part) for part in first.get("loc", ()) if part != "body"]
    field = ".".join(location)
    message = first.get("msg", "Invalid value")
    message = message.removeprefix("Value error, ")
    return f"{field}: {message}" if field else message


app = create_app()
