from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from ..services.plugin_artifacts import (
    latest_plugin,
    plugin_catalog,
    public_base_url,
    support_files,
)
from .common import success_response


class AgentDownloadLatestView(APIView):
    def get(self, request):
        return _catalog_response(request)


class PublicAgentDownloadLatestView(APIView):
    """Anonymous catalog used only by the installer bootstrap."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return _catalog_response(request)


def _catalog_response(request):
    base = public_base_url(request)
    return success_response(
        {
            "cdn_base": base,
            "agents": [latest_plugin(plugin, base) for plugin in plugin_catalog()],
            "support_files": support_files(base),
        }
    )
