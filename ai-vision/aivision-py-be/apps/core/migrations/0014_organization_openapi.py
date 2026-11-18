import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_add_space_key_and_access_log"),
    ]

    operations = [
        migrations.CreateModel(
            name="Organization",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        db_comment="主键ID",
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        db_comment="组织名称",
                        max_length=32,
                        unique=True,
                        validators=[
                            django.core.validators.RegexValidator(
                                message=(
                                    "Organization name must be a lowercase slug "
                                    "between 2 and 32 characters"
                                ),
                                regex="^[a-z][a-z0-9-]{1,31}$",
                            )
                        ],
                    ),
                ),
                (
                    "display_name",
                    models.CharField(db_comment="组织展示名称", max_length=128),
                ),
                (
                    "description",
                    models.CharField(
                        blank=True,
                        db_comment="组织描述",
                        max_length=512,
                        null=True,
                    ),
                ),
                (
                    "platform_meta",
                    models.CharField(
                        blank=True,
                        db_comment="平台扩展元数据，仅存储非敏感业务配置",
                        max_length=1024,
                        null=True,
                    ),
                ),
                (
                    "status",
                    models.SmallIntegerField(
                        choices=[(0, "Disabled"), (1, "Enabled")],
                        db_comment="状态: 1-启用, 0-禁用",
                        default=1,
                    ),
                ),
                (
                    "gmt_create",
                    models.DateTimeField(auto_now_add=True, db_comment="创建时间"),
                ),
                (
                    "gmt_modified",
                    models.DateTimeField(auto_now=True, db_comment="最后修改时间"),
                ),
                (
                    "gmt_deleted",
                    models.DateTimeField(
                        blank=True,
                        db_comment="删除时间",
                        null=True,
                    ),
                ),
            ],
            options={
                "db_table": "aivision_organization",
                "db_table_comment": "组织表",
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="OrganizationCredential",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        db_comment="主键ID",
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(db_comment="凭证名称", max_length=128),
                ),
                (
                    "key_prefix",
                    models.CharField(
                        db_comment="凭证前缀",
                        db_index=True,
                        max_length=20,
                        unique=True,
                    ),
                ),
                (
                    "key_hash",
                    models.CharField(
                        db_comment="凭证哈希值",
                        db_index=True,
                        max_length=64,
                        unique=True,
                    ),
                ),
                (
                    "enabled",
                    models.BooleanField(
                        db_comment="是否启用: 1-启用, 0-禁用",
                        default=True,
                    ),
                ),
                (
                    "expires_at",
                    models.DateTimeField(
                        blank=True,
                        db_comment="过期时间",
                        null=True,
                    ),
                ),
                (
                    "last_used_at",
                    models.DateTimeField(
                        blank=True,
                        db_comment="最后使用时间",
                        null=True,
                    ),
                ),
                (
                    "gmt_create",
                    models.DateTimeField(auto_now_add=True, db_comment="创建时间"),
                ),
                (
                    "gmt_modified",
                    models.DateTimeField(auto_now=True, db_comment="最后修改时间"),
                ),
                (
                    "gmt_deleted",
                    models.DateTimeField(
                        blank=True,
                        db_comment="删除时间",
                        null=True,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        db_comment="所属组织ID",
                        db_constraint=False,
                        db_index=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="credentials",
                        to="core.organization",
                    ),
                ),
            ],
            options={
                "db_table": "aivision_organization_credential",
                "db_table_comment": "组织凭证表",
                "ordering": ["-gmt_create"],
                "indexes": [
                    models.Index(
                        fields=["organization"],
                        name="org_credential_org_idx",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="space",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                db_comment="所属组织ID",
                db_constraint=False,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="spaces",
                to="core.organization",
            ),
        ),
        migrations.AddField(
            model_name="space",
            name="org_space_key",
            field=models.CharField(
                blank=True,
                db_comment="组织内空间唯一键",
                max_length=64,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="agenttypeconfig",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                db_comment="所属组织ID",
                db_constraint=False,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="agent_type_configs",
                to="core.organization",
            ),
        ),
        migrations.AddIndex(
            model_name="agenttypeconfig",
            index=models.Index(
                fields=["organization"],
                name="agent_type_config_org_idx",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="agent",
                    name="api_key",
                ),
                migrations.AddField(
                    model_name="agent",
                    name="api_key_id",
                    field=models.BigIntegerField(
                        blank=True,
                        db_column="api_key_id",
                        db_comment="已废弃的API Key ID",
                        null=True,
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="spacekey",
            name="key_alias",
            field=models.CharField(
                blank=True,
                db_comment="空间内SpaceKey稳定唯一标识",
                max_length=64,
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="space",
            constraint=models.UniqueConstraint(
                fields=("organization", "org_space_key"),
                name="uniq_org_space_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="spacekey",
            constraint=models.UniqueConstraint(
                fields=("space", "key_alias"),
                name="uk_space_key_alias",
            ),
        ),
        migrations.CreateModel(
            name="OrgOperationLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        db_comment="主键ID",
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "operator",
                    models.CharField(
                        db_comment="操作方组织名称",
                        max_length=32,
                    ),
                ),
                (
                    "resource_type",
                    models.CharField(db_comment="资源类型", max_length=32),
                ),
                (
                    "resource_key",
                    models.CharField(db_comment="资源唯一键", max_length=128),
                ),
                (
                    "action",
                    models.CharField(db_comment="操作类型", max_length=32),
                ),
                (
                    "detail",
                    models.CharField(
                        blank=True,
                        db_comment="操作详情",
                        default="",
                        max_length=512,
                    ),
                ),
                (
                    "extra",
                    models.CharField(
                        blank=True,
                        db_comment="扩展信息文本",
                        max_length=1024,
                        null=True,
                    ),
                ),
                (
                    "gmt_create",
                    models.DateTimeField(auto_now_add=True, db_comment="创建时间"),
                ),
                (
                    "gmt_modified",
                    models.DateTimeField(auto_now=True, db_comment="最后修改时间"),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        db_comment="所属组织ID",
                        db_constraint=False,
                        db_index=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="operation_logs",
                        to="core.organization",
                    ),
                ),
                (
                    "space",
                    models.ForeignKey(
                        blank=True,
                        db_comment="关联空间ID",
                        db_constraint=False,
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="org_operation_logs",
                        to="core.space",
                    ),
                ),
            ],
            options={
                "db_table": "aivision_org_operation_log",
                "db_table_comment": "组织操作日志表",
                "ordering": ["-gmt_create"],
                "indexes": [
                    models.Index(
                        fields=["organization", "-gmt_create"],
                        name="org_op_org_created_idx",
                    ),
                    models.Index(
                        fields=["space", "-gmt_create"],
                        name="org_op_space_created_idx",
                    ),
                ],
            },
        ),
    ]
