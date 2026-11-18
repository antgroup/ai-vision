"""Provision personal-space diagnosis through AgentLens's scoped resource APIs."""

import logging
import re

import requests
from django.conf import settings
from django.db import transaction

from apps.core.models import Agent
from apps.core.trace_query.dataset_spec import (
    UnsupportedDataSourceError,
    build_default_dataset,
)

logger = logging.getLogger(__name__)

MANAGED_BY = "aivision.personal_diagnosis"
JOB_NAME = "内置诊断"
BUILTIN_EXECUTORS = (
    "rewrite_overlap",
    "stuck_loop",
    "tool_success",
    "total_latency",
    "high_risk_command",
    "sensitive_leakage",
)


class PersonalDiagnosisError(Exception):
    pass


def _request(method, path, **kwargs):
    try:
        response = requests.request(
            method,
            f"{settings.AGENTLENS_BASE_URL.rstrip('/')}/api/v1/{path}",
            headers={"x-antmonitor-token": settings.AGENTLENS_TOKEN},
            timeout=10,
            **kwargs,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        # Never expose remote response bodies (which can contain datasource secrets).
        raise PersonalDiagnosisError("内置诊断初始化失败，请稍后重试") from exc


def _managed_resource(items, scope):
    for item in items:
        if any(item.get(key) != value for key, value in scope.items()):
            raise PersonalDiagnosisError("内置诊断资源不属于当前空间和 Agent")
        if (item.get("spec") or {}).get("managed_by") == MANAGED_BY:
            return item
    if any(item.get("name") == JOB_NAME for item in items):
        raise PersonalDiagnosisError(
            "已有同名自建任务或数据集，请先由管理员处理名称冲突"
        )
    return None


def _upgrade_hourly_dataset(dataset, scope):
    spec = dataset.get("spec") or {}
    if (
        any(dataset.get(key) != value for key, value in scope.items())
        or spec.get("managed_by") != MANAGED_BY
    ):
        raise PersonalDiagnosisError("内置诊断数据集的归属或管理标识不匹配")
    query = spec.get("query") or {}
    if query.get("time_range") == "2m":
        # Upgrade the old hourly job's two-minute sampling window without
        # changing its source, identity filters, or any user-owned dataset.
        return _request(
            "PATCH",
            f"dataset/{dataset['uuid']}",
            json={
                "spec": {**spec, "query": {**query, "time_range": "1h"}},
                "modifier": "AI Vision",
            },
        )
    return dataset


@transaction.atomic
def ensure_personal_diagnosis(agent_uuid):
    """Idempotent across page visits, installation callbacks and backfills.

    The Agent row serializes provisioning. Dataset creation can survive a failed
    Job request; a retry reuses it. Existing user-created Jobs are never changed.
    """
    # Personal spaces provide no default diagnosis job unless explicitly
    # re-enabled (AIVISION_PERSONAL_DIAGNOSIS_ENABLED) for local testing.
    if not getattr(settings, "AIVISION_PERSONAL_DIAGNOSIS_ENABLED", False):
        return None
    agent = (
        Agent.objects.select_for_update()
        .select_related("space__creator")
        .filter(uuid=agent_uuid, space__type=1, gmt_deleted__isnull=True)
        .first()
    )
    if agent is None or re.sub(r"[^a-z0-9]", "", agent.agent_product.lower()) not in {
        "antcc",
        "codex",
        "deepseekharness",
    }:
        return None
    scope = {"space_uuid": agent.space.uuid, "agent_uuid": agent.uuid}
    jobs = _request("GET", "jobs", params=scope)["items"]
    job = _managed_resource(jobs, scope)
    active = (
        agent.status == 1
        and agent.space.status == 1
        and agent.space.gmt_deleted is None
    )
    if not active:
        if job and job["status"] != "disabled":
            _request("PATCH", f"job/{job['uuid']}", json={"status": "disabled"})
        return job
    if job:
        if (
            job.get("trigger_type") == "schedule"
            and job.get("schedule_cron") == "0 * * * *"
        ):
            dataset = _request("GET", f"dataset/{job['dataset_uuid']}")
            _upgrade_hourly_dataset(dataset, scope)
        if job["status"] != "enabled":
            job = _request("PATCH", f"job/{job['uuid']}", json={"status": "enabled"})
        return job

    try:
        default = build_default_dataset(
            agent.space, agent.space.creator, agent.agent_product
        )
    except UnsupportedDataSourceError as exc:
        raise PersonalDiagnosisError(str(exc)) from exc
    except Exception as exc:
        raise PersonalDiagnosisError(
            "无法解析内置诊断的数据源，请检查 Agent 接入配置"
        ) from exc
    if not default or not default["spec"].get("username"):
        raise PersonalDiagnosisError(
            "内置诊断缺少个人数据源或采集身份，请完成 Agent 接入"
        )

    # Listing an empty scope provisions the standard rules in AgentLens. Reuse
    # existing definitions so user-tuned thresholds on legacy Jobs stay intact.
    diagnosers = _request("GET", "diagnosers", params=scope)["items"]
    rules = {
        row["spec"].get("executor"): row["uuid"]
        for row in diagnosers
        if row.get("type") == "builtin"
        and all(row.get(key) == value for key, value in scope.items())
    }
    if any(key not in rules for key in BUILTIN_EXECUTORS):
        raise PersonalDiagnosisError("内置诊断规则不完整，请检查 AgentLens 服务版本")

    datasets = _request("GET", "datasets", params=scope)["items"]
    dataset = _managed_resource(datasets, scope)
    if dataset is None:
        spec = default["spec"]
        spec["managed_by"] = MANAGED_BY
        spec["query"].update(time_range="1h", time_offset="3h", limit=20000)
        dataset = _request(
            "POST",
            "dataset",
            json={
                **scope,
                **default,
                "name": JOB_NAME,
                "creator": "AI Vision",
                "modifier": "AI Vision",
            },
        )
    dataset = _upgrade_hourly_dataset(dataset, scope)
    return _request(
        "POST",
        "job",
        json={
            **scope,
            "name": JOB_NAME,
            "description": JOB_NAME,
            "trigger_type": "schedule",
            "schedule_cron": "0 * * * *",
            "status": "enabled",
            "dataset_uuid": dataset["uuid"],
            "diagnoser_uuids": [rules[key] for key in BUILTIN_EXECUTORS],
            "spec": {"managed_by": MANAGED_BY, "save_badcase_only": True},
            "creator": "AI Vision",
            "modifier": "AI Vision",
        },
    )


def sync_personal_diagnosis_after_commit(agent):
    """Installation stays usable during an AgentLens outage; list/backfill retries."""
    if agent.space.type != 1:
        return
    agent_uuid = agent.uuid

    def sync():
        try:
            ensure_personal_diagnosis(agent_uuid)
        except PersonalDiagnosisError:
            logger.warning("Personal diagnosis sync failed for Agent %s", agent_uuid)

    transaction.on_commit(sync)
