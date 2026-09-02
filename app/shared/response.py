"""Envelope respons + error API, mengikuti konvensi ordina."""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

_STATUS_NAME = {
    200: "OK",
    201: "CREATED",
    204: "NO_CONTENT",
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    500: "INTERNAL_SERVER_ERROR",
}


def status_name(code: int) -> str:
    return _STATUS_NAME.get(code, "INTERNAL_SERVER_ERROR")


class ApiError(Exception):
    """Error yang sudah dipetakan ke status HTTP; dirender pakai envelope yang sama."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def success(code: int, message: str, data: Any = None) -> JSONResponse:
    body: dict[str, Any] = {
        "success": True,
        "code": code,
        "status": status_name(code),
        "message": message,
    }
    if data is not None:
        body["data"] = data
    return JSONResponse(status_code=code, content=body)


def error(code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"success": False, "code": code, "status": status_name(code), "message": message},
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return error(exc.code, exc.message)
