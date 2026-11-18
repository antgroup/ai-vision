from django.core.management.base import BaseCommand

from apps.core.models import (
    Agent,
    AgentTypeConfig,
    ObservationSubjectBinding,
    Space,
    SpaceMember,
    User,
)


class Command(BaseCommand):
    help = "Seed database with mockdata"

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            default="developer@example.com",
            help="Local account email used for seed data",
        )
        parser.add_argument(
            "--name",
            default="示例用户",
            help="User name used for seed data",
        )

    def handle(self, *args, **options):
        self.stdout.write("Seeding database...")

        email = User.normalize_email(options["email"])
        name = options["name"]

        user, created = User.objects.update_or_create(
            normalized_email=email,
            defaults={
                "name": name,
                "email": email,
                "avatar_url": "https://example.com/avatar.jpg",
                "status": 1,
            },
        )
        self.stdout.write(f"User: {user.name} ({'created' if created else 'exists'})")

        space = Space.objects.filter(creator=user, type=1).first()
        if space:
            created = False
            updates = []
            expected_space_name = f"{user.name}的空间"
            if space.name != expected_space_name:
                space.name = expected_space_name
                updates.append("name")
            if space.status != 1:
                space.status = 1
                updates.append("status")
            if updates:
                space.save(update_fields=updates + ["gmt_modified"])
        else:
            created = True
            space = Space.objects.create(
                name=f"{user.name}的空间",
                type=1,
                creator=user,
                status=1,
            )
        self.stdout.write(f"Space: {space.name} ({'created' if created else 'exists'})")

        member, created = SpaceMember.objects.get_or_create(
            space=space, user=user, defaults={"role": 1}
        )
        self.stdout.write(f"Member: {member} ({'created' if created else 'exists'})")

        agent_types = [
            # Initial agent catalog for local deployments.
            # Last element is runtime_type: the frontend resolves dashboard and
            # trace components from it (vfe src/agents/registry/registry.ts,
            # RUNTIME_REGISTRY), falling back to the raw product string which
            # never matches a registry key ("Codex" != "codex"). The admin
            # console derives it via _default_runtime_type on manual creation;
            # a seeded row that leaves it empty renders "当前未配置 X 的数据看板"
            # for every scope. Explicit mapping also covers the legacy
            # Claude Code product key used by existing observations.
            (
                "ANT_CLAUDE_CODE",
                "Ant Claude Code",
                "BUILT_IN",
                "内置 Agent，按用户标识关联采集数据",
                "🤖",
                1,
                "claudecode",
            ),
            (
                "CLAUDE_CODE",
                "Claude Code",
                "BUILT_IN",
                "支持本地/云端多实例接入",
                "💻",
                3,
                "claudecode",
            ),
            (
                "CODEX",
                "Codex",
                "CUSTOM",
                "支持本地 Codex CLI 一键接入观测",
                "/assets/agents/codex.svg",
                5,
                "codex",
            ),
            (
                "CUSTOM",
                "自定义接入",
                "CUSTOM",
                "面向技术用户手动埋点接入，支持实例命名",
                "⚙️",
                99,
                "other",
            ),
        ]
        # Backfill the dashboards of older installs without creating retired
        # catalog rows or overwriting configured values.
        for agent_name, runtime_type in (
            ("CODEFUSE", "codefuse"),
            ("HOMICLAW", "openclaw"),
        ):
            for config in AgentTypeConfig.objects.shared_runtime().filter(
                agent_name=agent_name, runtime_type=""
            ):
                config.runtime_type = runtime_type
                config.save(update_fields=["runtime_type", "gmt_modified"])

        # Per-agent field overrides applied on top of the get_or_create
        # defaults. They also converge rows that already exist, so a re-seed
        # flips them to the intended values (Codex uses the CLI connect
        # command rather than the AUTO auto-discovery card).
        agent_overrides = {
            "CODEX": {
                "category": "CUSTOM",
                "setup_mode": "CLI",
                "cli_command": (
                    "aiv connect codex "
                    "--base-url http://<AI-Vision地址>:8001 "
                    "--agent-uuid {agent_uuid}"
                ),
            },
        }

        for (
            agent_type,
            name,
            category,
            desc,
            icon,
            sort_order,
            runtime_type,
        ) in agent_types:
            atc, created = AgentTypeConfig.objects.get_or_create(
                agent_name=agent_type,
                defaults={
                    "agent_product": name,
                    "display_name": "Claude Code" if agent_type == "ANT_CLAUDE_CODE" else None,
                    "category": category,
                    "description": desc,
                    "icon": icon,
                    "cli_command": f'ai-vision connect --template "{name}" --instance "YOUR_INSTANCE_NAME" --key YOUR_API_KEY',
                    "sort_order": sort_order,
                    "runtime_type": runtime_type,
                    "status": 1,
                },
            )
            # runtime_type converges too: rows created before it was seeded
            # would otherwise keep the empty value and stay undashboardable.
            # icon converges for the same reason (the Codex card icon moved
            # from an emoji to the bundled /assets/agents/codex.svg asset,
            # matching the path agents.json uses for the other market cards).
            converged = {"runtime_type": runtime_type, "icon": icon}
            converged.update(agent_overrides.get(agent_type, {}))
            for field, value in converged.items():
                if getattr(atc, field) != value:
                    setattr(atc, field, value)
                    atc.save()
            self.stdout.write(
                f"AgentType: {atc.agent_product} ({'created' if created else 'exists'})"
            )

        # Agent requires a non-null observation_subject_binding (see migration
        # 0021 AlterField). Seed the same default collector binding a personal
        # space gets at provisioning time (personal_space_service / space view)
        # so the demo Agent below can attach to it.
        binding, _ = ObservationSubjectBinding.objects.update_or_create(
            space=space,
            source_system="collector",
            subject_id=user.uuid,
            defaults={
                "user": user,
                "binding_source": "account_default",
                "status": 1,
                "created_by": user,
                "gmt_deleted": None,
            },
        )

        agent, created = Agent.objects.get_or_create(
            space=space,
            agent_product="ANT_CLAUDE_CODE",
            defaults={
                "name": "Claude Code Agent",
                "custom_name": "我的Claude助手",
                "observation_subject_binding": binding,
                "status": 1,
            },
        )
        self.stdout.write(f"Agent: {agent.name} ({'created' if created else 'exists'})")

        self.stdout.write(self.style.SUCCESS("Database seeded successfully!"))
