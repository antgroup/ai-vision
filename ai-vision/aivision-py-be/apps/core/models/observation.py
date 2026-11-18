from django.db import models

from .space import Space


class ObservationEndpointConfig(models.Model):
    STATUS_CHOICES = [
        (0, "Disabled"),
        (1, "Active"),
    ]
    BACKEND_TYPE_CHOICES = [
        ("langfuse", "Langfuse"),
    ]

    name = models.CharField(max_length=128)
    base_url = models.CharField(max_length=512)
    tenant_name = models.CharField(max_length=128)
    backend_type = models.CharField(
        max_length=32,
        default="langfuse",
        choices=BACKEND_TYPE_CHOICES,
    )
    is_default = models.SmallIntegerField(default=0)
    auth_mode = models.CharField(max_length=32, null=True, blank=True)
    admin_public_key = models.CharField(max_length=256, null=True, blank=True)
    admin_secret_ciphertext = models.TextField(null=True, blank=True)
    config = models.JSONField(null=True, blank=True)
    status = models.SmallIntegerField(default=1, choices=STATUS_CHOICES)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_observation_endpoint_config"
        ordering = ["-gmt_create"]

    def __str__(self):
        return f"{self.name} ({self.base_url})"


class SpaceObservationProject(models.Model):
    STATUS_CHOICES = [
        (0, "Disabled"),
        (1, "Active"),
    ]
    PROVISION_STATUS_CHOICES = [
        (0, "Pending"),
        (1, "Active"),
        (2, "Failed"),
        (3, "Disabled"),
    ]

    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name="observation_projects",
        db_constraint=False,
    )
    endpoint_config = models.ForeignKey(
        ObservationEndpointConfig,
        on_delete=models.CASCADE,
        related_name="projects",
        db_constraint=False,
    )
    tenant_name = models.CharField(max_length=128)
    upstream_project_id = models.CharField(max_length=128, null=True, blank=True)
    upstream_project_name = models.CharField(max_length=256, null=True, blank=True)
    upstream_project_slug = models.CharField(max_length=128, null=True, blank=True)
    status = models.SmallIntegerField(default=1, choices=STATUS_CHOICES)
    provision_status = models.SmallIntegerField(
        default=0, choices=PROVISION_STATUS_CHOICES
    )
    last_error = models.TextField(null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_space_observation_project"
        unique_together = ["space"]
        ordering = ["-gmt_create"]

    def __str__(self):
        return f"{self.space.name} -> {self.upstream_project_name or 'pending'}"


class SpaceDatasourceBinding(models.Model):
    DATASOURCE_TYPE_CHOICES = [
        ("metrics", "Metrics"),
        ("traces", "Traces"),
        ("logs", "Logs"),
    ]
    SOURCE_SYSTEM_CHOICES = [
        ("langfuse", "Langfuse"),
        ("signoz", "SigNoz"),
    ]
    STATUS_CHOICES = [
        (0, "Disabled"),
        (1, "Enabled"),
    ]

    space_observation_project = models.ForeignKey(
        SpaceObservationProject,
        on_delete=models.CASCADE,
        related_name="datasource_bindings",
        db_constraint=False,
    )
    datasource_type = models.CharField(max_length=32, choices=DATASOURCE_TYPE_CHOICES)
    source_system = models.CharField(max_length=32, choices=SOURCE_SYSTEM_CHOICES)
    config = models.JSONField(null=True, blank=True)
    enabled = models.SmallIntegerField(default=1, choices=STATUS_CHOICES)
    is_default = models.SmallIntegerField(default=0)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_space_datasource_binding"
        ordering = ["space_observation_project", "datasource_type"]
        unique_together = [
            ["space_observation_project", "datasource_type", "source_system"]
        ]
        verbose_name = "[废弃] Space Datasource Binding"
        verbose_name_plural = "[废弃] Space Datasource Bindings"

    def __str__(self):
        return (
            f"{self.space_observation_project.space.name} - "
            f"{self.datasource_type} ({self.source_system})"
        )


class SpaceObservationCredential(models.Model):
    SCOPE_CHOICES = [
        ("READ", "Read"),
        ("WRITE", "Write"),
    ]
    STATUS_CHOICES = [
        (0, "Disabled"),
        (1, "Active"),
    ]

    space_observation_project = models.ForeignKey(
        SpaceObservationProject,
        on_delete=models.CASCADE,
        related_name="credentials",
        db_constraint=False,
    )
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES)
    public_key = models.CharField(max_length=256, unique=True)
    secret_hash = models.CharField(max_length=128)
    secret_ciphertext = models.TextField()
    name = models.CharField(max_length=128, null=True, blank=True)
    status = models.SmallIntegerField(default=1, choices=STATUS_CHOICES)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.CharField(max_length=64, null=True, blank=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_space_observation_credential"
        unique_together = ["space_observation_project", "scope"]
        ordering = ["-gmt_create"]
        verbose_name = "Space Observation Credential"
        verbose_name_plural = "Space Observation Credentials"

    def __str__(self):
        return f"{self.scope} - {self.public_key[:16]}..."
