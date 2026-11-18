from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Agent
from apps.core.services.personal_diagnosis import (
    PersonalDiagnosisError,
    ensure_personal_diagnosis,
)


class Command(BaseCommand):
    help = "为已有个人 Agent 补齐内置定时诊断，保留自建任务；移除的 Agent 停用内置任务"

    def handle(self, *args, **options):
        failed = []
        for uuid in Agent.objects.filter(
            space__type=1, gmt_deleted__isnull=True
        ).values_list("uuid", flat=True):
            try:
                job = ensure_personal_diagnosis(uuid)
                if job:
                    self.stdout.write(f"{uuid}: {job['uuid']}")
            except PersonalDiagnosisError as exc:
                failed.append(uuid)
                self.stderr.write(f"{uuid}: {exc}")
        if failed:
            raise CommandError(f"{len(failed)} 个 Agent 同步失败，可修复配置后重跑")
