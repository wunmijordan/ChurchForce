"""
Management command: provision_white_label

Provisions a dedicated database for a white-label church tenant.

What it does:
    1. Verifies the church exists and is on a white-label plan
    2. Checks the WL_DB_URL_<SLUG> env var is set and reachable
    3. Registers the DB alias in settings.DATABASES at runtime
    4. Runs migrate --database=wl_<slug> to clone the full schema
    5. Optionally seeds the church's own data row into the dedicated DB

Usage:
    python manage.py provision_white_label <church_slug>
    python manage.py provision_white_label <church_slug> --skip-seed
"""

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import connections
import dj_database_url
import os


class Command(BaseCommand):
    help = "Provision a dedicated database for a white-label church tenant."

    def add_arguments(self, parser):
        parser.add_argument(
            "church_slug",
            type=str,
            help="The slug of the Church to provision (must match Church.slug exactly).",
        )
        parser.add_argument(
            "--skip-seed",
            action="store_true",
            default=False,
            help="Skip copying the Church row into the dedicated DB after migration.",
        )

    def handle(self, *args, **options):
        slug = options["church_slug"]
        alias = f"wl_{slug}"

        # ----------------------------------------------------------------
        # 1. Resolve the church
        # ----------------------------------------------------------------
        from tenants.models import Church

        try:
            church = Church.raw_objects.get(slug=slug)
        except Church.DoesNotExist:
            raise CommandError(f"No Church found with slug '{slug}'.")

        if not church.is_active:
            raise CommandError(f"Church '{slug}' is inactive.")

        if not church.is_white_label:
            raise CommandError(
                f"Church '{slug}' is not on a white-label plan. "
                "Upgrade the subscription before provisioning a dedicated DB."
            )

        # ----------------------------------------------------------------
        # 2. Verify the DB URL env var is present
        # ----------------------------------------------------------------
        env_key = f"WL_DB_URL_{slug.upper().replace('-', '_')}"
        db_url = os.environ.get(env_key)

        if not db_url:
            raise CommandError(
                f"Environment variable '{env_key}' is not set.\n"
                f"Add it to your .env and restart before provisioning."
            )

        # ----------------------------------------------------------------
        # 3. Register the alias in settings.DATABASES at runtime
        #    (it may already exist if the server was started after the env
        #    var was set — idempotent either way)
        # ----------------------------------------------------------------
        if alias not in settings.DATABASES:
            if settings.ENVIRONMENT == "production":
                settings.DATABASES[alias] = dj_database_url.config(
                    default=db_url,
                    conn_max_age=600,
                    ssl_require=True,
                )
            else:
                settings.DATABASES[alias] = dj_database_url.parse(db_url)

            self.stdout.write(f"  Registered DB alias '{alias}'.")
        else:
            self.stdout.write(f"  DB alias '{alias}' already registered — skipping.")

        # ----------------------------------------------------------------
        # 4. Test connectivity before running migrations
        # ----------------------------------------------------------------
        try:
            conn = connections[alias]
            conn.ensure_connection()
            self.stdout.write(self.style.SUCCESS(f"  Connected to '{alias}' successfully."))
        except Exception as exc:
            raise CommandError(
                f"Could not connect to database '{alias}'.\n"
                f"Check '{env_key}' and ensure the database server is reachable.\n"
                f"Error: {exc}"
            )

        # ----------------------------------------------------------------
        # 5. Run migrations against the dedicated DB
        #    allow_migrate is overridden in the router for this alias
        #    so we call migrate directly with --database
        # ----------------------------------------------------------------
        self.stdout.write(f"  Running migrations on '{alias}'...")

        from django.core.management import call_command

        # Temporarily allow migrations on this alias by bypassing the router
        # We patch allow_migrate on the router instance for this one call
        from tenants.db_router import TenantDatabaseRouter

        original_allow_migrate = TenantDatabaseRouter.allow_migrate

        def patched_allow_migrate(self_inner, db, app_label, model_name=None, **hints):
            if db == alias:
                return True
            return db == "default"

        TenantDatabaseRouter.allow_migrate = patched_allow_migrate

        try:
            call_command("migrate", database=alias, verbosity=1)
        finally:
            TenantDatabaseRouter.allow_migrate = original_allow_migrate

        self.stdout.write(self.style.SUCCESS(f"  Migrations complete on '{alias}'."))

        # ----------------------------------------------------------------
        # 6. Seed the Church row into the dedicated DB
        #    The church record itself lives in 'default' (shared registry)
        #    but the dedicated DB needs it too for FK integrity.
        # ----------------------------------------------------------------
        if not options["skip_seed"]:
            self._seed_church(church, alias)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nWhite-label DB for '{slug}' is ready.\n"
                f"Alias: {alias}\n"
                f"Add '{env_key}' to your production environment to activate routing."
            )
        )

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    def _seed_church(self, church, alias):
        """
        Copy the Church row (and its ChurchTemplate FK if present) into
        the dedicated DB so FK constraints resolve locally.
        """
        from bootstrap.models import ChurchTemplate

        self.stdout.write("  Seeding Church row into dedicated DB...")

        # Seed template first if it exists
        if church.template_id:
            template = church.template
            ChurchTemplate.objects.using(alias).update_or_create(
                id=template.id,
                defaults={
                    "name": template.name,
                    "description": template.description,
                    "is_default": template.is_default,
                    "is_system": template.is_system,
                    "is_active": template.is_active,
                },
            )

        from tenants.models import Church as ChurchModel

        ChurchModel.raw_objects.using(alias).update_or_create(
            id=church.id,
            defaults={
                "name": church.name,
                "slug": church.slug,
                "subdomain": church.subdomain,
                "custom_domain": church.custom_domain,
                "primary_color": church.primary_color,
                "is_active": church.is_active,
                "template_id": church.template_id,
            },
        )

        self.stdout.write(self.style.SUCCESS("  Church row seeded."))