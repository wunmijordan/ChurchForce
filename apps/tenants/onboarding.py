"""
tenants/onboarding.py

Provisions a brand-new church tenant on the path-slug routing tier (free).

Design decisions:
    - Path-first: every new signup uses workforce.church/<slug>/...
    - All routing tiers are free; churches switch tiers from Account → Routing.
    - raw_objects is used here because this runs before any request
      context exists. Once the user logs in and a real request is
      processed, ChurchContextMiddleware sets the context var and all
      subsequent Model.objects queries auto-scope to their church
      normally. raw_objects is a query-time choice, not a state change.

Usage:
    from tenants.onboarding import provision_church

    church, user = provision_church(
        church_name="Grace Chapel",
        slug="grace-chapel",
        admin_full_name="John Doe",
        admin_email="john@gracechapel.org",
        admin_password="securepassword123",
    )
"""

from django.db import transaction
from decimal import Decimal


@transaction.atomic
def provision_church(
    *,
    church_name: str,
    slug: str,
    admin_full_name: str,
    admin_email: str,
    admin_password: str,
    primary_color: str = "#206bc4",
    latitude: float | None = None,
    longitude: float | None = None,
):
    """
    Provision a new church tenant on the path-slug routing tier (free).

    New churches access the app via workforce.church/<slug>/... until they
    switch to subdomain or custom-domain routing from Account settings.

    Returns:
        (Church, CustomUser) — created church and its first admin user.

    Raises:
        ValueError on slug/email uniqueness violations (human-readable).
    """

    from tenants.models import Church
    from accounts.models import CustomUser, ChurchMember
    from billing.models import SubscriptionPlan, ChurchSubscription
    from bootstrap.models import ChurchTemplate
    from bootstrap.services import (
        apply_template_to_church,
        ensure_default_template,
        _ensure_admin_bootstrap,
    )

    # ----------------------------------------------------------------
    # 1. Validate uniqueness up front for clean user-facing errors
    # ----------------------------------------------------------------

    from django.utils.text import slugify

    slug = slugify(slug)
    if not slug:
        raise ValueError("Please enter a valid URL handle.")

    if Church.raw_objects.filter(slug=slug).exists():
        raise ValueError(
            f"The URL handle '{slug}' is already taken. Please choose another."
        )

    if CustomUser.objects.filter(email__iexact=admin_email).exists():
        raise ValueError(
            f"An account with email '{admin_email}' already exists. "
            "Try logging in instead."
        )

    # ----------------------------------------------------------------
    # 2. Create the Church (path-slug routing — no subdomain yet)
    # ----------------------------------------------------------------

    lat_value = Decimal(str(latitude)) if latitude is not None else Decimal("0.0")
    lon_value = Decimal(str(longitude)) if longitude is not None else Decimal("0.0")

    church = Church.raw_objects.create(
        name=church_name,
        slug=slug,
        subdomain=None,
        custom_domain=None,
        primary_color=primary_color,
        latitude=lat_value,
        longitude=lon_value,
        is_active=True,
    )

    # ----------------------------------------------------------------
    # 3. Create the admin user
    # ----------------------------------------------------------------

    base_username = slugify(admin_email.split("@")[0])[:30] or "user"
    username = base_username
    counter = 1
    while CustomUser.objects.filter(username=username).exists():
        username = f"{base_username}{counter}"
        counter += 1

    user = CustomUser.objects.create_user(
        username=username,
        email=admin_email,
        password=admin_password,
        full_name=admin_full_name,
        is_active=True,
    )

    # ----------------------------------------------------------------
    # 4. Create ChurchMember (links user -> church)
    # ----------------------------------------------------------------

    member, created = ChurchMember.objects.get_or_create(
        church=church,
        user=user,
        defaults={"is_active": True, "is_admin": True},
    )
    if not created:
        if not member.is_active or not member.is_admin:
            member.is_active = True
            member.is_admin = True
            member.save(update_fields=["is_active", "is_admin"])

    # ----------------------------------------------------------------
    # 5. Free path-slug routing subscription (never expires)
    # ----------------------------------------------------------------

    trial_plan, _ = SubscriptionPlan.objects.get_or_create(
        name="trial",
        defaults={
            "multi_campus": True,
            "white_label": False,
            "campus_limit": -1,
            "monthly_price_ngn": 0,
            "description": "Path-slug routing — workforce.church/your-church/...",
        },
    )

    ChurchSubscription.objects.create(
        church=church,
        plan=trial_plan,
        is_trial=False,
        is_active=True,
        expires_at=None,
    )

    # ----------------------------------------------------------------
    # 6. Bootstrap lookup data from the default template.
    # ----------------------------------------------------------------

    template = (
        church.template
        or ChurchTemplate.objects.filter(is_default=True, is_active=True).first()
        or ensure_default_template()
    )
    if template:
        from units.models import ChurchUnit

        already_seeded = ChurchUnit.raw_objects.filter(church=church).exists()
        if not already_seeded:
            apply_template_to_church(
                template, church, admin_member=member, admin_user=user
            )
        else:
            _ensure_admin_bootstrap(
                church,
                admin_member=member,
                admin_user=user,
            )

    # ----------------------------------------------------------------
    # 7. Seed starter media/music baseline.
    # ----------------------------------------------------------------
    try:
        from music.services.catalog import seed_tracks_for_plan
        from media.services import seed_bible_content_for_plan

        seed_tracks_for_plan(church, "trial")
        seed_bible_content_for_plan(church, "trial")
    except Exception:
        pass

    return church, user


def activate_subdomain(church, subdomain: str):
    """
    Called during subdomain routing switch.
    Assigns a subdomain and switches routing from slug to subdomain.
    Raises ValueError if subdomain is taken.
    """
    from tenants.models import Church
    from django.utils.text import slugify

    subdomain = slugify(subdomain)
    if Church.raw_objects.filter(subdomain=subdomain).exclude(pk=church.pk).exists():
        raise ValueError(
            f"The subdomain '{subdomain}' is already taken. Please choose another."
        )

    church.subdomain = subdomain
    church.save(update_fields=["subdomain", "updated_at"])
    return church


def activate_custom_domain(church, custom_domain: str):
    """
    Called during custom-domain routing switch.
    Assigns a custom domain. Raises ValueError if already registered.
    """
    from tenants.models import Church

    domain = custom_domain.strip().lower()
    if Church.raw_objects.filter(custom_domain=domain).exclude(pk=church.pk).exists():
        raise ValueError(
            f"The domain '{domain}' is already registered to another account."
        )

    church.custom_domain = domain
    church.save(update_fields=["custom_domain", "updated_at"])
    return church
