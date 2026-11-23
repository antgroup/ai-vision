"""Idempotently configure native and framework integrations and their routes."""

from pathlib import Path

import yaml

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import AgentTypeConfig
from apps.core.services.plugin_artifacts import plugins_root, plugin_catalog
from apps.core.management.commands.configure_signoz_datasource import _validate_endpoint


def installation_guide(plugin):
    requirements = [
        {
            "label": "aivision-cli",
            "value": "请确保本机已安装好 aivision-cli（aiv）；接入命令由它执行",
        },
        {"label": plugin["name"], "value": "已安装并配置好，能够调用模型和执行工具"},
        {
            "label": "Python",
            "value": "3.11 或以上；安装器会查找 PATH 或 uv 中已安装的兼容版本",
        },
    ]
    if not plugin.get("binary"):
        requirements.append({"label": "Node.js", "value": "已安装"})
    # The native Windows client does not support agent installation. The
    # browser fills in the macOS/Linux command's placeholders after authorization.
    # --base-url is anchored to the SAME origin the onboarding catalog serves
    # its plugin downloads from (public_base_url): the installer enforces a
    # same-origin check on the artifact URL, so the browser origin (the vite
    # dev-server port) would be rejected. Bake the deployment's public base URL
    # when pinned; fall back to the {baseUrl} placeholder only for single-origin
    # setups where AIVISION_PUBLIC_BASE_URL is unset.
    public_base = (getattr(settings, "AIVISION_PUBLIC_BASE_URL", "") or "").rstrip("/")
    base_arg = public_base or "{baseUrl}"
    connect_command = (
        "aiv connect "
        + plugin["key"]
        + f" --base-url '{base_arg}' --space-id '{{spaceId}}' "
        "--agent-uuid '{agentUuid}' --installation-token '{installationToken}'"
    )
    restart = f"重启 {plugin['name']}"
    if plugin["key"] == "codex":
        restart += "，输入 /hooks 检查并信任新增的 AI Vision hooks"
    return {
        "schema": "v2",
        "plugin_key": plugin["key"],
        "sections": [
            {"type": "prerequisites", "title": "接入前提", "items": requirements},
            {
                "type": "setup_steps",
                "title": "在 Agent 所在机器安装",
                "platforms": {
                    "macOS/Linux": {
                        "modes": {
                            "cli": {
                                "label": "原生安装",
                                "steps": [
                                    {
                                        "title": "复制并执行安装命令，授权和上报配置将自动完成",
                                        "command": connect_command,
                                    }
                                ],
                            }
                        }
                    }
                },
            },
            {
                "type": "verification",
                "title": "查看采集结果",
                "steps": [
                    restart + "，执行一次包含模型调用和工具调用的任务",
                    "打开当前空间的数据看板和任务轨迹，确认数据已上报",
                    '使用自定义配置目录时，安装命令追加 --config-dir "目录路径"，Agent 启动时也使用该目录',
                ],
            },
        ],
        "success_message": "请在 Agent 所在机器安装采集插件，运行任务后到数据看板查看上报结果。",
    }


def plugin_config_guide(plugin):
    """Return the config_guide for a plugin.

    Plugins that publish per-framework onboarding demos list an
    ``examples_dir`` (relative to the plugins root) in the catalog; each
    ``*.yaml`` under it is one framework guide. CLI collector plugins without
    an ``examples_dir`` keep using the generated install guide.
    """
    if plugin.get("source_install"):
        # These integrations have no published aiv installer. Hermes uses its
        # native Relay guide; do not offer the retired source-plugin steps.
        guide = {
            "schema": "v2",
            "sections": [],
        }
        if plugin["key"] == "hermes" and plugin.get("native_relay_onboarding"):
            public_base = (getattr(settings, "AIVISION_PUBLIC_BASE_URL", "") or "").rstrip("/")
            guide["native_relay_onboarding"] = {
                **plugin["native_relay_onboarding"],
                "otlp_endpoint": f"{public_base or '{baseUrl}'}/api/public/otel/v1/traces",
            }
        return guide
    examples_dir_rel = plugin.get("examples_dir")
    if not examples_dir_rel:
        return installation_guide(plugin)
    examples_dir = plugins_root() / examples_dir_rel
    if not examples_dir.is_dir():
        return {}
    frameworks = []
    for f in sorted(examples_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text())
        except (yaml.YAMLError, OSError):
            continue
        if isinstance(data, dict) and data:
            bootstrap = data.get("bootstrapFile")
            if bootstrap is not None:
                plugin_dir = (plugins_root() / plugin["key"]).resolve()
                if not isinstance(bootstrap, str):
                    raise CommandError(f"Invalid bootstrapFile in {f.name}")
                relative = Path(bootstrap)
                source = (plugin_dir / relative).resolve()
                if (relative.is_absolute() or ".." in relative.parts
                        or not source.is_relative_to(plugin_dir)
                        or source.suffix != ".py" or not source.is_file()):
                    raise CommandError(f"bootstrapFile must be a Python file inside {plugin['key']}")
                data["bootstrapExample"] = source.read_text()
                # The UI copies/downloads exactly the assembled runnable program.
                data["pipExample"] = data["bootstrapExample"] + "\n\n" + data.get("pipExample", "")
            frameworks.append(data)
    guide = {"frameworks": frameworks}
    if plugin.get("integration"):
        guide["integration"] = plugin["integration"]
    return guide


class Command(BaseCommand):
    help = "Configure the Agent market and SigNoz routes, preserving existing data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--endpoint",
            required=True,
            help="SigNoz query service URL (server reachable)",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        endpoint = _validate_endpoint(options["endpoint"])
        for order, plugin in enumerate(plugin_catalog(), 1):
            matches = list(
                AgentTypeConfig.objects.shared_runtime().filter(
                    agent_product=plugin["product"]
                )
            )
            if len(matches) > 1:
                raise CommandError(
                    f"Multiple shared catalog entries for {plugin['product']}"
                )
            config = (
                matches[0]
                if matches
                else AgentTypeConfig(
                    agent_name=plugin["product"],
                    agent_product=plugin["product"],
                )
            )
            config.display_name = plugin["name"]
            config.icon = config.icon or plugin["icon"]
            config.runtime_type = plugin["runtime"]
            config.category = "CUSTOM"
            config.setup_mode = plugin.get("setup_mode", "CLI")
            config.display_scope = plugin.get("display_scope", "ALL")
            config.sort_order = order
            config.status = 1
            integration_kind = (plugin.get("integration") or {}).get("kind")
            if integration_kind == "observability_sdk":
                config.description = f"通过 {plugin['name']} SDK 接入自建 Agent 项目"
            elif integration_kind == "partner_runtime":
                config.description = f"通过 {plugin['name']} 运行时接入自建 Agent 框架"
            else:
                config.description = (
                    "在 Agent 所在机器安装采集插件，任务数据上报到当前 AI Vision 空间"
                )
            config.config_guide = plugin_config_guide(plugin)
            config.cli_command = ""
            config.save()
            call_command(
                "configure_signoz_datasource",
                agent_product=config.agent_product,
                agent_product_filter=plugin["product"],
                endpoint=endpoint,
                make_default=plugin.get("make_default", True),
                stdout=self.stdout,
            )
        self.stdout.write(
            self.style.SUCCESS("Agent plugins and self-built integrations configured")
        )
