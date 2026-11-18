"""V2 Agent Query error types.

Each error stores an HTTP status code, a type string, a message, and
an optional details dict. The ``to_response()`` method returns a DRF
Response in the standard V2 error envelope:

    {
        "code": <http_status>,
        "message": "...",
        "error": {"type": "...", "details": {...}}
    }
"""

from __future__ import annotations

from typing import Any

from rest_framework.response import Response


class AgentQueryError(Exception):
    """Base class for all agent-query errors."""

    http_status: int = 500
    type_str: str = "AGENT_QUERY_ERROR"
    default_message: str = "Agent query error"

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)

    def to_response(self) -> Response:
        body = {
            "code": self.http_status,
            "message": self.message,
            "error": {
                "type": self.type_str,
                "details": self.details,
            },
        }
        return Response(body, status=self.http_status)


class AgentQueryConfigNotFoundError(AgentQueryError):
    http_status = 404
    type_str = "AGENT_QUERY_CONFIG_NOT_FOUND"
    default_message = "Agent query config not found"


class AgentQueryUnsupportedScopeError(AgentQueryError):
    http_status = 400
    type_str = "AGENT_QUERY_UNSUPPORTED_SCOPE"
    default_message = "Unsupported scope for this agent product"


class AgentQueryDecryptFailedError(AgentQueryError):
    http_status = 400
    type_str = "AGENT_QUERY_DECRYPT_FAILED"
    default_message = "Failed to decrypt agent query payload"


class AgentQueryInvalidPayloadError(AgentQueryError):
    http_status = 400
    type_str = "AGENT_QUERY_INVALID_PAYLOAD"
    default_message = "Invalid agent query payload"


class AgentQueryUnknownTemplateVariableError(AgentQueryError):
    http_status = 400
    type_str = "AGENT_QUERY_UNKNOWN_TEMPLATE_VARIABLE"
    default_message = "Unknown template variable"


class AgentQueryPermissionDeniedError(AgentQueryError):
    http_status = 403
    type_str = "AGENT_QUERY_PERMISSION_DENIED"
    default_message = "Permission denied for agent query"


class AgentQueryPlainDisabledError(AgentQueryError):
    http_status = 403
    type_str = "AGENT_QUERY_PLAIN_DISABLED"
    default_message = "Plain (unencrypted) agent query is disabled"


class AgentQueryExecutionFailedError(AgentQueryError):
    http_status = 500
    type_str = "AGENT_QUERY_EXECUTION_FAILED"
    default_message = "Agent query execution failed"


class AgentQueryResourceLimitError(AgentQueryError):
    http_status = 413
    type_str = "AGENT_QUERY_RESOURCE_LIMIT"
    default_message = "Query exceeds the resource budget; narrow the time range or page size"


class AgentQueryBusyError(AgentQueryError):
    http_status = 429
    type_str = "AGENT_QUERY_BUSY"
    default_message = "Query capacity is busy; retry shortly"
