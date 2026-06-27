"""
billing/services.py

Plan resolution, subscription validation, and upgrade logic.

Plan hierarchy (visible to clients — all free, routing-only choices):
    trial            → path-slug routing, shared DB
    saas             → subdomain routing, shared DB
    white_label      → custom domain, optional dedicated DB

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

# Public-facing routing tiers on the pricing page
PUBLIC_PLANS = ["trial", "saas", "white_label"]

ROUTING_PLAN_LABELS = {
    "trial": "Path URL",
    "saas": "Subdomain",
    "white_label": "Custom Domain",
    # Founders tiers map to routing labels — never show "Founders" in UI.
    "founders_saas_lifetime": "Subdomain",
    "founders_white_label_lifetime": "Custom Domain",
}


def routing_tier_label(church):
    """Human routing label for templates (never exposes founders branding)."""
    plan_name = get_plan_name(church)
    if plan_name in ROUTING_PLAN_LABELS:
        return ROUTING_PLAN_LABELS[plan_name]
    if church and church.custom_domain:
        return ROUTING_PLAN_LABELS["white_label"]
    if church and church.subdomain:
        return ROUTING_PLAN_LABELS["saas"]
    return ROUTING_PLAN_LABELS["trial"]


def routing_plan_key(church):
    """Public plan card key for the church's active routing tier."""
    plan_name = get_plan_name(church)
    if plan_name in PUBLIC_PLANS:
        return plan_name
    if church and church.custom_domain:
        return "white_label"
    if church and church.subdomain:
        return "saas"
    return "trial"


def get_routing_display_context(church):
    """
    Routing URLs and DNS hints for account/pricing templates.
    Founders plans are presented as their routing tier only.
    """
    from django.conf import settings

    if not church:
        return {}

    app_host = (getattr(settings, "APP_DOMAINS", None) or ["workforce.church"])[0]
    app_host = app_host.split(":")[0]
    scheme = "https"

    path_url = f"{scheme}://{app_host}/{church.slug}/"
    subdomain_url = (
        f"{scheme}://{church.subdomain}.{app_host}/" if church.subdomain else ""
    )
    custom_url = (
        f"{scheme}://{church.custom_domain}/" if church.custom_domain else ""
    )

    tier = routing_plan_key(church)
    primary_url = custom_url or subdomain_url or path_url

    return {
        "routing_tier_label": routing_tier_label(church),
        "routing_plan_key": tier,
        "routing_path_url": path_url,
        "routing_subdomain_url": subdomain_url,
        "routing_custom_url": custom_url,
        "routing_primary_url": primary_url,
        "routing_app_host": app_host,
        "routing_dns_cname_target": app_host,
        "routing_show_dns": tier == "white_label" or bool(church.custom_domain),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Plan resolution
# ─────────────────────────────────────────────────────────────────────────────


def get_subscription(church):
    if not church:
        return None
    sub = getattr(church, "churchsubscription", None)
    if sub is not None:
        return sub
    if getattr(church, "parent_church_id", None):
        return church.get_effective_subscription()
    return None


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
            raise PermissionDenied("Custom domain access requires a White Label plan.")


# ─────────────────────────────────────────────────────────────────────────────
# Routing tier switches (all free)
# ─────────────────────────────────────────────────────────────────────────────


def apply_routing_plan(church, plan_key, *, subdomain=None, custom_domain=None):
    """
    Switch a church between routing tiers instantly. All tiers are free.

    trial       → workforce.church/<slug>/...  (clears subdomain + custom domain)
    saas        → <subdomain>.workforce.church
    white_label → <custom_domain>

    Raises ValueError on invalid plan or missing routing fields.
    """
    if plan_key not in PUBLIC_PLANS:
        raise ValueError("Please select a valid routing option.")

    if plan_key == "trial":
        return switch_to_path_routing(church)
    if plan_key == "saas":
        sub = (subdomain or "").strip() or (church.subdomain or "")
        if not sub:
            raise ValueError("Please enter your desired subdomain.")
        church.custom_domain = None
        church.hide_platform_credit = False
        church.save(
            update_fields=["custom_domain", "hide_platform_credit", "updated_at"]
        )
        return upgrade_to_saas(church, sub, expires_at=None)
    domain = (custom_domain or "").strip() or (church.custom_domain or "")
    if not domain:
        raise ValueError("Please enter your custom domain.")
    church.hide_platform_credit = True
    church.save(update_fields=["hide_platform_credit", "updated_at"])
    return upgrade_to_white_label(church, domain, expires_at=None)


def switch_to_path_routing(church):
    """
    Path-slug routing: keeps slug, clears subdomain/custom domain fields.
    """
    from billing.models import SubscriptionPlan, ChurchSubscription

    church.subdomain = None
    church.custom_domain = None
    church.hide_platform_credit = False
    church.save(
        update_fields=[
            "subdomain",
            "custom_domain",
            "hide_platform_credit",
            "updated_at",
        ]
    )

    plan, _ = SubscriptionPlan.objects.get_or_create(
        name="trial",
        defaults={
            "multi_campus": True,
            "white_label": False,
            "campus_limit": -1,
            "monthly_price_ngn": 0,
            "description": "Path-slug routing — workforce.church/your-church/...",
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

    try:
        from music.services.catalog import seed_tracks_for_plan

        seed_tracks_for_plan(church, "trial")
    except Exception:
        pass

    return church


def upgrade_to_saas(church, subdomain: str, expires_at=None):
    """
    Switch to subdomain routing (SaaS tier).

    - Assigns the subdomain (switches routing from slug to subdomain).
    - Updates or creates ChurchSubscription on the saas plan.
    - expires_at is None for free churches; set only for future paid tiers.

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
        sub.expires_at = expires_at  # None = free / never expires
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

    # Expand seeded content pack for subdomain tier.
    try:
        from music.services.catalog import seed_tracks_for_plan

        seed_tracks_for_plan(church, "saas")
        # bible seeding removed — handled by ai_skills
    except Exception:
        pass

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

    # Expand seeded content pack for Enterprise plan.
    try:
        from music.services.catalog import seed_tracks_for_plan

        seed_tracks_for_plan(church, "white_label")
        # bible seeding removed — handled by ai_skills
    except Exception:
        pass

    return church


# ─────────────────────────────────────────────────────────────────────────────
# Activate subscription from a completed PaymentRecord
# ─────────────────────────────────────────────────────────────────────────────


def activate_subscription_from_payment(payment):
    """
    Called after a PaymentRecord is confirmed successful (via webhook or
    payment_callback). Reads the payment's plan and interval, computes the
    valid_until date, and delegates to the appropriate upgrade function.

    payment.plan.white_label == True  -> upgrade_to_white_label()
    payment.plan.white_label == False -> upgrade_to_saas()

    The subdomain / custom_domain are read from payment.raw_payload metadata
    which is stored by initiate_payment() before hitting Paystack.
    """
    from datetime import timedelta
    from django.utils.timezone import now

    church = payment.church
    plan = payment.plan
    interval = payment.interval or "monthly"
    metadata = payment.raw_payload.get("metadata", {}) if payment.raw_payload else {}

    # Compute expiry from billing interval
    interval_days = {
        "monthly": 30,
        "annual": 365,
        "biannual": 730,
    }
    days = interval_days.get(interval, 30)
    expires_at = now() + timedelta(days=days)

    if plan.white_label:
        custom_domain = metadata.get("custom_domain", "")
        upgrade_to_white_label(
            church, custom_domain=custom_domain, expires_at=expires_at
        )
    else:
        subdomain = metadata.get("subdomain", "") or church.subdomain or ""
        upgrade_to_saas(church, subdomain=subdomain, expires_at=expires_at)
        addon_qty = metadata.get("campus_addon_qty")
        if addon_qty:
            add_extra_campus_slots(church, addon_qty)


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

    # Founders plans should receive an enterprise-grade starter pack.
    try:
        from music.services.catalog import seed_tracks_for_plan

        plan_name = (
            "founders_white_label_lifetime"
            if tier == "white_label"
            else "founders_saas_lifetime"
        )
        seed_tracks_for_plan(church, plan_name)
        # bible seeding removed — handled by ai_skills
    except Exception:
        pass

    return church


# ─────────────────────────────────────────────────────────────────────────────
# Campus access helpers
# ─────────────────────────────────────────────────────────────────────────────


def campus_limit(church):
    """
    Return the campus limit for the church's plan.
    0 = none, -1 = unlimited, n = capped.
    All public routing tiers are currently unlimited (free-first).
    """
    plan = get_plan(church)
    if not plan:
        return 0
    if is_founders_plan(church):
        return -1
    if plan.name in PUBLIC_PLANS:
        limit = getattr(plan, "campus_limit", -1)
        return -1 if limit in (None, -1) else limit
    return getattr(plan, "campus_limit", 0)


def extra_campus_slots(church):
    """
    Return paid add-on campus slots purchased beyond plan base.
    Stored in ChurchSetting.workforce_config["extra_campus_slots"].
    """
    settings_obj = getattr(church, "settings", None)
    if not settings_obj:
        return 0
    cfg = dict(getattr(settings_obj, "workforce_config", {}) or {})
    try:
        return max(0, int(cfg.get("extra_campus_slots", 0)))
    except (TypeError, ValueError):
        return 0


def add_extra_campus_slots(church, slots):
    """Persist additional campus slots after successful add-on payment."""
    settings_obj = getattr(church, "settings", None)
    if not settings_obj:
        return
    try:
        slots_int = int(slots)
    except (TypeError, ValueError):
        return
    if slots_int <= 0:
        return
    cfg = dict(getattr(settings_obj, "workforce_config", {}) or {})
    cfg["extra_campus_slots"] = (
        max(0, int(cfg.get("extra_campus_slots", 0))) + slots_int
    )
    settings_obj.workforce_config = cfg
    settings_obj.save(update_fields=["workforce_config", "updated_at"])


def can_add_campus(church):
    """
    Return True if the church can add another campus.
    Checks against the plan's campus_limit and the current campus count.
    """
    from tenants.models import Campus

    if is_founders_plan(church):
        return True
    limit = campus_limit(church)
    if limit == 0:
        return False
    if limit == -1:
        return True
    current = Campus.raw_objects.filter(church=church, is_active=True).count()
    return current < (limit + extra_campus_slots(church))


def requires_campus_addon(church):
    """
    True when campuses above the plan limit require a paid add-on.
    Disabled while all routing tiers are free.
    """
    plan = get_plan(church)
    if not plan:
        return False
    return (
        plan.name == "saas"
        and getattr(plan, "campus_price_ngn", 0) > 0
        and campus_limit(church) > -1
    )


# ── Plan seeding ───────────────────────────────────────────────────────────────


def ensure_plans_seeded():
    """
    Idempotently create all SubscriptionPlan records from the canonical
    PLAN_DEFAULTS table in dev_grant_plan management command.

    This is the single source of truth for plan definitions. Safe to call
    on every app startup — skips plans that already exist.

    Called from:
      - billing.apps.BillingConfig.ready()   — on every server start
      - seed_demo management command          — after each demo reset
      - billing.views.pricing()              — lazy fallback safety net
    """
    try:
        from billing.management.commands.dev_grant_plan import PLAN_DEFAULTS
        from billing.models import SubscriptionPlan

        ALLOWED_FIELDS = {
            "monthly_price_ngn",
            "campus_limit",
            "campus_price_ngn",
            "annual_discount_pct",
            "biannual_discount_pct",
            "multi_campus",
            "white_label",
            "description",
        }
        for name, defaults in PLAN_DEFAULTS.items():
            SubscriptionPlan.objects.get_or_create(
                name=name,
                defaults={k: v for k, v in defaults.items() if k in ALLOWED_FIELDS},
            )
        # Free-first: all public routing tiers are ₦0 with unlimited campuses.
        SubscriptionPlan.objects.filter(
            name__in=["trial", "saas", "white_label"],
        ).update(
            monthly_price_ngn=0,
            campus_price_ngn=0,
            campus_limit=-1,
            multi_campus=True,
        )
        SubscriptionPlan.objects.filter(name="trial").update(white_label=False)
        SubscriptionPlan.objects.filter(name="white_label").update(white_label=True)
        # Lifetime founders plans should remain unlimited.
        SubscriptionPlan.objects.filter(
            name__in=[
                "founders_saas_lifetime",
                "founders_white_label_lifetime",
            ]
        ).exclude(campus_limit=-1).update(campus_limit=-1)
    except Exception:
        # Never crash startup over seeding (e.g. during initial migrations)
        pass
