from django.contrib import admin

from .mixins import SoftDeleteAdminMixin
from ..models import ObservationEndpointConfig, User


def get_active_observation_endpoints():
    return ObservationEndpointConfig.objects.filter(
        status=1,
        gmt_deleted__isnull=True,
    ).order_by("-is_default", "name", "id")


@admin.register(User)
class UserAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = [
        "uuid",
        "name",
        "email",
        "status",
        "gmt_create",
        "is_deleted",
    ]
    list_filter = ["status", "gmt_create"]
    search_fields = ["uuid", "name", "email"]
    readonly_fields = ["uuid", "gmt_create", "gmt_modified"]
