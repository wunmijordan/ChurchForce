"""
marketing/views.py

Public-facing pages served from churchforce.io (MARKETING_DOMAIN).
No tenant context, no login required, no church middleware applies.
"""
from django.shortcuts import render, redirect
from django.conf import settings
from billing.views import PLAN_CARDS, INTERVAL_LABELS


def home(request):
    """Marketing homepage."""
    features = [
        ("👥", "Workforce Management",    "Organise your Workforce into Units and Groups, Manage Roles, and track every Member from Induction to Leadership."),
        ("📋", "Guest Follow-up Pipeline","Capture first-time Visitors and track their journey from Guest to Planted Member with automated pipeline stages."),
        ("🎓", "LMS & Induction",         "Run structured Induction Programmes with Courses, Modules, Quizzes and Certificates — all inside ChurchForce."),
        ("📍", "Attendance Tracking",     "Geo-fenced clock-in/clock-out. Members check in from within the building. No hardware required."),
        ("🏛️", "Multi-Campus Support",    "Manage multiple Campuses under one account. Track growth stages and transfer Members between locations."),
        ("💬", "Real-time Chat",          "Unit-scoped chat rooms, a church-wide general room, file sharing, reactions, and Guest card attachments."),
    ]
    return render(request, "marketing/home.html", {
        "plans":          PLAN_CARDS,
        "interval_labels": INTERVAL_LABELS,
        "signup_url":     _app_url("signup/"),
        "features":       features,
    })


def pricing(request):
    """Public pricing page — no church context needed."""
    from billing.models import SubscriptionPlan
    plans_with_prices = []
    for card in PLAN_CARDS:
        plan_obj = SubscriptionPlan.objects.filter(name=card["key"]).first()
        prices = {}
        if plan_obj:
            for interval in ("monthly", "annual", "biannual"):
                prices[interval] = plan_obj.price_for_interval(interval)
        plans_with_prices.append({**card, "prices": prices})

    return render(request, "marketing/pricing.html", {
        "plans": plans_with_prices,
        "interval_labels": INTERVAL_LABELS,
        "signup_url": _app_url("signup/"),
    })


def _app_url(path=""):
    """Build an absolute URL on the app domain."""
    domain = getattr(settings, "APP_DOMAIN", "workforce.church")
    scheme = "https" if not settings.DEBUG else "http"
    return f"{scheme}://{domain}/{path}"
