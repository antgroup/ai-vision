from django.contrib import admin
from django.db import models
from django_json_widget.widgets import JSONEditorWidget

from ..models import PlatformConfig, Session


@admin.register(PlatformConfig)
class PlatformConfigAdmin(admin.ModelAdmin):
    formfield_overrides = {
        models.JSONField: {"widget": JSONEditorWidget},
    }
    list_display = ["id", "platform_code", "platform_name", "status"]
    list_filter = ["status"]
    search_fields = ["platform_code", "platform_name"]


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "agent",
        "trace_id",
        "status",
        "tokens_input",
        "tokens_output",
        "gmt_create",
    ]
    list_filter = ["status", "gmt_create"]
    search_fields = ["trace_id"]
    autocomplete_fields = ["agent"]
