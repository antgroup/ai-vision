"""Base class for all external OpenAPI views."""

from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from ..authentication import SpaceKeyAuthentication
from ..throttling import UnifiedExternalRateThrottle


class OpenAPIBaseView(APIView):
    authentication_classes = [SpaceKeyAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [UnifiedExternalRateThrottle]
    throttle_scope = "external_api"
