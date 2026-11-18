from collections.abc import Mapping
from typing import Any

from fastapi.responses import JSONResponse


def openai_error_response(
    status_code: int,
    message: str,
    error_type: str,
    code: str,
    *,
    param: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {
        "message": message,
        "type": error_type,
        "param": param,
        "code": code,
    }
    return JSONResponse({"error": error}, status_code=status_code, headers=dict(headers or {}))
