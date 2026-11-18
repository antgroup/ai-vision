from rest_framework.exceptions import AuthenticationFailed, Throttled
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.core.permissions.space import SpaceAuthorizationError
from apps.core.services.observation_subject_service import ObservationSubjectError


def custom_exception_handler(exc, context):
    if isinstance(exc, SpaceAuthorizationError):
        return Response(
            {
                "code": 4003,
                "message": exc.error_key,
                "data": {"error_key": exc.error_key},
            },
            status=exc.status,
        )
    if isinstance(exc, ObservationSubjectError):
        return Response(
            {
                "code": 4003,
                "message": exc.error_key,
                "data": {"error_key": exc.error_key},
            },
            status=409,
        )
    response = drf_exception_handler(exc, context)
    if response is not None:
        if isinstance(exc, AuthenticationFailed):
            response.data = {
                "code": 4001,
                "message": str(exc.detail),
                "data": None,
            }
        elif isinstance(exc, Throttled):
            response.data = {
                "code": 4029,
                "message": "RATE_LIMITED",
                "data": {"error_key": "RATE_LIMITED"},
            }
    return response
