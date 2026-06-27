"""
founder/views.py

Superuser control panel served at founder.workforce.church.
All views require is_superuser=True — enforced by the @founder_required decorator.

Sections:
  /           — dashboard: stats, recent signups, plan breakdown
  /churches/  — list all churches, search/filter
  /churches/<pk>/ — church detail: edit, grant plan, deactivate
  /grant-plan/    — AJAX: grant any plan to any church
  /users/     — all CustomUser records
  /users/<pk>/— edit user, toggle superuser
"""

import json
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

# ── Guard decorator ───────────────────────────────────────────────────────────


def founder_required(view_fn):
    """Allow only authenticated Django superusers on the founder panel."""

    @wraps(view_fn)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            return HttpResponseForbidden(
                "<h2>403</h2><p>Superuser access required.</p>"
            )
        return view_fn(request, *args, **kwargs)

    return _wrapped


# ── Dashboard ─────────────────────────────────────────────────────────────────


@founder_required
def dashboard(request):
    from tenants.models import Church
    from billing.models import ChurchSubscription, SubscriptionPlan
    from accounts.models import CustomUser

    from django.db.models import Sum
    from billing.models import PaymentRecord

    # Top-line stats
    total_churches = Church.raw_objects.count()
    active_churches = Church.raw_objects.filter(is_active=True).count()
    total_users = CustomUser.objects.count()

    # Revenue
    revenue_total = (
        PaymentRecord.objects.filter(status="success").aggregate(t=Sum("amount_ngn"))[
            "t"
        ]
        or 0
    )
    revenue_30d = (
        PaymentRecord.objects.filter(
            status="success",
            created_at__gte=timezone.now() - timezone.timedelta(days=30),
        ).aggregate(t=Sum("amount_ngn"))["t"]
        or 0
    )

    # Monthly revenue series (last 6 months)
    from django.db.models.functions import TruncMonth

    revenue_by_month = list(
        PaymentRecord.objects.filter(status="success")
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(total=Sum("amount_ngn"))
        .order_by("-month")[:6]
    )
    revenue_chart = [
        {"month": r["month"].strftime("%b %Y"), "total": r["total"] or 0}
        for r in reversed(revenue_by_month)
    ]

    # Plan distribution
    plan_dist = (
        ChurchSubscription.objects.values("plan__name")
        .annotate(n=Count("id"))
        .order_by("-n")
    )

    # Recent signups (last 10 churches)
    recent = (
        Church.raw_objects.filter(is_active=True)
        .select_related("churchsubscription__plan")
        .order_by("-id")[:10]
    )

    # Trial churches expiring in the next 7 days
    expiring = ChurchSubscription.objects.filter(
        is_trial=True,
        is_active=True,
        trial_expires_at__lte=timezone.now() + timezone.timedelta(days=7),
        trial_expires_at__gte=timezone.now(),
    ).select_related("church")[:10]

    # Demo status
    from django.conf import settings as _settings

    demo_sub = getattr(_settings, "DEMO_SUBDOMAIN", "")
    demo_password = getattr(_settings, "DEMO_PASSWORD", "Demo@1234")
    demo_church = None
    if demo_sub:
        demo_church = Church.raw_objects.filter(subdomain=demo_sub).first()

    return render(
        request,
        "founder/dashboard.html",
        {
            "page_title": "Founder Panel",
            "total_churches": total_churches,
            "active_churches": active_churches,
            "total_users": total_users,
            "plan_dist": list(plan_dist),
            "recent_churches": recent,
            "expiring_trials": expiring,
            "revenue_total": revenue_total,
            "revenue_30d": revenue_30d,
            "revenue_chart": revenue_chart,
            "demo_sub": demo_sub,
            "demo_password": demo_password,
            "demo_church": demo_church,
        },
    )


# ── Church list ───────────────────────────────────────────────────────────────


@founder_required
def church_list(request):
    from tenants.models import Church

    q = (request.GET.get("q") or "").strip()
    status = request.GET.get("status", "active")
    plan = request.GET.get("plan", "")

    qs = Church.raw_objects.select_related("churchsubscription__plan").order_by("name")

    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)

    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(slug__icontains=q)
            | Q(subdomain__icontains=q)
            | Q(custom_domain__icontains=q)
        )

    if plan:
        qs = qs.filter(churchsubscription__plan__name=plan)

    from billing.models import SubscriptionPlan

    all_plans = SubscriptionPlan.objects.all()

    return render(
        request,
        "founder/church_list.html",
        {
            "page_title": "All Churches",
            "churches": qs,
            "q": q,
            "status": status,
            "plan": plan,
            "all_plans": all_plans,
            "total": qs.count(),
        },
    )


# ── Church detail ─────────────────────────────────────────────────────────────


@founder_required
def church_detail(request, pk):
    from django.conf import settings
    from tenants.models import Church, ChurchSetting
    from billing.models import ChurchSubscription, PaymentRecord, SubscriptionPlan
    from accounts.models import ChurchMember
    from units.models import ChurchUnit
    from tenants.models import Campus
    from billing.views import PLAN_CARDS, INTERVAL_LABELS
    from music.models import Track
    from music.services.catalog import PLAN_TRACK_PACKS

    church = get_object_or_404(Church.raw_objects, pk=pk)
    sub = getattr(church, "churchsubscription", None)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "edit":
            church.name = request.POST.get("name", church.name).strip()
            church.timezone = request.POST.get("timezone", church.timezone).strip()
            ar = request.POST.get("attendance_radius_km", "").strip()
            if ar:
                try:
                    church.attendance_radius_km = float(ar)
                except ValueError:
                    pass
            church.hide_platform_credit = (
                request.POST.get("hide_platform_credit") == "on"
            )
            church.save()
            messages.success(request, "Church profile updated.")

        elif action == "grant_plan":
            from billing.services import grant_founders_plan

            tier = request.POST.get("tier", "saas")
            grant_founders_plan(church, tier)
            messages.success(request, f"Founders {tier} plan granted to {church.name}.")

        elif action == "set_plan":
            from billing.models import SubscriptionPlan, ChurchSubscription
            from billing.services import get_subscription

            plan_name = request.POST.get("plan_name", "").strip()
            days = int(request.POST.get("days", 30))
            plan_obj = SubscriptionPlan.objects.filter(name=plan_name).first()
            if plan_obj:
                is_trial = plan_name == "trial"
                lifetime = "lifetime" in plan_name
                expires = (
                    None if lifetime else timezone.now() + timezone.timedelta(days=days)
                )
                trial_exp = expires if is_trial else None
                active_exp = None if is_trial else expires
                if sub:
                    sub.plan = plan_obj
                    sub.is_trial = is_trial
                    sub.is_active = True
                    sub.expires_at = active_exp
                    sub.trial_expires_at = trial_exp
                    sub.save()
                else:
                    ChurchSubscription.objects.create(
                        church=church,
                        plan=plan_obj,
                        is_trial=is_trial,
                        is_active=True,
                        expires_at=active_exp,
                        trial_expires_at=trial_exp,
                    )
                messages.success(request, f"Plan set to {plan_name}.")
            else:
                messages.error(request, f"Plan '{plan_name}' not found.")

        elif action == "toggle_active":
            church.is_active = not church.is_active
            church.save()
            state = "activated" if church.is_active else "deactivated"
            messages.success(request, f"Church {state}.")

        elif action == "set_campus_limit":
            limit = request.POST.get("campus_limit", "").strip()
            price = request.POST.get("campus_price_ngn", "").strip()
            if sub and sub.plan:
                if limit:
                    try:
                        sub.plan.campus_limit = int(limit)
                    except ValueError:
                        pass
                if price:
                    try:
                        sub.plan.campus_price_ngn = int(price)
                    except ValueError:
                        pass
                sub.plan.save()
                messages.success(request, "Campus limits updated.")

        elif action == "seed_music_tracks":
            from io import StringIO
            from django.core.management import call_command

            out = StringIO()
            try:
                call_command(
                    "dev_seed_christian_tracks",
                    church_id=church.id,
                    stdout=out,
                )
                messages.success(
                    request,
                    "Popular Christian tracks seeded. "
                    "New data is now available in Music library.",
                )
            except Exception as exc:
                messages.error(request, f"Track seed failed: {exc}")

        elif action == "seed_plan_content":
            from music.services.catalog import seed_tracks_for_plan

            active_plan = sub.plan.name if sub and sub.plan else "trial"
            try:
                track_stats = seed_tracks_for_plan(church, active_plan)
                bible_stats = {
                    "versions_created": 0,
                    "passages_created": 0,
                }  # seeding removed
                messages.success(
                    request,
                    (
                        f"Plan content refreshed for {active_plan}: "
                        f"{track_stats['created']} track(s) added, "
                        f"{bible_stats['passages_created']} Bible cache passage(s) warmed."
                    ),
                )
            except Exception as exc:
                messages.error(request, f"Plan content seed failed: {exc}")

        return redirect("founder:church_detail", pk=pk)

    from billing.models import SubscriptionPlan

    members_count = ChurchMember.raw_objects.filter(
        church=church, is_active=True
    ).count()
    units_count = ChurchUnit.raw_objects.filter(church=church, is_active=True).count()
    campuses_count = Campus.raw_objects.filter(church=church, is_active=True).count()
    payments = PaymentRecord.objects.filter(church=church).order_by("-created_at")[:20]
    all_plans = SubscriptionPlan.objects.all()
    active_plan = sub.plan.name if sub and sub.plan else "trial"

    track_count = Track.raw_objects.filter(church=church, is_active=True).count()
    bible_passage_count = 0  # bible seeding removed — handled by ai_skills
    bible_version_count = 0  # bible seeding removed

    track_pack = PLAN_TRACK_PACKS.get(active_plan, PLAN_TRACK_PACKS["trial"])
    bible_pack = {"versions": [], "references": []}  # PLAN_BIBLE_PACKS removed
    expected_track_limit = track_pack.get("limit", 0)
    expected_refs = len(bible_pack.get("references", []))
    warmed_versions = min(2, len(bible_pack.get("versions", [])))
    expected_warm_cache = expected_refs * warmed_versions
    plan_entitlements = [
        {
            "label": "Trial",
            "plan": "trial",
            "track_cap": PLAN_TRACK_PACKS.get("trial", {}).get("limit", 0),
            "bible_versions": 0,
        },
        {
            "label": "PRO",
            "plan": "saas",
            "track_cap": PLAN_TRACK_PACKS.get("saas", {}).get("limit", 0),
            "bible_versions": 0,
        },
        {
            "label": "Enterprise",
            "plan": "white_label",
            "track_cap": PLAN_TRACK_PACKS.get("white_label", {}).get("limit", 0),
            "bible_versions": 0,
        },
        {
            "label": "Founder",
            "plan": "founders_white_label_lifetime",
            "track_cap": PLAN_TRACK_PACKS.get("founders_white_label_lifetime", {}).get(
                "limit", 0
            ),
            "bible_versions": 0,
        },
    ]

    return render(
        request,
        "founder/church_detail.html",
        {
            "page_title": church.name,
            "church": church,
            "sub": sub,
            "members_count": members_count,
            "units_count": units_count,
            "campuses_count": campuses_count,
            "payments": payments,
            "all_plans": all_plans,
            "plan_cards": PLAN_CARDS,
            "active_plan_name": active_plan,
            "track_count": track_count,
            "bible_passage_count": bible_passage_count,
            "bible_version_count": bible_version_count,
            "expected_track_limit": expected_track_limit,
            "expected_warm_cache": expected_warm_cache,
            "is_production_env": not settings.DEBUG,
            "plan_entitlements": plan_entitlements,
        },
    )


# ── User list ─────────────────────────────────────────────────────────────────


@founder_required
def user_list(request):
    from accounts.models import CustomUser

    q = (request.GET.get("q") or "").strip()
    qs = CustomUser.objects.order_by("-date_joined")

    if q:
        qs = qs.filter(
            Q(full_name__icontains=q) | Q(email__icontains=q) | Q(username__icontains=q)
        )

    # Annotate with church memberships for display
    from accounts.models import ChurchMember

    user_churches = {}
    for cm in (
        ChurchMember.raw_objects.filter(user__in=qs[:200])
        .select_related("church", "user")
        .order_by("church__name")
    ):
        user_churches.setdefault(cm.user_id, []).append(cm.church.name)

    users_with_churches = []
    for u in qs[:200]:
        u.church_names = ", ".join(user_churches.get(u.id, []))
        users_with_churches.append(u)

    return render(
        request,
        "founder/user_list.html",
        {
            "page_title": "All Users",
            "users": users_with_churches,
            "q": q,
            "total": qs.count(),
        },
    )


# ── Toggle superuser ──────────────────────────────────────────────────────────


@founder_required
@require_POST
def toggle_superuser(request, pk):
    from accounts.models import CustomUser

    user = get_object_or_404(CustomUser, pk=pk)
    if user == request.user:
        return JsonResponse(
            {"ok": False, "error": "Cannot change your own superuser status."}
        )
    user.is_superuser = not user.is_superuser
    user.is_staff = user.is_superuser
    user.save(update_fields=["is_superuser", "is_staff"])
    return JsonResponse({"ok": True, "is_superuser": user.is_superuser})


# ── Seed demo ─────────────────────────────────────────────────────────────────


@founder_required
@require_POST
def run_seed_demo(request):
    """Trigger seed_demo via the panel. Returns JSON progress."""
    from io import StringIO
    from django.core.management import call_command
    from django.conf import settings as _settings

    reset = request.POST.get("reset") == "true"
    subdomain = request.POST.get("subdomain", "demo").strip()
    # Use panel-supplied password, or fall back to settings, or hardcoded default
    password = request.POST.get("password", "").strip() or getattr(
        _settings, "DEMO_PASSWORD", "Demo@1234"
    )
    buf = StringIO()
    try:
        call_command(
            "seed_demo", subdomain=subdomain, reset=reset, password=password, stdout=buf
        )
        return JsonResponse(
            {"ok": True, "output": buf.getvalue(), "password": password}
        )
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e), "output": buf.getvalue()})


@founder_required
@require_POST
def run_seed_music_catalog(request):
    """Seed curated music catalog across active churches."""
    from io import StringIO
    from django.core.management import call_command

    buf = StringIO()
    try:
        call_command("dev_seed_christian_tracks", stdout=buf)
        return JsonResponse({"ok": True, "output": buf.getvalue()})
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc), "output": buf.getvalue()})


# ── Root alias ────────────────────────────────────────────────────────────────


# ── Church create ─────────────────────────────────────────────────────────────


@founder_required
def church_create(request):
    """
    Create a new church directly from the founder panel — bypassing the public
    signup form. Lets the operator provision SaaS or lifetime churches without
    needing to go through trial first.
    """
    from django.conf import settings as _settings

    error = None

    if request.method == "POST":
        church_name = request.POST.get("church_name", "").strip()
        slug = request.POST.get("slug", "").strip().lower()
        admin_name = request.POST.get("admin_name", "").strip()
        admin_email = request.POST.get("admin_email", "").strip().lower()
        admin_password = request.POST.get("admin_password", "").strip()
        plan_key = request.POST.get("plan_key", "trial")
        days = int(request.POST.get("days", 30))
        subdomain = request.POST.get("subdomain", "").strip().lower() or None

        if not all([church_name, slug, admin_name, admin_email, admin_password]):
            error = "All fields except subdomain are required."
        else:
            try:
                from tenants.onboarding import provision_church

                church, user = provision_church(
                    church_name=church_name,
                    slug=slug,
                    admin_full_name=admin_name,
                    admin_email=admin_email,
                    admin_password=admin_password,
                )

                # Set subdomain if provided
                if subdomain:
                    from tenants.models import Church

                    if (
                        Church.raw_objects.filter(subdomain=subdomain)
                        .exclude(id=church.id)
                        .exists()
                    ):
                        error = f"Subdomain '{subdomain}' is already taken."
                    else:
                        church.subdomain = subdomain
                        church.save(update_fields=["subdomain"])

                # Assign the requested plan (override the trial that provision_church creates)
                if plan_key != "trial":
                    from billing.models import SubscriptionPlan, ChurchSubscription
                    from billing.services import grant_founders_plan
                    from django.utils import timezone
                    from datetime import timedelta

                    if "lifetime" in plan_key:
                        tier = "saas" if "saas" in plan_key else "white_label"
                        grant_founders_plan(church, tier)
                    else:
                        plan_obj = SubscriptionPlan.objects.filter(
                            name=plan_key
                        ).first()
                        if plan_obj:
                            sub = ChurchSubscription.objects.filter(
                                church=church
                            ).first()
                            lifetime = "lifetime" in plan_key
                            exp = (
                                None
                                if lifetime
                                else timezone.now() + timedelta(days=days)
                            )
                            if sub:
                                sub.plan = plan_obj
                                sub.is_trial = False
                                sub.is_active = True
                                sub.expires_at = exp
                                sub.trial_expires_at = None
                                sub.save()

                if not error:
                    messages.success(
                        request,
                        f"Church '{church_name}' created. Login: {admin_email} / {admin_password}",
                    )
                    return redirect("founder:church_detail", pk=church.pk)

            except ValueError as exc:
                error = str(exc)
            except Exception as exc:
                error = f"Unexpected error: {exc}"

    from billing.models import SubscriptionPlan

    all_plans = SubscriptionPlan.objects.all()

    return render(
        request,
        "founder/church_create.html",
        {
            "page_title": "Create Church",
            "error": error,
            "all_plans": all_plans,
        },
    )


@founder_required
def founder_home(request):
    """Root of founder panel — alias for dashboard."""
    return dashboard(request)


# ─────────────────────────────────────────────────────────────────────────────
# Model Admin — Django-admin-style CRUD for every registered model
# ─────────────────────────────────────────────────────────────────────────────

# Registry: app_label.ModelName → display config
# Each entry: (label, qs_fn, list_fields, search_fields)
# qs_fn receives no args and returns a base queryset.
_MODEL_REGISTRY = {
    "tenants.Church": {
        "label": "Churches",
        "app": "tenants",
        "model": "Church",
        "slug_field": "slug",
        "display_field": "name",
        "list_fields": ["name", "slug", "subdomain", "is_active", "created_at"],
        "search_fields": ["name", "slug", "subdomain", "custom_domain"],
    },
    "accounts.CustomUser": {
        "label": "Users",
        "app": "accounts",
        "model": "CustomUser",
        "slug_field": None,
        "display_field": "email",
        "list_fields": [
            "email",
            "full_name",
            "is_superuser",
            "is_active",
            "date_joined",
        ],
        "search_fields": ["email", "full_name", "username"],
    },
    "accounts.ChurchMember": {
        "label": "Church Members",
        "app": "accounts",
        "model": "ChurchMember",
        "slug_field": None,
        "display_field": "user",
        "list_fields": ["user", "church", "is_active", "created_at"],
        "search_fields": ["user__email", "user__full_name", "church__name"],
    },
    "units.ChurchUnit": {
        "label": "Units",
        "app": "units",
        "model": "ChurchUnit",
        "slug_field": "slug",
        "display_field": "name",
        "list_fields": ["name", "slug", "church", "unit_type", "is_active"],
        "search_fields": ["name", "church__name"],
    },
    "units.UnitMembership": {
        "label": "Unit Memberships",
        "app": "units",
        "model": "UnitMembership",
        "slug_field": None,
        "display_field": "workforce_member",
        "list_fields": ["unit", "workforce_member", "is_active", "created_at"],
        "search_fields": ["unit__name"],
    },
    "guests.GuestEntry": {
        "label": "Guests",
        "app": "guests",
        "model": "GuestEntry",
        "slug_field": "uid",
        "display_field": "full_name",
        "list_fields": ["full_name", "church", "status", "date_of_visit", "is_deleted"],
        "search_fields": ["full_name", "email", "phone", "church__name"],
    },
    "guests.MembershipApplication": {
        "label": "Membership Applications",
        "app": "guests",
        "model": "MembershipApplication",
        "slug_field": None,
        "display_field": "guest",
        "list_fields": ["guest", "status", "church", "applied_at"],
        "search_fields": ["guest__full_name", "church__name"],
    },
    "services.Event": {
        "label": "Events",
        "app": "services",
        "model": "Event",
        "slug_field": "uid",
        "display_field": "name",
        "list_fields": ["name", "church", "event_type", "date", "unit"],
        "search_fields": ["name", "church__name"],
    },
    "billing.SubscriptionPlan": {
        "label": "Subscription Plans",
        "app": "billing",
        "model": "SubscriptionPlan",
        "slug_field": "name",
        "display_field": "name",
        "list_fields": [
            "name",
            "monthly_price_ngn",
            "annual_discount_pct",
            "biannual_discount_pct",
            "campus_limit",
            "campus_price_ngn",
            "multi_campus",
            "white_label",
        ],
        "search_fields": ["name"],
    },
    "billing.ChurchSubscription": {
        "label": "Church Subscriptions",
        "app": "billing",
        "model": "ChurchSubscription",
        "slug_field": None,
        "display_field": "church",
        "list_fields": ["church", "plan", "is_trial", "is_active", "expires_at"],
        "search_fields": ["church__name", "plan__name"],
    },
    "billing.PaymentRecord": {
        "label": "Payment Records",
        "app": "billing",
        "model": "PaymentRecord",
        "slug_field": "reference",
        "display_field": "reference",
        "list_fields": ["reference", "church", "amount_ngn", "status", "created_at"],
        "search_fields": ["church__name", "reference"],
    },
    "lms.LMSCourse": {
        "label": "LMS Courses",
        "app": "lms",
        "model": "LMSCourse",
        "slug_field": "slug",
        "display_field": "title",
        "list_fields": ["title", "slug", "church", "course_type", "is_active"],
        "search_fields": ["title", "church__name"],
    },
    "lms.LMSEnrollment": {
        "label": "LMS Enrollments",
        "app": "lms",
        "model": "LMSEnrollment",
        "slug_field": None,
        "display_field": "member",
        "list_fields": ["member", "course", "status", "church"],
        "search_fields": ["member__user__email", "course__title"],
    },
    "workforce.WorkforceMember": {
        "label": "Workforce Members",
        "app": "workforce",
        "model": "WorkforceMember",
        "slug_field": None,
        "display_field": "member",
        "list_fields": ["member", "church", "is_active", "created_at"],
        "search_fields": ["member__user__email", "church__name"],
    },
    "workforce.WorkforceTraineeProfile": {
        "label": "Trainee Profiles",
        "app": "workforce",
        "model": "WorkforceTraineeProfile",
        "slug_field": None,
        "display_field": "member",
        "list_fields": ["member", "church", "is_active", "reason"],
        "search_fields": ["member__user__email", "church__name"],
    },
    "music.Track": {
        "label": "Music Tracks",
        "app": "music",
        "model": "Track",
        "slug_field": "uid",
        "display_field": "title",
        "list_fields": ["title", "artist", "church", "original_key", "is_active"],
        "search_fields": ["title", "artist", "church__name"],
    },
    "music.Setlist": {
        "label": "Setlists",
        "app": "music",
        "model": "Setlist",
        "slug_field": "uid",
        "display_field": "title",
        "list_fields": ["title", "church", "unit", "finalized", "created_at"],
        "search_fields": ["title", "church__name"],
    },
    "music.RehearsalSession": {
        "label": "Rehearsal Sessions",
        "app": "music",
        "model": "RehearsalSession",
        "slug_field": "uid",
        "display_field": "event",
        "list_fields": ["uid", "event", "unit", "church", "completed"],
        "search_fields": ["event__name", "church__name"],
    },
    "media.ServicePresentation": {
        "label": "Service Presentations",
        "app": "media",
        "model": "ServicePresentation",
        "slug_field": "uid",
        "display_field": "title",
        "list_fields": ["title", "church", "unit", "is_live", "created_at"],
        "search_fields": ["title", "church__name"],
    },
    "notifications.Notification": {
        "label": "Notifications",
        "app": "notifications",
        "model": "Notification",
        "slug_field": None,
        "display_field": "verb",
        "list_fields": ["recipient", "verb", "read", "created_at"],
        "search_fields": ["verb", "recipient__email"],
    },
    "automation.AutomationLog": {
        "label": "Automation Logs",
        "app": "automation",
        "model": "AutomationLog",
        "slug_field": None,
        "display_field": "action",
        "list_fields": ["church", "action", "status", "created_at"],
        "search_fields": ["action", "church__name"],
    },
}


def _get_model_class(app_label, model_name):
    from django.apps import apps as django_apps

    try:
        return django_apps.get_model(app_label, model_name)
    except LookupError:
        return None


def _get_qs(cfg, search=None):
    """Return base queryset for a registry entry, with optional search."""
    Model = _get_model_class(cfg["app"], cfg["model"])
    if Model is None:
        return None
    # Use raw_objects if available (tenant-scoped models), else objects
    manager = getattr(Model, "raw_objects", Model.objects)
    qs = manager.all().order_by("-id")
    if search and cfg.get("search_fields"):
        from django.db.models import Q as DQ

        q_obj = DQ()
        for field in cfg["search_fields"]:
            q_obj |= DQ(**{f"{field}__icontains": search})
        qs = qs.filter(q_obj)
    return qs


def _best_identifier(obj, cfg):
    """Prefer human-readable identifiers over raw PKs."""
    for key in (
        cfg.get("slug_field"),
        cfg.get("display_field"),
        "name",
        "title",
        "full_name",
        "email",
        "reference",
    ):
        if not key:
            continue
        try:
            value = getattr(obj, key, None)
            if value:
                return str(value)
        except Exception:
            continue

    # Church-linked models should surface church names by default.
    church = getattr(obj, "church", None)
    if church:
        try:
            return f"{obj.__class__.__name__} — {church.name}"
        except Exception:
            pass

    return str(obj) if str(obj).strip() else str(obj.pk)


@founder_required
def model_list(request, model_key):
    """Generic model list view — replaces django admin changelist."""
    key = model_key.replace("-", ".")
    cfg = _MODEL_REGISTRY.get(key)
    if not cfg:
        return HttpResponseForbidden("Model not registered in founder panel.")

    search = request.GET.get("q", "").strip()
    page = max(1, int(request.GET.get("p", 1)))
    per_page = 50

    qs = _get_qs(cfg, search)
    if qs is None:
        return render(
            request,
            "founder/model_list.html",
            {
                "page_title": cfg["label"],
                "cfg": cfg,
                "model_key": model_key,
                "error": f"Model {cfg['app']}.{cfg['model']} not found.",
                "objects": [],
                "total": 0,
            },
        )

    total = qs.count()
    offset = (page - 1) * per_page
    objects = list(qs[offset : offset + per_page])

    # Build rows: list of (pk, identifier_label, [(field_name, display_value)])
    rows = []
    for obj in objects:
        pk = obj.pk
        identifier = _best_identifier(obj, cfg)
        cells = []
        for field in cfg["list_fields"]:
            try:
                val = obj
                for part in field.split("__"):
                    val = getattr(val, part, "—")
                    if callable(val):
                        val = val()
            except Exception:
                val = "—"
            cells.append((field, str(val)[:80] if val is not None else "—"))
        rows.append((pk, str(identifier), cells))

    num_pages = max(1, (total + per_page - 1) // per_page)

    return render(
        request,
        "founder/model_list.html",
        {
            "page_title": cfg["label"],
            "cfg": cfg,
            "model_key": model_key,
            "rows": rows,
            "total": total,
            "search": search,
            "page": page,
            "num_pages": num_pages,
            "has_prev": page > 1,
            "has_next": page < num_pages,
        },
    )


@founder_required
def model_detail(request, model_key, pk):
    """Generic model detail — shows all fields, inline edit for simple types, delete."""
    key = model_key.replace("-", ".")
    cfg = _MODEL_REGISTRY.get(key)
    if not cfg:
        return HttpResponseForbidden("Model not registered.")

    Model = _get_model_class(cfg["app"], cfg["model"])
    if Model is None:
        return HttpResponseForbidden("Model not found.")

    manager = getattr(Model, "raw_objects", Model.objects)
    obj = get_object_or_404(manager, pk=pk)

    msg_ok = msg_err = None

    if request.method == "POST":
        action = request.POST.get("_action")

        if action == "delete":
            try:
                repr_str = str(obj)
                obj.delete()
                messages.success(request, f"Deleted: {repr_str}")
                return redirect("founder:model_list", model_key=model_key)
            except Exception as exc:
                msg_err = f"Delete failed: {exc}"

        elif action == "toggle_active":
            if hasattr(obj, "is_active"):
                obj.is_active = not obj.is_active
                obj.save(update_fields=["is_active"])
                msg_ok = f"is_active set to {obj.is_active}"
            else:
                msg_err = "This model has no is_active field."

        elif action == "toggle_superuser" and hasattr(obj, "is_superuser"):
            if obj == request.user:
                msg_err = "Cannot change your own superuser status."
            else:
                obj.is_superuser = not obj.is_superuser
                obj.is_staff = obj.is_superuser
                obj.save(update_fields=["is_superuser", "is_staff"])
                msg_ok = f"is_superuser set to {obj.is_superuser}"

        elif action == "set_field":
            field_name = request.POST.get("field_name", "")
            field_value = request.POST.get("field_value", "")
            # String fields — set directly
            allowed_str = {
                "name",
                "email",
                "timezone",
                "subdomain",
                "custom_domain",
                "slug",
                "title",
                "body",
                "notes",
                "status",
                "priority",
                "description",
                "recording_url",
                "reference",
            }
            # Integer/numeric fields — cast before saving
            allowed_int = {
                "monthly_price_ngn",
                "annual_discount_pct",
                "biannual_discount_pct",
                "campus_price_ngn",
                "campus_limit",
                "font_size",
                "tempo",
                "duration_seconds",
                "attendance_radius_km",
            }
            # Float fields
            allowed_float = {"overlay_opacity", "latitude", "longitude"}

            if field_name in allowed_str and hasattr(obj, field_name):
                try:
                    setattr(obj, field_name, field_value)
                    obj.save(update_fields=[field_name])
                    msg_ok = f"{field_name} updated to '{field_value}'."
                except Exception as exc:
                    msg_err = str(exc)
            elif field_name in allowed_int and hasattr(obj, field_name):
                try:
                    setattr(obj, field_name, int(field_value))
                    obj.save(update_fields=[field_name])
                    msg_ok = f"{field_name} updated to {int(field_value)}."
                except (ValueError, TypeError):
                    msg_err = f"{field_name} must be a whole number."
                except Exception as exc:
                    msg_err = str(exc)
            elif field_name in allowed_float and hasattr(obj, field_name):
                try:
                    setattr(obj, field_name, float(field_value))
                    obj.save(update_fields=[field_name])
                    msg_ok = f"{field_name} updated to {float(field_value)}."
                except (ValueError, TypeError):
                    msg_err = f"{field_name} must be a number."
                except Exception as exc:
                    msg_err = str(exc)
            else:
                msg_err = (
                    f"Field '{field_name}' cannot be edited here. "
                    f"Use Django Admin for complex fields."
                )

        elif action == "update_plan_pricing":
            # Dedicated action for SubscriptionPlan pricing — safe, typed, logged
            from billing.models import SubscriptionPlan as _SP

            if not isinstance(obj, _SP):
                msg_err = "This action is only valid for SubscriptionPlan records."
            else:
                changed = []
                try:
                    monthly = request.POST.get("monthly_price_ngn", "").strip()
                    campus_price = request.POST.get("campus_price_ngn", "").strip()
                    campus_limit = request.POST.get("campus_limit", "").strip()
                    annual_discount = request.POST.get(
                        "annual_discount_pct", ""
                    ).strip()
                    biannual_discount = request.POST.get(
                        "biannual_discount_pct", ""
                    ).strip()
                    description = request.POST.get("description", "").strip()
                    multi_campus = request.POST.get("multi_campus") == "on"
                    white_label = request.POST.get("white_label") == "on"

                    update_fields = ["multi_campus", "white_label"]
                    obj.multi_campus = multi_campus
                    obj.white_label = white_label
                    changed += ["multi_campus", "white_label"]

                    if description:
                        obj.description = description
                        update_fields.append("description")
                        changed.append("description")
                    if monthly != "":
                        obj.monthly_price_ngn = int(monthly)
                        update_fields.append("monthly_price_ngn")
                        changed.append(f"monthly_price_ngn=₦{int(monthly):,}")
                    if campus_price != "":
                        obj.campus_price_ngn = int(campus_price)
                        update_fields.append("campus_price_ngn")
                        changed.append(f"campus_price_ngn=₦{int(campus_price):,}")
                    if campus_limit != "":
                        obj.campus_limit = int(campus_limit)
                        update_fields.append("campus_limit")
                        changed.append(f"campus_limit={int(campus_limit)}")
                    if annual_discount != "":
                        obj.annual_discount_pct = max(0, min(int(annual_discount), 100))
                        update_fields.append("annual_discount_pct")
                        changed.append(
                            f"annual_discount_pct={obj.annual_discount_pct}%"
                        )
                    if biannual_discount != "":
                        obj.biannual_discount_pct = max(
                            0, min(int(biannual_discount), 100)
                        )
                        update_fields.append("biannual_discount_pct")
                        changed.append(
                            f"biannual_discount_pct={obj.biannual_discount_pct}%"
                        )

                    obj.save(update_fields=update_fields)
                    msg_ok = f"Plan '{obj.name}' updated: {', '.join(changed)}."
                except (ValueError, TypeError) as exc:
                    msg_err = f"Invalid value: {exc}. Prices and limits must be whole numbers."
                except Exception as exc:
                    msg_err = str(exc)

    # Build field table: all concrete fields
    field_rows = []
    try:
        for f in obj._meta.get_fields():
            if not hasattr(f, "column"):
                continue  # skip reverse relations
            try:
                val = getattr(obj, f.name, "—")
                if callable(val):
                    val = val()
            except Exception:
                val = "—"
            field_rows.append(
                {
                    "name": f.name,
                    "verbose": getattr(f, "verbose_name", f.name),
                    "value": str(val)[:500] if val is not None else "—",
                    "editable": f.name
                    in {
                        "name",
                        "email",
                        "timezone",
                        "subdomain",
                        "custom_domain",
                        "slug",
                        "title",
                        "body",
                        "notes",
                        "status",
                        "priority",
                        "description",
                        "recording_url",
                        "reference",
                        "monthly_price_ngn",
                        "annual_discount_pct",
                        "biannual_discount_pct",
                        "campus_price_ngn",
                        "campus_limit",
                        "font_size",
                        "overlay_opacity",
                        "latitude",
                        "longitude",
                    },
                }
            )
    except Exception:
        pass

    return render(
        request,
        "founder/model_detail.html",
        {
            "page_title": f"{cfg['label']} — {str(obj)[:40]}",
            "cfg": cfg,
            "model_key": model_key,
            "obj": obj,
            "obj_str": str(obj),
            "field_rows": field_rows,
            "msg_ok": msg_ok,
            "msg_err": msg_err,
            "has_active": hasattr(obj, "is_active"),
            "has_superuser": hasattr(obj, "is_superuser"),
            "django_admin_url": f"/admin/{cfg['app']}/{cfg['model'].lower()}/{pk}/change/",
            "is_plan": cfg["app"] == "billing" and cfg["model"] == "SubscriptionPlan",
        },
    )


@founder_required
def model_registry_index(request):
    """Index of all registered models — the founder panel's 'app index'."""
    from django.apps import apps as django_apps

    sections = {}
    for key, cfg in _MODEL_REGISTRY.items():
        app = cfg["app"]
        Model = _get_model_class(cfg["app"], cfg["model"])
        count = "?"
        if Model:
            try:
                manager = getattr(Model, "raw_objects", Model.objects)
                count = manager.count()
            except Exception:
                count = "?"
        sections.setdefault(app, []).append(
            {
                "key": key.replace(".", "-"),
                "label": cfg["label"],
                "count": count,
            }
        )

    return render(
        request,
        "founder/model_registry.html",
        {
            "page_title": "Model Registry",
            "sections": sections,
        },
    )


@founder_required
def impersonate_user(request, pk):
    """
    Log in as any user without their password.
    Sets a session flag so the banner can show 'Impersonating as X'.
    GET: show confirmation. POST: do it.
    """
    from accounts.models import CustomUser

    target = get_object_or_404(CustomUser, pk=pk)

    if request.method == "POST":
        from django.contrib.auth import login as auth_login

        # Store the original superuser id so they can return
        request.session["_impersonate_from"] = request.user.pk
        request.session["_impersonate_as"] = target.pk
        auth_login(request, target, backend="django.contrib.auth.backends.ModelBackend")
        messages.warning(
            request,
            f"⚠️ You are now impersonating {target.email}. "
            "Go to /founder/impersonate/end/ to return.",
        )
        return redirect("/")

    return render(
        request,
        "founder/impersonate_confirm.html",
        {
            "page_title": f"Impersonate {target.email}",
            "target": target,
        },
    )


@founder_required
def impersonate_end(request):
    """Return to the original superuser account after impersonation."""
    from accounts.models import CustomUser

    original_pk = request.session.pop("_impersonate_from", None)
    request.session.pop("_impersonate_as", None)
    if original_pk:
        from django.contrib.auth import login as auth_login

        original = get_object_or_404(CustomUser, pk=original_pk)
        auth_login(
            request, original, backend="django.contrib.auth.backends.ModelBackend"
        )
        messages.success(request, f"Returned to {original.email}.")
    return redirect("founder:home")


@founder_required
def system_health(request):
    """System health dashboard — Redis, DB, Cloudinary, Channels, scheduler."""
    import django
    from django.conf import settings as _settings
    from django.db import connections

    checks = []

    # Database
    try:
        from django.db import connection

        with connection.cursor() as c:
            c.execute("SELECT 1")
        checks.append(("PostgreSQL", "ok", "Connected"))
    except Exception as e:
        checks.append(("PostgreSQL", "error", str(e)))

    # Redis
    try:
        import redis

        r = redis.from_url(_settings.REDIS_URL)
        r.ping()
        checks.append(("Redis", "ok", f"Connected · {_settings.REDIS_URL[:30]}…"))
    except Exception as e:
        checks.append(("Redis", "error", str(e)))

    # Cloudinary
    try:
        import cloudinary

        cfg = cloudinary.config()
        if cfg.cloud_name:
            checks.append(("Cloudinary", "ok", f"cloud={cfg.cloud_name}"))
        else:
            checks.append(("Cloudinary", "warn", "CLOUDINARY_CLOUD_NAME not set"))
    except Exception as e:
        checks.append(("Cloudinary", "error", str(e)))

    # AI keys
    anthropic_key = bool(getattr(_settings, "ANTHROPIC_API_KEY", ""))
    checks.append(
        (
            "Anthropic API",
            "ok" if anthropic_key else "warn",
            "Key set" if anthropic_key else "ANTHROPIC_API_KEY not configured",
        )
    )

    openai_key = bool(getattr(_settings, "OPENAI_API_KEY", ""))
    checks.append(
        (
            "OpenAI API",
            "ok" if openai_key else "warn",
            "Key set" if openai_key else "OPENAI_API_KEY not configured",
        )
    )

    # Django version
    checks.append(("Django", "ok", django.__version__))

    # Debug mode
    debug = getattr(_settings, "DEBUG", False)
    checks.append(
        (
            "DEBUG mode",
            "warn" if debug else "ok",
            "ON — disable in production" if debug else "Off",
        )
    )

    return render(
        request,
        "founder/system_health.html",
        {
            "page_title": "System Health",
            "checks": checks,
        },
    )
