from datetime import datetime, timedelta
import pytz

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.contrib.auth.models import Group
from django.contrib.auth.views import LoginView
from django.contrib.auth import views as auth_views
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.utils.timezone import localtime, now
from django.views.decorators.http import require_POST
from .forms import CustomUserCreationForm, CustomUserChangeForm
from .models import CustomUser, ChurchMember
from units.models import ChurchUnit, UnitMembership
from workforce.models import (
    AttendanceRecord,
    ClockRecord,
    WorkforceMember,
    WorkforceMembershipRole,
    WorkforceTraineeProfile,
)
from workforce.utils import classify_event_location
from permissions.models import UnitRole
from tenants.middleware import ACTIVE_CHURCH_SESSION_KEY
from accounts.username_utils import (
    format_username,
    normalize_username_base,
    username_preview,
)
from tenants.time_utils import format_time_value

try:
    from services.models import Event
except ImportError:
    Event = None


User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def has_perm(request, perm):
    """Delegate to PermissionResolver via request.permissions."""
    if request.user.is_superuser:
        return True
    perms = getattr(request, "permissions", None)
    if perms and perms.can(perm):
        return True
    if perm != "dashboard.admin":
        return False
    church = getattr(request, "church", None)
    if not church:
        return False
    return ChurchMember.raw_objects.filter(
        church=church,
        user=request.user,
        is_active=True,
        is_admin=True,
    ).exists()


def _is_active_trainee(request):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not church or not member:
        return False
    return WorkforceTraineeProfile.raw_objects.filter(
        church=church,
        member=member,
        reason="induction",
        is_active=True,
    ).exists()


def _scoped_url(request, url_or_path):
    """Return slug-prefixed URL when using app host or slug paths."""
    # Accept either a URL name or a raw path
    if isinstance(url_or_path, str) and url_or_path.startswith("/"):
        path = url_or_path
    else:
        try:
            path = reverse(url_or_path)
        except Exception:
            path = "/"

    church = getattr(request, "church", None)
    if not church:
        return path

    # If already slug-prefixed, don't add again
    if path.startswith(f"/{church.slug}/"):
        return path

    host = request.get_host().split(":")[0]
    app_domains = list(getattr(settings, "APP_DOMAINS", []) or [])
    app_hosts = [domain.split(":")[0] for domain in app_domains]

    if request.path.startswith(f"/{church.slug}/") or host in app_hosts:
        if not path.startswith("/"):
            path = "/" + path
        return f"/{church.slug}{path}"
    return path


def _scoped_absolute_url(request, url_or_path):
    """Absolute URL variant of `_scoped_url`, preserving current scheme/host."""
    return request.build_absolute_uri(_scoped_url(request, url_or_path))


def _church_coordinates(church):
    """
    Return (lat, lon) for a church from Church.latitude / Church.longitude.
    Both fields are mandatory on the Church model — no fallback needed.
    """
    return float(church.latitude), float(church.longitude)


def haversine_distance(lat1, lon1, lat2, lon2):
    import math

    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────


class CustomLoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        self.request = kwargs.get("request")
        super().__init__(*args, **kwargs)
        church = getattr(self.request, "church", None) if self.request else None
        if church:
            preview = username_preview(church)
            self.fields["username"].widget.attrs["data-username-prefix"] = preview[
                "prefix"
            ]
            self.fields["username"].widget.attrs["data-username-suffix"] = preview[
                "suffix"
            ]


# apps/accounts/views.py


class CustomLoginView(LoginView):
    form_class = CustomLoginForm
    template_name = "accounts/login.html"

    def form_valid(self, form):
        remember_me = self.request.POST.get("remember_me")
        if not remember_me:
            self.request.session.set_expiry(0)
        else:
            self.request.session.set_expiry(60 * 60 * 24 * 28)
        church = getattr(self.request, "church", None)
        if church:
            self.request.session[ACTIVE_CHURCH_SESSION_KEY] = church.slug
        return super().form_valid(form)

    def get_success_url(self):
        church = getattr(self.request, "church", None)
        intent = (
            self.request.POST.get("intent")
            or self.request.GET.get("intent")
            or self.request.GET.get("highlight")
        )

        if church and intent in ("saas", "white_label"):
            return _scoped_url(
                self.request,
                f"/billing/pricing/?highlight={intent}",
            )

        # 1. Get the current church from the request context
        # 2. Check if the welcome screen is enabled in settings
        if church and hasattr(church, "settings"):
            if church.settings.enable_welcome_screen:
                return _scoped_url(self.request, "post_login_redirect")

        # 3. If disabled or church not found, go straight to dashboard logic
        if _is_active_trainee(self.request):
            return _scoped_url(self.request, "dashboard:trainee_dashboard")
        if has_perm(self.request, "dashboard.admin"):
            return _scoped_url(self.request, "dashboard:admin_dashboard")
        return _scoped_url(self.request, "dashboard:dashboard")


def username_check(request):
    """AJAX endpoint: check whether a username exists in this church context.

    GET /accounts/username-check/?u=<value>

    Returns JSON {exists: bool} so the login page can show is-valid /
    is-invalid feedback without submitting the form.  Checks raw value,
    stripped base, and decorated variant so the result is accurate for both
    formats a member might type.
    """
    from accounts.models import CustomUser
    from accounts.username_utils import login_username_candidates

    value = (request.GET.get("u") or "").strip()
    if not value or len(value) < 2:
        return JsonResponse({"exists": None})

    church = getattr(request, "church", None)
    candidates = login_username_candidates(value, church=church)

    qs = CustomUser.objects.none()
    for candidate in candidates:
        if church:
            qs = CustomUser.objects.filter(
                church_memberships__church=church,
                username__iexact=candidate,
            )
        else:
            qs = CustomUser.objects.filter(username__iexact=candidate)
        if qs.exists():
            return JsonResponse({"exists": True})

    return JsonResponse({"exists": False})


def post_login_redirect(request):
    church = getattr(request, "church", None)
    if church:
        request.session[ACTIVE_CHURCH_SESSION_KEY] = church.slug

    # Safety: If admin turned it off, bypass the modal even if they visit the URL
    if (
        church
        and hasattr(church, "settings")
        and not church.settings.enable_welcome_screen
    ):
        if has_perm(request, "dashboard.admin"):
            return redirect(_scoped_url(request, "dashboard:admin_dashboard"))
        return redirect(_scoped_url(request, "dashboard:dashboard"))

    tz = pytz.timezone(getattr(church, "timezone", "Africa/Lagos"))
    now_in_wat = localtime(now(), timezone=tz)
    today_str = now_in_wat.strftime("%Y-%m-%d")
    day_name = now_in_wat.strftime("%A")
    time_str = now_in_wat.strftime("%I:%M %p")

    member = getattr(request, "member", None)
    is_admin = has_perm(request, "dashboard.admin")
    is_trainee = _is_active_trainee(request)
    dashboard_url = _scoped_url(
        request,
        (
            "dashboard:admin_dashboard"
            if is_admin
            else (
                "dashboard:trainee_dashboard" if is_trainee else "dashboard:dashboard"
            )
        ),
    )

    show_hint = member and not member.hint_shown
    hint_type = None
    if show_hint:
        hint_type = "admin_setup" if is_admin else "member_welcome"

    if request.session.get("welcome_shown") != today_str:
        request.session["welcome_shown"] = today_str
        request.session.modified = True
        # AI-generated Bible verse themed to today's calendar event
        try:
            from core.ai_skills import bible_verse_for_date

            verse_data = bible_verse_for_date(now_in_wat.date())
            quote = f'"{verse_data["text"]}" — {verse_data["reference"]}'
            verse_theme = verse_data.get("theme", "")
        except Exception:
            quote = "Stay faithful — your work in the Kingdom is never in vain."
            verse_theme = ""

        return render(
            request,
            "accounts/welcome_modal.html",
            {
                "day_name": day_name,
                "time_str": time_str,
                "quote": quote,
                "verse_theme": verse_theme,
                "dashboard_url": dashboard_url,
                "dashboard_label": "Proceed to Dashboard",
                "show_hint": show_hint,
                "hint_type": hint_type,
                "member": member,
                "church": church,
                "is_trainee": is_trainee,
            },
        )

    if is_admin:
        return redirect(_scoped_url(request, "dashboard:admin_dashboard"))
    if is_trainee:
        return redirect(_scoped_url(request, "dashboard:trainee_dashboard"))
    return redirect(_scoped_url(request, "dashboard:dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Member list
# ─────────────────────────────────────────────────────────────────────────────


@login_required
@require_POST
def dismiss_hint(request):
    """Mark the setup/onboarding hint as seen for this member."""
    member = getattr(request, "member", None)
    if member and not member.hint_shown:
        member.hint_shown = True
        member.save(update_fields=["hint_shown"])
    return JsonResponse({"ok": True})


@login_required
def user_list(request):
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    search_query = request.GET.get("q", "")
    stage_filter = request.GET.get("stage", "")  # trainee | probation | active | leader

    # When this church is a campus-church, members are recorded against the HQ
    # church in ChurchMember but linked here via CampusMembership.
    # Union: (a) direct ChurchMember records and (b) campus-assigned members.
    from tenants.models import CampusMembership as _CM

    hq_church = getattr(church, "hq_church", None)
    is_campus_church = bool(getattr(church, "parent_church_id", None))

    if is_campus_church and hq_church:
        campus_user_ids = set(
            _CM.raw_objects.filter(
                campus__campus_church=church,
                is_active=True,
            ).values_list("member__user_id", flat=True)
        )
        base_users = (
            CustomUser.objects.filter(
                Q(church_memberships__church=church, church_memberships__is_active=True)
                | Q(id__in=campus_user_ids)
            )
            .distinct()
            .order_by("-church_memberships__joined_at", "-id")
        )
    else:
        base_users = (
            CustomUser.objects.filter(
                church_memberships__church=church,
                church_memberships__is_active=True,
            )
            .distinct()
            .order_by("-church_memberships__joined_at", "-id")
        )

    if has_perm(request, "accounts.view_all_users"):
        users = base_users
    else:
        user_units = ChurchUnit.raw_objects.filter(
            church=church,
            memberships__workforce_member__member__user=request.user,
        ).distinct()

        users = base_users.filter(
            church_memberships__workforce_profiles__unit_memberships__unit__in=user_units
        ).distinct()

    if search_query:
        users = users.filter(
            Q(full_name__icontains=search_query)
            | Q(username__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(
                church_memberships__workforce_profiles__unit_memberships__unit__name__icontains=search_query
            )
        ).distinct()

    # Stage filter
    if stage_filter == "trainee":
        from workforce.models import WorkforceTraineeProfile

        trainee_member_ids_filter = WorkforceTraineeProfile.raw_objects.filter(
            church=church, reason="induction", is_active=True
        ).values_list("member__user_id", flat=True)
        users = users.filter(id__in=trainee_member_ids_filter)
    elif stage_filter in ("probation", "active", "leader"):
        from workforce.models import WorkforceMember

        wf_filter = WorkforceMember.raw_objects.filter(
            church=church,
            is_active=True,
        )
        if stage_filter == "probation":
            wf_user_ids = wf_filter.filter(
                Q(stage__slug="probationer")
                | Q(
                    unit_memberships__is_probation=True,
                    unit_memberships__is_active=True,
                )
            ).values_list("member__user_id", flat=True)
        else:
            wf_user_ids = wf_filter.filter(
                stage__slug=stage_filter,
            ).values_list("member__user_id", flat=True)
        users = users.filter(id__in=wf_user_ids)
    elif stage_filter == "full":  # all non-trainees
        from workforce.models import WorkforceTraineeProfile

        trainee_user_ids = WorkforceTraineeProfile.raw_objects.filter(
            church=church, reason="induction", is_active=True
        ).values_list("member__user_id", flat=True)
        users = users.exclude(id__in=trainee_user_ids)

    view_type = request.GET.get("view", "cards")
    per_page = 50 if view_type == "list" else 45

    paginator = Paginator(users, per_page)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    from permissions.services.resolver import PermissionResolver
    from units.models import UnitMembership
    from accounts.models import ChurchMember
    from guests.models import GuestEntry

    # ✅ PRELOAD MEMBERSHIPS (avoids N+1 queries)
    # For a campus-church, some users' ChurchMember records live on the HQ church.
    # Collect both sets and let campus records take precedence (they may carry
    # campus-admin overrides), otherwise fall back to the HQ-church record.
    _direct_memberships = {
        m.user_id: m
        for m in ChurchMember.raw_objects.filter(
            church=church, user__in=page_obj.object_list
        )
    }
    if is_campus_church and hq_church:
        _hq_memberships = {
            m.user_id: m
            for m in ChurchMember.raw_objects.filter(
                church=hq_church, user__in=page_obj.object_list
            )
        }
        # Merge: campus record wins if it exists (campus-admin may have overrides),
        # otherwise fall back to HQ record.
        memberships = {**_hq_memberships, **_direct_memberships}
    else:
        memberships = _direct_memberships

    def _fallback_role_label(user):
        if user.is_superuser:
            return "Superuser"

        resolver = PermissionResolver(user, church)
        if resolver.can("dashboard.admin"):
            return "Admin"
        return "Unit Head" if user.id in head_user_ids else "Member"

    page_user_ids = [u.id for u in page_obj.object_list]
    page_memberships = list(
        UnitMembership.raw_objects.filter(
            church=church,
            workforce_member__member__user_id__in=page_user_ids,
            is_active=True,
        )
        .select_related("unit", "workforce_member__member__user")
        .prefetch_related("roles__role")
    )

    user_units_map = {}
    head_user_ids = set()
    membership_ids_by_user = {}
    probation_by_user = {}
    disciplinary_probation_by_user = {}
    total_membership_count_by_user = {}   # user_id → total active memberships
    prob_membership_count_by_user = {}    # user_id → probation memberships
    for membership in page_memberships:
        user_id = membership.workforce_member.member.user_id
        user_units_map.setdefault(user_id, []).append(membership.unit.name)
        membership_ids_by_user.setdefault(user_id, []).append(membership.id)
        total_membership_count_by_user[user_id] = total_membership_count_by_user.get(user_id, 0) + 1
        if membership.is_unit_head:
            head_user_ids.add(user_id)
        if membership.is_probation:
            probation_by_user[user_id] = True
            prob_membership_count_by_user[user_id] = prob_membership_count_by_user.get(user_id, 0) + 1

        # Distinguish disciplinary probation from pipeline probation
        if membership.is_probation:
            from workforce.models import WorkforceTraineeProfile

            # If there's an active trainee profile for induction → pipeline probation
            # If no trainee profile (or reason="probation") → disciplinary
            trainee_profile = (
                WorkforceTraineeProfile.raw_objects.filter(
                    church=church,
                    member=membership.workforce_member.member,
                )
                .order_by("-created_at")
                .first()
            )
            if not trainee_profile or trainee_profile.reason == "probation":
                disciplinary_probation_by_user[user_id] = True

    guest_counts_by_membership = {
        row["assigned_to"]: row["total"]
        for row in GuestEntry.raw_objects.filter(
            church=church,
            is_deleted=False,
            assigned_to_id__in=[membership.id for membership in page_memberships],
        )
        .values("assigned_to")
        .annotate(total=Count("id"))
    }

    user_guest_count_map = {
        user_id: sum(
            guest_counts_by_membership.get(membership_id, 0)
            for membership_id in membership_ids
        )
        for user_id, membership_ids in membership_ids_by_user.items()
    }

    # Trainee detection — users who have an active WorkforceTraineeProfile
    from workforce.models import WorkforceTraineeProfile, WorkforceMember

    page_member_ids = [
        memberships[u.id].id for u in page_obj.object_list if u.id in memberships
    ]

    # For campus-churches, WorkforceTraineeProfile / WorkforceMember records also
    # live on the HQ church, so we include both church contexts in the lookups.
    _wf_church_filter = (
        Q(church=church) | Q(church=hq_church)
        if (is_campus_church and hq_church)
        else Q(church=church)
    )

    trainee_member_ids = set(
        WorkforceTraineeProfile.raw_objects.filter(
            _wf_church_filter,
            member_id__in=page_member_ids,
            reason="induction",
            is_active=True,
        ).values_list("member_id", flat=True)
    )

    # WorkforceMember stage map — HQ record unless campus admin has overridden
    # the stage locally (campus WorkforceMember takes precedence).
    _wf_stage_qs = (
        WorkforceMember.raw_objects.filter(
            _wf_church_filter,
            member_id__in=page_member_ids,
        )
        .select_related("stage")
        .order_by("church_id")  # HQ rows first so campus rows overwrite them
    )
    wf_stage_map = {}
    for wf in _wf_stage_qs:
        if wf.stage:
            wf_stage_map[wf.member_id] = wf.stage.name

    # Build unit→role_name map for display
    # unit_name and unit_role cycling in the card bottom
    unit_role_pairs_by_user = (
        {}
    )  # {user_id: [(unit_name, unit_role_label, is_probation)]}
    probation_unit_count_by_user = {}  # {user_id: int}
    for membership in page_memberships:
        uid2 = membership.workforce_member.member.user_id
        unit_name = membership.unit.name
        role_names_for_membership = [
            role_assignment.role.name
            for role_assignment in membership.roles.all()
            if getattr(role_assignment, "role", None)
            and not (
                str(getattr(role_assignment.role, "name", "")).strip().lower()
                == "member"
                and getattr(role_assignment.role, "tier", None) == 6
            )
        ]
        unit_role_label = (
            role_names_for_membership[0] if role_names_for_membership else "Member"
        )
        unit_role_pairs_by_user.setdefault(uid2, []).append(
            (unit_name, unit_role_label, membership.is_probation)
        )
        if membership.is_probation:
            probation_unit_count_by_user[uid2] = (
                probation_unit_count_by_user.get(uid2, 0) + 1
            )

    # Also get global WorkforceRole names for each user
    from workforce.models import WorkforceMembershipRole

    # wf_role_names must cover both church and hq_church so HQ-assigned members
    # show their global workforce role on the campus list.
    _wf_role_church_filter = (
        Q(church=church) | Q(church=hq_church)
        if (is_campus_church and hq_church)
        else Q(church=church)
    )
    wf_role_names_by_member = {}
    for wmr in (
        WorkforceMembershipRole.raw_objects.filter(
            _wf_role_church_filter,
            workforce_member__member_id__in=page_member_ids,
        )
        .select_related("role", "workforce_member__member")
        .order_by("role__order", "role__name")
    ):
        mid = wmr.workforce_member.member_id
        wf_role_names_by_member.setdefault(mid, []).append(wmr.role.name)

    # Per-user: is this user's ChurchMember record on the HQ church?
    # (i.e. they were assigned here via CampusMembership, not created natively)
    direct_user_ids = set(_direct_memberships.keys())

    enriched_page = [
        {
            "user": u,
            "member": memberships.get(u.id),
            "units": user_units_map.get(u.id, []),
            "unit_role_pairs": unit_role_pairs_by_user.get(u.id, []),
            "wf_role_names": wf_role_names_by_member.get(
                memberships[u.id].id if u.id in memberships else -1, []
            ),
            "guest_count": user_guest_count_map.get(u.id, 0),
            "is_trainee": (
                memberships.get(u.id) and memberships[u.id].id in trainee_member_ids
            ),
            "is_probation": probation_by_user.get(u.id, False),
            "probation_unit_count": probation_unit_count_by_user.get(u.id, 0),
            "workforce_stage": (
                wf_stage_map.get(memberships[u.id].id) if u.id in memberships else None
            ),
            "is_disciplinary_probation": disciplinary_probation_by_user.get(
                u.id, False
            ),
            # True only when ALL active memberships are on probation (global lock)
            "is_global_probation": (
                bool(probation_by_user.get(u.id))
                and total_membership_count_by_user.get(u.id, 0) > 0
                and prob_membership_count_by_user.get(u.id, 0) >= total_membership_count_by_user.get(u.id, 0)
            ),
            # True when the user has at least one membership and at least one (but not all) is probated
            "is_unit_probation_only": (
                bool(probation_by_user.get(u.id))
                and prob_membership_count_by_user.get(u.id, 0) < total_membership_count_by_user.get(u.id, 1)
            ),
            # True when this user was assigned/transferred from HQ (their ChurchMember
            # lives on the HQ church; custom_id and stage come from there).
            "is_hq_assigned": is_campus_church and u.id not in direct_user_ids,
        }
        for u in page_obj.object_list
    ]

    page_obj.enriched = enriched_page

    available_units = ChurchUnit.raw_objects.filter(
        church=church, is_active=True
    ).order_by("name")

    return render(
        request,
        "accounts/user_list.html",
        {
            "page_obj": page_obj,
            "view_type": view_type,
            "search_query": search_query,
            "page_title": "Workforce",
            "available_units": available_units,
            "stage_filter": stage_filter,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Member create / edit
# ─────────────────────────────────────────────────────────────────────────────
# AJAX: return roles for a selected unit (used by cascading dropdown in user form)
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def unit_roles_json(request):
    """
    GET /accounts/users/unit-roles/?unit_id=<id>
    Returns JSON list of {id, name, tier} for UnitRoles belonging to the given unit.
    Used by the cascading unit → role dropdowns in the user creation form.
    """
    church = getattr(request, "church", None)
    unit_id = request.GET.get("unit_id", "").strip()
    if not church or not unit_id or not unit_id.isdigit():
        return JsonResponse({"roles": []})

    roles = (
        UnitRole.raw_objects.filter(church=church, unit_id=int(unit_id), is_active=True)
        .order_by("order", "name")
        .values("id", "name", "tier")
    )
    return JsonResponse({"roles": list(roles)})


# ─────────────────────────────────────────────────────────────────────────────


@login_required
def manage_user(request, username=None):
    """
    Combined create / edit view for church members.

    On creation:
        - form.save() generates credentials (username + temp password)
          if admin left them blank.
        - Credentials are stored in the session and the admin is redirected
          to the credential display page (show_credentials).
        - Credentials are cleared from the session immediately after display.

    On edit:
        - Structural fields (roles, units) are updated by the form.
        - Password change handled separately via SetPasswordForm.
    """
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not has_perm(request, "accounts.manage_users"):
        messages.error(request, "You don't have permission to manage users.")
        return redirect("accounts:user_list")

    is_edit = username is not None
    user_obj = get_object_or_404(CustomUser, username=username) if is_edit else None
    section = request.GET.get("section", "edit" if is_edit else "single")

    trainee_profile = None
    workforce_profile = None
    assigned_workforce_role = None
    user_memberships = []

    # Probation / membership context defaults
    memberships = []
    probation_unit_ids = set()
    probation_hold_memberships = []
    probation_service_memberships = []
    active_membership_count = 0
    active_probation_count = 0

    if is_edit:
        # A member can be in this church either via a direct ChurchMember record
        # (native or HQ member) or via a CampusMembership when this church is a
        # campus-church and the member's ChurchMember lives on the HQ church.
        from tenants.models import CampusMembership as _CM, Campus as _Campus

        in_church = ChurchMember.raw_objects.filter(
            user=user_obj, church=church
        ).exists()

        if not in_church:
            # Check whether this is a campus-church and the user is assigned here
            # via a CampusMembership (member record lives on HQ church).
            hq_church = getattr(church, "hq_church", None)
            is_campus_church = bool(getattr(church, "parent_church_id", None))
            if is_campus_church and hq_church:
                in_church = _CM.raw_objects.filter(
                    campus__campus_church=church,
                    member__user=user_obj,
                    is_active=True,
                ).exists()

        if not in_church:
            return HttpResponseForbidden("User not in this church.")

        # Track whether this user's ChurchMember lives on the HQ church
        # (i.e. they were assigned here from HQ) vs. being a native campus member.
        _hq_church = getattr(church, "hq_church", None)
        _is_hq_assigned = not ChurchMember.raw_objects.filter(
            user=user_obj, church=church
        ).exists()
        # The church under which their ChurchMember (custom_id, stage) is stored.
        _member_church = _hq_church if _is_hq_assigned and _hq_church else church

        from workforce.models import WorkforceTraineeProfile as _WTP

        workforce_profile = (
            WorkforceMember.raw_objects.filter(
                church=_member_church,
                member__user=user_obj,
            )
            .select_related("stage", "member")
            .first()
        )
        trainee_profile = (
            _WTP.raw_objects.filter(
                church=_member_church,
                member__user=user_obj,
                reason="induction",
                is_active=True,
            )
            .select_related(
                "preferred_unit", "probation_unit", "lms_enrollment__course"
            )
            .first()
        )
        from units.models import UnitMembership as _UM, ChurchUnit as _CU

        user_memberships = list(
            _UM.raw_objects.filter(
                church=_member_church,
                workforce_member__member__user=user_obj,
                is_active=True,
            ).select_related("unit")
        )
        assigned_workforce_role = (
            WorkforceMembershipRole.raw_objects.filter(
                church=_member_church,
                workforce_member=workforce_profile,
            )
            .select_related("role")
            .order_by("role__order", "role__name")
            .first()
            if workforce_profile
            else None
        )

    FormClass = CustomUserChangeForm if is_edit else CustomUserCreationForm
    form_kwargs = {
        "data": request.POST or None,
        "files": request.FILES or None,
        "instance": user_obj,
        "current_user": request.user,
        "church": church,
    }
    if is_edit:
        form_kwargs["edit_mode"] = True
        form_kwargs["trainee_profile"] = trainee_profile

    form = FormClass(**form_kwargs)
    password_form = SetPasswordForm(user_obj) if is_edit else None

    if request.method == "POST":
        section = request.POST.get("section", section)

        # ── Delete ────────────────────────────────────────────────────
        if "delete_user" in request.POST and is_edit:
            if user_obj.is_superuser:
                messages.error(request, "Cannot delete a superuser.")
            else:
                name = user_obj.full_name
                user_obj.delete()
                messages.success(request, f"{name} has been removed.")
            return redirect("accounts:user_list")

        # ── Password change (edit only) ───────────────────────────────
        elif "change_password" in request.POST and is_edit:
            password_form = SetPasswordForm(user_obj, request.POST)
            if password_form.is_valid():
                password_form.save()
                messages.success(request, f"Password updated for {user_obj.full_name}.")
                return redirect(
                    reverse("accounts:edit_user", kwargs={"username": user_obj.username})
                    + "?section=profile"
                )
            else:
                messages.error(request, "Please correct the password errors.")

        # ── Create / update ───────────────────────────────────────────
        else:
            if not request.user.is_superuser:
                form.instance.is_superuser = False

            if form.is_valid():
                saved_user = form.save()

                if is_edit:
                    # Save probation_unit for trainees (if submitted)
                    probation_unit_id = request.POST.get(
                        "probation_unit_id", ""
                    ).strip()
                    trainee_ready_for_probation_unit = bool(
                        trainee_profile
                        and trainee_profile.reason == "induction"
                        and trainee_profile.is_eligible_for_promotion()
                    )
                    if probation_unit_id and trainee_ready_for_probation_unit:
                        from units.models import ChurchUnit as _CU

                        pu = _CU.raw_objects.filter(
                            church=church, id=probation_unit_id
                        ).first()
                        if pu and not trainee_profile.probation_unit:
                            trainee_profile.probation_unit = pu
                            trainee_profile.save(update_fields=["probation_unit"])

                    messages.success(
                        request, f"{saved_user.full_name} updated successfully."
                    )
                    # Use reverse() so Django prepends the tenant prefix set by
                    # set_script_prefix() in the middleware (raw request.path is
                    # the slug-stripped path and would 404 on path-slug tenants).
                    return redirect(
                        reverse("accounts:edit_user", kwargs={"username": saved_user.username})
                        + "?section=profile"
                    )

                else:
                    # ── New member: store credentials in session ──────
                    # Credentials are shown ONCE on the next page, then
                    # cleared. Never stored in a message or the URL.
                    # Fetch the ChurchMember to get the custom_id
                    from accounts.models import ChurchMember as CM

                    cm = CM.raw_objects.filter(church=church, user=saved_user).first()

                    request.session["new_member_credentials"] = {
                        "title": saved_user.title,
                        "full_name": saved_user.full_name,
                        "member_id": cm.custom_id if cm else None,
                        "username": form.generated_username,
                        "temp_password": form.generated_password,
                    }

                    if "save_add_another" in request.POST:
                        # Show credentials then loop back to create another
                        request.session["credentials_next"] = reverse(
                            "accounts:create_user"
                        )
                    else:
                        request.session["credentials_next"] = reverse(
                            "accounts:user_list"
                        )

                    return redirect("accounts:show_credentials")

            else:
                messages.error(request, "Please correct the errors below.")

    # ── Context for GET / invalid POST ───────────────────────────────
    units = ChurchUnit.raw_objects.filter(church=church, is_active=True)

    unit_roles_data = [
        {
            "id": unit.id,
            "name": unit.name,
            "color_class": unit.color_class,
            "color_hex": unit.color_hex,
        }
        for unit in units
    ]

    if is_edit:
        from django.db.models import Q

        membership_rows = list(
            UnitMembership.raw_objects.filter(church=_member_church)
            .filter(
                Q(workforce_member__member__user=user_obj)
                | Q(trainee_profile__member__user=user_obj)
            )
            .select_related(
                "unit",
                "workforce_member__member__user",
                "trainee_profile__member__user",
            )
            .prefetch_related("roles__role")
        )

        probation_hold_memberships = [
            m for m in membership_rows if m.is_probation and not m.is_active
        ]
        probation_service_memberships = [
            m for m in membership_rows if m.is_probation and m.is_active
        ]
        # Unit-level disciplinary probation is keyed by the original unit where
        # discipline happened; the active service unit is stored as the paired
        # destination.
        probation_unit_ids = {
            str(m.unit_id) for m in (probation_hold_memberships or probation_service_memberships)
        }
        active_membership_count = sum(1 for m in membership_rows if m.is_active)
        active_probation_count = sum(
            1 for m in membership_rows if m.is_active and m.is_probation
        )

        for membership in membership_rows:
            role_names = [
                role_assignment.role.name
                for role_assignment in membership.roles.all()
                if getattr(role_assignment, "role", None)
                and not (
                    str(getattr(role_assignment.role, "name", "")).strip().lower()
                    == "member"
                    and getattr(role_assignment.role, "tier", None) == 6
                )
            ]
            memberships.append(
                {
                    "unit_id": membership.unit_id,
                    "unit__name": membership.unit.name,
                    "unit__unit_type": membership.unit.unit_type,
                    "unit__color_hex": membership.unit.color_hex,
                    "is_active": membership.is_active,
                    "roles": role_names,
                    "is_probation": membership.is_probation,
                    "workforce_stage": (
                        workforce_profile.stage.slug
                        if workforce_profile and workforce_profile.stage
                        else ""
                    ),
                    # True when this specific membership is the one serving probation.
                    # Used to highlight + lock the unit-role row in the assignment card.
                    "is_serving_probation": membership.is_probation,
                }
            )

    preloadedUnitRoles = {
        "units": unit_roles_data,
        "userMemberships": memberships,
    }
    workforce_roles_data = [
        {
            "id": role.id,
            "tier": getattr(role, "tier", 6),
            "name": role.name,
        }
        for role in form.fields["workforce_roles"].queryset
    ]

    return render(
        request,
        "accounts/user_form.html",
        {
            "form": form,
            "edit_mode": is_edit,
            "user_obj": user_obj,
            "password_form": password_form,
            "preloadedUnitRoles": preloadedUnitRoles,
            "section": section,
            "workforce_profile": workforce_profile,
            "assigned_workforce_role": assigned_workforce_role,
            "member_memberships": memberships,
            "probation_unit_ids": list(probation_unit_ids) if is_edit else [],
            "probation_hold_memberships": probation_hold_memberships,
            "probation_service_memberships": probation_service_memberships,
            "is_induction_trainee": bool(
                trainee_profile and trainee_profile.reason == "induction"
            ),
            # is_global_probation: ONLY when every active membership is on probation
            # AND there is more than one membership (a single-unit probation must
            # never disable the whole edit section — the member may be in other units).
            "is_global_probation": bool(
                is_edit
                and workforce_profile
                and workforce_profile.stage
                and workforce_profile.stage.slug == "probationer"
                and active_membership_count > 1  # must have multiple memberships
                and active_probation_count > 0
                and active_probation_count
                == active_membership_count  # ALL on probation
            ),
            # HQ-assignment context — tells the template to show HQ custom_id/stage
            # for this member and which fields the campus admin may override.
            "is_hq_assigned": _is_hq_assigned if is_edit else False,
            "hq_member": (
                ChurchMember.raw_objects.filter(
                    user=user_obj, church=_member_church
                ).first()
                if is_edit and _is_hq_assigned
                else None
            ),
            "workforce_roles_data": workforce_roles_data,
            "trainee_profile": trainee_profile,
            "trainee_probation_assignment_enabled": bool(
                trainee_profile
                and trainee_profile.reason == "induction"
                and trainee_profile.is_eligible_for_promotion()
            ),
            "user_memberships": user_memberships if is_edit else [],
            "available_units": (
                __import__("units.models", fromlist=["ChurchUnit"])
                .ChurchUnit.raw_objects.filter(church=church, is_active=True)
                .order_by("name")
                if is_edit
                else []
            ),
            "page_title": (
                "Workforce - Edit Member" if is_edit else "Workforce - Add Member"
            ),
            # Batch add modal context
            "workforce_stages": (
                __import__("workforce.models", fromlist=["WorkforceStage"])
                .WorkforceStage.raw_objects.filter(church=church, is_active=True)
                .order_by("order", "name")
                if not is_edit
                else []
            ),
            "workforce_roles_list": (
                __import__("workforce.models", fromlist=["WorkforceRole"])
                .WorkforceRole.raw_objects.filter(church=church, is_active=True)
                .order_by("name")
                if not is_edit
                else []
            ),
            "batch_units": list(
                ChurchUnit.raw_objects.filter(
                    church=church, is_active=True, unit_type="unit"
                )
                .order_by("name")
                .values("id", "name")
            ),
            "batch_groups": list(
                ChurchUnit.raw_objects.filter(
                    church=church, is_active=True, unit_type="group"
                )
                .order_by("name")
                .values("id", "name")
            ),
            # Pull title choices directly from the model so the batch modal
            # stays in sync with CustomUser.TITLE_CHOICES automatically.
            "user_title_choices": CustomUser.TITLE_CHOICES,
            # Dropdown to jump between members in edit mode
            "all_members": (
                CustomUser.objects.filter(church_memberships__church=church)
                .distinct()
                .order_by("full_name", "username")
                if is_edit
                else []
            ),
        },
    )


import csv
import io
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# HELPER — shared member creation logic used by single-form, batch, and bulk
# ─────────────────────────────────────────────────────────────────────────────


def _create_single_member(
    church,
    full_name,
    email="",
    phone="",
    unit_id=None,
    unit_name="",
    role_name="",
    title="",
    image=None,
    created_by=None,
):
    """
    Create a CustomUser + ChurchMember for the given church.

    Generates a username from full_name (lowercased, de-spaced) and a
    random temporary password. Applies username prefix/suffix if the church
    has them configured.

    Returns a dict: {user, member, username, temp_password}
    Raises ValueError with a human-readable message on failure.
    """
    from django.contrib.auth import get_user_model
    from django.utils.text import slugify
    import secrets, string

    User = get_user_model()

    if not full_name:
        raise ValueError("full_name is required")

    # ── Generate username ─────────────────────────────────────────────────
    base = slugify(full_name).replace("-", "")[:20] or "member"
    username = base
    n = 1
    while User.objects.filter(username=username).exists():
        username = f"{base}{n}"
        n += 1

    # ── Apply church prefix/suffix if configured ──────────────────────────
    settings_obj = getattr(church, "settings", None)
    prefix = getattr(settings_obj, "username_prefix", "") or ""
    suffix_on = getattr(settings_obj, "username_use_church_slug_suffix", False)
    # The stored username is bare (no decorators); decorators are display-only.
    # This keeps Django auth working normally.

    # ── Temp password ─────────────────────────────────────────────────────
    alphabet = string.ascii_letters + string.digits
    temp_password = "".join(secrets.choice(alphabet) for _ in range(10))

    # ── Create User ───────────────────────────────────────────────────────
    user = User.objects.create_user(
        username=username,
        email=email or "",
        password=temp_password,
        full_name=full_name,
    )
    if phone:
        user.phone_number = phone
        user.save(update_fields=["phone_number"])

    # ── Create ChurchMember ───────────────────────────────────────────────
    member = ChurchMember.raw_objects.create(
        church=church,
        user=user,
        is_active=True,
    )

    # ── Optional: assign to unit ──────────────────────────────────────────
    resolved_unit = None
    if unit_id:
        from units.models import ChurchUnit

        try:
            resolved_unit = ChurchUnit.raw_objects.get(
                church=church, id=int(unit_id), is_active=True
            )
        except (ChurchUnit.DoesNotExist, ValueError):
            pass
    elif unit_name:
        from units.models import ChurchUnit

        resolved_unit = ChurchUnit.raw_objects.filter(
            church=church, name__iexact=unit_name.strip(), is_active=True
        ).first()

    if resolved_unit:
        from units.models import UnitMembership

        # Workforce member must exist first (created on first unit assignment)
        from workforce.models import WorkforceMember, WorkforceStage

        default_stage = (
            WorkforceStage.raw_objects.filter(
                church=church, slug="probationer", is_active=True
            ).first()
            or WorkforceStage.raw_objects.filter(church=church, is_active=True)
            .order_by("order")
            .first()
        )
        if default_stage:
            wf, _ = WorkforceMember.raw_objects.get_or_create(
                church=church,
                member=member,
                defaults={"stage": default_stage, "is_active": True},
            )
            UnitMembership.raw_objects.get_or_create(
                church=church,
                workforce_member=wf,
                unit=resolved_unit,
                defaults={"is_active": True},
            )

    return {
        "user": user,
        "member": member,
        "username": username,
        "display_username": f"{prefix}{username}{'.'+church.slug if suffix_on else ''}",
        "temp_password": temp_password,
    }


# ─────────────────────────────────────────────────────────────────────────────
# VIEW 1 — In-form batch: up to 10 rows in one POST
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def batch_create_users(request):
    """
    Handle the batch-add inline section on the user management page.
    Accepts up to 10 members submitted as parallel lists.

    POST keys:
        full_name[]           required
        title[]               optional
        email[]               optional
        phone[]               optional
        workforce_stage_id[]  optional — WorkforceStage PK
        workforce_role_id[]   optional — WorkforceRole PK
        unit_id[]             optional — ChurchUnit PK (unit type)
        group_id[]            optional — ChurchUnit PK (group type)
        set_probation[]       optional — checkbox value "1" per row
        image[]               optional — file upload
    """
    from workforce.models import WorkforceMember, WorkforceStage, WorkforceRole
    from units.models import ChurchUnit, UnitMembership

    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not has_perm(request, "accounts.manage_users"):
        messages.error(request, "You don't have permission to manage users.")
        return redirect("accounts:user_list")

    if request.method != "POST":
        return redirect("accounts:create_user")

    full_names = request.POST.getlist("full_name[]")
    titles = request.POST.getlist("title[]")
    emails = request.POST.getlist("email[]")
    phones = request.POST.getlist("phone[]")
    stage_ids = request.POST.getlist("workforce_stage_id[]")
    role_ids = request.POST.getlist("workforce_role_id[]")
    unit_ids = request.POST.getlist("unit_id[]")
    group_ids = request.POST.getlist("group_id[]")
    set_probations = request.POST.getlist("set_probation[]")
    images = request.FILES.getlist("image[]")

    if len(full_names) > 10:
        messages.error(
            request,
            "Maximum 10 members per in-form batch. Use bulk CSV upload for larger sets.",
        )
        return redirect("accounts:create_user")

    def _get(lst, i, default=""):
        return lst[i].strip() if i < len(lst) else default

    created_results, skipped = [], []

    for i, name in enumerate(full_names):
        name = name.strip()
        if not name:
            continue
        try:
            with transaction.atomic():
                # ── Basic user + member ───────────────────────────────────
                result = _create_single_member(
                    church=church,
                    full_name=name,
                    title=_get(titles, i),
                    email=_get(emails, i),
                    phone=_get(phones, i),
                    image=(images[i] if i < len(images) else None),
                    created_by=request.user,
                )
                member = result["member"]
                is_probation = _get(set_probations, i) == "1"

                # ── Resolve stage ─────────────────────────────────────────
                stage_id = _get(stage_ids, i)
                stage = None
                if is_probation:
                    # Probation always → Probationer stage
                    stage = WorkforceStage.raw_objects.filter(
                        church=church, slug="probationer", is_active=True
                    ).first()
                if not stage and stage_id and stage_id.isdigit():
                    stage = WorkforceStage.raw_objects.filter(
                        church=church, id=int(stage_id), is_active=True
                    ).first()
                if not stage:
                    stage = (
                        WorkforceStage.raw_objects.filter(
                            church=church, slug="probationer", is_active=True
                        ).first()
                        or WorkforceStage.raw_objects.filter(
                            church=church, is_active=True
                        )
                        .order_by("order")
                        .first()
                    )

                if not stage:
                    skipped.append(f"{name}: no active stage configured")
                    continue

                # ── WorkforceMember ───────────────────────────────────────
                wf, _ = WorkforceMember.raw_objects.get_or_create(
                    church=church,
                    member=member,
                    defaults={"stage": stage, "is_active": True},
                )

                # ── Resolve + assign role ─────────────────────────────────
                role_id = _get(role_ids, i)
                if role_id and role_id.isdigit():
                    role = WorkforceRole.raw_objects.filter(
                        church=church, id=int(role_id), is_active=True
                    ).first()
                    if role:
                        wf.role = role
                        wf.save(update_fields=["role"])

                # ── Assign unit membership ────────────────────────────────
                uid = _get(unit_ids, i)
                if uid and uid.isdigit():
                    unit = ChurchUnit.raw_objects.filter(
                        church=church, id=int(uid), is_active=True
                    ).first()
                    if unit:
                        UnitMembership.raw_objects.get_or_create(
                            church=church,
                            workforce_member=wf,
                            unit=unit,
                            defaults={"is_active": True, "is_probation": is_probation},
                        )

                # ── Assign group membership ───────────────────────────────
                gid = _get(group_ids, i)
                if gid and gid.isdigit():
                    group = ChurchUnit.raw_objects.filter(
                        church=church, id=int(gid), is_active=True
                    ).first()
                    if group:
                        UnitMembership.raw_objects.get_or_create(
                            church=church,
                            workforce_member=wf,
                            unit=group,
                            defaults={"is_active": True, "is_probation": is_probation},
                        )

            created_results.append(result)

        except Exception as exc:
            logger.warning("batch_create_users: row %d (%s) failed: %s", i, name, exc)
            skipped.append(f"{name}: {exc}")

    if created_results:
        messages.success(
            request, f"{len(created_results)} member(s) created successfully."
        )
    if skipped:
        messages.warning(request, "Some rows were skipped: " + "; ".join(skipped[:5]))

    return redirect("accounts:user_list")


# ─────────────────────────────────────────────────────────────────────────────
# VIEW 2 — Bulk CSV / Excel upload
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def bulk_upload_users(request):
    """
    Upload a CSV or XLSX to create workforce members in bulk.

    Expected columns (case-insensitive):
        full_name (required), email, phone, unit_name, role_name,
        title, marital_status

    Returns redirect to user_list with success/error messages.
    """
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not has_perm(request, "accounts.manage_users"):
        messages.error(request, "You don't have permission to upload members.")
        return redirect("accounts:user_list")

    if request.method != "POST":
        return redirect("accounts:user_list")

    uploaded = request.FILES.get("bulk_file")
    if not uploaded:
        messages.error(request, "No file uploaded.")
        return redirect("accounts:user_list")

    try:
        import pandas as pd

        fname = uploaded.name.lower()
        if fname.endswith(".csv"):
            df = pd.read_csv(uploaded)
        elif fname.endswith((".xlsx", ".xls")):
            df = pd.read_excel(uploaded)
        else:
            messages.error(request, "Unsupported file format. Use .csv or .xlsx")
            return redirect("accounts:user_list")
    except Exception as exc:
        messages.error(request, f"Could not read file: {exc}")
        return redirect("accounts:user_list")

    # Normalise column names
    df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")

    if "full_name" not in df.columns:
        messages.error(request, "File must have a 'full_name' column.")
        return redirect("accounts:user_list")

    created_count, skipped = 0, []

    for idx, row in df.iterrows():
        name = str(row.get("full_name", "") or "").strip()
        if not name:
            continue
        try:
            _create_single_member(
                church=church,
                full_name=name,
                email=str(row.get("email", "") or "").strip(),
                phone=str(row.get("phone", "") or "").strip(),
                unit_name=str(row.get("unit_name", "") or "").strip(),
                role_name=str(row.get("role_name", "") or "").strip(),
                created_by=request.user,
            )
            created_count += 1
        except Exception as exc:
            logger.warning(
                "bulk_upload_users: row %d (%s) failed: %s", idx + 2, name, exc
            )
            skipped.append(f"Row {idx + 2} ({name}): {exc}")

    messages.success(request, f"{created_count} member(s) imported successfully.")
    if skipped:
        # Show first 8 failures in the flash; full log is in server logs
        messages.warning(request, "Skipped rows: " + "; ".join(skipped[:8]))

    return redirect("accounts:user_list")


# ─────────────────────────────────────────────────────────────────────────────
# VIEW 3 — Downloadable blank upload template
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def bulk_upload_template(request):
    """
    Serve a blank CSV template so admins know what columns to fill.
    ?type=members (default) or ?type=guests
    """
    ttype = request.GET.get("type", "members")

    col_map = {
        "members": [
            "full_name",
            "email",
            "phone",
            "unit_name",
            "role_name",
            "title",
            "marital_status",
        ],
    }
    cols = col_map.get(ttype, col_map["members"])

    from django.http import HttpResponse

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="{ttype}_upload_template.csv"'
    )

    writer = csv.writer(response)
    writer.writerow(cols)
    # One example row with placeholders
    writer.writerow(["Example Name"] + [""] * (len(cols) - 1))

    return response


@login_required
def upload_user_avatar(request, username):
    if request.method != "POST":
        return HttpResponseForbidden()

    user_obj = get_object_or_404(CustomUser, username=username)

    avatar = request.FILES.get("avatar")

    if not avatar:
        return JsonResponse({"ok": False, "error": "No file selected."}, status=400)

    # CustomUser stores profile pictures on the `image` field.
    user_obj.image = avatar
    user_obj.save(update_fields=["image"])
    return JsonResponse({"ok": True, "image_url": user_obj.image.url})


@login_required
def show_credentials(request):
    """
    One-time credentials display page shown after creating a new member.

    Reads from session, renders the credentials, then clears them
    immediately so they cannot be seen again by refreshing the page.

    The admin copies/screenshots these to share with the new member
    verbally, via WhatsApp, or any out-of-band channel.

    Template: accounts/credentials.html
    """
    credentials = request.session.pop("new_member_credentials", None)
    next_url = request.session.pop("credentials_next", reverse("accounts:user_list"))

    if not credentials:
        # Credentials already viewed or session expired
        messages.info(
            request, "No credentials to display — they may have already been viewed."
        )
        return redirect("accounts:user_list")

    return render(
        request,
        "accounts/credentials.html",
        {
            "credentials": credentials,
            "next_url": next_url,
            "page_title": "New Member Credentials",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Attendance
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def attendance_summary(request):
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    user = request.user
    user_id = request.GET.get("user_id")
    today = timezone.localdate()
    last_30_days = today - timedelta(days=30)

    if has_perm(request, "attendance.view_all") and not user_id:
        total_users = (
            CustomUser.objects.filter(
                church_memberships__church=church,
                is_active=True,
                is_superuser=False,
            )
            .distinct()
            .count()
            or 1
        )

        records = AttendanceRecord.objects.filter(
            church=church,
            user__user__is_superuser=False,
            date__gte=last_30_days,
        )

        present = records.filter(status="present").count()
        excused = records.filter(status="excused").count()
        absent = records.filter(status="absent").count()

        def pct(v):
            return round((v / total_users) * 100, 1)

        total_clock_in = ClockRecord.objects.filter(
            church=church,
            clock_in__isnull=False,
            user__user__is_superuser=False,
        ).count()
        total_clock_out = ClockRecord.objects.filter(
            church=church,
            clock_out__isnull=False,
            user__user__is_superuser=False,
        ).count()

        return JsonResponse(
            {
                "summary": {
                    "present": f"{pct(present)}%",
                    "excused": f"{pct(excused)}%",
                    "absent": f"{pct(absent)}%",
                },
                "today": {"clocked_in": False, "clock_in": None, "clock_out": None},
                "totals": {
                    "clock_in": f"{pct(total_clock_in)}%",
                    "clock_out": f"{pct(total_clock_out)}%",
                },
            }
        )

    target_user = user
    if user_id and has_perm(request, "attendance.view_all"):
        target_user = get_object_or_404(CustomUser, id=user_id)

    church_member = ChurchMember.raw_objects.filter(
        user=target_user, church=church, is_active=True
    ).first()

    if not church_member:
        return JsonResponse(
            {
                "summary": {"present": 0, "excused": 0, "absent": 0},
                "today": {
                    "clocked_in": False,
                    "attendance_marked": False,
                    "clock_in": None,
                    "clock_out": None,
                },
                "totals": {"clock_in": 0, "clock_out": 0},
            }
        )

    records = AttendanceRecord.objects.filter(
        church=church, user=church_member, date__gte=last_30_days
    )
    present = records.filter(status="present").count()
    excused = records.filter(status="excused").count()
    absent = records.filter(status="absent").count()

    today_clock = ClockRecord.objects.filter(
        church=church, user=church_member, date=today
    ).first()
    clocked_in = today_clock.is_clocked_in if today_clock else False

    total_clock_in = ClockRecord.objects.filter(
        church=church, user=church_member, clock_in__isnull=False
    ).count()
    total_clock_out = ClockRecord.objects.filter(
        church=church, user=church_member, clock_out__isnull=False
    ).count()

    return JsonResponse(
        {
            "summary": {"present": present, "excused": excused, "absent": absent},
            "today": {
                "clocked_in": clocked_in,
                "attendance_marked": today_clock is not None,
                "clock_in": (
                    format_time_value(today_clock.clock_in, church)
                    if today_clock and today_clock.clock_in
                    else None
                ),
                "clock_out": (
                    format_time_value(today_clock.clock_out, church)
                    if today_clock and today_clock.clock_out
                    else None
                ),
            },
            "totals": {"clock_in": total_clock_in, "clock_out": total_clock_out},
        }
    )


@login_required
def clock_action(request):
    church = getattr(request, "church", None)
    if not church:
        return JsonResponse(
            {"success": False, "error": "No church context."}, status=403
        )

    if not Event:
        return JsonResponse(
            {"success": False, "error": "Events module not available."}, status=500
        )

    user = request.user
    today = timezone.localdate()
    event_id = request.POST.get("event_id")
    latitude = request.POST.get("latitude")
    longitude = request.POST.get("longitude")
    action = request.POST.get("action")

    if action not in ("clock_in", "clock_out"):
        return JsonResponse(
            {"success": False, "error": "Invalid or missing action."}, status=400
        )

    event = Event.objects.filter(church=church, id=event_id).first()
    if not event:
        return JsonResponse({"success": False, "error": "Event not found."}, status=404)

    # ClockRecord.user and AttendanceRecord.user are FKs to ChurchMember
    church_member = ChurchMember.raw_objects.filter(
        church=church, user=user, is_active=True
    ).first()
    if not church_member:
        return JsonResponse(
            {
                "success": False,
                "error": "You are not registered as a church member here.",
            },
            status=400,
        )

    clock, _ = ClockRecord.objects.get_or_create(
        church=church, user=church_member, date=today, event=event
    )

    if action == "clock_out":
        if event.attendance_mode.lower() == "physical":
            try:
                church_lat, church_lon = _church_coordinates(church)
                distance_km = haversine_distance(
                    float(latitude), float(longitude), church_lat, church_lon
                )
                radius_km = float(church.attendance_radius_km)
                if distance_km > radius_km:
                    return JsonResponse(
                        {
                            "success": False,
                            "error": "You are outside the allowed range for clock-out.",
                        }
                    )
            except Exception:
                return JsonResponse(
                    {"success": False, "error": "Unable to verify your location."}
                )

        if clock.clock_out:
            return JsonResponse(
                {"success": False, "error": "You have already clocked out today."}
            )
        clock.mark_clock_out()

    elif action == "clock_in":
        if clock.clock_in:
            return JsonResponse(
                {"success": False, "error": "You have already clocked in today."}
            )
        clock.mark_clock_in()
        location_tag = classify_event_location(event, church, latitude, longitude)
        # Keep dashboard clock-in aligned with attendance tracking.
        attendance_obj, attendance_created = AttendanceRecord.raw_objects.get_or_create(
            church=church,
            user=church_member,
            event=event,
            date=today,
            defaults={
                "status": "present",
                "remarks": "Auto-marked from dashboard clock-in.",
                "location_tag": location_tag,
            },
        )
        if attendance_created or attendance_obj.location_tag != location_tag:
            attendance_obj.location_tag = location_tag
            attendance_obj.save(update_fields=["location_tag", "updated_at"])

    return JsonResponse(
        {
            "success": True,
            "action": action,
            "clock_in": (
                format_time_value(clock.clock_in, church) if clock.clock_in else None
            ),
            "clock_out": (
                format_time_value(clock.clock_out, church) if clock.clock_out else None
            ),
            "location_label": (
                attendance_obj.location_tag.capitalize()
                if action == "clock_in"
                and attendance_created
                and attendance_obj.location_tag
                else ""
            ),
            "auto_attendance_marked": bool(action == "clock_in" and attendance_created),
        }
    )


def attendance_check(request):
    church = getattr(request, "church", None)
    user = request.user
    event_id = request.GET.get("event_id")

    if not church:
        return JsonResponse(
            {"clock_in": False, "clock_out": False, "location_label": ""}
        )

    # ClockRecord.user is a ChurchMember FK — direct lookup via user__user
    clock = ClockRecord.objects.filter(
        church=church,
        user__user=user,
        event_id=event_id,
    ).first()

    return JsonResponse(
        {
            "clock_in": bool(clock and clock.clock_in),
            "clock_out": bool(clock and clock.clock_out),
            "location_label": (
                getattr(
                    AttendanceRecord.raw_objects.filter(
                        church=church,
                        user__user=user,
                        event_id=event_id,
                        date=timezone.localdate(),
                    ).first(),
                    "location_tag",
                    "",
                ).capitalize()
                if event_id
                else ""
            ),
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Invitation system
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def send_invitation(request):
    """
    Send a member invitation email.
    Requires: accounts.manage_users permission.

    POST fields:
        email       — required
        full_name   — optional pre-fill
        suggested_unit — optional unit PK

    On success → creates MemberInvitation, sends email, redirects to user_list.
    """
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not has_perm(request, "accounts.manage_users"):
        messages.error(request, "You do not have permission to invite members.")
        return redirect("accounts:user_list")

    if request.method != "POST":
        return redirect("accounts:user_list")

    from django.utils import timezone
    from datetime import timedelta
    from accounts.models import MemberInvitation
    from units.models import ChurchUnit

    email = request.POST.get("email", "").strip().lower()
    full_name = request.POST.get("full_name", "").strip()
    unit_id = request.POST.get("suggested_unit", "").strip()
    msg_text = request.POST.get("message", "").strip()

    if not email:
        messages.error(request, "An email address is required.")
        return redirect("accounts:user_list")

    # Check if already a member
    if CustomUser.objects.filter(
        email__iexact=email, church_memberships__church=church
    ).exists():
        messages.warning(request, f"{email} is already a member of this church.")
        return redirect("accounts:user_list")

    # Check for existing unused invitation
    existing = MemberInvitation.objects.filter(
        church=church, email=email, accepted_at__isnull=True
    ).first()
    if existing and existing.is_valid:
        messages.warning(request, f"A valid invitation already exists for {email}.")
        return redirect("accounts:user_list")

    unit = None
    if unit_id and unit_id.isdigit():
        unit = ChurchUnit.raw_objects.filter(
            church=church, id=int(unit_id), is_active=True
        ).first()

    invitation = MemberInvitation.objects.create(
        church=church,
        email=email,
        full_name=full_name,
        invited_by=request.user,
        suggested_unit=unit,
        message=msg_text,
        expires_at=timezone.now() + timedelta(days=7),
    )

    _send_invitation_email(invitation, request)

    messages.success(request, f"Invitation sent to {email}.")
    return redirect("accounts:user_list")


def accept_invitation(request, token):
    """
    Public view — no login required.
    Handles the invitation link click. Shows and processes the registration form.

    GET  → pre-filled signup form
    POST → create user + ChurchMember, mark invitation accepted
    """
    from accounts.models import MemberInvitation

    try:
        invitation = MemberInvitation.objects.select_related(
            "church", "suggested_unit"
        ).get(token=token)
    except MemberInvitation.DoesNotExist:
        messages.error(request, "This invitation link is invalid.")
        return redirect("tenants:signup")

    if not invitation.is_valid:
        if invitation.is_used:
            messages.info(
                request, "This invitation has already been used. Please sign in."
            )
            return redirect("login")
        messages.error(
            request, "This invitation has expired. Please ask for a new one."
        )
        return redirect("tenants:signup")

    church = invitation.church
    request.church = church  # allow middleware-dependent logic to work

    if request.method == "POST":
        from django.contrib.auth import login

        username_input = request.POST.get("username", "").strip()
        full_name = request.POST.get("full_name", "").strip() or invitation.full_name
        password = request.POST.get("password", "")
        password_confirm = request.POST.get("password_confirm", "")
        base_username = normalize_username_base(username_input)
        canonical_username = format_username(base_username, church=church)

        if not full_name:
            messages.error(request, "Full name is required.")
        elif not base_username:
            messages.error(request, "Please enter a valid username.")
        elif password != password_confirm:
            messages.error(request, "Passwords do not match.")
        elif CustomUser.objects.filter(username__iexact=canonical_username).exists():
            messages.error(request, "That username is already in use.")
        else:
            saved_user = CustomUser.objects.create_user(
                username=canonical_username,
                email=invitation.email,
                password=password,
                full_name=full_name,
                is_active=True,
            )
            ChurchMember.raw_objects.get_or_create(
                church=church,
                user=saved_user,
                defaults={"is_active": True},
            )

            # Mark invitation used
            from django.utils import timezone as tz

            invitation.accepted_at = tz.now()
            invitation.accepted_by = saved_user
            invitation.save(update_fields=["accepted_at", "accepted_by"])

            # Store credentials for one-time display
            request.session["new_member_credentials"] = {
                "full_name": saved_user.full_name,
                "username": saved_user.username,
                "temp_password": password,
            }
            request.session["credentials_next"] = "/"

            login(
                request, saved_user, backend="django.contrib.auth.backends.ModelBackend"
            )

            messages.success(
                request, f"Welcome to {church.name}! Your account has been created."
            )
            return redirect("accounts:show_credentials")
    else:
        form = None  # template uses invitation data to pre-fill

    return render(
        request,
        "accounts/accept_invitation.html",
        {
            "invitation": invitation,
            "church": church,
            "form": form,
            "page_title": "Accept Invitation",
        },
    )


class TenantPasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/emails/password_reset_email.txt"
    subject_template_name = "accounts/emails/password_reset_subject.txt"
    success_url = "/password_reset/sent/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["church"] = getattr(self.request, "church", None)
        return context


@login_required
def resend_invitation(request, token):
    """Resend an existing invitation email."""
    church = getattr(request, "church", None)
    if not church or not has_perm(request, "accounts.manage_users"):
        return HttpResponseForbidden("Not allowed.")

    from accounts.models import MemberInvitation
    from django.utils import timezone
    from datetime import timedelta

    try:
        invitation = MemberInvitation.objects.get(token=token, church=church)
    except MemberInvitation.DoesNotExist:
        messages.error(request, "Invitation not found.")
        return redirect("accounts:user_list")

    if invitation.is_used:
        messages.warning(request, "This invitation has already been accepted.")
        return redirect("accounts:user_list")

    # Extend expiry
    invitation.expires_at = timezone.now() + timedelta(days=7)
    invitation.save(update_fields=["expires_at"])

    _send_invitation_email(invitation, request)
    messages.success(request, f"Invitation resent to {invitation.email}.")
    return redirect("accounts:user_list")


def _send_invitation_email(invitation, request):
    """
    Send the invitation email. Uses Django's send_mail.
    Falls back silently if email is not configured.
    """
    try:
        from django.core.mail import send_mail
        from django.conf import settings as django_settings

        accept_url = request.build_absolute_uri(invitation.get_accept_url())
        church_name = invitation.church.name

        body_lines = [
            f"You've been invited to join {church_name} Workforce.",
        ]
        if invitation.message:
            body_lines.append(
                f"\nMessage from {invitation.invited_by.full_name or 'the admin'}:"
            )
            body_lines.append(invitation.message)

        body_lines += [
            f"\nClick the link below to create your account (expires in 7 days):",
            f"\n{accept_url}",
            f"\nThis link can only be used once.",
        ]

        send_mail(
            subject=f"You're invited to join {church_name} Workforce",
            message="\n".join(body_lines),
            from_email=getattr(
                django_settings, "DEFAULT_FROM_EMAIL", "noreply@churchforce.io"
            ),
            recipient_list=[invitation.email],
            fail_silently=True,
        )
    except Exception:
        pass  # Email is best-effort — don't crash the request
