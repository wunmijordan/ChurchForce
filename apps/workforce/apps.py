from django.apps import AppConfig


class WorkforceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workforce"

    def ready(self):
        """
        Connect workforce signals.

        NOTE: The scheduler is no longer started here. It is started
        once from core/apps.py CoreConfig.ready() which registers
        workforce jobs via workforce/scheduler_jobs.py.
        """
        import workforce.signals  # noqa: F401