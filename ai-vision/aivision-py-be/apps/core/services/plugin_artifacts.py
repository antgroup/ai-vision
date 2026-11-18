"""Local production plugin catalog. No credentials or upstream downloads."""
from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

from django.conf import settings

SUPPORT_FILES = {
    "native_config.py": "scripts/native_config.py",
    "hook_runtime.py": "scripts/hook_runtime.py",
    "register-opencode.mjs": "opencode/scripts/register-opencode-plugin.mjs",
    "configure-pi.mjs": "pi/scripts/configure-local-install.mjs",
    "register-dsh.mjs": "deepseek/scripts/install-profile.mjs",
}


def support_files(base: str) -> list[dict]:
    result = []
    for name, relative in SUPPORT_FILES.items():
        if name.endswith(".py") and native_onboarding_enabled():
            continue
        try:
            content = (plugins_root() / relative).read_bytes()
        except OSError:
            # Native distributions intentionally omit readable helper modules.
            continue
        result.append({"name": name, "url": f"{base}/api/public/onboarding/support/{name}",
                       "sha256": hashlib.sha256(content).hexdigest()})
    return result


def plugins_root() -> Path:
    override = settings.AIVISION_AGENT_PLUGINS_DIR or settings.AIVISION_OBSERV_AGENTS_DIR
    return Path(override).expanduser().resolve() if override else Path(settings.BASE_DIR).parent / "agent-plugins"


def native_onboarding_enabled() -> bool:
    root = plugins_root() / "native-onboarding"
    return root.exists() or root.is_symlink()


def native_installer_path(platform: str, arch: str) -> Path | None:
    if platform not in {"linux-gnu", "darwin"} or arch not in {"x64", "arm64"}:
        return None
    root = (plugins_root() / "native-onboarding").resolve()
    candidate = (root / f"{platform}-{arch}" / "connect.tar.gz").resolve()
    return candidate if candidate.is_relative_to(root) and candidate.is_file() else None


def artifacts_root() -> Path:
    override = settings.AIVISION_PLUGIN_ARTIFACTS_DIR
    return Path(override).expanduser().resolve() if override else plugins_root() / "dist"


def plugin_catalog() -> list[dict]:
    return json.loads((plugins_root() / "agents.json").read_text())


def public_base_url(request) -> str:
    return (settings.AIVISION_PUBLIC_BASE_URL or request.build_absolute_uri("/")).rstrip("/")


def safe_component(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)) and ".." not in value


def artifact_path(key: str, version: str, filename: str) -> Path | None:
    if not all(safe_component(x) for x in (key, version, filename)):
        return None
    if key not in {p["key"] for p in plugin_catalog()}:
        return None
    root = artifacts_root().resolve()
    candidate = (root / key / version / filename).resolve()
    return candidate if candidate.is_relative_to(root) and candidate.is_file() else None


def latest_plugin(plugin: dict, base: str) -> dict:
    key = plugin["key"]
    prefix = f"{base}/api/public/onboarding/plugins/{key}"
    result = {"key": key, "display_name": plugin["name"], "version": None,
              "latest_url": f"{prefix}/latest.txt", "downloads": [],
              "install_url": None if plugin.get("source_install") or native_onboarding_enabled() else f"{base}/api/public/onboarding/install-{key}.py"}
    result["plugin"] = {k: v for k, v in plugin.items() if k not in {"source", "features"}}
    try:
        manifest = json.loads((artifacts_root() / key / "latest.json").read_text())
        version = manifest["version"]
        if not safe_component(version):
            return result
        for item in manifest["downloads"]:
            filename = item["filename"]
            path = artifact_path(key, version, filename)
            if path is None or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
                continue
            result["downloads"].append({
                "label": item["label"], "platform": item["platform"], "arch": item["arch"],
                "url": f"{prefix}/{version}/{filename}",
                "url_template": f"{prefix}/{{version}}/{filename.replace(version, '{version}', 1)}",
                "target_filename": item["target_filename"], "executable": bool(plugin.get("binary")),
                "sha256": item["sha256"], "size": path.stat().st_size,
                "collection_modes": [mode for mode in item.get("collection_modes", ["full"]) if mode in ("full", "privacy")],
            })
        if result["downloads"]:
            result["version"] = version
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return result
