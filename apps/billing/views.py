"""
billing/views.py

Public-facing billing views:
    - pricing      : shows Trial / SaaS / White Label plan cards
    - upgrade      : handles plan selection form submission
    - portal       : current subscription status for logged-in admins
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages


# ─────────────────────────────────────────────────────────────────────────────
# Plan definitions shown on the pricing page.
# Founders plans are intentionally absent.
# ─────────────────────────────────────────────────────────────────────────────

PLAN_CARDS = [
    {
        "key": "trial",
        "label": "Free Trial",
        "price": "Free",
        "duration": "14 days",
        "description": "Full access to every feature. No card required.",
        "features": [
            "All core features",
            "Up to 50 workforce members",
            "Guest management",
            "Real-time messaging",
            "Push notifications",
        ],
        "cta": "Current plan",
        "highlight": False,
    },
    {
        "key": "saas",
        "label": "SaaS",
        "price": "₦15,000",
        "duration": "/ month",
        "description": "Your own subdomain. Full multi-campus support.",
        "features": [
            "Everything in Trial",
            "Subdomain: yourchurch.workforce.church",
            "Multi-campus support",
            "Priority support",
            "Unlimited members",
        ],
        "cta": "Upgrade to SaaS",
        "highlight": True,
    },
    {
        "key": "white_label",
        "label": "White Label",
        "price": "₦45,000",
        "duration": "/ month",
        "description": "Your own domain, your own brand. Dedicated infrastructure.",
        "features": [
            "Everything in SaaS",
            "Custom domain: app.yourchurch.org",
            "Dedicated database",
            "White-label branding (no ChurchForce mention)",
            "Dedicated onboarding support",
        ],
        "cta": "Upgrade to White Label",
        "highlight": False,
    },
]


def pricing(request):
    """
    Public pricing page — accessible without login.
    Shows plan cards and links to signup or upgrade.

    Template: billing/pricing.html
    """
    church = getattr(request, "church", None)
    current_plan = None

    if church:
        from billing.services import get_plan_name
        current_plan = get_plan_name(church)

    return render(request, "billing/pricing.html", {
        "plans": PLAN_CARDS,
        "current_plan": current_plan,
        "church": church,
    })


@login_required
def upgrade(request):
    """
    Handles plan upgrade form submission.
    GET  → redirect to pricing page
    POST → process the selected plan

    For now this initiates the upgrade flow — payment integration
    (Paystack webhook) will complete the subscription activation.

    Template: billing/upgrade_confirm.html
    """
    church = getattr(request, "church", None)
    if not church:
        messages.error(request, "No organisation context found.")
        return redirect("tenants:signup")

    if request.method == "GET":
        return redirect("billing:pricing")

    selected_plan = request.POST.get("plan")

    if selected_plan not in ("saas", "white_label"):
        messages.error(request, "Please select a valid plan.")
        return redirect("billing:pricing")

    # Collect routing details depending on the plan
    subdomain = request.POST.get("subdomain", "").strip()
    custom_domain = request.POST.get("custom_domain", "").strip()

    context = {
        "church": church,
        "selected_plan": selected_plan,
        "subdomain": subdomain,
        "custom_domain": custom_domain,
        "plan_label": "SaaS" if selected_plan == "saas" else "White Label",
        # Price shown on confirmation screen
        "plan_price": "₦15,000/month" if selected_plan == "saas" else "₦45,000/month",
    }

    # Render confirmation page before initiating payment
    # The confirmation page posts to billing:initiate_payment
    return render(request, "billing/upgrade_confirm.html", context)


@login_required
def portal(request):
    """
    Subscription management portal for the church admin.
    Shows current plan, expiry, and upgrade options.

    Template: billing/portal.html
    """
    church = getattr(request, "church", None)
    if not church:
        return redirect("tenants:signup")

    from billing.services import (
        get_subscription,
        get_plan_name,
        is_founders_plan,
        subscription_valid,
    )

    sub = get_subscription(church)
    plan_name = get_plan_name(church)
    is_founders = is_founders_plan(church)
    is_valid = subscription_valid(church)

    return render(request, "billing/portal.html", {
        "church": church,
        "subscription": sub,
        "plan_name": plan_name,
        "is_founders": is_founders,
        "is_valid": is_valid,
        "plans": PLAN_CARDS,
    })