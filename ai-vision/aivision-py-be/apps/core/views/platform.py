from rest_framework.views import APIView
from rest_framework.permissions import AllowAny

from .common import success_response
from ..models import PlatformConfig


class PlatformNotifyView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            record = PlatformConfig.objects.get(platform_code="notify", status=1)
        except PlatformConfig.DoesNotExist:
            return success_response(None)

        config = record.config or {}
        if not config.get("enabled"):
            return success_response(None)

        return success_response(config)
