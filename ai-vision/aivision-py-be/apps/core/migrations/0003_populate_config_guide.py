import json

from django.db import migrations


CONFIG_GUIDE_DATA = {
    "ANT_CLAUDE_CODE": {
        "setup_mode": "AUTO",
        "config_guide": {
            "setup_guide": {
                "title": "自动发现模式",
                "description": "此 Agent 支持自动发现，无需手动配置 API Key。启动后会自动接入平台。",
                "type": "auto",
            },
            "features": {
                "preset_dashboard": True,
                "trace_supported": True,
            },
            "success_message": "使用 {name} 后数据将自动采集到平台",
        },
    },
    "CODEFUSE": {
        "setup_mode": "AUTO",
        "config_guide": {
            "setup_guide": {
                "title": "自动发现模式",
                "description": "此 Agent 支持自动发现，无需手动配置 API Key。启动后会自动接入平台。",
                "type": "auto",
            },
            "limitations": [
                "目前仅支持上报 CodeFuse CLI 数据，暂不支持 CodeFuse IDE。"
            ],
            "features": {
                "preset_dashboard": True,
                "trace_supported": False,
            },
            "empty_state": {
                "no_trace": "CodeFuse 暂不支持轨迹上报，请关注后续版本更新。",
                "no_session": "CodeFuse 暂不支持 Session 上报，请关注后续版本更新。",
            },
            "success_message": "使用 {name} 后数据将自动采集到平台",
        },
    },
    "CLAUDE_CODE": {
        "setup_mode": "APIKEY",
        "config_guide": {
            "setup_guide": {
                "title": "API Key 接入",
                "description": "需要配置 API Key 完成接入，支持本地和云端多实例部署。",
                "type": "manual",
            },
            "features": {
                "preset_dashboard": False,
                "trace_supported": True,
            },
            "success_message": "使用 {name} 后数据将自动采集到平台",
        },
    },
    "HOMICLAW": {
        "setup_mode": "AUTO",
        "config_guide": {
            "setup_guide": {
                "title": "自动发现模式",
                "description": "此 Agent 支持自动发现，无需手动配置 API Key。启动后会自动接入平台。",
                "type": "auto",
            },
            "features": {
                "preset_dashboard": True,
                "trace_supported": True,
            },
            "success_message": "使用 {name} 后数据将自动采集到平台",
        },
    },
    "CUSTOM": {
        "setup_mode": "OTHER",
        "config_guide": {
            "setup_guide": {
                "title": "手动接入",
                "description": "面向技术用户，通过手动埋点方式接入平台。",
                "type": "manual",
            },
            "features": {
                "preset_dashboard": False,
                "trace_supported": False,
            },
            "custom_guide": {
                "title": "接入说明",
                "description": "当前版本支持为非内置 Agent 预留自定义接入入口。接入前需要先确认 Agent 命名、上报字段以及空间 API Key，再按照平台规范完成埋点配置。",
                "checklist": [
                    "确认 Agent 名称与实例名规则。",
                    "准备空间级 API Key 或平台授权信息。",
                    "与平台管理员确认事件字段、Token 指标和 Trace 映射方式。",
                ],
            },
            "success_message": "使用 {name} 后数据将自动采集到平台",
        },
    },
}


def populate_config_guide(apps, schema_editor):
    AgentTypeConfig = apps.get_model("core", "AgentTypeConfig")
    for agent_name, data in CONFIG_GUIDE_DATA.items():
        AgentTypeConfig.objects.filter(agent_name=agent_name).update(
            setup_mode=data["setup_mode"],
            config_guide=json.dumps(data["config_guide"], ensure_ascii=False),
        )


def reverse_config_guide(apps, schema_editor):
    AgentTypeConfig = apps.get_model("core", "AgentTypeConfig")
    AgentTypeConfig.objects.filter(
        agent_name__in=CONFIG_GUIDE_DATA.keys()
    ).update(config_guide=None)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_fix_space_uuid_default"),
    ]

    operations = [
        migrations.RunPython(populate_config_guide, reverse_config_guide),
    ]
