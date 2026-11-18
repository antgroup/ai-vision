from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"

    def ready(self):
        from aivision_backend.observability import init_observability

        init_observability()

        from .cache import connect_cache_signals

        connect_cache_signals()
