"""One reader emergency control, shared by Trace, Session and facets."""
from django.conf import settings

from ..types import TraceQueryServiceError


def query_mode():
    mode = getattr(settings, "AIVISION_QUERY_MODE", "auto")
    if mode not in {"auto", "raw"}:
        raise TraceQueryServiceError("Invalid AIVISION_QUERY_MODE; expected auto or raw", 3011)
    return mode
