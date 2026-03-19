"""
billing/services.py

Plan resolution, subscription validation, and upgrade logic.

Plan hierarchy (visible to clients):
    trial            → 14-day free, slug routing, shared DB
    saas             → paid monthly, subdomain routing, shared DB
    white_label      → paid monthly, custom domain, dedicated DB

Founders plans (internal only, never shown on pricing page):
    founders_saas_lifetime          → SaaS features, no expiry
    founders_white_label_lifetime   → White Label features, no expiry
    These are granted via: manage.py grant_founders_plan <slug> <tier>
    or via the Django admin action on ChurchSubscription.
"""

from django.core.exceptions import PermissionDenied
from django.utils import timezone


LIFETIME_PLANS = {
    "founders_saas_lifetime",
    "founders_white_label_lifetime",
}

# Plans that include white-label features (custom domain + dedicated DB)
WHITE_LABEL_PLANS = {
    "white_label",
    "founders_white_label_lifetime",
}

# Plans that include multi-campus features
MULTI_CAMPUS_PLANS = {
    "saas",
    "white_label",
    "founders_saas_lifetime",
    "founders_white_label_lifetime",
}

# Public-facing plans shown on the pricing/upgrade page
PUBLIC_PLANS = ["trial", "saas", "white_label"]


# ─────────────────────────────────────────────────────────────────────────────
# Plan resolution
# ─────────────────────────────────────────────────────────────────────────────

def get_subscription(church):
    return getattr(church, "churchsubscription", None)


def get_plan(church):
    sub = get_subscription(church)
    return sub.plan if sub else None


def get_plan_name(church):
    plan = get_plan(church)
    return plan.name if plan else None


# ─────────────────────────────────────────────────────────────────────────────
# Subscription status
# ─────────────────────────────────────────────────────────────────────────────

def subscription_valid(church):
    sub = get_subscription(church)
    return sub.is_valid() if sub else False


def require_active_subscription(church):
    if not subscription_valid(church):
        raise PermissionDenied("Active subscription required.")


def is_white_label(church):
    plan = get_plan(church)
    return plan.name in WHITE_LABEL_PLANS if plan else False


def allows_multicampus(church):
    plan = get_plan(church)
    return plan.name in MULTI_CAMPUS_PLANS if plan else False


def is_founders_plan(church):
    plan = get_plan(church)
    return plan.name in LIFETIME_PLANS if plan else False


def validate_white_label_access(church, host):
    """
    Guard called in TenantMiddleware. Ensures a church accessing via a
    custom domain is actually on a white-label plan.
    """
    if church.custom_domain and host == church.custom_domain:
        if not is_white_label(church):
            raise PermissionDenied(
                "Custom domain access requires a White Label plan."
            )


# ─────────────────────────────────────────────────────────────────────────────
# Upgrade logic
# ─────────────────────────────────────────────────────────────────────────────

def upgrade_to_saas(church, subdomain: str, expires_at=None):
    """
    Upgrade a church from trial to the SaaS plan.

    - Assigns the subdomain (switches routing from slug to subdomain).
    - Updates or creates ChurchSubscription on the saas plan.
    - expires_at should be set by the payment webhook after confirmation.

    Raises ValueError if the subdomain is taken.
    """
    from billing.models import SubscriptionPlan, ChurchSubscription
    from tenants.onboarding import activate_subdomain

    activate_subdomain(church, subdomain)

    plan, _ = SubscriptionPlan.objects.get_or_create(
        name="saas",
        defaults={
            "multi_campus": True,
            "white_label": False,
            "description": "SaaS plan with subdomain routing.",
        },
    )

    sub = get_subscription(church)
    if sub:
        sub.plan = plan
        sub.is_trial = False
        sub.is_active = True
        sub.expires_at = expires_at
        sub.trial_expires_at = None
        sub.save()
    else:
        ChurchSubscription.objects.create(
            church=church,
            plan=plan,
            is_trial=False,
            is_active=True,
            expires_at=expires_at,
        )

    return church


def upgrade_to_white_label(church, custom_domain: str, expires_at=None):
    """
    Upgrade a church to the White Label plan.

    - Assigns the custom domain.
    - Updates ChurchSubscription.
    - White-label DB provisioning is a separate step:
        manage.py provision_white_label <slug>

    Raises ValueError if the custom domain is already registered.
    """
    from billing.models import SubscriptionPlan, ChurchSubscription
    from tenants.onboarding import activate_custom_domain

    activate_custom_domain(church, custom_domain)

    plan, _ = SubscriptionPlan.objects.get_or_create(
        name="white_label",
        defaults={
            "multi_campus": True,
            "white_label": True,
            "description": "White Label plan with custom domain and dedicated DB.",
        },
    )

    sub = get_subscription(church)
    if sub:
        sub.plan = plan
        sub.is_trial = False
        sub.is_active = True
        sub.expires_at = expires_at
        sub.trial_expires_at = None
        sub.save()
    else:
        ChurchSubscription.objects.create(
            church=church,
            plan=plan,
            is_trial=False,
            is_active=True,
            expires_at=expires_at,
        )

    return church


# ─────────────────────────────────────────────────────────────────────────────
# Founders plan grant (internal only)
# ─────────────────────────────────────────────────────────────────────────────

def grant_founders_plan(church, tier: str):
    """
    Grant a founders lifetime plan to a church.

    tier must be "saas" or "white_label".

    This is an internal operation — never exposed on the public pricing page.
    Called by:
        - manage.py grant_founders_plan <slug> <tier>
        - Django admin action on ChurchSubscription

    For white_label tier: still need to run provision_white_label separately
    and set the custom_domain on the Church.
    """
    from billing.models import SubscriptionPlan, ChurchSubscription

    if tier not in ("saas", "white_label"):
        raise ValueError("tier must be 'saas' or 'white_label'.")

    plan_name = f"founders_{tier}_lifetime"

    plan, _ = SubscriptionPlan.objects.get_or_create(
        name=plan_name,
        defaults={
            "multi_campus": True,
            "white_label": tier == "white_label",
            "description": f"Founders lifetime access — {tier}.",
        },
    )

    sub = get_subscription(church)
    if sub:
        sub.plan = plan
        sub.is_trial = False
        sub.is_active = True
        sub.expires_at = None
        sub.trial_expires_at = None
        sub.save()
    else:
        ChurchSubscription.objects.create(
            church=church,
            plan=plan,
            is_trial=False,
            is_active=True,
            expires_at=None,
        )

    return church

# ─────────────────────────────────────────────────────────────────────────────
# Campus access helpers
# ─────────────────────────────────────────────────────────────────────────────

def campus_limit(church):
    """
    Return the campus limit for the church's plan.
    0 = none (trial), -1 = unlimited, n = capped.
    """
    plan = get_plan(church)
    if not plan:
        return 0
    return getattr(plan, "campus_limit", 0)


def can_add_campus(church):
    """
    Return True if the church can add another campus.
    Checks against the plan's campus_limit and the current campus count.
    """
    from units.models import Campus
    limit = campus_limit(church)
    if limit == 0:
        return False
    if limit == -1:
        return True
    current = Campus.raw_objects.filter(church=church, is_active=True).count()
    return current < limit


def requires_campus_addon(church):
    """
    True if the church is on the SaaS plan and has a campus price > 0.
    Used to show a billing prompt when adding campuses above the base limit.
    """
    plan = get_plan(church)
    if not plan:
        return False
    return (
        plan.name == "saas"
        and getattr(plan, "campus_price_ngn", 0) > 0
    )