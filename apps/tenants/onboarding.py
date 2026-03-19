"""
tenants/onboarding.py

Provisions a brand-new church tenant on the trial plan.

Design decisions:
    - Trial-first: every new signup starts on a 14-day trial.
      Plan selection (SaaS / White Label) happens on the billing/upgrade
      page after signup, or when the trial expires.
    - Trial tenants use root-slug routing: app.com/<slug>/...
      Subdomain routing activates when the tenant upgrades to SaaS.
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
        trial_days=14,
    )
"""

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from datetime import timedelta


@transaction.atomic
def provision_church(
    *,
    church_name: str,
    slug: str,
    admin_full_name: str,
    admin_email: str,
    admin_password: str,
    primary_color: str = "#206bc4",
    trial_days: int = 14,
):
    """
    Provision a new church tenant on the trial plan.

    Trial tenants have no subdomain — they access the app via
    app.com/<slug>/... until they upgrade to SaaS or White Label.

    Returns:
        (Church, CustomUser) — created church and its first admin user.

    Raises:
        ValueError on slug/email uniqueness violations (human-readable).
    """

    from tenants.models import Church
    from accounts.models import CustomUser, ChurchMember
    from billing.models import SubscriptionPlan, ChurchSubscription
    from bootstrap.models import ChurchTemplate
    from bootstrap.services import apply_template_to_church

    # ----------------------------------------------------------------
    # 1. Validate uniqueness up front for clean user-facing errors
    # ----------------------------------------------------------------

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
    # 2. Create the Church (no subdomain yet — that's a paid feature)
    # ----------------------------------------------------------------

    church = Church.raw_objects.create(
        name=church_name,
        slug=slug,
        subdomain=None,          # assigned on upgrade to SaaS
        custom_domain=None,      # assigned on upgrade to White Label
        primary_color=primary_color,
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

    ChurchMember.raw_objects.create(
        church=church,
        user=user,
        is_active=True,
    )

    # ----------------------------------------------------------------
    # 5. Start trial subscription
    # ----------------------------------------------------------------

    trial_plan, _ = SubscriptionPlan.objects.get_or_create(
        name="trial",
        defaults={
            "multi_campus": False,
            "white_label": False,
            "description": "14-day free trial with full feature access.",
        },
    )

    ChurchSubscription.objects.create(
        church=church,
        plan=trial_plan,
        is_trial=True,
        is_active=True,
        trial_expires_at=timezone.now() + timedelta(days=trial_days),
    )

    # ----------------------------------------------------------------
    # 6. Bootstrap lookup data from the default template.
    #    The post_save signal on Church fires this too, but we guard
    #    against double-seeding in case the signal already ran.
    # ----------------------------------------------------------------

    template = (
        church.template
        or ChurchTemplate.objects.filter(is_default=True, is_active=True).first()
    )

    if template:
        from units.models import ChurchUnit
        already_seeded = ChurchUnit.raw_objects.filter(church=church).exists()
        if not already_seeded:
            apply_template_to_church(template, church)

    return church, user


def activate_subdomain(church, subdomain: str):
    """
    Called during SaaS upgrade.
    Assigns a subdomain and switches routing from slug to subdomain.
    Raises ValueError if subdomain is taken.
    """
    from tenants.models import Church

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
    Called during White Label upgrade.
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