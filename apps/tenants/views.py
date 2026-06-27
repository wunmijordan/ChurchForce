"""
tenants/views.py

Public-facing tenant views:
    signup                  — self-serve church registration (path-slug first)
    trial_expired           — wall shown when subscription is missing/invalid
    subscription_inactive   — wall shown when a subscription is deactivated
"""

import logging
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from django.utils.text import slugify

from .forms import ChurchSignupForm
from .models import ChurchSetting, Campus, CampusMembership
from units.forms import UnitQuickCreateForm
from permissions.registry import WORKFORCE_TIER_CHOICES, UNIT_TIER_CHOICES
from workforce.models import ChatSettings
from units.models import ChatRoom
from core.scheduler import get_scheduler
from core.tasks import send_welcome_email  # Ensure you created this task
from tenants.middleware import ACTIVE_CHURCH_SESSION_KEY

logger = logging.getLogger(__name__)


def _app_origin(request):
    """
    Build the app host origin robustly for both production domains and local
    multi-host dev setups such as `workforce.localhost:8000`.
    """
    scheme = request.scheme or "http"
    configured_hosts = list(getattr(settings, "APP_DOMAINS", []) or [])
    current_host = request.get_host()
    current_host_only = current_host.split(":")[0]

    for configured in configured_hosts:
        conf_host_only = configured.split(":")[0]
        if conf_host_only == current_host_only:
            return f"{scheme}://{configured}"

    if configured_hosts:
        configured = (
            configured_hosts[-1] if len(configured_hosts) > 1 else configured_hosts[0]
        )
        if ":" not in configured and ":" in current_host:
            configured = f"{configured}:{request.get_port()}"
        return f"{scheme}://{configured}"

    return f"{scheme}://{current_host}"


def _tenant_admin(request, church=None):
    """
    Tenant-admin check used by tenant-facing views.

    Mirrors the wider app behavior by allowing the ChurchMember admin flag as a
    compatibility fallback when the resolver path has not been fully hydrated yet.
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return False
    if request.user.is_superuser:
        return True

    church = church or getattr(request, "church", None)
    perms = getattr(request, "permissions", None)
    if perms and perms.can("dashboard.admin"):
        return True

    if not church:
        return False

    from accounts.models import ChurchMember

    return ChurchMember.raw_objects.filter(
        church=church,
        user=request.user,
        is_active=True,
        is_admin=True,
    ).exists()


def _safe_schedule_welcome(scheduler, user, church, username=None, password=None):
    if not scheduler:
        return

    scheme = "https" if not settings.DEBUG else "http"
    app_hosts = list(getattr(settings, "APP_DOMAINS", []) or [])
    app_domain = (
        app_hosts[-1]
        if app_hosts
        else ((getattr(settings, "APP_DOMAIN", "") or "workforce.church").strip())
    )
    login_path = (
        f"/{church.slug}/accounts/login/?next=/{church.slug}/post-login/&signup=1"
    )
    login_url = f"{scheme}://{app_domain}{login_path}"

    try:
        scheduler.add_job(
            send_welcome_email,
            trigger="date",
            args=[
                user.email,
                church.name,
                user.full_name,
                login_url,
                username,
                password,
            ],
            id=f"welcome_email_{user.id}",
            replace_existing=True,
        )
    except Exception:
        logger.exception("Welcome email scheduling failed")


def signup(request):
    """
    Public self-serve signup for ChurchForce on Trial Plan.

    1. Validates unique URL, email, and password strength via Form.
    2. Provisions Church, Admin User, and Settings in an atomic transaction.
    3. Schedules a background welcome email.
    4. Logs the user in and redirects to pricing.
    """
    if request.user.is_authenticated:
        return redirect("post_login_redirect")

    form = ChurchSignupForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data

        try:
            # Wrap everything in a transaction so we don't get 'partial' signups
            with transaction.atomic():
                from tenants.onboarding import provision_church

                church, user = provision_church(
                    church_name=cd["church_name"],
                    slug=cd["url_handle"],
                    admin_full_name=cd["full_name"],
                    admin_email=cd["email"],
                    admin_password=cd["password"],
                )

                # Schedule the background email
                # We use on_commit to ensure email only sends if DB save succeeds
                scheduler = get_scheduler()
                transaction.on_commit(
                    lambda: _safe_schedule_welcome(
                        scheduler,
                        user,
                        church,
                        username=user.username,
                        password=cd["password"],
                    )
                )

            # Check if user came from marketing page with a plan intent
            plan_intent = request.GET.get("intent", "").strip()
            if plan_intent in ("saas", "white_label"):
                messages.success(
                    request,
                    f"Welcome to ChurchForce, {church.name}! "
                    f"Your workspace is ready — choose your routing option anytime.",
                )
            else:
                messages.success(
                    request,
                    f"Welcome to ChurchForce, {church.name}! "
                    f"Your free workspace is ready at /{church.slug}/.",
                )

            if settings.DEBUG:
                messages.info(
                    request,
                    f"DEV login: email {user.email}, username {user.username}. Use the password you just set.",
                )

            # Redirect to slug-aware post-login on the app host.
            app_origin = _app_origin(request)
            app_domain = app_origin.split("://", 1)[1]
            app_host = app_domain.split(":")[0]
            req_host = request.get_host().split(":")[0]
            post_login_path = f"/{church.slug}/post-login/"
            _intent_qs = f"&intent={plan_intent}" if plan_intent else ""
            login_url = f"{app_origin}/{church.slug}/accounts/login/?next={post_login_path}&signup=1{_intent_qs}"

            # If we're on the marketing host, send the subscriber to tenant login
            # on the app host. Do not keep them on the signup domain.
            if req_host != app_host:
                return redirect(login_url)

            # Same host — safe to log in and continue directly.
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            request.session[ACTIVE_CHURCH_SESSION_KEY] = church.slug
            # If a plan intent was passed from the marketing pricing page, send
            # straight to billing/pricing so they can complete the upgrade.
            if plan_intent in ("saas", "white_label"):
                pricing_url = f"{app_origin}/{church.slug}/billing/pricing/?highlight={plan_intent}"
                return redirect(pricing_url)
            return redirect(f"{app_origin}{post_login_path}")
        except ValueError as exc:
            # Catches business logic errors (e.g. duplicate slug found in provision_church)
            messages.error(request, str(exc))
        except Exception as e:
            logger.exception("Church signup failed")
            if settings.DEBUG:
                messages.error(request, f"Setup error: {e}")
            else:
                messages.error(
                    request,
                    "An unexpected error occurred during setup. Please try again or contact support.",
                )

    return render(request, "tenants/signup.html", {"form": form})


def signup_url_check(request):
    handle = request.GET.get("url_handle", "")
    ok, error = ChurchSignupForm.is_url_handle_available(handle)
    return JsonResponse({"ok": ok, "normalized": slugify(handle or ""), "error": error})


def signup_credentials(request):
    credentials = request.session.pop("signup_credentials", None)
    next_url = request.session.pop("signup_credentials_next", None)

    if not credentials or not next_url:
        return redirect("tenants:signup")

    return render(
        request,
        "tenants/signup_credentials.html",
        {
            "credentials": credentials,
            "next_url": next_url,
            "page_title": "Signup Credentials",
        },
    )


def trial_expired(request):
    """
    Shown when a trial has ended and the tenant hasn't upgraded.
    Links to the billing pricing page.

    Template: tenants/trial_expired.html
    """
    church = getattr(request, "church", None)
    return render(
        request,
        "tenants/trial_expired.html",
        {
            "church": church,
            "pricing_url": "billing:pricing",
        },
    )


def subscription_inactive(request):
    """
    Shown when a paid subscription lapses (non-trial).

    Template: tenants/subscription_inactive.html
    """
    church = getattr(request, "church", None)
    return render(
        request,
        "tenants/subscription_inactive.html",
        {
            "church": church,
            "portal_url": "billing:portal",
        },
    )


@login_required
def account_page(request):
    """
    Unified Account page — three left-side tabs:
      1. Church Profile  — read-only rich overview of the church
      2. Routing           — current URL style + DNS setup (section=subscription)
      3. Settings        — existing church_settings content, opening
                           with its own sub-tabs on the right

    URL: /account/  (or /account/?section=profile|subscription|settings)
    """
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    is_admin = _tenant_admin(request, church)
    if not is_admin:
        messages.error(request, "Admin access required.")
        return redirect("dashboard:dashboard")

    # ── Active left-side section ──────────────────────────────────────────
    section = request.GET.get("section", "profile")

    # ── Church Profile data ───────────────────────────────────────────────
    settings_obj, _ = ChurchSetting.objects.get_or_create(church=church)

    from units.models import ChurchUnit, UnitMembership
    from workforce.models import WorkforceMember
    from accounts.models import ChurchMember
    from lms.models import LMSCourse
    from guests.models import MembershipTrack

    total_members = ChurchMember.raw_objects.filter(
        church=church, is_active=True
    ).count()
    total_workforce = WorkforceMember.raw_objects.filter(
        church=church, is_active=True
    ).count()
    total_units = ChurchUnit.raw_objects.filter(church=church, is_active=True).count()
    total_campuses = Campus.raw_objects.filter(church=church, is_active=True).count()
    total_courses = LMSCourse.raw_objects.filter(church=church, is_active=True).count()
    units_list = ChurchUnit.raw_objects.filter(church=church, is_active=True).order_by(
        "unit_type", "name"
    )
    campus_list = Campus.raw_objects.filter(church=church, is_active=True).order_by(
        "name"
    )
    tracks_list = (
        MembershipTrack.raw_objects.filter(church=church, is_active=True)
        .select_related("lms_course")
        .prefetch_related("lms_courses", "steps__requirement__lms_courses")
        .order_by("name")
    )
    default_membership_track = tracks_list.filter(is_default=True).first()

    campus_cfg = settings_obj.campus_growth_stages or {}
    if isinstance(campus_cfg, list):
        campus_stages_list = campus_cfg
    else:
        campus_stages_list = sorted(
            campus_cfg.get("stages", []), key=lambda x: x.get("order", 0)
        )

    profile_ctx = {
        "total_members": total_members,
        "total_workforce": total_workforce,
        "total_units": total_units,
        "total_campuses": total_campuses,
        "total_courses": total_courses,
        "units_list": units_list,
        "campus_list": campus_list,
        "tracks_list": tracks_list,
        "campus_stages_list": campus_stages_list,
    }

    # ── Subscription data (mirrors billing.views.portal) ─────────────────
    from billing.models import PaymentRecord
    from billing.services import (
        get_subscription,
        get_plan_name,
        is_founders_plan,
        subscription_valid,
        get_routing_display_context,
    )
    from billing.views import PLAN_CARDS, INTERVAL_LABELS

    sub = get_subscription(church)
    recent_payments = PaymentRecord.objects.filter(church=church).order_by(
        "-created_at"
    )[:10]

    billing_ctx = {
        "subscription": sub,
        "plan_name": get_plan_name(church),
        "is_founders": is_founders_plan(church),
        "is_valid": subscription_valid(church),
        "days_remaining": sub.days_until_expiry() if sub else None,
        "recent_payments": recent_payments,
        "plans": PLAN_CARDS,
        "interval_labels": INTERVAL_LABELS,
        **get_routing_display_context(church),
    }

    # ── Settings data (mirrors church_settings) ───────────────────────────
    from tenants.forms import (
        GeneralSettingsForm,
        GuestPipelineSettingsForm,
        LMSSettingsForm,
        WorkforceSettingsForm,
        DisplaySettingsForm,
        FeatureFlagsForm,
    )
    from units.forms import UnitQuickCreateForm
    from guests.models import VisitChannel, VisitPurpose, ChurchService
    from workforce.models import WorkforceStage, WorkforceRole
    from permissions.models import UnitRole

    form_classes = {
        "general": GeneralSettingsForm,
        "feature_flags": FeatureFlagsForm,
        "pipeline": GuestPipelineSettingsForm,
        "trainings": LMSSettingsForm,
        "workforce": WorkforceSettingsForm,
        "display": DisplaySettingsForm,
    }
    forms_map = {}
    active_settings_tab = request.GET.get("tab", "general")

    if request.method == "POST" and section == "settings":
        tab = request.POST.get("tab", "general")

        if tab == "unit_create":
            unit_form_inst = UnitQuickCreateForm(
                request.POST, church=church, settings=settings_obj
            )
            if unit_form_inst.is_valid():
                unit = unit_form_inst.save()
                messages.success(request, f"Unit '{unit.name}' created.")
            else:
                messages.error(request, "Please correct the unit form errors.")
            return redirect(f"{request.path}?section=settings&tab=general")

        # ── Trainings tab: track / step sub-actions ───────────────────────
        if tab == "trainings":
            action = request.POST.get("action", "")

            if action == "create_course":
                from lms.models import LMSCourse as _LC
                from units.models import ChurchUnit as _CU

                course_title = request.POST.get("course_title", "").strip()
                if not course_title:
                    messages.error(request, "Course title is required.")
                    return redirect(f"{request.path}?section=settings&tab=trainings")

                course_type = request.POST.get("course_type", "unit_training")
                delivery = request.POST.get("delivery_mode", "physical")
                passing = int(request.POST.get("passing_score", 70) or 70)
                max_retakes = int(request.POST.get("max_retakes", 2) or 2)
                allow_retake = bool(request.POST.get("allow_retake"))
                strict_seq = bool(request.POST.get("strict_sequence"))
                unit_id = request.POST.get("unit_id", "").strip()
                member = getattr(request, "member", None)

                unit = None
                if unit_id and unit_id.isdigit():
                    from units.models import ChurchUnit as _CU2

                    unit = _CU2.raw_objects.filter(
                        church=church, id=int(unit_id)
                    ).first()

                _LC.raw_objects.create(
                    church=church,
                    title=course_title,
                    course_type=course_type,
                    delivery_mode=delivery,
                    passing_score=passing,
                    max_retakes=max_retakes,
                    allow_retake=allow_retake,
                    strict_sequence=strict_seq,
                    unit=unit,
                    created_by=member,
                    is_active=True,
                )
                messages.success(request, f"Course '{course_title}' created.")
                return redirect(f"{request.path}?section=settings&tab=trainings")

            if action == "create_track":
                from guests.models import MembershipTrack as _MT

                track_name = request.POST.get("track_name", "").strip()
                description = request.POST.get("track_description", "").strip()
                threshold = int(request.POST.get("attendance_threshold", 4) or 4)
                window = int(request.POST.get("attendance_window_weeks", 6) or 6)
                lms_course_ids = [
                    int(cid)
                    for cid in request.POST.getlist("track_lms_course_ids")
                    if str(cid).isdigit()
                ]
                if not lms_course_ids:
                    legacy_track_course = request.POST.get(
                        "track_lms_course_id", ""
                    ).strip()
                    if legacy_track_course.isdigit():
                        lms_course_ids = [int(legacy_track_course)]
                is_default = bool(request.POST.get("track_is_default"))
                if track_name:
                    from lms.models import LMSCourse as _LC

                    selected_courses_qs = _LC.raw_objects.filter(
                        church=church, id__in=lms_course_ids, is_active=True
                    ).select_related("membership_track")
                    selected_courses = list(selected_courses_qs)
                    primary_course = selected_courses[0] if selected_courses else None
                    moved_from_track_ids = {
                        course.membership_track_id
                        for course in selected_courses
                        if course.membership_track_id
                    }
                    if is_default:
                        _MT.raw_objects.filter(church=church).update(is_default=False)
                    track, created = _MT.raw_objects.get_or_create(
                        church=church,
                        name=track_name,
                        defaults=dict(
                            description=description,
                            attendance_threshold=threshold,
                            attendance_window_weeks=window,
                            is_default=is_default,
                            lms_course=primary_course,
                            is_active=True,
                        ),
                    )
                    if created:
                        if selected_courses:
                            for course in selected_courses:
                                course.membership_track = track
                                course.save(update_fields=["membership_track"])
                            for old_track_id in moved_from_track_ids:
                                old_track = _MT.raw_objects.filter(
                                    church=church, id=old_track_id
                                ).first()
                                if old_track:
                                    remaining = list(old_track.lms_courses.all())
                                    old_track.lms_course = (
                                        remaining[0] if remaining else None
                                    )
                                    old_track.save(update_fields=["lms_course"])
                        messages.success(request, f"Track '{track_name}' created.")
                    else:
                        messages.warning(
                            request, f"A track named '{track_name}' already exists."
                        )
                else:
                    messages.error(request, "Track name is required.")
                return redirect(f"{request.path}?section=settings&tab=trainings")

            if action == "add_step":
                from guests.models import (
                    MembershipTrack as _MT,
                    MembershipTrackStep as _MTS,
                    StepRequirement as _SR,
                )
                from lms.models import LMSCourse as _LC

                track_id = request.POST.get("track_id", "").strip()
                step_name = request.POST.get("step_name", "").strip()
                req_type = request.POST.get("requirement_type", "").strip()
                is_required = bool(request.POST.get("step_required"))
                lms_course_ids = [
                    int(cid)
                    for cid in request.POST.getlist("step_lms_course_ids")
                    if str(cid).isdigit()
                ]

                if not track_id or not step_name:
                    messages.error(request, "Step name is required.")
                    return redirect(f"{request.path}?section=settings&tab=trainings")

                track = _MT.raw_objects.filter(
                    church=church, id=track_id, is_active=True
                ).first()
                if not track:
                    messages.error(request, "Track not found.")
                    return redirect(f"{request.path}?section=settings&tab=trainings")

                # Map template option values to StepRequirement types
                type_map = {
                    "attendance": _SR.TYPE_ATTENDANCE,
                    "lms": _SR.TYPE_LMS,
                    "manual": _SR.TYPE_MANUAL,
                    "auto": _SR.TYPE_AUTO,
                    "form": _SR.TYPE_MANUAL,  # no separate form type — treat as manual
                }
                req = None
                if req_type in type_map:
                    selected_courses_qs = _LC.raw_objects.filter(
                        church=church, id__in=lms_course_ids, is_active=True
                    )
                    primary_course = (
                        selected_courses_qs.first() if req_type == "lms" else None
                    )
                    req = _SR.raw_objects.create(
                        church=church,
                        name=step_name,
                        requirement_type=type_map[req_type],
                        lms_course=primary_course,
                        is_active=True,
                    )
                    if req_type == "lms" and lms_course_ids:
                        req.lms_courses.set(selected_courses_qs)

                last_order = (
                    _MTS.raw_objects.filter(church=church, track=track)
                    .order_by("-order")
                    .values_list("order", flat=True)
                    .first()
                ) or 0

                _MTS.raw_objects.create(
                    church=church,
                    track=track,
                    name=step_name,
                    is_required=is_required,
                    order=last_order + 1,
                    requirement=req,
                    is_active=True,
                )
                messages.success(request, f"Step '{step_name}' added to {track.name}.")
                return redirect(f"{request.path}?section=settings&tab=trainings")

        elif tab == "chat":
            cs, _ = ChatSettings.raw_objects.get_or_create(church=church)

            chat_action = request.POST.get("chat_action")

            if chat_action == "tiers":
                selected_tiers = [
                    int(t)
                    for t in request.POST.getlist("chat_default_tiers")
                    if t.isdigit()
                ]
                cs.default_tier_list = selected_tiers

                # Per-room overrides
                overrides = {}
                for room in ChatRoom.raw_objects.filter(church=church):
                    room_tiers = [
                        int(t)
                        for t in request.POST.getlist(f"chat_room_tier_{room.id}")
                        if t.isdigit()
                    ]
                    if room_tiers:
                        overrides[str(room.id)] = room_tiers
                cs.room_tier_overrides = overrides
                cs.save()

            elif chat_action == "background":
                cs.bg_type = request.POST.get("chat_bg_type", "logo")
                if request.FILES.get("chat_bg_image"):
                    cs.bg_image = request.FILES["chat_bg_image"]
                if request.POST.get("chat_bg_clear"):
                    cs.bg_image = None
                cs.save()

        FormClass = form_classes.get(tab)
        if FormClass:
            form = FormClass(
                request.POST, request.FILES, church=church, settings=settings_obj
            )
            if form.is_valid():
                # ── Capture old prefix before save (for member ID migration) ──
                old_member_prefix = (
                    getattr(settings_obj, "member_id_prefix", None) or "MBR"
                )
                form.save()
                # ── Core values: saved via hidden JSON field (not a form field) ──
                if tab == "general":
                    import json as _json

                    raw = request.POST.get("core_values_json", "").strip()
                    if raw:
                        try:
                            vals = _json.loads(raw)
                            if isinstance(vals, list):
                                # Sanitise: keep only dicts with a non-empty title
                                clean = [
                                    {
                                        "title": str(v.get("title", ""))[:80].strip(),
                                        "description": str(v.get("description", ""))[
                                            :300
                                        ].strip(),
                                    }
                                    for v in vals
                                    if isinstance(v, dict)
                                    and str(v.get("title", "")).strip()
                                ]
                                settings_obj.core_values = clean[:10]
                                settings_obj.save(update_fields=["core_values"])
                        except (_json.JSONDecodeError, Exception):
                            pass
                messages.success(request, "Settings saved.")
                # ── Migrate existing member custom_ids if prefix changed ───────
                import re as _re
                from accounts.models import ChurchMember as _CM

                new_prefix = getattr(settings_obj, "member_id_prefix", None) or "MBR"
                if old_member_prefix != new_prefix:
                    updated_count = 0
                    # Migrate members with old prefix, preserving their numeric part
                    for mbr in _CM.raw_objects.filter(
                        church=church,
                        custom_id__startswith=old_member_prefix,
                    ):
                        num_part = _re.sub(r"^\D+", "", mbr.custom_id or "")
                        if num_part:
                            mbr.custom_id = f"{new_prefix}{num_part}"
                            mbr.save(update_fields=["custom_id"])
                            updated_count += 1
                    # Also migrate any members with OTHER prefixes (shouldn't normally exist,
                    # but handle edge cases) — find the max number across all existing IDs
                    # so new assignments continue from the highest number already used.
                    all_ids = _CM.raw_objects.filter(
                        church=church,
                        custom_id__isnull=False,
                    ).values_list("custom_id", flat=True)
                    existing_nums = []
                    for cid in all_ids:
                        n = _re.sub(r"^\D+", "", cid or "")
                        if n.isdigit():
                            existing_nums.append(int(n))
                    # The next new member will get max+1 — this is handled naturally
                    # by the ChurchMember.save() method which queries custom_id__startswith=new_prefix
                    # So after migration all old numbers are now under new_prefix and
                    # the sequence is automatically correct.
                    if updated_count:
                        messages.info(
                            request,
                            f"Member ID prefix updated — {updated_count} existing member ID(s) migrated to '{new_prefix}'. Numbering continues from the last assigned ID.",
                        )
                redirect_tab = request.POST.get("redirect_tab", tab)
                return redirect(f"{request.path}?section=settings&tab={redirect_tab}")
            else:
                forms_map[tab] = form
                messages.error(request, "Please correct the errors below.")
                section = "settings"
                active_settings_tab = tab

    for key, FormClass in form_classes.items():
        if key not in forms_map:
            forms_map[key] = FormClass(church=church, settings=settings_obj)

    lookup_tables = [
        (
            "channel",
            "Visit Channels",
            VisitChannel.raw_objects.filter(church=church).order_by("order"),
        ),
        (
            "purpose",
            "Visit Purposes",
            VisitPurpose.raw_objects.filter(church=church).order_by("order"),
        ),
        (
            "service",
            "Church Services",
            ChurchService.raw_objects.filter(church=church).order_by("order"),
        ),
    ]

    all_courses = LMSCourse.raw_objects.filter(church=church, is_active=True).order_by(
        "course_type", "title"
    )
    all_units_qs = ChurchUnit.raw_objects.filter(
        church=church, is_active=True
    ).order_by("unit_type", "name")
    from accounts.models import ChurchMember

    all_members_qs = ChurchMember.raw_objects.filter(
        church=church, is_active=True
    ).select_related("user")
    all_campuses_qs = (
        Campus.raw_objects.filter(church=church, is_active=True)
        .exclude(campus_church__parent_church__isnull=True)
        .select_related("report_to", "campus_leader__user")
        .prefetch_related(
            Prefetch(
                "members",
                queryset=CampusMembership.raw_objects.filter(
                    church=church, is_active=True
                ).select_related("member__user"),
            )
        )
        .order_by("name")
    )
    import json as _json

    all_campuses_list = list(all_campuses_qs)
    for campus in all_campuses_list:
        campus.workspace_config_json = _json.dumps(
            getattr(campus, "workspace_config", {}) or {}
        )
    campus_cfg2 = settings_obj.campus_growth_stages or {}
    if isinstance(campus_cfg2, list):
        cs_enabled = True
        cs_list = [
            {"name": s, "description": "", "order": i + 1}
            for i, s in enumerate(campus_cfg2)
        ]
    else:
        cs_enabled = campus_cfg2.get("enabled", True)
        cs_list = sorted(campus_cfg2.get("stages", []), key=lambda x: x.get("order", 0))

    from billing.services import can_add_campus, campus_limit, extra_campus_slots

    campus_limit_value = campus_limit(church)
    campus_extra_slots = extra_campus_slots(church)
    campus_count = all_campuses_qs.count()
    effective_campus_limit = (
        -1
        if campus_limit_value == -1
        else max(campus_limit_value + campus_extra_slots, 0)
    )
    campus_limit_remaining = (
        None
        if effective_campus_limit == -1
        else max(effective_campus_limit - campus_count, 0)
    )
    campus_limit_reached = (
        effective_campus_limit != -1 and campus_count >= effective_campus_limit
    )

    unit_module_fields = [
        ("guest_management", "Guest Management", "orange"),
        ("music_module", "Music", "green"),
        ("media_module", "Media", "blue"),
        ("children_module", "Children", "yellow"),
        ("youth_module", "Youth", "purple"),
        ("teenagers_module", "Teenagers", "red"),
    ]

    settings_ctx = {
        "forms": forms_map,
        "unit_form": UnitQuickCreateForm(church=church, settings=settings_obj),
        "active_tab": active_settings_tab,
        "lookup_tables": lookup_tables,
        "all_courses": all_courses,
        "all_units_list": all_units_qs,
        "all_members_list": all_members_qs,
        "all_campuses": all_campuses_list,
        "can_add_campus": can_add_campus(church),
        "campus_limit_value": campus_limit_value,
        "campus_extra_slots": campus_extra_slots,
        "campus_count": campus_count,
        "campus_limit_remaining": campus_limit_remaining,
        "campus_limit_reached": campus_limit_reached,
        "campus_stages_enabled": cs_enabled,
        "campus_stages_list": cs_list,
        "workforce_stages": WorkforceStage.raw_objects.filter(church=church).order_by(
            "order"
        ),
        "workforce_roles": WorkforceRole.raw_objects.filter(church=church).order_by(
            "order"
        ),
        "membership_tracks": tracks_list,
        "default_membership_track": default_membership_track,
        "induction_courses": LMSCourse.raw_objects.filter(
            church=church, course_type="induction", is_active=True
        ),
        "visit_channels": VisitChannel.raw_objects.filter(church=church).order_by(
            "order"
        ),
        "visit_purposes": VisitPurpose.raw_objects.filter(church=church).order_by(
            "order"
        ),
        "church_services": ChurchService.raw_objects.filter(church=church).order_by(
            "order"
        ),
        "unit_roles": UnitRole.raw_objects.filter(church=church, is_active=True)
        .select_related("unit")
        .order_by("unit__name", "order"),
        "unit_module_fields": unit_module_fields,
        "settings_obj": settings_obj,
        # Tier choices for workforce role tier dropdowns in the settings UI
        "tier_choices": WORKFORCE_TIER_CHOICES,
        "unit_tier_choices": UNIT_TIER_CHOICES,
        # ── Chat settings ──────────────────────────────
        "chat_settings": ChatSettings.raw_objects.get_or_create(church=church)[0],
        "all_chat_rooms": ChatRoom.raw_objects.filter(
            church=church, parent_room__isnull=True, is_active=True
        ),
        "chat_bg_type_choices": ChatSettings.BG_CHOICES,
    }

    profile_stats = [
        ("Members", total_members, "#6366f1", None),
        ("Workforce", total_workforce, "#22c55e", None),
        ("Units", total_units, "#f59f00", None),
        ("Campuses", total_campuses, "#8b5cf6", None),
        ("Training Tracks", tracks_list.count(), "#fb923c", None),
        ("Courses", total_courses, "#0ea5e9", None),
    ]

    return render(
        request,
        "tenants/account_page.html",
        {
            "section": section,
            "church": church,
            "settings_obj": settings_obj,
            "page_title": "Account",
            "profile_stats": profile_stats,
            **profile_ctx,
            **billing_ctx,
            **settings_ctx,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Lookup table AJAX CRUD
# Shared by VisitChannel, VisitPurpose, ChurchService
# ─────────────────────────────────────────────────────────────────────────────


@require_http_methods(["POST"])
def unit_inline_edit(request, unit_id):
    """
    AJAX endpoint: edit a unit's name, description, type, default flag,
    and module toggles — all from the settings page unit list.
    Returns JSON with updated unit data for the row.
    """
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from units.models import ChurchUnit

    unit = get_object_or_404(
        ChurchUnit.raw_objects.filter(church=church, is_active=True), id=unit_id
    )

    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)

    # Guard: unique name within church (excluding self)
    if (
        ChurchUnit.raw_objects.filter(church=church, name__iexact=name)
        .exclude(id=unit.id)
        .exists()
    ):
        return JsonResponse(
            {"ok": False, "error": f"A unit named '{name}' already exists."}, status=400
        )

    unit.name = name
    unit.description = request.POST.get("description", "").strip()
    unit.unit_type = request.POST.get("unit_type", unit.unit_type)
    report_to_raw = request.POST.get("report_to", "").strip()
    if report_to_raw:
        if not report_to_raw.isdigit():
            return JsonResponse(
                {"ok": False, "error": "Invalid parent unit."}, status=400
            )
        parent_id = int(report_to_raw)
        if parent_id == unit.id:
            return JsonResponse(
                {"ok": False, "error": "A unit cannot report to itself."}, status=400
            )
        parent_unit = ChurchUnit.raw_objects.filter(
            church=church,
            is_active=True,
            id=parent_id,
        ).first()
        if not parent_unit:
            return JsonResponse(
                {"ok": False, "error": "Parent unit not found."}, status=400
            )
        unit.report_to = parent_unit
    else:
        unit.report_to = None

    # Module toggles — sent as "on"/"off" or presence/absence
    for field in [
        "guest_management",
        "music_module",
        "media_module",
        "children_module",
        "youth_module",
        "teenagers_module",
    ]:
        setattr(unit, field, request.POST.get(field) == "on")

    is_default = request.POST.get("is_default") == "on"
    if is_default and not unit.is_default:
        # Clear other defaults for same type
        ChurchUnit.raw_objects.filter(
            church=church, unit_type=unit.unit_type, is_default=True
        ).exclude(id=unit.id).update(is_default=False)
    unit.is_default = is_default
    unit.save()

    # Build module label string for the table cell
    module_labels = []
    if unit.guest_management:
        module_labels.append("Guests")
    if unit.music_module:
        module_labels.append("Music")
    if unit.media_module:
        module_labels.append("Media")
    if unit.children_module:
        module_labels.append("Children")
    if unit.youth_module:
        module_labels.append("Youth")
    if unit.teenagers_module:
        module_labels.append("Teens")

    return JsonResponse(
        {
            "ok": True,
            "id": unit.id,
            "name": unit.name,
            "description": unit.description,
            "unit_type": unit.get_unit_type_display(),
            "report_to_name": unit.report_to.name if unit.report_to else "",
            "is_default": unit.is_default,
            "color_hex": unit.color_hex,
            "modules": module_labels,
            "member_count": unit.member_count,
        }
    )


def _lookup_model(lookup_type):
    from guests.models import VisitChannel, VisitPurpose, ChurchService

    return {
        "channel": VisitChannel,
        "purpose": VisitPurpose,
        "service": ChurchService,
    }.get(lookup_type)


@require_http_methods(["POST"])
def lookup_add(request, lookup_type):
    """Add a new lookup item (channel / purpose / service)."""
    church = getattr(request, "church", None)
    if not church or not _tenant_admin(request, church):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    Model = _lookup_model(lookup_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown lookup type"}, status=400)

    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)

    if Model.raw_objects.filter(church=church, name__iexact=name).exists():
        return JsonResponse(
            {"ok": False, "error": f"'{name}' already exists"}, status=400
        )

    order = Model.raw_objects.filter(church=church).count()
    item = Model.raw_objects.create(
        church=church, name=name, order=order, is_active=True
    )
    return JsonResponse(
        {"ok": True, "id": item.id, "name": item.name, "order": item.order}
    )


@require_http_methods(["POST"])
def lookup_toggle(request, lookup_type, pk):
    """Toggle is_active on a lookup item."""
    church = getattr(request, "church", None)
    if not church or not _tenant_admin(request, church):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    Model = _lookup_model(lookup_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown lookup type"}, status=400)

    item = get_object_or_404(Model.raw_objects, id=pk, church=church)
    item.is_active = not item.is_active
    item.save(update_fields=["is_active"])
    return JsonResponse({"ok": True, "is_active": item.is_active})


@require_http_methods(["POST"])
def lookup_reorder(request, lookup_type):
    """Reorder lookup items by accepting an ordered list of ids."""
    church = getattr(request, "church", None)
    if not church or not _tenant_admin(request, church):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    Model = _lookup_model(lookup_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown lookup type"}, status=400)

    ids = request.POST.getlist("ids[]")
    try:
        ids = [int(i) for i in ids]
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid ids"}, status=400)

    items = {obj.id: obj for obj in Model.raw_objects.filter(church=church, id__in=ids)}
    for new_order, item_id in enumerate(ids):
        if item_id in items:
            items[item_id].order = new_order
            items[item_id].save(update_fields=["order"])

    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
def lookup_rename(request, lookup_type, pk):
    """Rename a lookup item."""
    church = getattr(request, "church", None)
    if not church or not _tenant_admin(request, church):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    Model = _lookup_model(lookup_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown lookup type"}, status=400)

    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)

    item = get_object_or_404(Model.raw_objects, id=pk, church=church)
    item.name = name
    item.save(update_fields=["name"])
    return JsonResponse({"ok": True, "name": item.name})


def _wf_auth(request):
    church = getattr(request, "church", None)
    is_admin = bool(church and _tenant_admin(request, church))
    return church, is_admin


@require_http_methods(["POST"])
def workforce_item_add(request, item_type):
    """Add WorkforceStage or WorkforceRole (inline, no popup)."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from workforce.models import WorkforceStage, WorkforceRole

    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)

    if item_type == "stage":
        if WorkforceStage.raw_objects.filter(church=church, name__iexact=name).exists():
            return JsonResponse(
                {"ok": False, "error": f"Stage '{name}' already exists"}, status=400
            )
        # Custom stages always sit after the 4 system stages (order >= 4)
        last = (
            WorkforceStage.raw_objects.filter(church=church).order_by("-order").first()
        )
        order = max((last.order + 1) if last else 4, 4)
        from django.utils.text import slugify as _slugify

        item = WorkforceStage.raw_objects.create(
            church=church,
            name=name,
            slug=_slugify(name),
            order=order,
            is_locked=False,
            is_order_locked=False,
            is_default=False,
            is_active=True,
        )
        return JsonResponse(
            {
                "ok": True,
                "id": item.id,
                "name": item.name,
                "order": item.order,
                "is_locked": item.is_locked,
                "is_order_locked": item.is_order_locked,
                "is_default": item.is_default,
            }
        )

    elif item_type == "role":
        # Accept tier (int 1-3,7) from POST. Scope is auto-derived from tier.
        from permissions.registry import (
            TIER_ADMIN,
            TIER_SUB_ADMIN,
            TIER_ASSISTANT_PASTOR,
            TIER_CUSTOM,
            permissions_for_tier,
        )

        try:
            tier = int(request.POST.get("tier", TIER_CUSTOM))
            if tier not in (
                TIER_ADMIN,
                TIER_SUB_ADMIN,
                TIER_ASSISTANT_PASTOR,
                TIER_CUSTOM,
            ):
                tier = TIER_CUSTOM
        except (ValueError, TypeError):
            tier = TIER_CUSTOM

        scope = "global"

        if WorkforceRole.raw_objects.filter(church=church, name__iexact=name).exists():
            return JsonResponse(
                {"ok": False, "error": f"Role '{name}' already exists"}, status=400
            )
        last = (
            WorkforceRole.raw_objects.filter(church=church).order_by("-order").first()
        )
        order = (last.order + 1) if last else 1
        item = WorkforceRole.raw_objects.create(
            church=church,
            name=name,
            tier=tier,
            scope=scope,
            permissions=permissions_for_tier(tier),
            is_leadership=(tier in (TIER_ADMIN, TIER_SUB_ADMIN, TIER_ASSISTANT_PASTOR)),
            order=order,
            is_active=True,
        )
        from permissions.registry import TIER_LABELS

        return JsonResponse(
            {
                "ok": True,
                "id": item.id,
                "name": item.name,
                "tier": item.tier,
                "tier_label": TIER_LABELS.get(item.tier, "Custom Role"),
                "scope": item.scope,
                "order": item.order,
                "is_locked": item.is_locked,
                "is_default": item.is_default,
            }
        )

    return JsonResponse({"ok": False, "error": "Unknown item type"}, status=400)


@require_http_methods(["POST"])
def workforce_item_rename(request, item_type, pk):
    """Rename a non-locked WorkforceStage or WorkforceRole."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from workforce.models import WorkforceStage, WorkforceRole

    Model = {"stage": WorkforceStage, "role": WorkforceRole}.get(item_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown type"}, status=400)

    item = get_object_or_404(Model.raw_objects, id=pk, church=church)
    name = request.POST.get("name", "").strip()
    description = request.POST.get("description", "").strip()

    if item.is_locked:
        # Locked stages: description editable, name immutable
        if item_type == "stage" and hasattr(item, "description"):
            item.description = description
            item.save(update_fields=["description"])
            return JsonResponse(
                {"ok": True, "name": item.name, "description": item.description}
            )
        return JsonResponse(
            {
                "ok": False,
                "error": f"'{item.name}' is a system item and cannot be changed.",
            },
            status=400,
        )

    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)

    item.name = name
    update_fields = ["name"]
    if item_type == "stage" and hasattr(item, "description"):
        item.description = description
        update_fields.append("description")
    item.save(update_fields=update_fields)
    return JsonResponse(
        {"ok": True, "name": item.name, "description": getattr(item, "description", "")}
    )


@require_http_methods(["POST"])
def workforce_item_reorder(request, item_type):
    """Swap order of two items. Expects ids[]=id1&ids[]=id2."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from workforce.models import WorkforceStage, WorkforceRole

    Model = {"stage": WorkforceStage, "role": WorkforceRole}.get(item_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown type"}, status=400)

    # Receive ordered list of all ids
    ids = request.POST.getlist("ids[]")
    try:
        ids = [int(i) for i in ids]
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid ids"}, status=400)

    items = {obj.id: obj for obj in Model.raw_objects.filter(church=church, id__in=ids)}
    for new_order, item_id in enumerate(ids):
        obj = items.get(item_id)
        if not obj:
            continue
        # Never reorder order-locked stages (Inductee, Probationer, Active)
        if getattr(obj, "is_order_locked", False):
            continue
        # For non-order-locked items, enforce minimum order of 4 for stages
        if item_type == "stage":
            effective_order = max(new_order, 4)
        else:
            effective_order = new_order
        if not obj.is_locked:
            obj.order = effective_order
            obj.save(update_fields=["order"])

    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
def workforce_item_delete(request, item_type, pk):
    """Delete a non-locked, non-default WorkforceStage or WorkforceRole."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from workforce.models import WorkforceStage, WorkforceRole, WorkforceMember

    Model = {"stage": WorkforceStage, "role": WorkforceRole}.get(item_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown type"}, status=400)

    item = get_object_or_404(Model.raw_objects, id=pk, church=church)
    if item.is_locked:
        return JsonResponse(
            {
                "ok": False,
                "error": f"'{item.name}' is a system item and cannot be deleted.",
            },
            status=400,
        )

    # Safety: don't delete if members are on this stage/role
    if (
        item_type == "stage"
        and WorkforceMember.raw_objects.filter(church=church, stage=item).exists()
    ):
        return JsonResponse(
            {
                "ok": False,
                "error": f"'{item.name}' has active members. Reassign them first.",
            },
            status=400,
        )

    item.delete()
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
def workforce_role_set_tier(request, pk):
    """
    Set the tier on a WorkforceRole.
    Replaces the old toggle_leadership endpoint.
    Accepts POST: tier=<int 1-5>
    """
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from workforce.models import WorkforceRole
    from permissions.registry import (
        TIER_ADMIN,
        TIER_SUB_ADMIN,
        TIER_ASSISTANT_PASTOR,
        TIER_CUSTOM,
        TIER_LABELS,
        permissions_for_tier,
    )

    role = get_object_or_404(WorkforceRole.raw_objects, id=pk, church=church)

    try:
        new_tier = int(request.POST.get("tier", role.tier))
        if new_tier not in (
            TIER_ADMIN,
            TIER_SUB_ADMIN,
            TIER_ASSISTANT_PASTOR,
            TIER_CUSTOM,
        ):
            new_tier = TIER_CUSTOM
    except (ValueError, TypeError):
        new_tier = TIER_CUSTOM

    role.tier = new_tier
    role.scope = "global"
    role.is_leadership = new_tier in (TIER_ADMIN, TIER_SUB_ADMIN, TIER_ASSISTANT_PASTOR)
    # A tier change should make the registry definition the new source of truth.
    # Re-merging the old JSON keeps higher-tier permissions alive after downgrade.
    role.permissions = dict(permissions_for_tier(new_tier))
    role.save(update_fields=["tier", "scope", "is_leadership", "permissions"])

    return JsonResponse(
        {
            "ok": True,
            "tier": role.tier,
            "tier_label": TIER_LABELS.get(role.tier, "Custom Role"),
            "scope": role.scope,
            "is_leadership": role.is_leadership,
        }
    )


# Keep old URL name working for backwards compat during deploy
workforce_role_toggle_leadership = workforce_role_set_tier


@require_http_methods(["POST"])
def unit_role_add(request):
    """
    POST /tenants/settings/unit-role/add/
    Create a UnitRole for a specific unit/group.
    Body: unit_id, name, tier (int 3-5)
    """
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from units.models import ChurchUnit
    from permissions.models import UnitRole
    from permissions.registry import (
        TIER_OVERSEER,
        TIER_UNIT_HEAD,
        TIER_ASSISTANT,
        TIER_CUSTOM,
        TIER_LABELS,
        permissions_for_tier,
    )
    from units.signals import _unit_permissions

    unit_id = request.POST.get("unit_id", "").strip()
    name = request.POST.get("name", "").strip()
    try:
        tier = int(request.POST.get("tier", TIER_CUSTOM))
        if tier not in (TIER_OVERSEER, TIER_UNIT_HEAD, TIER_ASSISTANT, TIER_CUSTOM):
            tier = TIER_CUSTOM
    except (ValueError, TypeError):
        tier = TIER_CUSTOM

    if not unit_id or not name:
        return JsonResponse(
            {"ok": False, "error": "Unit and name required"}, status=400
        )

    unit = get_object_or_404(
        ChurchUnit.raw_objects, id=unit_id, church=church, is_active=True
    )

    if UnitRole.raw_objects.filter(
        church=church, unit=unit, name__iexact=name
    ).exists():
        return JsonResponse(
            {"ok": False, "error": f"Role '{name}' already exists for this unit"},
            status=400,
        )

    last = (
        UnitRole.raw_objects.filter(church=church, unit=unit).order_by("-order").first()
    )
    order = (last.order + 1) if last else 1

    # Merge tier defaults with unit module permissions
    base_perms = _unit_permissions(unit)
    tier_perms = permissions_for_tier(tier)
    merged = {**tier_perms, **base_perms}

    role = UnitRole.raw_objects.create(
        church=church,
        unit=unit,
        name=name,
        tier=tier,
        permissions=merged,
        is_leadership=(tier in (TIER_OVERSEER, TIER_UNIT_HEAD, TIER_ASSISTANT)),
        order=order,
        is_active=True,
    )

    return JsonResponse(
        {
            "ok": True,
            "id": role.id,
            "name": role.name,
            "tier": role.tier,
            "tier_label": TIER_LABELS.get(role.tier, "Custom Role"),
            "unit_id": unit.id,
            "unit_name": unit.name,
            "unit_type": unit.unit_type,
        }
    )


@require_http_methods(["POST"])
def unit_role_update(request, pk):
    """
    Inline update of UnitRole name or tier.
    """
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from permissions.models import UnitRole
    from permissions.registry import (
        TIER_OVERSEER,
        TIER_UNIT_HEAD,
        TIER_ASSISTANT,
        TIER_CUSTOM,
        TIER_LABELS,
        permissions_for_tier,
    )
    from units.signals import _unit_permissions

    role = get_object_or_404(
        UnitRole.raw_objects,
        id=pk,
        church=church,
    )

    name = request.POST.get("name")
    tier = request.POST.get("tier")

    changed = False

    # ── Update name ─────────────────────────
    if name is not None:
        name = name.strip()
        if not name:
            return JsonResponse({"ok": False, "error": "Name required"}, status=400)

        role.name = name
        changed = True

    # ── Update tier ─────────────────────────
    if tier is not None:
        try:
            tier = int(tier)
        except ValueError:
            tier = TIER_CUSTOM

        if tier not in (
            TIER_OVERSEER,
            TIER_UNIT_HEAD,
            TIER_ASSISTANT,
            TIER_CUSTOM,
        ):
            tier = TIER_CUSTOM

        role.tier = tier

        # regenerate permissions safely
        base_perms = _unit_permissions(role.unit)
        tier_perms = permissions_for_tier(tier)
        role.permissions = {**tier_perms, **base_perms}

        role.is_leadership = tier in (
            TIER_OVERSEER,
            TIER_UNIT_HEAD,
            TIER_ASSISTANT,
        )

        changed = True

    if changed:
        role.save()

    return JsonResponse(
        {
            "ok": True,
            "name": role.name,
            "tier": role.tier,
            "tier_label": TIER_LABELS.get(role.tier, "Custom Role"),
        }
    )


@require_http_methods(["POST"])
def unit_role_delete(request, pk):
    """
    POST /tenants/settings/unit-role/<pk>/delete/
    Delete a UnitRole (only if no memberships currently use it).
    """
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from permissions.models import UnitRole, MembershipRole

    role = get_object_or_404(UnitRole.raw_objects, id=pk, church=church)

    if MembershipRole.raw_objects.filter(church=church, role=role).exists():
        return JsonResponse(
            {
                "ok": False,
                "error": f"'{role.name}' is assigned to one or more members. Reassign them first.",
            },
            status=400,
        )

    role.delete()
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
def campus_create_inline(request):
    """
    Create a campus from the settings page Campus tab.

    This now provisions a FULL campus-church (a real Church instance) via
    campus_provisioning.provision_campus_church(), bootstrapped with the
    complete default template and the campus leader as admin inside it.

    The Campus record (HQ-side management record) is also created/updated
    with the campus_church FK pointing to the new Church instance.

    Slug/subdomain are derived from the HQ church routing mode + location_name:
      - Trial (path-routed):   gatewaynation-abuja
      - SaaS (subdomain):      abuja.gatewaynation
      - White-label (subdomain): abuja.gatewaynation  (custom domain set separately)
    """
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)
    from billing.services import can_add_campus

    if not can_add_campus(church):
        return JsonResponse(
            {
                "ok": False,
                "error": "Campus limit reached. Contact support if you need help.",
                "upgrade_url": f"{reverse('billing:pricing')}?campus_limit_reached=1",
            },
            status=400,
        )

    name = request.POST.get("name", "").strip()
    location_name = request.POST.get("location_name", "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)
    if not location_name:
        # Derive from name if not explicitly supplied
        location_name = slugify(name)

    # Resolve campus leader (optional at creation time)
    from accounts.models import ChurchMember as _ChurchMember

    leader_member = None
    campus_leader_id = request.POST.get("campus_leader_id", "").strip()
    if campus_leader_id.isdigit():
        leader_member = _ChurchMember.raw_objects.filter(
            church=church, id=int(campus_leader_id), is_active=True
        ).first()

    # 1. Create the HQ-side Campus management record
    campus = Campus.raw_objects.create(
        church=church,
        name=name,
        growth_stage=request.POST.get("growth_stage", "").strip(),
        address=request.POST.get("address", "").strip(),
        location_name=location_name,
        campus_leader=leader_member,
        is_active=True,
    )

    # 2. Provision a full Church instance for this campus
    from tenants.campus_provisioning import provision_campus_church

    try:
        campus_church = provision_campus_church(
            church,
            campus,
            leader_member=leader_member,
            location_name=location_name,
        )
        campus_church_url = _campus_church_url(campus_church)
    except Exception as exc:
        import logging as _logging

        _logging.getLogger(__name__).exception(
            "campus_create_inline: provisioning failed for %s", name
        )
        # Clean up the campus record if provisioning fails
        campus.delete()
        return JsonResponse(
            {"ok": False, "error": f"Campus provisioning failed: {exc}"},
            status=500,
        )

    return JsonResponse(
        {
            "ok": True,
            "id": campus.id,
            "name": campus.name,
            "slug": campus.slug,
            "location_name": campus.location_name,
            "description": campus.description,
            "address": campus.address,
            "latitude": str(campus.latitude) if campus.latitude else "",
            "longitude": str(campus.longitude) if campus.longitude else "",
            "growth_stage": campus.growth_stage or "—",
            "report_to_id": campus.report_to_id,
            "campus_leader_id": campus.campus_leader_id,
            "contributes_to_parent_metrics": campus.contributes_to_parent_metrics,
            "workspace_config": campus.workspace_config,
            "campus_church_slug": campus_church.slug,
            "campus_church_url": campus_church_url,
            "members": _serialize_campus_members(campus),
        }
    )


def _campus_church_url(campus_church):
    """Build the human-readable access URL for a campus-church.

    In DEBUG / dev:  http://<app-domain>/<slug>   (path-routed, no subdomain)
    SaaS prod:       https://<subdomain>.<app-domain>/
    White-label:     https://<custom-domain>/
    """
    from django.conf import settings as _settings

    app_domains = getattr(_settings, "APP_DOMAINS", None) or ["workforce.church"]
    app_domain = app_domains[0]

    # In dev, always path-route regardless of subdomain or custom_domain fields
    if _settings.DEBUG:
        return f"http://{app_domain}/{campus_church.slug}"

    if campus_church.custom_domain:
        return f"https://{campus_church.custom_domain}"

    if campus_church.subdomain:
        return f"https://{campus_church.subdomain}.{app_domain}"

    # Path routing (trial)
    return f"https://{app_domain}/{campus_church.slug}"


def _serialize_campus_members(campus):
    """Return serialised list of all members assigned to campus.

    We query CampusMembership.raw_objects directly instead of relying on the
    prefetched `campus.members` manager, because the prefetch may be scoped to
    a single church tenant and miss members whose ChurchMember record lives on
    the HQ church (i.e. members assigned/transferred from HQ).
    """
    members = []
    qs = CampusMembership.raw_objects.filter(
        campus=campus, is_active=True
    ).select_related("member__user")
    for membership in qs:
        member = getattr(membership, "member", None)
        members.append(
            {
                "campus_membership_id": str(membership.id),
                "member_id": str(membership.member_id),
                "member_name": str(member) if member else "",
                "is_primary": bool(membership.is_primary),
                "notes": membership.notes or "",
            }
        )
    return members


@require_http_methods(["POST"])
def campus_update_inline(request, slug=None):
    """Update a campus from the settings modal."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    campus_slug = request.POST.get("campus_slug", "").strip() or (slug or "").strip()
    if not campus_slug:
        return JsonResponse({"ok": False, "error": "Campus not found."}, status=400)

    campus = get_object_or_404(Campus.raw_objects, slug=campus_slug, church=church)
    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)

    campus.name = name
    campus.description = request.POST.get("description", "").strip()
    campus.address = request.POST.get("address", "").strip()
    campus.growth_stage = request.POST.get("growth_stage", "").strip()
    campus.contributes_to_parent_metrics = (
        request.POST.get("contributes_to_parent_metrics") == "on"
    )

    latitude = request.POST.get("latitude", "").strip()
    longitude = request.POST.get("longitude", "").strip()
    campus.latitude = latitude or None
    campus.longitude = longitude or None

    report_to_slug = request.POST.get("report_to_slug", "").strip()
    campus.report_to = (
        Campus.raw_objects.filter(church=church, slug=report_to_slug, is_active=True)
        .exclude(id=campus.id)
        .first()
        if report_to_slug
        else None
    )

    campus_leader_id = request.POST.get("campus_leader_id", "").strip()
    if campus_leader_id.isdigit():
        from accounts.models import ChurchMember

        campus.campus_leader = ChurchMember.raw_objects.filter(
            church=church, id=int(campus_leader_id), is_active=True
        ).first()
    else:
        campus.campus_leader = None

    from .models import default_campus_workspace_config

    workspace_cfg = dict(getattr(campus, "workspace_config", {}) or {})
    workspace_cfg["inherit_hq"] = request.POST.get("workspace_inherit_hq") == "on"
    module_defaults = default_campus_workspace_config()["modules"]
    workspace_cfg["modules"] = {
        key: request.POST.get(f"module_{key}") == "on" for key in module_defaults.keys()
    }
    workspace_cfg["notes"] = request.POST.get("workspace_notes", "").strip()
    campus.workspace_config = workspace_cfg

    campus.save()

    # ── Sync campus-church if it exists ───────────────────────────────
    campus_church = getattr(campus, "campus_church", None)
    campus_church_url = ""
    if campus_church:
        campus_church.name = f"{church.name} — {campus.name}"
        update_fields = ["name", "updated_at"]
        if campus.latitude and campus.longitude:
            campus_church.latitude = campus.latitude
            campus_church.longitude = campus.longitude
            update_fields += ["latitude", "longitude"]
        campus_church.save(update_fields=update_fields)

        # If leader changed, provision them in the campus-church
        if campus.campus_leader:
            from tenants.campus_provisioning import _provision_leader_in_campus

            _provision_leader_in_campus(campus.campus_leader, campus_church)

        campus_church_url = _campus_church_url(campus_church)

    return JsonResponse(
        {
            "ok": True,
            "id": campus.id,
            "name": campus.name,
            "slug": campus.slug,
            "location_name": campus.location_name,
            "description": campus.description,
            "address": campus.address,
            "latitude": str(campus.latitude) if campus.latitude else "",
            "longitude": str(campus.longitude) if campus.longitude else "",
            "growth_stage": campus.growth_stage or "—",
            "report_to_id": campus.report_to_id,
            "campus_leader_id": campus.campus_leader_id,
            "report_to_name": campus.report_to.name if campus.report_to else "",
            "campus_leader_name": (
                str(campus.campus_leader) if campus.campus_leader else ""
            ),
            "contributes_to_parent_metrics": campus.contributes_to_parent_metrics,
            "workspace_config": campus.workspace_config,
            "campus_church_slug": campus_church.slug if campus_church else "",
            "campus_church_url": campus_church_url,
            "members": _serialize_campus_members(campus),
        }
    )


@require_http_methods(["POST"])
def campus_delete_inline(request, slug=None):
    """Soft-delete a campus from the settings modal."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    campus_slug = request.POST.get("campus_slug", "").strip() or (slug or "").strip()
    if not campus_slug:
        return JsonResponse({"ok": False, "error": "Campus not found."}, status=400)

    campus = get_object_or_404(Campus.raw_objects, slug=campus_slug, church=church)
    CampusMembership.raw_objects.filter(church=church, campus=campus).update(
        is_active=False,
        is_primary=False,
    )
    campus.is_active = False
    campus.save(update_fields=["is_active", "updated_at"])

    # Also deactivate the provisioned campus-church so it disappears from the
    # Django admin and middleware routing. The Church row is preserved (not
    # hard-deleted) so historical data stays intact; is_active=False stops the
    # middleware from resolving it as a live tenant.
    campus_church = getattr(campus, "campus_church", None)
    if campus_church and campus_church.is_active:
        campus_church.is_active = False
        campus_church.save(update_fields=["is_active", "updated_at"])

    return JsonResponse({"ok": True, "slug": campus.slug, "id": campus.id})


@require_http_methods(["POST"])
def campus_hq_override(request, slug=None):
    """
    HQ admin pushes restriction overrides down to a campus-church.

    POST body (JSON or form):
        disable_modules: comma-separated module names to force-disable
        read_only: 'on' to make campus settings read-only
        restrict_member_create: 'on' to block campus from creating members
        custom_message: banner text shown to campus admins
    """
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    campus_slug = request.POST.get("campus_slug", "").strip() or (slug or "").strip()
    if not campus_slug:
        return JsonResponse({"ok": False, "error": "Campus not found."}, status=400)

    campus = get_object_or_404(Campus.raw_objects, slug=campus_slug, church=church)
    campus_church = getattr(campus, "campus_church", None)
    if not campus_church:
        return JsonResponse(
            {"ok": False, "error": "This campus has no provisioned church yet."},
            status=400,
        )

    raw_modules = request.POST.get("disable_modules", "")
    disable_modules = [m.strip() for m in raw_modules.split(",") if m.strip()]

    override_payload = {
        "disable_modules": disable_modules,
        "read_only": request.POST.get("read_only") == "on",
        "restrict_member_create": request.POST.get("restrict_member_create") == "on",
        "custom_message": request.POST.get("custom_message", "").strip(),
    }

    from tenants.campus_provisioning import apply_hq_overrides

    apply_hq_overrides(campus_church, override_payload)

    return JsonResponse(
        {"ok": True, "campus_id": campus.id, "override": override_payload}
    )


@require_http_methods(["POST"])
def campus_transfer_inline(request):
    """Assign a member to a campus from the campus manager modal."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from accounts.models import ChurchMember

    campus_id = request.POST.get("campus_id", "").strip()
    member_id = request.POST.get("member_id", "").strip()
    is_primary = request.POST.get("is_primary") == "on"

    if not campus_id.isdigit() or not member_id.isdigit():
        return JsonResponse(
            {"ok": False, "error": "Campus and member are required."}, status=400
        )

    campus = get_object_or_404(Campus.raw_objects, id=int(campus_id), church=church)
    member = get_object_or_404(
        ChurchMember.raw_objects.filter(church=church, is_active=True),
        id=int(member_id),
    )

    CampusMembership.raw_objects.filter(church=church, member=member).exclude(
        campus=campus
    ).update(is_active=False, is_primary=False)

    CampusMembership.raw_objects.update_or_create(
        church=church,
        member=member,
        campus=campus,
        defaults={"is_primary": True, "is_active": True},
    )
    return JsonResponse(
        {
            "ok": True,
            "campus_id": campus.id,
            "member_id": member.id,
            "members": _serialize_campus_members(campus),
        }
    )


@require_http_methods(["POST"])
def campus_assign_inline(request):
    """Assign a member to a campus without removing them from others."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from accounts.models import ChurchMember

    campus_id = request.POST.get("campus_id", "").strip()
    member_id = request.POST.get("member_id", "").strip()
    is_primary = request.POST.get("is_primary") == "on"

    if not campus_id.isdigit() or not member_id.isdigit():
        return JsonResponse(
            {"ok": False, "error": "Campus and member are required."}, status=400
        )

    campus = get_object_or_404(Campus.raw_objects, id=int(campus_id), church=church)
    member = get_object_or_404(
        ChurchMember.raw_objects.filter(church=church, is_active=True),
        id=int(member_id),
    )

    if is_primary:
        CampusMembership.raw_objects.filter(
            church=church, member=member, is_primary=True
        ).update(is_primary=False)

    CampusMembership.raw_objects.update_or_create(
        church=church,
        member=member,
        campus=campus,
        defaults={"is_primary": is_primary, "is_active": True},
    )
    return JsonResponse(
        {
            "ok": True,
            "campus_id": campus.id,
            "member_id": member.id,
            "members": _serialize_campus_members(campus),
        }
    )


@require_http_methods(["POST"])
def campus_remove_inline(request):
    """Remove a member from a campus."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    campus_id = request.POST.get("campus_id", "").strip()
    member_id = request.POST.get("member_id", "").strip()

    if not campus_id.isdigit() or not member_id.isdigit():
        return JsonResponse(
            {"ok": False, "error": "Campus and member are required."}, status=400
        )

    membership = CampusMembership.raw_objects.filter(
        church=church,
        campus_id=int(campus_id),
        member_id=int(member_id),
    ).first()
    if not membership:
        return JsonResponse({"ok": False, "error": "Membership not found."}, status=404)

    membership.is_active = False
    membership.is_primary = False
    membership.save(update_fields=["is_active", "is_primary", "updated_at"])
    return JsonResponse(
        {
            "ok": True,
            "campus_id": campus_id,
            "member_id": member_id,
            "members": _serialize_campus_members(campus),
        }
    )


@login_required
def campus_settings(request):
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")
    return redirect(f"{reverse('tenants:account_page')}?section=settings&tab=campus")


@login_required
def campus_workspace(request, slug):
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    # Campus leaders may also access their own workspace; admins always can.
    member = getattr(request, "member", None)
    is_admin = _tenant_admin(request, church)
    is_campus_leader = (
        member
        and Campus.raw_objects.filter(
            church=church, is_active=True, campus_leader=member, slug=slug
        ).exists()
    )
    if not is_admin and not is_campus_leader:
        return HttpResponseForbidden("Not authorised.")

    campus = get_object_or_404(
        Campus.raw_objects.filter(church=church, is_active=True)
        .select_related("report_to", "campus_leader__user")
        .prefetch_related(
            Prefetch(
                "members",
                queryset=CampusMembership.raw_objects.filter(
                    church=church, is_active=True
                ).select_related("member__user"),
            )
        ),
        slug=slug,
    )

    # Expose the campus-church to template tags so role badges can resolve
    # campus-specific roles while the HQ workspace is open.
    request.campus_workspace = campus
    request.campus_church = getattr(campus, "campus_church", None)

    # ── Handle POST (workspace save) ─────────────────────────────────────────
    if request.method == "POST":
        from .models import default_campus_workspace_config

        workspace_cfg = dict(getattr(campus, "workspace_config", {}) or {})
        workspace_cfg["inherit_hq"] = request.POST.get("workspace_inherit_hq") == "on"
        module_defaults = default_campus_workspace_config()["modules"]
        workspace_cfg["modules"] = {
            key: request.POST.get(f"module_{key}") == "on"
            for key in module_defaults.keys()
        }
        workspace_cfg["notes"] = request.POST.get("workspace_notes", "").strip()

        campus.name = request.POST.get("name", campus.name).strip() or campus.name
        campus.slug = request.POST.get("slug", campus.slug).strip() or campus.slug
        campus.description = request.POST.get("description", "").strip()
        campus.address = request.POST.get("address", "").strip()
        campus.growth_stage = request.POST.get("growth_stage", "").strip()
        campus.workspace_config = workspace_cfg
        campus.save()
        from django.contrib import messages

        messages.success(request, "Campus workspace saved.")
        return redirect(
            reverse("tenants:campus_workspace", kwargs={"slug": campus.slug})
        )

    from .models import default_campus_workspace_config

    workspace_cfg = dict(getattr(campus, "workspace_config", {}) or {})
    module_states = (
        workspace_cfg.get("modules") or default_campus_workspace_config()["modules"]
    )

    # ── Full settings-mirror context (same as account_page settings_ctx) ─────
    import json as _json
    from lms.models import LMSCourse
    from units.models import ChurchUnit
    from units.forms import UnitQuickCreateForm
    from permissions.models import UnitRole
    from guests.models import VisitChannel, VisitPurpose, ChurchService
    from accounts.models import ChurchMember as _ChurchMember
    from tenants.forms import (
        GeneralSettingsForm,
        GuestPipelineSettingsForm,
        LMSSettingsForm,
        WorkforceSettingsForm,
        DisplaySettingsForm,
        FeatureFlagsForm,
    )
    from workforce.models import WorkforceStage, WorkforceRole
    from permissions.registry import WORKFORCE_TIER_CHOICES, UNIT_TIER_CHOICES

    settings_obj = getattr(church, "settings", None)

    # Build forms for the campus workspace (same classes, bound to church settings)
    from guests.models import MembershipTrack

    tracks_list = MembershipTrack.raw_objects.filter(
        church=church, is_active=True
    ).prefetch_related("steps__track")
    default_membership_track = tracks_list.filter(is_default=True).first()

    form_classes = {
        "general": GeneralSettingsForm,
        "feature_flags": FeatureFlagsForm,
        "pipeline": GuestPipelineSettingsForm,
        "trainings": LMSSettingsForm,
        "workforce": WorkforceSettingsForm,
        "display": DisplaySettingsForm,
    }
    forms_map = {
        key: FormClass(church=church, settings=settings_obj)
        for key, FormClass in form_classes.items()
    }

    all_campuses_qs = (
        Campus.raw_objects.filter(church=church, is_active=True)
        .exclude(growth_stage="headquarters")
        .select_related("report_to", "campus_leader__user")
        .prefetch_related(
            Prefetch(
                "members",
                queryset=CampusMembership.raw_objects.filter(
                    church=church, is_active=True
                ).select_related("member__user"),
            )
        )
        .order_by("name")
    )
    all_campuses_list = list(all_campuses_qs)
    for c in all_campuses_list:
        c.workspace_config_json = _json.dumps(getattr(c, "workspace_config", {}) or {})

    campus_cfg2 = settings_obj.campus_growth_stages or {} if settings_obj else {}
    if isinstance(campus_cfg2, list):
        cs_enabled = True
        cs_list = [
            {"name": s, "description": "", "order": i + 1}
            for i, s in enumerate(campus_cfg2)
        ]
    else:
        cs_enabled = campus_cfg2.get("enabled", True)
        cs_list = sorted(campus_cfg2.get("stages", []), key=lambda x: x.get("order", 0))

    lookup_tables = [
        (
            "channel",
            "Visit Channels",
            VisitChannel.raw_objects.filter(church=church).order_by("order"),
        ),
        (
            "purpose",
            "Visit Purposes",
            VisitPurpose.raw_objects.filter(church=church).order_by("order"),
        ),
        (
            "service",
            "Church Services",
            ChurchService.raw_objects.filter(church=church).order_by("order"),
        ),
    ]

    unit_module_fields = [
        ("guest_management", "Guest Management", "orange"),
        ("music_module", "Music", "green"),
        ("media_module", "Media", "blue"),
        ("children_module", "Children", "yellow"),
        ("youth_module", "Youth", "purple"),
        ("teenagers_module", "Teenagers", "red"),
    ]

    inherited_settings = {
        "name": church.name,
        "timezone": church.timezone,
        "primary_color": church.primary_color,
        "logo_url": church.logo_url,
    }

    return render(
        request,
        "tenants/campus_workspace.html",
        {
            "church": church,
            "campus": campus,
            "page_title": f"{campus.name} — Campus Settings",
            "workspace_cfg": workspace_cfg,
            "module_states": module_states,
            "inherited_settings": inherited_settings,
            "settings_obj": settings_obj,
            # Full settings-mirror context
            "forms": forms_map,
            "active_tab": request.GET.get("tab", "general"),
            "lookup_tables": lookup_tables,
            "all_courses": LMSCourse.raw_objects.filter(
                church=church, is_active=True
            ).order_by("course_type", "title"),
            "all_units_list": ChurchUnit.raw_objects.filter(
                church=church, is_active=True
            ).order_by("unit_type", "name"),
            "all_members_list": _ChurchMember.raw_objects.filter(
                church=church, is_active=True
            ).select_related("user"),
            "all_campuses": all_campuses_list,
            "campus_stages_enabled": cs_enabled,
            "campus_stages_list": cs_list,
            "workforce_stages": WorkforceStage.raw_objects.filter(
                church=church
            ).order_by("order"),
            "workforce_roles": WorkforceRole.raw_objects.filter(church=church).order_by(
                "order"
            ),
            "membership_tracks": tracks_list,
            "default_membership_track": default_membership_track,
            "induction_courses": LMSCourse.raw_objects.filter(
                church=church, course_type="induction", is_active=True
            ),
            "visit_channels": VisitChannel.raw_objects.filter(church=church).order_by(
                "order"
            ),
            "visit_purposes": VisitPurpose.raw_objects.filter(church=church).order_by(
                "order"
            ),
            "church_services": ChurchService.raw_objects.filter(church=church).order_by(
                "order"
            ),
            "unit_roles": UnitRole.raw_objects.filter(church=church, is_active=True)
            .select_related("unit")
            .order_by("unit__name", "order"),
            "unit_module_fields": unit_module_fields,
            "tier_choices": WORKFORCE_TIER_CHOICES,
            "unit_tier_choices": UNIT_TIER_CHOICES,
            "unit_form": UnitQuickCreateForm(church=church, settings=settings_obj),
        },
    )


@require_http_methods(["POST"])
def campus_stage_save(request):
    """Save the full campus_growth_stages config (enabled flag + ordered list)."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    settings_obj, _ = ChurchSetting.objects.get_or_create(church=church)
    enabled = request.POST.get("enabled", "true").lower() == "true"

    # stages is a JSON array sent from the frontend
    import json

    try:
        stages_raw = request.POST.get("stages", "[]")
        stages = json.loads(stages_raw)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid stages data"}, status=400)

    # Validate each entry
    clean_stages = []
    for i, s in enumerate(stages):
        name = (s.get("name") or "").strip()
        if not name:
            continue
        clean_stages.append(
            {
                "name": name,
                "description": (s.get("description") or "").strip(),
                "order": i + 1,
            }
        )

    settings_obj.campus_growth_stages = {"enabled": enabled, "stages": clean_stages}
    settings_obj.save(update_fields=["campus_growth_stages", "updated_at"])
    return JsonResponse({"ok": True, "count": len(clean_stages)})


@require_http_methods(["POST"])
def lms_course_create(request):
    """Quick-create an LMS course from the settings page."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from lms.models import LMSCourse
    from units.models import ChurchUnit

    title = request.POST.get("title", "").strip()
    if not title:
        return JsonResponse({"ok": False, "error": "Title is required"}, status=400)

    course_type = request.POST.get("course_type", "unit_training")
    delivery = request.POST.get("delivery_mode", "physical")
    passing = int(request.POST.get("passing_score", 70))
    allow_retake = request.POST.get("allow_retake", "true").lower() == "true"
    max_retakes = int(request.POST.get("max_retakes", 2))
    strict_seq = request.POST.get("strict_sequence", "true").lower() == "true"
    unit_id = request.POST.get("unit_id") or None

    member = getattr(request, "member", None)
    unit = None
    if unit_id:
        unit = ChurchUnit.raw_objects.filter(id=unit_id, church=church).first()

    course = LMSCourse.raw_objects.create(
        church=church,
        title=title,
        course_type=course_type,
        delivery_mode=delivery,
        passing_score=passing,
        allow_retake=allow_retake,
        max_retakes=max_retakes,
        strict_sequence=strict_seq,
        unit=unit,
        created_by=member,
        is_active=True,
    )
    return JsonResponse(
        {
            "ok": True,
            "id": course.id,
            "slug": course.slug,
            "title": course.title,
            "course_type": course.course_type,
            "course_type_display": course.get_course_type_display(),
            "delivery_mode": course.delivery_mode,
            "delivery_mode_display": course.get_delivery_mode_display(),
            "passing_score": course.passing_score,
        }
    )


@require_http_methods(["POST"])
def membership_track_save(request, pk=None):
    """Create or update a MembershipTrack."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from guests.models import MembershipTrack

    name = request.POST.get("name", "").strip()
    description = request.POST.get("description", "").strip()
    threshold = int(request.POST.get("attendance_threshold", 4))
    window = int(request.POST.get("attendance_window_weeks", 6))
    is_default = request.POST.get("is_default", "false").lower() == "true"
    lms_course_ids = [
        int(cid) for cid in request.POST.getlist("lms_course_ids") if str(cid).isdigit()
    ]
    if not lms_course_ids:
        legacy_course_id = request.POST.get("lms_course_id", "").strip()
        if legacy_course_id.isdigit():
            lms_course_ids = [int(legacy_course_id)]

    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)

    if pk:
        track = get_object_or_404(MembershipTrack.raw_objects, id=pk, church=church)
        track.name = name
        track.description = description
        track.attendance_threshold = threshold
        track.attendance_window_weeks = window
    else:
        if MembershipTrack.raw_objects.filter(
            church=church, name__iexact=name
        ).exists():
            return JsonResponse(
                {"ok": False, "error": f"Track '{name}' already exists"}, status=400
            )
        track = MembershipTrack.raw_objects.create(
            church=church,
            name=name,
            description=description,
            attendance_threshold=threshold,
            attendance_window_weeks=window,
            is_default=is_default,
            is_active=True,
        )

    from lms.models import LMSCourse

    selected_courses_qs = LMSCourse.raw_objects.filter(
        church=church, id__in=lms_course_ids, is_active=True
    ).select_related("membership_track")
    selected_courses = list(selected_courses_qs)
    primary_course = selected_courses[0] if selected_courses else None
    moved_from_track_ids = {
        course.membership_track_id
        for course in selected_courses
        if course.membership_track_id and course.membership_track_id != track.id
    }

    if pk:
        LMSCourse.raw_objects.filter(church=church, membership_track=track).exclude(
            id__in=lms_course_ids
        ).update(membership_track=None)

    if selected_courses:
        for course in selected_courses:
            course.membership_track = track
            course.save(update_fields=["membership_track"])
        track.lms_course = primary_course
        for old_track_id in moved_from_track_ids:
            old_track = MembershipTrack.raw_objects.filter(
                church=church, id=old_track_id
            ).first()
            if old_track:
                remaining = list(old_track.lms_courses.all())
                old_track.lms_course = remaining[0] if remaining else None
                old_track.save(update_fields=["lms_course"])
    elif pk:
        track.lms_course = None

    if is_default:
        # Only one track can be default
        MembershipTrack.raw_objects.filter(church=church).exclude(pk=track.pk).update(
            is_default=False
        )
        track.is_default = True

    track.save()
    return JsonResponse(
        {
            "ok": True,
            "id": track.id,
            "name": track.name,
            "is_default": track.is_default,
            "attendance_threshold": track.attendance_threshold,
            "attendance_window_weeks": track.attendance_window_weeks,
            "lms_course_title": track.lms_course.title if track.lms_course else "",
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Track step delete — AJAX endpoint used by the Trainings settings tab
# ─────────────────────────────────────────────────────────────────────────────


@login_required
@require_POST
def track_step_delete(request, pk):
    """
    DELETE a MembershipTrackStep.
    Called from the inline ✕ button on each step row in settings → Trainings.
    Returns JSON {ok: true} or {ok: false, error: "..."}.
    """
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from guests.models import MembershipTrackStep, StepRequirement

    step = get_object_or_404(MembershipTrackStep.raw_objects, id=pk, church=church)

    # Clean up the orphaned StepRequirement if this was the only step using it
    req = step.requirement
    step.delete()
    if req:
        still_used = MembershipTrackStep.raw_objects.filter(requirement=req).exists()
        if not still_used:
            req.delete()

    return JsonResponse({"ok": True})
