# Final local-auth schema for empty-database initialization.

import apps.core.models.user
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_add_signoz_datasource"),
    ]

    operations = [
        migrations.AddField(
            model_name="agent",
            name="uuid",
            field=models.CharField(
                blank=True, editable=False, max_length=36, null=True
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="last_login_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="last_login_ip",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="normalized_email",
            field=models.CharField(blank=True, max_length=256, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="user",
            name="password_changed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="uuid",
            field=models.CharField(
                blank=True, editable=False, max_length=36, null=True
            ),
        ),
        migrations.AlterField(
            model_name="space",
            name="creator",
            field=models.ForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="created_spaces",
                to="core.user",
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="work_id",
            field=models.CharField(blank=True, max_length=32, null=True, unique=True),
        ),
        migrations.CreateModel(
            name="AuthSession",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "session_uuid",
                    models.CharField(
                        default=apps.core.models.user.generate_uuid,
                        editable=False,
                        max_length=36,
                        unique=True,
                    ),
                ),
                (
                    "status",
                    models.SmallIntegerField(
                        choices=[(0, "Revoked"), (1, "Active")], default=1
                    ),
                ),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                (
                    "last_used_ip",
                    models.CharField(blank=True, max_length=64, null=True),
                ),
                ("created_ip", models.CharField(blank=True, max_length=64, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=512, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "revoke_reason",
                    models.CharField(blank=True, max_length=128, null=True),
                ),
                ("gmt_create", models.DateTimeField(auto_now_add=True)),
                ("gmt_modified", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="auth_sessions",
                        to="core.user",
                    ),
                ),
            ],
            options={
                "db_table": "aivision_auth_session",
            },
        ),
        migrations.CreateModel(
            name="AuthRefreshToken",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("generation", models.PositiveIntegerField()),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("gmt_create", models.DateTimeField(auto_now_add=True)),
                (
                    "session",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="refresh_tokens",
                        to="core.authsession",
                    ),
                ),
            ],
            options={
                "db_table": "aivision_auth_refresh_token",
            },
        ),
        migrations.CreateModel(
            name="ObservationSubjectBinding",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("source_system", models.CharField(max_length=64)),
                ("subject_id", models.CharField(max_length=256)),
                ("binding_source", models.CharField(max_length=32)),
                (
                    "status",
                    models.SmallIntegerField(
                        choices=[(0, "Disabled"), (1, "Active")], default=1
                    ),
                ),
                ("gmt_create", models.DateTimeField(auto_now_add=True)),
                ("gmt_modified", models.DateTimeField(auto_now=True)),
                ("gmt_deleted", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_observation_subject_bindings",
                        to="core.user",
                    ),
                ),
                (
                    "space",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="observation_subject_bindings",
                        to="core.space",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="observation_subject_bindings",
                        to="core.user",
                    ),
                ),
            ],
            options={
                "db_table": "aivision_observation_subject_binding",
            },
        ),
        migrations.AddField(
            model_name="agent",
            name="observation_subject_binding",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="agents",
                to="core.observationsubjectbinding",
            ),
        ),
        migrations.CreateModel(
            name="SpaceInvitation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "invitation_uuid",
                    models.CharField(
                        default=apps.core.models.user.generate_uuid,
                        editable=False,
                        max_length=36,
                        unique=True,
                    ),
                ),
                ("normalized_email", models.CharField(max_length=256)),
                (
                    "role",
                    models.SmallIntegerField(
                        choices=[(1, "Admin"), (2, "Developer"), (3, "Member")]
                    ),
                ),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("Pending", "Pending"),
                            ("Accepted", "Accepted"),
                            ("Revoked", "Revoked"),
                            ("Expired", "Expired"),
                        ],
                        default="Pending",
                        max_length=16,
                    ),
                ),
                (
                    "pending_marker",
                    models.SmallIntegerField(blank=True, default=1, null=True),
                ),
                ("expires_at", models.DateTimeField()),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("gmt_create", models.DateTimeField(auto_now_add=True)),
                ("gmt_modified", models.DateTimeField(auto_now=True)),
                (
                    "accepted_by",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="accepted_space_invitations",
                        to="core.user",
                    ),
                ),
                (
                    "invited_by",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sent_space_invitations",
                        to="core.user",
                    ),
                ),
                (
                    "revoked_by",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="revoked_space_invitations",
                        to="core.user",
                    ),
                ),
                (
                    "space",
                    models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="invitations",
                        to="core.space",
                    ),
                ),
            ],
            options={
                "db_table": "aivision_space_invitation",
            },
        ),
        migrations.CreateModel(
            name="AuthAuditLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("event", models.CharField(max_length=64)),
                ("success", models.BooleanField()),
                (
                    "session_uuid",
                    models.CharField(blank=True, max_length=36, null=True),
                ),
                ("request_id", models.CharField(blank=True, max_length=128, null=True)),
                ("source_ip", models.CharField(blank=True, max_length=64, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=512, null=True)),
                ("error_key", models.CharField(blank=True, max_length=64, null=True)),
                ("gmt_create", models.DateTimeField(auto_now_add=True)),
                (
                    "actor_user",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="auth_audit_logs",
                        to="core.user",
                    ),
                ),
                (
                    "space",
                    models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="auth_audit_logs",
                        to="core.space",
                    ),
                ),
            ],
            options={
                "db_table": "aivision_auth_audit_log",
                "indexes": [
                    models.Index(
                        fields=["event", "gmt_create"], name="idx_auth_audit_event_time"
                    ),
                    models.Index(
                        fields=["actor_user", "gmt_create"],
                        name="idx_auth_audit_user_time",
                    ),
                ],
            },
        ),
        migrations.AlterField(
            model_name="agent",
            name="uuid",
            field=models.CharField(
                default=apps.core.models.user.generate_uuid,
                editable=False,
                max_length=36,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="uuid",
            field=models.CharField(
                default=apps.core.models.user.generate_uuid,
                editable=False,
                max_length=36,
                unique=True,
            ),
        ),
        migrations.AddIndex(
            model_name="authsession",
            index=models.Index(
                fields=["user", "status", "expires_at"],
                name="idx_auth_session_user_state",
            ),
        ),
        migrations.AddIndex(
            model_name="authsession",
            index=models.Index(
                fields=["status", "expires_at"], name="idx_auth_session_state_exp"
            ),
        ),
        migrations.AddConstraint(
            model_name="authrefreshtoken",
            constraint=models.UniqueConstraint(
                fields=("session", "generation"), name="uk_auth_refresh_generation"
            ),
        ),
        migrations.AddIndex(
            model_name="observationsubjectbinding",
            index=models.Index(
                fields=["space", "user", "source_system", "status", "gmt_deleted"],
                name="idx_subject_space_user_state",
            ),
        ),
        migrations.AddConstraint(
            model_name="observationsubjectbinding",
            constraint=models.UniqueConstraint(
                fields=("space", "source_system", "subject_id"),
                name="uk_space_source_subject",
            ),
        ),
        migrations.AddIndex(
            model_name="spaceinvitation",
            index=models.Index(
                fields=["space", "status", "expires_at"],
                name="idx_invitation_space_state",
            ),
        ),
        migrations.AddIndex(
            model_name="spacemember",
            index=models.Index(
                fields=["user", "gmt_deleted"], name="idx_member_user_deleted"
            ),
        ),
        migrations.AddIndex(
            model_name="spacemember",
            index=models.Index(
                fields=["space", "gmt_deleted", "role"],
                name="idx_member_space_state_role",
            ),
        ),
        migrations.AddConstraint(
            model_name="spaceinvitation",
            constraint=models.UniqueConstraint(
                fields=("space", "normalized_email", "pending_marker"),
                name="uk_space_pending_invitation",
            ),
        ),
        migrations.DeleteModel(name="UserRemoteMapping"),
        migrations.AlterModelOptions(
            name="platformconfig",
            options={
                "ordering": ["platform_name"],
                "verbose_name": "Platform config (aivision配置)",
                "verbose_name_plural": "Platform configs (aivision配置)",
            },
        ),
        migrations.AlterField(
            model_name="agenttypeconfig",
            name="agent_product",
            field=models.CharField(
                db_comment=(
                    "仅作为 agent 唯一 id（platform/space namespace 化）；"
                    "查询过滤用 DataSource.config['agent_product_filter']"
                ),
                max_length=128,
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.CharField(max_length=256),
        ),
        migrations.AlterField(
            model_name="user",
            name="normalized_email",
            field=models.CharField(max_length=256, unique=True),
        ),
        migrations.AlterField(
            model_name="user",
            name="password",
            field=models.CharField(max_length=256),
        ),
        migrations.AlterField(
            model_name="user",
            name="status",
            field=models.SmallIntegerField(
                choices=[(0, "Disabled"), (1, "Normal"), (2, "Pending")],
                default=1,
            ),
        ),
        migrations.RemoveField(
            model_name="user",
            name="work_id",
        ),
        migrations.AlterField(
            model_name="agent",
            name="observation_subject_binding",
            field=models.ForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="agents",
                to="core.observationsubjectbinding",
            ),
        ),
    ]
