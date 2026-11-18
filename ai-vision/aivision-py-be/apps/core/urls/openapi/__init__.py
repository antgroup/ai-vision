"""External OpenAPI URL registrations."""

from .observability import urlpatterns as observability_urlpatterns

urlpatterns = [
    *observability_urlpatterns,
]
