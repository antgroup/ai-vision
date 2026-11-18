"""Public downloads contain only code; users supply their SpaceKey locally."""

import json
import hashlib
import shlex

from django.http import FileResponse, HttpResponse, JsonResponse
from django.views import View

from ..services.plugin_artifacts import (
    SUPPORT_FILES,
    artifact_path,
    latest_plugin,
    native_installer_path,
    native_onboarding_enabled,
    plugin_catalog,
    plugins_root,
    public_base_url,
)


def _script(request, name, agent=""):
    path = plugins_root() / "scripts" / name
    if not path.is_file():
        return JsonResponse({"code": 4040, "message": "Script not found"}, status=404)
    encoded_base = (
        shlex.quote(public_base_url(request))
        if name.endswith(".sh")
        else json.dumps(public_base_url(request))
    )
    content = path.read_text().replace('"__AIVISION_BASE_URL__"', encoded_base)
    content = content.replace('"__AIVISION_AGENT__"', json.dumps(agent))
    response = HttpResponse(content, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    return response


class InstallScriptView(View):
    def get(self, request):
        if native_onboarding_enabled():
            return JsonResponse(
                {"code": 4090, "message": "This deployment uses native installers. Run 'aiv connect <agent>' with a current aiv CLI."},
                status=409,
            )
        return _script(request, "install.sh")


class OnboardingSupportView(View):
    def get(self, request, filename):
        relative = SUPPORT_FILES.get(filename)
        if relative is None or (filename.endswith(".py") and native_onboarding_enabled()):
            return JsonResponse(
                {"code": 4040, "message": "Support file not found"}, status=404
            )
        try:
            stream = (plugins_root() / relative).open("rb")
        except OSError:
            return JsonResponse(
                {"code": 4040, "message": "Support file not found"}, status=404
            )
        return FileResponse(
            stream,
            as_attachment=True,
            filename=filename,
        )


class ConnectScriptView(View):
    def get(self, request):
        if native_onboarding_enabled():
            platform = request.GET.get("platform", "")
            arch = request.GET.get("arch", "")
            if platform not in {"linux-gnu", "darwin"} or arch not in {"x64", "arm64"}:
                return JsonResponse(
                    {"code": 4000, "message": "A supported platform and arch are required"},
                    status=400,
                )
            path = native_installer_path(platform, arch)
            if path is None:
                return JsonResponse(
                    {"code": 4040, "message": "Native installer not built for this platform"},
                    status=404,
                )
            try:
                stream = path.open("rb")
            except OSError:
                return JsonResponse(
                    {"code": 4040, "message": "Native installer not found"}, status=404
                )
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            stream.seek(0)
            response = FileResponse(
                stream, as_attachment=True, filename="connect.tar.gz",
                content_type="application/gzip",
            )
            response["X-AIV-Installer-Format"] = "native-tar-v1"
            response["X-AIV-Installer-SHA256"] = digest
            response["Cache-Control"] = "no-cache"
            return response
        return _script(request, "connect")


class AgentInstallScriptView(View):
    def get(self, request, agent):
        if agent not in {p["key"] for p in plugin_catalog() if not p.get("source_install")}:
            return JsonResponse({"code": 4040, "message": "Unknown plugin"}, status=404)
        if native_onboarding_enabled():
            return JsonResponse(
                {"code": 4090, "message": f"This deployment uses native installers. Run 'aiv connect {agent}' with a current aiv CLI."},
                status=409,
            )
        return _script(request, "connect", agent)


class PluginLatestVersionView(View):
    def get(self, request, agent):
        plugin = next((p for p in plugin_catalog() if p["key"] == agent), None)
        version = (
            latest_plugin(plugin, public_base_url(request))["version"]
            if plugin
            else None
        )
        if not version:
            return JsonResponse(
                {"code": 4040, "message": "Plugin has not been built"}, status=404
            )
        response = HttpResponse(version + "\n", content_type="text/plain")
        response["Cache-Control"] = "no-cache"
        return response


class PluginArtifactView(View):
    def get(self, request, agent, version, filename):
        path = artifact_path(agent, version, filename)
        if path is None or path.suffix == ".json":
            return JsonResponse(
                {"code": 4040, "message": "Artifact not found"}, status=404
            )
        return FileResponse(path.open("rb"), as_attachment=True, filename=filename)


class AgentBinaryView(View):
    """Compatibility for old cdxhooks/cchooks URLs; serve only this host's build."""

    def get(self, request, artifact):
        import platform

        plugin = next(
            (p for p in plugin_catalog() if p.get("binary") == artifact), None
        )
        if plugin:
            system = platform.system().lower()
            system = "linux-gnu" if system == "linux" else system
            arch = (
                "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
            )
            latest = latest_plugin(plugin, public_base_url(request))
            item = next(
                (
                    item
                    for item in latest["downloads"]
                    if item["platform"] == system and item["arch"] == arch
                ),
                None,
            )
            if item:
                return PluginArtifactView().get(
                    request,
                    plugin["key"],
                    latest["version"],
                    item["url"].rsplit("/", 1)[1],
                )
        return JsonResponse(
            {"code": 4040, "message": "Artifact not built for this platform"},
            status=404,
        )
