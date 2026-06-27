"""
Management command: dev_grant_plan

DEV / STAGING ONLY — quickly assign any plan to a tenant without going
through Paystack.  Also seeds the SubscriptionPlan records if they are
missing (common on a fresh dev database).

Usage:
    python manage.py dev_grant_plan <church_slug> <plan>

    plan choices:
        trial           — path-slug routing (workforce.church/slug/...)
        saas            — subdomain routing (slug.workforce.church)
        white_label     — custom domain routing
        founders_saas   — lifetime SaaS (no expiry)
        founders_wl     — lifetime white-label (no expiry)

Examples:
    python manage.py dev_grant_plan grace-chapel saas
    python manage.py dev_grant_plan city-church founders_saas

Notes:
    • This command is intentionally harmless in production because it
      guards against DEBUG=False unless --force is passed.
    • For white_label / founders_wl you still need to set
      church.custom_domain manually if you want domain routing.
"""

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone
from datetime import timedelta


PLAN_DEFAULTS = {
    "trial": {
        "monthly_price_ngn": 0,
        "annual_discount_pct": 10,
        "biannual_discount_pct": 20,
        "campus_limit": -1,
        "multi_campus": True,
        "white_label": False,
        "description": "Path-slug routing — workforce.church/your-church/...",
    },
    "saas": {
        "monthly_price_ngn": 0,
        "annual_discount_pct": 10,
        "biannual_discount_pct": 20,
        "campus_limit": -1,
        "campus_price_ngn": 0,
        "multi_campus": True,
        "white_label": False,
        "description": "Subdomain routing — your-church.workforce.church",
    },
    "white_label": {
        "monthly_price_ngn": 0,
        "annual_discount_pct": 10,
        "biannual_discount_pct": 20,
        "campus_limit": -1,
        "campus_price_ngn": 0,
        "multi_campus": True,
        "white_label": True,
        "description": "Custom domain routing — app.yourchurch.org",
    },
    "founders_saas_lifetime": {
        "monthly_price_ngn": 0,
        "annual_discount_pct": 10,
        "biannual_discount_pct": 20,
        "campus_limit": -1,
        "multi_campus": True,
        "white_label": False,
        "description": "Founders lifetime SaaS.",
    },
    "founders_white_label_lifetime": {
        "monthly_price_ngn": 0,
        "annual_discount_pct": 10,
        "biannual_discount_pct": 20,
        "campus_limit": -1,
        "multi_campus": True,
        "white_label": True,
        "description": "Founders lifetime white-label.",
    },
}

# Human-friendly aliases → internal plan name
ALIASES = {
    "trial": "trial",
    "saas": "saas",
    "white_label": "white_label",
    "wl": "white_label",
    "founders_saas": "founders_saas_lifetime",
    "founders_wl": "founders_white_label_lifetime",
    "lifetime": "founders_saas_lifetime",
    "lifetime_saas": "founders_saas_lifetime",
    "lifetime_wl": "founders_white_label_lifetime",
}


class Command(BaseCommand):
    help = "DEV: assign any plan to a church tenant without Paystack."

    def add_arguments(self, parser):
        parser.add_argument("church_slug", type=str, help="Church slug")
        parser.add_argument(
            "plan",
            type=str,
            choices=list(ALIASES.keys()),
            help="Plan alias to assign",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Validity in days for non-lifetime plans (default: 30)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow running in production (dangerous — use with care)",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "This command is for dev/staging only.\n"
                "Pass --force to run it in production (not recommended)."
            )

        slug = options["church_slug"]
        alias = options["plan"]
        days = options["days"]
        plan_name = ALIASES[alias]

        from tenants.models import Church
        from billing.models import SubscriptionPlan, ChurchSubscription

        # ── Resolve church ────────────────────────────────────────────
        church = Church.raw_objects.filter(slug=slug).first()
        if not church:
            # Try by name fragment (handy in dev)
            church = Church.raw_objects.filter(name__icontains=slug).first()
        if not church:
            raise CommandError(
                f"No church found with slug or name containing '{slug}'.\n"
                f"Available: {', '.join(Church.raw_objects.values_list('slug', flat=True))}"
            )

        # ── Seed ALL plan records if any are missing ──────────────────
        seeded = []
        for name, defaults in PLAN_DEFAULTS.items():
            obj, created = SubscriptionPlan.objects.get_or_create(
                name=name, defaults=defaults
            )
            if created:
                seeded.append(name)
        if seeded:
            self.stdout.write(
                self.style.SUCCESS(f"  Seeded missing plans: {', '.join(seeded)}")
            )

        # ── Assign the plan ───────────────────────────────────────────
        plan = SubscriptionPlan.objects.get(name=plan_name)
        lifetime = "lifetime" in plan_name
        # Public routing tiers are always free and non-expiring.
        is_trial = False
        expires_at = None
        trial_expires_at = None

        if not lifetime and plan_name not in ("trial", "saas", "white_label"):
            expires_at = timezone.now() + timedelta(days=days)

        sub = ChurchSubscription.objects.filter(church=church).first()
        if sub:
            sub.plan = plan
            sub.is_trial = is_trial
            sub.is_active = True
            sub.expires_at = expires_at
            sub.trial_expires_at = trial_expires_at
            sub.interval = "monthly"
            sub.save()
            action = "Updated"
        else:
            ChurchSubscription.objects.create(
                church=church,
                plan=plan,
                is_trial=is_trial,
                is_active=True,
                expires_at=expires_at,
                trial_expires_at=trial_expires_at,
                interval="monthly",
            )
            action = "Created"

        # ── Summary ───────────────────────────────────────────────────
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("  ✓ Done"))
        self.stdout.write(f"  Church  : {church.name}  ({church.slug})")
        self.stdout.write(f"  Plan    : {plan_name}")
        if is_trial:
            self.stdout.write(
                f"  Expires : {trial_expires_at.strftime('%d %b %Y')}  ({days} days trial)"
            )
        elif lifetime:
            self.stdout.write(f"  Expires : never  (lifetime)")
        else:
            self.stdout.write(
                f"  Expires : {expires_at.strftime('%d %b %Y')}  ({days} days)"
            )
        self.stdout.write(f"  Action  : {action} subscription record")
        self.stdout.write("")
