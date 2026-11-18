from django.db import models

from .space import Space
from .user import User


class ObservationSubjectBinding(models.Model):
    STATUS_DISABLED = 0
    STATUS_ACTIVE = 1
    STATUS_CHOICES = [(STATUS_DISABLED, "Disabled"), (STATUS_ACTIVE, "Active")]

    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name="observation_subject_bindings",
        db_constraint=False,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="observation_subject_bindings",
        db_constraint=False,
    )
    source_system = models.CharField(max_length=64)
    subject_id = models.CharField(max_length=256)
    binding_source = models.CharField(max_length=32)
    status = models.SmallIntegerField(default=STATUS_ACTIVE, choices=STATUS_CHOICES)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="created_observation_subject_bindings",
        db_constraint=False,
        null=True,
        blank=True,
    )
    gmt_create = models.DateTimeField(auto_now_add=True)
    gmt_modified = models.DateTimeField(auto_now=True)
    gmt_deleted = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "aivision_observation_subject_binding"
        constraints = [
            models.UniqueConstraint(
                fields=["space", "source_system", "subject_id"],
                name="uk_space_source_subject",
            )
        ]
        indexes = [
            models.Index(
                fields=["space", "user", "source_system", "status", "gmt_deleted"],
                name="idx_subject_space_user_state",
            )
        ]
