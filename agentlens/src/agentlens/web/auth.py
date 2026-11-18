import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from agentlens.web.openai import openai_error_response

HEADER = "x-antmonitor-token"
OPEN_PATHS = {"/internal/healthz", "/internal/readyz"}
INFERENCE_PATHS = {
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/responses",
}


def _bearer_token(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


class TokenAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, expected_token: str) -> None:
        super().__init__(app)
        self.expected = expected_token

    async def dispatch(self, request: Request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)
        if request.url.path in INFERENCE_PATHS:
            token = _bearer_token(request.headers.get("authorization"))
            if token is None or not hmac.compare_digest(token, self.expected):
                return openai_error_response(
                    401,
                    "Incorrect API key provided.",
                    "invalid_request_error",
                    "invalid_api_key",
                )
            return await call_next(request)
        token = request.headers.get(HEADER)
        if not token:
            return JSONResponse(
                {"code": "unauthorized", "msg": f"missing {HEADER}"},
                status_code=401,
            )
        if not hmac.compare_digest(token, self.expected):
            return JSONResponse({"code": "forbidden", "msg": f"invalid {HEADER}"}, status_code=403)
        return await call_next(request)
