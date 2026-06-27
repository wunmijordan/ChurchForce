from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"

    def ready(self):
        """
        Seed SubscriptionPlan records on startup so pricing always works.
        Uses post_migrate signal to run after migrations complete safely.
        """
        from django.db.models.signals import post_migrate

        def _seed_plans(sender, **kwargs):
            from billing.services import ensure_plans_seeded

            ensure_plans_seeded()

        post_migrate.connect(_seed_plans, sender=self)
