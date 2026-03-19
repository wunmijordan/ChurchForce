"""
Management command: grant_founders_plan

Grants a founders lifetime plan to a church tenant.
This is an internal operation — never exposed publicly.

Usage:
    python manage.py grant_founders_plan <church_slug> <tier>

    tier: saas | white_label

Examples:
    python manage.py grant_founders_plan grace-chapel saas
    python manage.py grant_founders_plan city-church white_label

For white_label tier, you still need to:
    1. Set the church's custom_domain field manually (via shell or admin)
    2. Run: python manage.py provision_white_label <slug>
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Grant a founders lifetime plan to a church tenant (internal use only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "church_slug",
            type=str,
            help="Slug of the church to grant the founders plan to.",
        )
        parser.add_argument(
            "tier",
            type=str,
            choices=["saas", "white_label"],
            help="Founders plan tier: 'saas' or 'white_label'.",
        )

    def handle(self, *args, **options):
        slug = options["church_slug"]
        tier = options["tier"]

        from tenants.models import Church
        from billing.services import grant_founders_plan

        try:
            church = Church.raw_objects.get(slug=slug)
        except Church.DoesNotExist:
            raise CommandError(f"No church found with slug '{slug}'.")

        if not church.is_active:
            raise CommandError(f"Church '{slug}' is inactive.")

        try:
            grant_founders_plan(church, tier)
        except ValueError as exc:
            raise CommandError(str(exc))

        plan_name = f"founders_{tier}_lifetime"

        self.stdout.write(
            self.style.SUCCESS(
                f"\nFounders plan granted successfully.\n"
                f"  Church : {church.name} ({slug})\n"
                f"  Plan   : {plan_name}\n"
                f"  Expiry : Never\n"
            )
        )

        if tier == "white_label":
            self.stdout.write(
                self.style.WARNING(
                    "  Next steps for White Label:\n"
                    "  1. Set church.custom_domain via Django admin or shell.\n"
                    "  2. Add WL_DB_URL_{} to your .env\n"
                    "  3. Run: python manage.py provision_white_label {}\n".format(
                        slug.upper().replace("-", "_"), slug
                    )
                )
            )