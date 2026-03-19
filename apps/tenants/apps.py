from django.apps import AppConfig


class TenantsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tenants"

    def ready(self):
        # Connect post_save signal that bootstraps a new church
        import tenants.signals  # noqa: F401