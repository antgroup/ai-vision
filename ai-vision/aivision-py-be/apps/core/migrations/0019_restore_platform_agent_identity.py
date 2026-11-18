from django.db import migrations

PLACEHOLDER_AGENT_PRODUCT = "-"
MAX_AGENT_PRODUCT_LENGTH = 128


def _namespaced_product(space_name, agent_product):
    prefix = str(space_name or "").strip()
    return f"{prefix}_{agent_product}" if prefix else agent_product


def _ensure_target_available(AgentTypeConfig, agent_type, target_product):
    conflict_exists = AgentTypeConfig.objects.filter(
        platform_id=agent_type.platform_id,
        agent_product=target_product,
    ).exclude(pk=agent_type.pk).exists()
    if conflict_exists:
        raise RuntimeError(
            "Cannot restore Platform Agent identity because the namespaced "
            f"AgentType product already exists: {target_product}"
        )


def restore_platform_agent_identity(apps, schema_editor):
    Agent = apps.get_model("core", "Agent")
    AgentTypeConfig = apps.get_model("core", "AgentTypeConfig")
    PlatformOperationLog = apps.get_model("core", "PlatformOperationLog")

    bound_agents = Agent.objects.filter(
        space__platform__isnull=False,
        agent_type__platform__isnull=False,
    ).exclude(agent_product=PLACEHOLDER_AGENT_PRODUCT).select_related(
        "space",
        "agent_type",
    )

    for agent in bound_agents.iterator():
        agent_type = agent.agent_type
        if (
            agent_type.platform_id != agent.space.platform_id
            or agent.agent_product != agent_type.agent_product
        ):
            continue
        if Agent.objects.filter(agent_type_id=agent_type.pk).exclude(pk=agent.pk).exists():
            raise RuntimeError(
                "Cannot restore Platform Agent identity because one AgentType "
                f"is bound to multiple Agents: {agent_type.pk}"
            )

        target_product = _namespaced_product(
            agent.space.platform_space_name,
            agent_type.agent_product,
        )
        if len(target_product) > MAX_AGENT_PRODUCT_LENGTH:
            raise RuntimeError(
                "Cannot restore Platform Agent identity because the namespaced "
                f"product is too long: {target_product}"
            )
        _ensure_target_available(AgentTypeConfig, agent_type, target_product)
        AgentTypeConfig.objects.filter(pk=agent_type.pk).update(
            agent_product=target_product
        )
        Agent.objects.filter(pk=agent.pk).update(
            agent_product=PLACEHOLDER_AGENT_PRODUCT
        )

    unbound_types = AgentTypeConfig.objects.filter(
        platform__isnull=False,
        agents__isnull=True,
    )
    for agent_type in unbound_types.iterator():
        operation = PlatformOperationLog.objects.filter(
            platform_id=agent_type.platform_id,
            resource_type="agent_type",
            resource_key=agent_type.agent_product,
            space__isnull=False,
        ).select_related("space").order_by("-gmt_create", "-id").first()
        if operation is None:
            continue

        target_product = _namespaced_product(
            operation.space.platform_space_name,
            agent_type.agent_product,
        )
        if len(target_product) > MAX_AGENT_PRODUCT_LENGTH:
            raise RuntimeError(
                "Cannot restore unbound Platform AgentType identity because the "
                f"namespaced product is too long: {target_product}"
            )
        _ensure_target_available(AgentTypeConfig, agent_type, target_product)
        AgentTypeConfig.objects.filter(pk=agent_type.pk).update(
            agent_product=target_product
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_fix_platform_event_user_columns"),
    ]

    operations = [
        migrations.RunPython(
            restore_platform_agent_identity,
            migrations.RunPython.noop,
        ),
    ]
