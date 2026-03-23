from core.request_context import get_current_church


# Apps whose tables must always live in the shared (default) DB.
# Django internals, sessions, and the tenant registry itself never move.
SHARED_APPS = {
    "contenttypes",
    "auth",
    "admin",
    "sessions",
    "tenants",
    "billing",
    "bootstrap",
}


def _db_alias_for_church(church):
    """
    Return the DB alias for a resolved church, or 'default' if the church
    is not on a white-label plan or has no dedicated DB configured.
    """
    if church is None:
        return "default"

    if not church.is_white_label:
        return "default"

    alias = f"wl_{church.slug}"

    # Only route to the dedicated DB if it has actually been registered
    # in settings.DATABASES.  Unregistered aliases fall back to default
    # so a misconfigured tenant never hard-crashes the request.
    from django.conf import settings
    if alias not in settings.DATABASES:
        return "default"

    return alias


class TenantDatabaseRouter:
    """
    Routes database queries based on the current tenant context.

    Rules:
        1. Shared-app models (auth, tenants, billing, etc.) always use
           the 'default' DB — they must be visible across all tenants.
        2. White-label tenants get a dedicated DB alias ('wl_<slug>').
        3. Every other tenant (trial / SaaS) uses 'default'.
        4. Cross-DB relations are never allowed (enforced by allow_relation).
        5. Migrations always run on 'default'; white-label DBs are
           provisioned separately via the management command below.
    """

    # ----------------------------------------------------------------
    # Read routing
    # ----------------------------------------------------------------

    def db_for_read(self, model, **hints):
        if model._meta.app_label in SHARED_APPS:
            return "default"

        # Prefer an instance hint if Django provides one (e.g. related lookups)
        instance = hints.get("instance")
        if instance is not None:
            church = getattr(instance, "church", None)
            if church is not None:
                return _db_alias_for_church(church)

        church = get_current_church()
        return _db_alias_for_church(church)

    # ----------------------------------------------------------------
    # Write routing
    # ----------------------------------------------------------------

    def db_for_write(self, model, **hints):
        if model._meta.app_label in SHARED_APPS:
            return "default"

        instance = hints.get("instance")
        if instance is not None:
            church = getattr(instance, "church", None)
            if church is not None:
                return _db_alias_for_church(church)

        church = get_current_church()
        return _db_alias_for_church(church)

    # ----------------------------------------------------------------
    # Relation guard
    # ----------------------------------------------------------------

    def allow_relation(self, obj1, obj2, **hints):
        """
        Allow relations only when both objects live in the same DB tier.
        Shared-app objects may relate to anything (FK from ChurchMember
        to auth.User must be allowed).
        """
        app1 = obj1._meta.app_label
        app2 = obj2._meta.app_label

        if app1 in SHARED_APPS or app2 in SHARED_APPS:
            return True

        # Both tenant-owned: allow only if they resolve to the same alias
        church1 = getattr(obj1, "church", None)
        church2 = getattr(obj2, "church", None)

        return _db_alias_for_church(church1) == _db_alias_for_church(church2)

    # ----------------------------------------------------------------
    # Migration routing
    # ----------------------------------------------------------------

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        Standard migrations only touch 'default'.
        White-label DBs are provisioned via: manage.py provision_white_label
        which clones the schema from default into the dedicated DB.
        """
        return db == "default"
