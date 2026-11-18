import re
from django.core.management.base import BaseCommand
from django.core.management import call_command
from io import StringIO


class Command(BaseCommand):
    help = "生成 OceanBase 兼容的 SQL 迁移语句"

    TABLE_COMMENTS = {
        "aivision_user": "用户表",
        "aivision_space": "空间表",
        "aivision_agent_type_config": "Agent类型配置表",
        "aivision_platform_config": "AI Vision 平台配置表",
        "aivision_platform": "平台表",
        "aivision_platform_credential": "平台凭证表",
        "aivision_platform_operation_log": "平台操作日志表",
        "aivision_api_key": "API密钥表",
        "aivision_agent_dashboard_panel": "Agent Dashboard面板配置表",
        "aivision_agent": "Agent实例表",
        "aivision_session": "会话记录表",
        "aivision_datasource": "数据源配置表",
        "aivision_space_member": "空间成员表",
        "aivision_user_remote_mapping": "用户远程映射表",
        "aivision_business_onboarding_record": "AI Vision 业务接入申请记录表",
    }

    def add_arguments(self, parser):
        parser.add_argument("app_label", type=str, help="App label")
        parser.add_argument("migration_name", type=str, help="Migration name")

    def handle(self, *args, **options):
        output = StringIO()
        call_command(
            "sqlmigrate", options["app_label"], options["migration_name"], stdout=output
        )
        oceanbase_sql = self.convert_to_oceanbase(output.getvalue())
        self.stdout.write(oceanbase_sql)

    def convert_to_oceanbase(self, sql):
        lines = sql.split("\n")
        result = []
        current_table = None

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                result.append(line)
                continue

            if "CREATE TABLE" in line:
                match = re.search(r"CREATE TABLE `(\w+)`", line)
                if match:
                    current_table = match.group(1)
                line = self._convert_create_table(line)
            elif "CREATE INDEX" in line:
                line = self._convert_create_index(line)
            elif "ALTER TABLE" in line and "ADD CONSTRAINT" in line:
                line = self._convert_alter_constraint(line)

            result.append(line)

        return "\n".join(self._add_table_options(result))

    def _convert_create_table(self, line):
        transformations = [
            (r"bigint AUTO_INCREMENT NOT NULL", "bigint(20) NOT NULL AUTO_INCREMENT"),
            (r"\binteger\b", "int(11)"),
            (r"\bsmallint\b", "smallint(6)"),
            (r"\bbigint NOT NULL\b", "bigint(20) NOT NULL"),
            (r"\bbigint NULL\b", "bigint(20) NULL"),
            (r"\bbigint\b(?!\()", "bigint(20)"),
            (r"(varchar\(\d+\))", r"\1 COLLATE utf8mb4_unicode_ci"),
            (r"(longtext)", r"\1 COLLATE utf8mb4_unicode_ci"),
            (
                r"`gmt_modified` datetime\(6\) NOT NULL",
                "`gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
            ),
            (
                r"`gmt_create` datetime\(6\) NOT NULL",
                "`gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP",
            ),
            (
                r"`joined_at` datetime\(6\) NOT NULL",
                "`joined_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP",
            ),
            (
                r"`expires_at` datetime\(6\) NULL",
                "`expires_at` timestamp NULL DEFAULT NULL",
            ),
            (
                r"`last_used_at` datetime\(6\) NULL",
                "`last_used_at` timestamp NULL DEFAULT NULL",
            ),
            (
                r"`last_active_at` datetime\(6\) NULL",
                "`last_active_at` timestamp NULL DEFAULT NULL",
            ),
            (
                r"`gmt_deleted` datetime\(6\) NULL",
                "`gmt_deleted` timestamp NULL DEFAULT NULL",
            ),
            (r"json NULL COLLATE utf8mb4_unicode_ci", "json NULL"),
            (r"json COLLATE utf8mb4_unicode_ci", "json"),
        ]

        for pattern, replacement in transformations:
            line = re.sub(pattern, replacement, line)
        return line

    def _convert_create_index(self, line):
        if "LOCAL" not in line:
            line = line.rstrip(";") + " LOCAL;"
        return line

    def _convert_alter_constraint(self, line):
        match = re.search(
            r"ALTER TABLE `(\w+)` ADD CONSTRAINT `(\w+)` UNIQUE \(([^)]+)\)", line
        )
        if match:
            table, constraint, columns = match.groups()
            return f"ALTER TABLE `{table}` ADD UNIQUE KEY `{constraint}` ({columns}) LOCAL;"
        return line

    def _add_table_options(self, lines):
        result = []

        for line in lines:
            if "CREATE TABLE" in line:
                match = re.search(r"CREATE TABLE `(\w+)`", line)
                if match:
                    table_name = match.group(1)
                    comment = self.TABLE_COMMENTS.get(table_name, "")
                    line = line.rstrip(";").rstrip(")")
                    line += f") DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='{comment}';"
            result.append(line)

        return result
