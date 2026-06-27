from django.apps import AppConfig


class LMSConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "lms"
    verbose_name = "Church LMS"

    def ready(self):
        import lms.signals  # noqa: F401
