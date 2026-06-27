"""
units/views.py

Views for unit/group management, member transfer, probation,
attendance tracking, and campus management.
"""

import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from django.db.models import Prefetch
from units.models import (
    ChurchUnit,
    UnitMembership,
    UnitAttendanceRecord,
    UnitAnnouncement,
    UnitTask,
    UnitReport,
    TaskAssignment,
    UnitFunctionalRole,
    UnitFunctionalRoleAssignment,
    Campus,
    CampusMembership,
)
from units.services.unit_resolver import get_unit_for_request


def _perms(request):
    """Return the PermissionResolver for this request, or None."""
    return getattr(request, "permissions", None)


def _is_admin(request):
    """True if superuser or holds dashboard.admin permission (tier 1)."""
    if request.user.is_superuser:
        return True
    p = _perms(request)
    if p and p.can("dashboard.admin"):
        return True
    # Fallback for new tenants where WorkforceMember may not be wired yet
    from accounts.models import ChurchMember

    church = getattr(request, "church", None)
    if church:
        cm = ChurchMember.raw_objects.filter(
            church=church, user=request.user, is_active=True
        ).first()
        return bool(cm and cm.is_admin)
    return False


def _can_manage_unit(request, unit):
    """
    Resolver-driven unit management check.
    True if: admin (tier 1) OR has unit.manage_members permission for this unit.
    No direct is_unit_head DB query — the resolver handles tier 3/4 flags.
    """
    if _is_admin(request):
        return True
    p = _perms(request)
    return bool(p and p.can("unit.manage_members", unit=unit))


# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬


def _is_unit_member(request, unit):
    """
    True if the user is an active member of this unit.
    Checks via resolver first (any unit-scoped perm = member),
    then falls back to a direct DB membership check.
    """
    p = _perms(request)
    if p and p.can("chat.view", unit=unit):
        return True
    member = getattr(request, "member", None)
    if not member:
        return False
    return UnitMembership.raw_objects.filter(
        church=getattr(request, "church", None),
        unit=unit,
        workforce_member__member=member,
        is_active=True,
    ).exists()


def _can(request, permission, unit=None):
    """Shorthand resolver-based permission check."""
    if request.user.is_superuser:
        return True
    p = _perms(request)
    return bool(p and p.can(permission, unit=unit))


def _build_unit_guest_context(request, unit, *, status_slug=None, assigned_to=None):
    """
    Build the guest-module context used by both unit detail and unit guest views.
    """
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)

    from datetime import timedelta

    from django.db.models import Count, Max, Q
    from django.utils.timezone import now
    from guests.models import GuestEntry, GuestStatus
    from guests.views import SVG_ICONS, _build_field_data

    guest_qs = GuestEntry.raw_objects.filter(
        church=church,
        is_deleted=False,
        assigned_to__unit=unit,
    ).select_related(
        "status",
        "assigned_to__workforce_member__member__user",
    )

    if not _can_manage_unit(request, unit):
        membership = UnitMembership.raw_objects.filter(
            church=church,
            unit=unit,
            workforce_member__member=member,
            is_active=True,
        ).first()
        guest_qs = (
            guest_qs.filter(assigned_to=membership) if membership else guest_qs.none()
        )

    if assigned_to:
        guest_qs = guest_qs.filter(assigned_to_id=assigned_to)

    count_qs = guest_qs.values("status__slug").annotate(total=Count("id"))
    status_counts = {row["status__slug"]: row["total"] for row in count_qs}
    total_count = guest_qs.count()

    filtered_guest_qs = guest_qs
    if status_slug:
        filtered_guest_qs = filtered_guest_qs.filter(status__slug=status_slug)

    filtered_guest_qs = filtered_guest_qs.annotate(
        report_count=Count("reports", distinct=True),
        last_reported=Max("reports__report_date"),
        unread_reviews=Count(
            "reviews",
            filter=Q(reviews__is_read=False),
            distinct=True,
        ),
    ).prefetch_related("social_media_accounts", "reports", "reviews")

    guests = list(filtered_guest_qs)
    from guests.models import MembershipApplication
    from workforce.models import WorkforceTraineeProfile

    guest_ids = [guest.id for guest in guests]
    active_application_guest_ids = set(
        MembershipApplication.raw_objects.filter(
            church=church,
            guest_id__in=guest_ids,
            status__in=[
                MembershipApplication.STATUS_PENDING,
                MembershipApplication.STATUS_TRAINING,
                MembershipApplication.STATUS_PASSED,
            ],
            is_active=True,
        ).values_list("guest_id", flat=True)
    )
    active_trainee_member_ids = set(
        WorkforceTraineeProfile.raw_objects.filter(
            church=church,
            is_active=True,
        ).values_list("member_id", flat=True)
    )

    for guest in guests:
        guest.field_data = _build_field_data(guest)
        guest.has_unread_reviews = guest.unread_reviews > 0
        guest.is_new = guest.assigned_at and (
            now() - guest.assigned_at <= timedelta(days=14)
        )
        guest.force_commit_disabled = (
            bool(guest.status and guest.status.is_terminal)
            or guest.id in active_application_guest_ids
            or (getattr(guest, "converted_to_id", None) in active_trainee_member_ids)
        )
        guest.not_planted_disabled = bool(guest.status and guest.status.is_terminal)

    assignable_members = UnitMembership.raw_objects.filter(
        church=church,
        unit=unit,
        is_active=True,
        workforce_member__is_active=True,
        workforce_member__member__is_active=True,
        workforce_member__member__user__is_active=True,
    ).select_related("workforce_member__member__user")

    return {
        "guests": guests,
        "statuses": GuestStatus.objects.filter(church=church, is_active=True).order_by(
            "order"
        ),
        "assignable_members": assignable_members,
        "status_counts": status_counts,
        "total_count": total_count,
        "selected_status": status_slug or "",
        "selected_member": str(assigned_to or ""),
        "svg_icons": SVG_ICONS,
        "can_manage": _can_manage_unit(request, unit),
        "can_report": _can(request, "guests.report", unit=unit)
        or _can(request, "guests.manage_all", unit=unit),
        "can_update_status": _can(request, "guests.manage_all", unit=unit)
        or _can(request, "guests.update_status", unit=unit),
        "GUESTS_ROOM_ID": unit.chat_rooms.filter(is_default=True)
        .values_list("id", flat=True)
        .first(),
    }


# Unit / Group list & CRUD
# Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬


@login_required
def unit_list(request):
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    if _is_admin(request):
        units = ChurchUnit.raw_objects.filter(
            church=church, unit_type="unit", is_active=True
        )
        groups = ChurchUnit.raw_objects.filter(
            church=church, unit_type="group", is_active=True
        )
    else:
        member = getattr(request, "member", None)
        if not member:
            return HttpResponseForbidden("No member context.")
        ids = UnitMembership.raw_objects.filter(
            church=church, workforce_member__member=member, is_active=True
        ).values_list("unit_id", flat=True)
        units = ChurchUnit.raw_objects.filter(
            id__in=ids, unit_type="unit", is_active=True
        )
        groups = ChurchUnit.raw_objects.filter(
            id__in=ids, unit_type="group", is_active=True
        )

    return render(
        request,
        "units/unit_list.html",
        {
            "units": units,
            "groups": groups,
            "is_admin": _is_admin(request),
            "page_title": "Units & Groups",
        },
    )


@login_required
def unit_detail(request, slug):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)

    member = getattr(request, "member", None)
    is_member = _is_unit_member(request, unit)

    if not _is_admin(request) and not is_member:
        return HttpResponseForbidden("You are not a member of this unit.")

    can_manage = _can_manage_unit(request, unit)

    memberships = (
        UnitMembership.raw_objects.filter(church=church, unit=unit, is_active=True)
        .select_related(
            "workforce_member__member__user", "trainee_profile__member__user"
        )
        .order_by("-is_unit_head", "joined_at")
    )

    from workforce.models import WorkforceMember

    available_members = (
        WorkforceMember.raw_objects.filter(church=church, is_active=True)
        .exclude(unit_memberships__unit=unit, unit_memberships__is_active=True)
        .select_related("member__user")
    )

    announcements = UnitAnnouncement.raw_objects.filter(
        church=church, unit=unit, is_active=True
    ).select_related("created_by__user")

    tasks = (
        UnitTask.raw_objects.filter(
            church=church,
            unit=unit,
            is_active=True,
        )
        .select_related(
            "assigned_to__workforce_member__member__user",
            "created_by__user",
        )
        .prefetch_related(
            Prefetch(
                "assignments",
                queryset=TaskAssignment.raw_objects.select_related(
                    "membership__workforce_member__member__user"
                ),
            )
        )
    )

    from units.forms import UnitAnnouncementForm, UnitTaskForm, UnitReportForm

    announcement_form = UnitAnnouncementForm()
    task_form = UnitTaskForm(unit=unit)
    report_form = UnitReportForm(unit=unit)
    report_status_filter = (request.GET.get("report_status") or "").strip()
    report_category_filter = (request.GET.get("report_category") or "").strip()
    reports = UnitReport.raw_objects.filter(
        church=church, unit=unit, is_active=True
    ).select_related(
        "submitted_by__workforce_member__member__user",
        "submitted_by__trainee_profile__member__user",
        "report_to_unit",
    )
    if report_status_filter:
        reports = reports.filter(status=report_status_filter)
    if report_category_filter:
        reports = reports.filter(category=report_category_filter)
    incoming_reports = (
        UnitReport.raw_objects.filter(
            church=church,
            report_to_unit=unit,
            is_active=True,
        )
        .exclude(unit=unit)
        .select_related(
            "unit",
            "submitted_by__workforce_member__member__user",
            "submitted_by__trainee_profile__member__user",
        )
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_report":
            if not _can(request, "unit.report.submit", unit=unit):
                return HttpResponseForbidden("You cannot submit reports for this unit.")
            report_form = UnitReportForm(request.POST, unit=unit)
            if report_form.is_valid():
                submitter_membership = UnitMembership.raw_objects.filter(
                    church=church,
                    unit=unit,
                    workforce_member__member=member,
                    is_active=True,
                ).first()
                if not submitter_membership:
                    return HttpResponseForbidden(
                        "Active membership required to submit report."
                    )
                report = report_form.save(commit=False)
                report.church = church
                report.unit = unit
                report.submitted_by = submitter_membership
                report.is_active = True
                report.save()
                # Notify current unit head and escalation target head when applicable.
                from notifications.utils import notify_members

                notify_targets = []
                unit_head_membership = unit.get_unit_head()
                if (
                    unit_head_membership
                    and unit_head_membership.workforce_member
                    and unit_head_membership.workforce_member.member
                ):
                    notify_targets.append(unit_head_membership.workforce_member.member)
                if report.report_to_unit:
                    parent_head = report.report_to_unit.get_unit_head()
                    if (
                        parent_head
                        and parent_head.workforce_member
                        and parent_head.workforce_member.member
                    ):
                        notify_targets.append(parent_head.workforce_member.member)
                if notify_targets:
                    notify_members(
                        notify_targets,
                        title=f"New unit report: {unit.name}",
                        description=f"{report.title} ({report.get_category_display()})",
                        church=church,
                        is_urgent=False,
                    )
                if report.copy_pastor:
                    from workforce.models import WorkforceMembershipRole

                    pastors = [
                        rel.workforce_member.member
                        for rel in WorkforceMembershipRole.raw_objects.filter(
                            church=church,
                            role__tier=1,
                            role__name__iexact="Pastor",
                            workforce_member__is_active=True,
                            workforce_member__member__is_active=True,
                        ).select_related("workforce_member__member")
                    ]
                    if pastors:
                        notify_members(
                            pastors,
                            title=f"Unit report copied: {unit.name}",
                            description=f"{report.title} ({report.get_category_display()})",
                            church=church,
                            is_urgent=False,
                        )
                messages.success(request, "Report submitted.")
                return redirect("units:detail", slug=unit.slug)
            messages.error(request, "Please correct the report form.")

        elif action == "add_announcement":
            if not can_manage:
                return HttpResponseForbidden("Admin or unit head required.")
            announcement_form = UnitAnnouncementForm(request.POST)
            if announcement_form.is_valid():
                announcement = announcement_form.save(commit=False)
                announcement.church = church
                announcement.unit = unit
                announcement.created_by = member
                announcement.is_active = True
                announcement.save()
                messages.success(request, "Announcement posted.")
                return redirect("units:detail", slug=unit.slug)
            messages.error(request, "Please correct the announcement form.")

        elif action == "add_task":
            if not can_manage:
                return HttpResponseForbidden("Admin or unit head required.")
            task_form = UnitTaskForm(request.POST, unit=unit)
            if task_form.is_valid():
                task = task_form.save(commit=False)
                task.church = church
                task.unit = unit
                task.created_by = member
                task.is_active = True
                task.save()
                task_form.save_m2m()

                # --- AUTO CREATE ASSIGNMENTS ---
                memberships = []

                if task.task_type == "single" and task.assigned_to:
                    memberships = [task.assigned_to]

                elif task.task_type == "group":
                    memberships = task.assignees.all()

                TaskAssignment.objects.bulk_create(
                    [
                        TaskAssignment(
                            church=church,
                            task=task,
                            membership=m,
                            is_active=True,
                        )
                        for m in memberships
                    ]
                )
                messages.success(request, "Task created.")
                return redirect("units:detail", slug=unit.slug)
            messages.error(request, "Please correct the task form.")

    from permissions.services.resolver import PermissionResolver
    from django.db.models import Count

    church_settings = getattr(church, "settings", None)
    guest_counts = {}
    guest_module_context = {}
    music_module_context = {}
    media_module_context = {}

    # ── Functional roles — loaded for ALL units (music and non-music) ──
    # Music units get the full preset bundle; other units get Secretary only.
    # UnitFunctionalRole.seed_for_unit() is idempotent, called on ChurchUnit.save,
    # so this queryset will always find at least Secretary.
    _all_functional_roles = (
        UnitFunctionalRole.objects.filter(church=church, unit=unit, is_active=True)
        .prefetch_related("assignments__membership__workforce_member__member__user")
        .order_by("name")
    )

    # Seed functional roles for units that existed before seeding was introduced.
    # seed_for_unit() is idempotent — safe to call even if rows already exist.
    if not _all_functional_roles.exists():
        try:
            UnitFunctionalRole.seed_for_unit(unit)
            _all_functional_roles = (
                UnitFunctionalRole.objects.filter(
                    church=church, unit=unit, is_active=True
                )
                .prefetch_related(
                    "assignments__membership__workforce_member__member__user"
                )
                .order_by("name")
            )
        except Exception:
            pass

    if (
        church_settings
        and church_settings.enable_guest_module
        and unit.guest_management
    ):
        from guests.models import GuestEntry

        guest_counts = {
            row["assigned_to_id"]: row["count"]
            for row in GuestEntry.raw_objects.filter(
                church=church,
                is_deleted=False,
                assigned_to__unit=unit,
            )
            .values("assigned_to_id")
            .annotate(count=Count("id"))
        }

        guest_module_context = _build_unit_guest_context(
            request,
            unit,
            status_slug=request.GET.get("guest_status", "").strip() or None,
            assigned_to=request.GET.get("guest_assignee", "").strip() or None,
        )

    if church_settings and church_settings.enable_music_module and unit.music_module:
        from music.models import Setlist, RehearsalSession

        current_setlist = (
            Setlist.raw_objects.filter(church=church, unit=unit)
            .select_related("event")
            .prefetch_related(
                "songs__track__stems", "songs__track__sections", "songs__track"
            )
            .order_by("-created_at")
            .first()
        )
        previous_setlists = (
            Setlist.raw_objects.filter(church=church, unit=unit)
            .select_related("event")
            .order_by("-created_at")[1:6]
        )
        from django.utils import timezone as _tz

        _today = _tz.localdate()

        # Non-recurring: session event must be today or in the future
        upcoming_rehearsal = (
            RehearsalSession.raw_objects.filter(
                church=church,
                unit=unit,
                completed=False,
                event__date__gte=_today,
                event__is_recurring_weekly=False,
            )
            .select_related("event", "setlist")
            .order_by("event__date")
            .first()
        )

        # Recurring: sessions whose event has no concrete future date but
        # recurs every week — pick the earliest one not yet completed.
        if not upcoming_rehearsal:
            upcoming_rehearsal = (
                RehearsalSession.raw_objects.filter(
                    church=church,
                    unit=unit,
                    completed=False,
                    event__is_recurring_weekly=True,
                )
                .select_related("event", "setlist")
                .order_by("event__date", "event__time")
                .first()
            )
        music_module_context = {
            "music_current_setlist": current_setlist,
            "music_previous_setlists": previous_setlists,
            "music_upcoming_rehearsal": upcoming_rehearsal,
        }

    if church_settings and church_settings.enable_media_module and unit.media_module:
        from media.models import ServicePresentation, ProductionSchedule
        from services.models import Event

        media_presentations = (
            ServicePresentation.raw_objects.filter(church=church, unit=unit)
            .select_related("event")
            .order_by("-created_at")[:4]
        )
        upcoming_events = Event.raw_objects.filter(church=church, unit=unit).order_by(
            "date"
        )[:5]
        production_schedules = (
            ProductionSchedule.raw_objects.filter(church=church, unit=unit)
            .select_related("event")
            .order_by("-event__date")[:4]
        )
        media_module_context = {
            "media_presentations": media_presentations,
            "media_upcoming_events": upcoming_events,
            "media_schedules": production_schedules,
        }

    member_cards = []
    attendance_meta = {}
    if memberships:
        from workforce.models import AttendanceRecord

        # AttendanceRecord.user is ChurchMember. Map via workforce_member → member.
        from accounts.models import ChurchMember

        member_ids_by_membership = {}  # membership.id → church_member.id
        for m in memberships:
            try:
                cm_id = m.workforce_member.member_id
                if cm_id:
                    member_ids_by_membership[m.id] = cm_id
            except AttributeError:
                pass

        church_member_ids = list(member_ids_by_membership.values())
        attendance_qs = AttendanceRecord.raw_objects.filter(
            church=church, user_id__in=church_member_ids
        ).order_by("-date")

        from collections import defaultdict

        status_counts = defaultdict(lambda: {"present": 0, "excused": 0, "absent": 0})
        latest = {}
        for rec in attendance_qs[:800]:
            key = rec.user_id  # ChurchMember id
            if key not in latest:
                latest[key] = rec
            if rec.status in status_counts[key]:
                status_counts[key][rec.status] += 1

        for m in memberships:
            cm_id = member_ids_by_membership.get(m.id)
            counts = (
                status_counts.get(cm_id, {"present": 0, "excused": 0, "absent": 0})
                if cm_id
                else {"present": 0, "excused": 0, "absent": 0}
            )
            last = latest.get(cm_id) if cm_id else None
            attendance_meta[m.id] = {
                "present": counts["present"],
                "excused": counts["excused"],
                "absent": counts["absent"],
                "last_status": (last.status if last else ""),
                "last_date": (last.date if last else None),
            }

    for membership in memberships:
        user = membership.workforce_member.member.user
        if user.is_superuser:
            role_label = "Superuser"
        else:
            # Use the resolver tier label — no is_leadership or is_unit_head checks
            resolver = PermissionResolver(user, church)
            role_label = resolver.get_tier_label()
            # Narrow to unit context: if user has no global tier but manages this unit,
            # reflect that in the label
            if role_label == "Member" and resolver.can(
                "unit.manage_members", unit=unit
            ):
                role_label = "Unit Head"

        # Distinguish disciplinary probation from pipeline probation
        is_disciplinary_probation = False
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
                is_disciplinary_probation = True

        member_cards.append(
            {
                "membership": membership,
                "user": user,
                "member": membership.workforce_member.member,
                "role_label": role_label,
                "guest_count": guest_counts.get(membership.id, 0),
                "is_disciplinary_probation": is_disciplinary_probation,
                "attendance": attendance_meta.get(membership.id, {}),
            }
        )

    my_assignments = []
    current_unit_membership = None

    if member:
        membership = UnitMembership.raw_objects.filter(
            church=church,
            unit=unit,
            workforce_member__member=member,
            is_active=True,
        ).first()
        current_unit_membership = membership

        if membership:
            my_assignments = TaskAssignment.raw_objects.filter(
                church=church,
                task__unit=unit,
                membership=membership,
                is_active=True,
            ).select_related("task")

    return render(
        request,
        "units/unit_detail.html",
        {
            "unit": unit,
            "church_settings": church_settings,
            "memberships": memberships,
            "member_cards": member_cards,
            "available_members": available_members,
            "available_probation_units": ChurchUnit.raw_objects.filter(
                church=church, is_active=True
            ).order_by("name"),
            "announcements": announcements,
            "tasks": tasks,
            "my_assignments": my_assignments,
            "announcement_form": announcement_form,
            "task_form": task_form,
            "report_form": report_form,
            "reports": reports,
            "incoming_reports": incoming_reports,
            "can_manage": can_manage,
            "can_submit_report": _can(request, "unit.report.submit", unit=unit),
            "can_review_report": _can(request, "unit.report.review", unit=unit),
            "report_status_filter": report_status_filter,
            "report_category_filter": report_category_filter,
            "can_create_guest_for_unit": bool(
                church_settings
                and getattr(church_settings, "enable_guest_module", False)
                and unit.guest_management
                and (
                    _can_manage_unit(request, unit)
                    or _can(request, "guests.create", unit=unit)
                )
            ),
            "current_unit_membership": current_unit_membership,
            "is_admin": _is_admin(request),
            "unit_functional_roles": _all_functional_roles,
            "can_manage_roles": (
                _is_admin(request) or _can(request, "music.manage_roles", unit=unit)
            ),
            **guest_module_context,
            **music_module_context,
            **media_module_context,
        },
    )


@login_required
def unit_guest_cards(request, slug):
    unit = get_unit_for_request(request, slug)

    if not _is_admin(request) and not _is_unit_member(request, unit):
        return HttpResponseForbidden()

    status_slug = request.GET.get("status")
    assigned_to = request.GET.get("assigned_to")
    return render(
        request,
        "units/_unit_guest_cards.html",
        {
            "unit": unit,
            **_build_unit_guest_context(
                request,
                unit,
                status_slug=status_slug,
                assigned_to=assigned_to,
            ),
        },
    )


@login_required
def unit_guests(request, slug):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)

    if not _is_admin(request) and not _is_unit_member(request, unit):
        return HttpResponseForbidden("You are not a member of this unit.")

    settings_obj = getattr(church, "settings", None)
    if (
        not settings_obj
        or not settings_obj.enable_guest_module
        or not unit.guest_management
    ):
        return HttpResponseForbidden("Guest module is not enabled for this unit.")
    # Deprecated: route legacy unit guests page into full guest module
    # with scope preserved (all unit guests for managers, assigned guests for members).
    params = {"unit": unit.slug}
    if not _can_manage_unit(request, unit):
        member = getattr(request, "member", None)
        membership = UnitMembership.raw_objects.filter(
            church=church,
            unit=unit,
            workforce_member__member=member,
            is_active=True,
        ).first()
        if membership:
            params["user_filter"] = membership.id

    return redirect(f"{reverse('guests:guest_list')}?{urlencode(params)}")


@login_required
def unit_music(request, slug):
    from django.utils import timezone
    from music.models import Track, Setlist, RehearsalSession, MixNote, MUSICAL_KEYS
    from units.models import UnitMembership
    from music.services.catalog import seed_tracks_for_church, catalog_genres
    import requests
    from django.conf import settings

    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)

    if not _is_admin(request) and not _is_unit_member(request, unit):
        return HttpResponseForbidden("You are not a member of this unit.")

    settings_obj = getattr(church, "settings", None)
    if (
        not settings_obj
        or not settings_obj.enable_music_module
        or not unit.music_module
    ):
        return HttpResponseForbidden("Music module is not enabled for this unit.")

    can_manage = _is_admin(request) or _can(request, "music.manage")

    if request.method == "POST" and can_manage:
        action = request.POST.get("action", "").strip()
        if action == "import_tracks_by_genre":
            genre = (request.POST.get("genre") or "").strip().lower()
            if genre not in catalog_genres():
                messages.warning(request, "No seed catalog found for selected genre.")
                return redirect("units:unit_music", slug=unit.slug)

            stats = seed_tracks_for_church(
                church,
                unit=unit,
                genres=[genre],
            )
            messages.success(
                request,
                f"Imported {stats['created']} new {genre.replace('_', ' ').title()} track(s).",
            )
            return redirect("units:unit_music", slug=unit.slug)

    today = timezone.localdate()

    # Track library for this unit (+ church-wide shared tracks)
    from django.db.models import Q

    tracks = (
        Track.raw_objects.filter(church=church, is_active=True)
        .filter(Q(unit=unit) | Q(unit__isnull=True))
        .prefetch_related("tags", "sections", "chord_charts", "stems")
        .order_by("title")[:50]
    )

    # Recent + upcoming setlists
    setlists = (
        Setlist.raw_objects.filter(church=church, unit=unit)
        .select_related("event")
        .prefetch_related("songs__track")
        .order_by("-created_at")[:10]
    )

    # Next upcoming rehearsal
    from django.utils import timezone as _tz

    _today_music = _tz.localdate()

    # Upcoming: concrete-dated (not recurring) sessions from today forward,
    # PLUS any recurring-weekly sessions (they always recur so always upcoming)
    from django.db.models import Q as _Q

    upcoming_rehearsals = list(
        RehearsalSession.raw_objects.filter(
            church=church,
            unit=unit,
            completed=False,
            event__date__gte=_today_music,
            event__is_recurring_weekly=False,
        )
        .select_related("event", "setlist")
        .order_by("event__date")[:5]
    ) or list(
        RehearsalSession.raw_objects.filter(
            church=church,
            unit=unit,
            completed=False,
            event__is_recurring_weekly=True,
        )
        .select_related("event", "setlist")
        .order_by("event__date", "event__time")[:5]
    )

    # Past rehearsals
    past_rehearsals = (
        RehearsalSession.raw_objects.filter(church=church, unit=unit, completed=True)
        .select_related("event", "setlist")
        .order_by("-event__date")[:5]
    )

    # Unit members (for mix console channel strips)
    unit_members = (
        UnitMembership.raw_objects.filter(church=church, unit=unit, is_active=True)
        .select_related("workforce_member__member__user")
        .prefetch_related("roles__role")
    )

    # Active setlist (most recent finalized or latest draft)
    active_setlist = (
        Setlist.raw_objects.filter(church=church, unit=unit, finalized=True)
        .select_related("event")
        .prefetch_related("songs__track__sections", "songs__mix_notes")
        .order_by("-created_at")
        .first()
    ) or (
        Setlist.raw_objects.filter(church=church, unit=unit)
        .select_related("event")
        .prefetch_related("songs__track__sections", "songs__mix_notes")
        .order_by("-created_at")
        .first()
    )

    ai_health = {
        "provider": (getattr(settings, "AI_PROVIDER", "anthropic") or "anthropic"),
        "ok": False,
        "detail": "Not checked",
    }
    if ai_health["provider"] == "ollama":
        try:
            base_url = (
                getattr(settings, "OLLAMA_BASE_URL", "") or "http://127.0.0.1:11434"
            ).rstrip("/")
            model_name = getattr(settings, "OLLAMA_MODEL", "") or "qwen2.5-coder:7b"
            resp = requests.get(f"{base_url}/api/tags", timeout=4)
            resp.raise_for_status()
            models = [m.get("name", "") for m in (resp.json() or {}).get("models", [])]
            has_model = any(model_name in m for m in models)
            ai_health = {
                "provider": "ollama",
                "ok": has_model,
                "detail": f"{model_name} {'available' if has_model else 'missing'}",
            }
        except Exception as exc:
            ai_health = {
                "provider": "ollama",
                "ok": False,
                "detail": f"Unavailable: {exc}",
            }
    else:
        has_key = bool(getattr(settings, "ANTHROPIC_API_KEY", ""))
        ai_health = {
            "provider": "anthropic",
            "ok": has_key,
            "detail": "API key configured" if has_key else "API key missing",
        }

    # Tags for library filter chips
    from music.models import TrackTag

    all_tags = TrackTag.raw_objects.filter(church=church).order_by("name")

    # Track UIDs already in the active setlist (for greying add-song cards)
    setlist_track_uids = set()
    if active_setlist:
        setlist_track_uids = set(
            str(uid)
            for uid in active_setlist.songs.values_list("track__uid", flat=True)
        )

    # Setlist switcher param
    setlist_uid_param = request.GET.get("setlist", "")
    if setlist_uid_param:
        switched = (
            Setlist.raw_objects.filter(church=church, uid=setlist_uid_param)
            .prefetch_related("songs__track__sections", "songs__track__stems")
            .first()
        )
        if switched:
            active_setlist = switched
            setlist_track_uids = set(
                str(uid)
                for uid in active_setlist.songs.values_list("track__uid", flat=True)
            )

    # Catalog JSON for the "From Catalog" add-song tab (trimmed to 25/genre)
    import json as _json
    from music.seed_data import POPULAR_CHRISTIAN_TRACKS_BY_GENRE

    catalog_json = _json.dumps(
        {
            genre: entries[:25]
            for genre, entries in POPULAR_CHRISTIAN_TRACKS_BY_GENRE.items()
        }
    )

    # Service events this unit can link rehearsals to ("prepares for")
    from services.models import Event as ServiceEvent

    service_events = ServiceEvent.raw_objects.filter(
        church=church, event_type="Service"
    ).order_by("-date")[:20]

    first_song = None
    if active_setlist:
        first_song = active_setlist.songs.select_related("track").first()

    return render(
        request,
        "music/index.html",
        {
            "unit": unit,
            "page_title": f"{unit.name} — Music",
            "tracks": tracks,
            "setlists": setlists,
            "active_setlist": active_setlist,
            "upcoming_rehearsals": upcoming_rehearsals,
            "past_rehearsals": past_rehearsals,
            "unit_members": unit_members,
            "musical_keys": MUSICAL_KEYS,
            "can_manage": can_manage,
            "today": today,
            "ai_health": ai_health,
            "genre_seed_options": catalog_genres(),
            "all_tags": all_tags,
            "setlist_track_uids": setlist_track_uids,
            "catalog_json": catalog_json,
            "service_events": service_events,
            "first_song": first_song,
        },
    )


@login_required
def unit_media(request, slug):
    from media.models import (
        ServicePresentation,
        SlideTemplate,
        ProductionSchedule,
        PastorNote,
    )
    from music.models import Setlist

    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)

    if not _is_admin(request) and not _is_unit_member(request, unit):
        return HttpResponseForbidden("You are not a member of this unit.")

    settings_obj = getattr(church, "settings", None)
    if (
        not settings_obj
        or not settings_obj.enable_media_module
        or not unit.media_module
    ):
        return HttpResponseForbidden("Media module is not enabled for this unit.")

    # All presentations for this unit
    presentations = (
        ServicePresentation.raw_objects.filter(church=church, unit=unit)
        .select_related("event", "default_template", "setlist")
        .prefetch_related("slides")
        .order_by("-created_at")[:20]
    )

    # Live presentation if any
    live_presentation = (
        ServicePresentation.raw_objects.filter(church=church, unit=unit, is_live=True)
        .select_related("event")
        .prefetch_related("slides__template")
        .first()
    )

    # Slide templates available to this church
    templates = SlideTemplate.raw_objects.filter(church=church).order_by(
        "-is_default", "name"
    )

    # Active setlist: the most recent finalized setlist linked to a presentation,
    # or otherwise the most recent finalized setlist for this unit.
    active_setlist = (
        Setlist.raw_objects.filter(church=church, unit=unit, finalized=True)
        .select_related("event")
        .prefetch_related("songs__track")
        .order_by("-created_at")
        .first()
    )

    # Finalized setlists not yet synced to a presentation (ready to sync)
    ready_setlists = (
        Setlist.raw_objects.filter(church=church, unit=unit, finalized=True)
        .select_related("event")
        .exclude(presentation__isnull=False)
        .order_by("-created_at")[:10]
    )

    # Other setlists for the same event (draft or secondary)
    other_setlists = []
    if active_setlist and active_setlist.event:
        other_setlists = list(
            Setlist.raw_objects.filter(
                church=church, unit=unit, event=active_setlist.event
            )
            .exclude(pk=active_setlist.pk)
            .order_by("-created_at")[:5]
        )

    # Most recent production schedule for this unit
    production_schedule = (
        ProductionSchedule.raw_objects.filter(church=church, unit=unit)
        .select_related("event")
        .order_by("-event__date")
        .first()
    )

    # Pastor note: attached to the most recent presentation or event
    pastor_note = None
    if presentations:
        pastor_note = (
            PastorNote.raw_objects.filter(church=church, presentation__in=presentations)
            .select_related("author__user")
            .order_by("-created_at")
            .first()
        )

    # Determine if the current user is a Pastor-role member
    is_pastor = False
    try:
        from workforce.models import WorkforceMembershipRole

        membership = UnitMembership.raw_objects.filter(
            church=church,
            unit=unit,
            workforce_member__member__user=request.user,
            is_active=True,
        ).first()
        if membership:
            is_pastor = WorkforceMembershipRole.raw_objects.filter(
                church=church,
                workforce_member=membership.workforce_member,
                role__name__iexact="Pastor",
            ).exists()
    except Exception:
        pass

    can_manage = _is_admin(request) or _can(request, "media.manage")

    return render(
        request,
        "media/index.html",
        {
            "unit": unit,
            "page_title": f"{unit.name} — Media",
            "presentations": presentations,
            "live_presentation": live_presentation,
            "slide_templates": templates,
            "active_setlist": active_setlist,
            "ready_setlists": ready_setlists,
            "other_setlists": other_setlists,
            "production_schedule": production_schedule,
            "pastor_note": pastor_note,
            "is_pastor": is_pastor,
            "can_manage": can_manage,
        },
    )


@login_required
def unit_children(request, slug):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)

    if not _is_admin(request) and not _is_unit_member(request, unit):
        return HttpResponseForbidden("You are not a member of this unit.")

    settings_obj = getattr(church, "settings", None)
    if (
        not settings_obj
        or not settings_obj.enable_children_module
        or not unit.children_module
    ):
        return HttpResponseForbidden("Children module is not enabled for this unit.")

    return render(
        request,
        "children/index.html",
        {
            "unit": unit,
            "page_title": f"{unit.name}",
        },
    )


@login_required
def unit_youth(request, slug):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)

    if not _is_admin(request) and not _is_unit_member(request, unit):
        return HttpResponseForbidden("You are not a member of this unit.")

    settings_obj = getattr(church, "settings", None)
    if (
        not settings_obj
        or not settings_obj.enable_youth_module
        or not unit.youth_module
    ):
        return HttpResponseForbidden("Youth module is not enabled for this unit.")

    return render(
        request,
        "youth/index.html",
        {
            "unit": unit,
            "page_title": f"{unit.name}",
        },
    )


@login_required
def unit_teenagers(request, slug):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)

    if not _is_admin(request) and not _is_unit_member(request, unit):
        return HttpResponseForbidden("You are not a member of this unit.")

    settings_obj = getattr(church, "settings", None)
    if (
        not settings_obj
        or not settings_obj.enable_teenagers_module
        or not unit.teenagers_module
    ):
        return HttpResponseForbidden("Teenagers module is not enabled for this unit.")

    return render(
        request,
        "teenagers/index.html",
        {
            "unit": unit,
            "page_title": f"{unit.name}",
        },
    )


def unit_create(request):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden("Admin access required.")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        unit_type = request.POST.get("unit_type", "unit")
        description = request.POST.get("description", "").strip()
        color = request.POST.get("color", "blue").strip()
        report_to_id = request.POST.get("report_to", "").strip()
        is_default = request.POST.get("is_default") == "on"
        guest_management = request.POST.get("guest_management") == "on"
        music_module = request.POST.get("music_module") == "on"
        media_module = request.POST.get("media_module") == "on"
        children_module = request.POST.get("children_module") == "on"
        youth_module = request.POST.get("youth_module") == "on"
        teenagers_module = request.POST.get("teenagers_module") == "on"

        if not name:
            messages.error(request, "Name is required.")
        elif ChurchUnit.raw_objects.filter(church=church, slug=slugify(name)).exists():
            messages.error(request, "A unit with this name already exists.")
        else:
            report_to = (
                ChurchUnit.raw_objects.filter(
                    church=church, id=int(report_to_id)
                ).first()
                if report_to_id and report_to_id.isdigit()
                else None
            )

            unit = ChurchUnit.raw_objects.create(
                church=church,
                name=name,
                unit_type=unit_type,
                description=description,
                color=color,
                report_to=report_to,
                is_default=is_default,
                guest_management=guest_management,
                music_module=music_module,
                media_module=media_module,
                children_module=children_module,
                youth_module=youth_module,
                teenagers_module=teenagers_module,
                is_active=True,
            )
            messages.success(request, f"'{unit.name}' created.")
            return redirect("units:detail", slug=unit.slug)

    all_units = ChurchUnit.raw_objects.filter(church=church, is_active=True)
    return render(
        request,
        "units/unit_form.html",
        {
            "all_units": all_units,
            "page_title": "Create Unit / Group",
            "action": "create",
        },
    )


@login_required
def unit_edit(request, slug):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)

    if not _can_manage_unit(request, unit):
        return HttpResponseForbidden("Admin or unit head required.")

    if request.method == "POST":
        unit.name = request.POST.get("name", unit.name).strip()
        unit.description = request.POST.get("description", unit.description).strip()
        unit.color = request.POST.get("color", unit.color).strip()
        unit.is_default = request.POST.get("is_default") == "on"
        unit.guest_management = request.POST.get("guest_management") == "on"
        unit.music_module = request.POST.get("music_module") == "on"
        unit.media_module = request.POST.get("media_module") == "on"
        unit.children_module = request.POST.get("children_module") == "on"
        unit.youth_module = request.POST.get("youth_module") == "on"
        unit.teenagers_module = request.POST.get("teenagers_module") == "on"
        rid = request.POST.get("report_to", "").strip()
        unit.report_to = (
            ChurchUnit.raw_objects.filter(church=church, id=int(rid)).first()
            if rid and rid.isdigit()
            else None
        )
        unit.save()
        messages.success(request, "Unit updated.")
        return redirect("units:detail", slug=unit.slug)

    all_units = ChurchUnit.raw_objects.filter(church=church, is_active=True).exclude(
        id=unit.id
    )
    return render(
        request,
        "units/unit_form.html",
        {
            "unit": unit,
            "all_units": all_units,
            "page_title": f"Edit {unit.name}",
            "action": "edit",
        },
    )


@login_required
@require_POST
def unit_delete(request, slug):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()
    unit = get_unit_for_request(request, slug)
    unit.is_active = False
    unit.save(update_fields=["is_active"])
    messages.success(request, f"'{unit.name}' deactivated.")
    return redirect("units:list")


# -----------------------------------------------------------------------------
# Unit announcements & tasks
# -----------------------------------------------------------------------------
@login_required
def task_edit(request, slug, task_id):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)
    if not _can_manage_unit(request, unit):
        return HttpResponseForbidden("Admin or unit head required.")

    task = get_object_or_404(
        UnitTask.raw_objects.filter(church=church, unit=unit, is_active=True),
        id=task_id,
    )

    from units.forms import UnitTaskForm

    if request.method == "POST":
        form = UnitTaskForm(request.POST, instance=task, unit=unit)
        if form.is_valid():
            task = form.save()
            messages.success(request, "Task updated.")
            return redirect("units:detail", slug=unit.slug)
    else:
        form = UnitTaskForm(instance=task, unit=unit)

    return render(
        request,
        "units/task_form.html",
        {
            "unit": unit,
            "form": form,
            "task": task,
            "page_title": f"{unit.name} - Edit Task",
        },
    )


@login_required
def acknowledge_task(request, assignment_id):
    """Move task from 'open' → 'in_progress'. Supports AJAX and plain POST.

    GET requests (e.g. from notification bell links) redirect to the unit
    detail page so the member can acknowledge via the normal AJAX button.
    """
    assignment = get_object_or_404(TaskAssignment, id=assignment_id, is_active=True)

    # GET: redirect to unit detail — write operations require POST
    if request.method == "GET":
        unit = assignment.task.unit if assignment.task else None
        if unit:
            return redirect("units:detail", slug=unit.slug)
        return redirect("/")

    assigned_member = (
        assignment.membership.workforce_member.member
        if assignment.membership and assignment.membership.workforce_member
        else None
    )
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if assigned_member != request.member:
        if is_ajax:
            return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
        return HttpResponseForbidden()

    if assignment.status == "open":
        assignment.status = "in_progress"
        assignment.acknowledged_at = timezone.now()
        assignment.save(update_fields=["status", "acknowledged_at"])

    if is_ajax:
        return JsonResponse(
            {
                "ok": True,
                "status": assignment.status,
                "completion_pct": assignment.task.completion_percentage,
            }
        )

    messages.success(request, "Task acknowledged — marked In Progress.")
    return redirect(request.META.get("HTTP_REFERER", "/"))


@login_required
@require_POST
def complete_task(request, assignment_id):
    """Mark task assignment as done. Supports AJAX and plain POST."""
    assignment = get_object_or_404(TaskAssignment, id=assignment_id, is_active=True)

    assigned_member = (
        assignment.membership.workforce_member.member
        if assignment.membership and assignment.membership.workforce_member
        else None
    )
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if assigned_member != request.member:
        if is_ajax:
            return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
        return HttpResponseForbidden()

    assignment.completion_note = (request.POST.get("completion_notes") or "").strip()
    assignment.status = "done"
    assignment.completed_at = timezone.now()
    assignment.save(update_fields=["status", "completed_at", "completion_note"])

    if is_ajax:
        return JsonResponse(
            {
                "ok": True,
                "status": "done",
                "completion_pct": assignment.task.completion_percentage,
                "computed_status": assignment.task.computed_status,
            }
        )

    messages.success(request, "Task marked as complete.")
    return redirect(request.META.get("HTTP_REFERER", "/"))


@login_required
@require_POST
def task_delete(request, slug, task_id):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)
    if not _can_manage_unit(request, unit):
        return HttpResponseForbidden("Admin or unit head required.")

    task = get_object_or_404(
        UnitTask.raw_objects.filter(church=church, unit=unit, is_active=True),
        id=task_id,
    )
    task.is_active = False
    task.save(update_fields=["is_active"])
    messages.success(request, "Task removed.")
    return redirect("units:detail", slug=unit.slug)


@login_required
@require_POST
def update_report_status(request, slug, report_id):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)
    if not _can(request, "unit.report.review", unit=unit):
        return HttpResponseForbidden("Unit head or admin required.")

    report = get_object_or_404(
        UnitReport.raw_objects.filter(church=church, is_active=True), id=report_id
    )
    if report.unit_id != unit.id and report.report_to_unit_id != unit.id:
        return HttpResponseForbidden("Report is outside this unit scope.")
    status = (request.POST.get("status") or "").strip()
    if status not in {"reviewed", "escalated", "closed"}:
        messages.error(request, "Invalid report status.")
        return redirect("units:detail", slug=unit.slug)

    report.status = status
    if status == "escalated":
        if not report.report_to_unit and report.unit.report_to_id:
            report.report_to_unit_id = report.unit.report_to_id
        if report.report_to_unit:
            from notifications.utils import notify_members

            parent_head = report.report_to_unit.get_unit_head()
            if (
                parent_head
                and parent_head.workforce_member
                and parent_head.workforce_member.member
            ):
                notify_members(
                    [parent_head.workforce_member.member],
                    title=f"Escalated report: {report.unit.name}",
                    description=f"{report.title} ({report.get_category_display()})",
                    church=church,
                    is_urgent=False,
                )
    report.reviewed_by = getattr(request, "member", None)
    report.reviewed_at = timezone.now()
    report.escalation_note = (request.POST.get("escalation_note") or "").strip()
    report.save(
        update_fields=[
            "status",
            "report_to_unit",
            "reviewed_by",
            "reviewed_at",
            "escalation_note",
            "updated_at",
        ]
    )
    messages.success(request, "Report status updated.")
    return redirect("units:detail", slug=unit.slug)


@login_required
def announcement_edit(request, slug, announcement_id):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)
    if not _can_manage_unit(request, unit):
        return HttpResponseForbidden("Admin or unit head required.")

    announcement = get_object_or_404(
        UnitAnnouncement.raw_objects.filter(church=church, unit=unit, is_active=True),
        id=announcement_id,
    )

    from units.forms import UnitAnnouncementForm

    if request.method == "POST":
        form = UnitAnnouncementForm(request.POST, instance=announcement)
        if form.is_valid():
            form.save()
            messages.success(request, "Announcement updated.")
            return redirect("units:detail", slug=unit.slug)
    else:
        form = UnitAnnouncementForm(instance=announcement)

    return render(
        request,
        "units/announcement_form.html",
        {
            "unit": unit,
            "form": form,
            "announcement": announcement,
            "page_title": f"{unit.name} - Edit Announcement",
        },
    )


@login_required
@require_POST
def announcement_delete(request, slug, announcement_id):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)
    if not _can_manage_unit(request, unit):
        return HttpResponseForbidden("Admin or unit head required.")

    announcement = get_object_or_404(
        UnitAnnouncement.raw_objects.filter(church=church, unit=unit, is_active=True),
        id=announcement_id,
    )
    announcement.is_active = False
    announcement.save(update_fields=["is_active"])
    messages.success(request, "Announcement removed.")
    return redirect("units:detail", slug=unit.slug)


# ─────────────────────────────────────────────────────────────────────────────
# Member management
# ─────────────────────────────────────────────────────────────────────────────


@login_required
@require_POST
def add_member_to_unit(request, slug):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)
    if not _can_manage_unit(request, unit):
        return HttpResponseForbidden()

    from workforce.models import WorkforceMember

    wf_id = request.POST.get("workforce_member_id", "").strip()
    is_probation = request.POST.get("is_probation") == "on"

    wf = (
        WorkforceMember.raw_objects.filter(church=church, id=int(wf_id)).first()
        if wf_id and wf_id.isdigit()
        else None
    )

    if not wf:
        messages.error(request, "Member not found.")
        return redirect("units:detail", slug=unit.slug)

    m, created = UnitMembership.raw_objects.get_or_create(
        church=church,
        workforce_member=wf,
        unit=unit,
        defaults={"is_probation": is_probation, "is_active": True},
    )
    if not created:
        m.is_active = True
        m.is_probation = is_probation
        m.save(update_fields=["is_active", "is_probation"])

    messages.success(request, f"{wf} added to {unit.name}.")
    return redirect("units:detail", slug=unit.slug)


@login_required
@require_POST
def remove_member_from_unit(request, slug, membership_id):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)
    if not _can_manage_unit(request, unit):
        return HttpResponseForbidden()

    m = get_object_or_404(
        UnitMembership.raw_objects.filter(church=church, unit=unit), id=membership_id
    )
    m.is_active = False
    m.save(update_fields=["is_active"])
    messages.success(request, "Member removed.")
    return redirect("units:detail", slug=unit.slug)


@login_required
@require_POST
def transfer_member(request):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()

    m_id = request.POST.get("membership_id", "").strip()
    to_id = request.POST.get("to_unit_id", "").strip()
    is_prob = request.POST.get("is_probation") == "on"

    membership = get_object_or_404(
        UnitMembership.raw_objects.filter(church=church, is_active=True), id=m_id
    )
    to_unit = get_object_or_404(
        ChurchUnit.raw_objects.filter(church=church, is_active=True), id=to_id
    )

    old_unit = membership.unit
    membership.is_active = False
    membership.save(update_fields=["is_active"])

    new_m, _ = UnitMembership.raw_objects.get_or_create(
        church=church,
        workforce_member=membership.workforce_member,
        trainee_profile=membership.trainee_profile,
        unit=to_unit,
        defaults={"is_probation": is_prob, "is_active": True},
    )
    new_m.is_active = True
    new_m.is_probation = is_prob
    new_m.save(update_fields=["is_active", "is_probation"])

    messages.success(request, f"Transferred from {old_unit} → {to_unit}.")
    return redirect(request.POST.get("next") or request.META.get("HTTP_REFERER") or "/")


@login_required
@require_POST
def set_unit_head(request, slug, membership_id):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)
    if not _is_admin(request):
        return HttpResponseForbidden()

    # Clear previous head flag
    UnitMembership.raw_objects.filter(
        church=church, unit=unit, is_unit_head=True
    ).update(is_unit_head=False)

    m = get_object_or_404(
        UnitMembership.raw_objects.filter(church=church, unit=unit, is_active=True),
        id=membership_id,
    )
    m.is_unit_head = True
    m.save(update_fields=["is_unit_head"])

    # Also ensure the tier-3 UnitRole (Unit Head) is assigned via MembershipRole
    # so the resolver's explicit-role path also grants the right permissions.
    from permissions.models import UnitRole, MembershipRole
    from permissions.registry import TIER_UNIT_HEAD

    head_role = UnitRole.raw_objects.filter(
        church=church, unit=unit, tier=TIER_UNIT_HEAD, is_active=True
    ).first()
    if head_role:
        MembershipRole.raw_objects.get_or_create(
            church=church, membership=m, role=head_role
        )
    messages.success(request, "Unit head updated.")
    return redirect("units:detail", slug=unit.slug)


# ─────────────────────────────────────────────────────────────────────────────
# Probation & review flags
# ─────────────────────────────────────────────────────────────────────────────


@login_required
@require_POST
def set_probation(request, membership_id):
    church = getattr(request, "church", None)
    m = get_object_or_404(
        UnitMembership.raw_objects.filter(church=church, is_active=True),
        id=membership_id,
    )
    if not _can_manage_unit(request, m.unit):
        return HttpResponseForbidden()
    probation_unit_id = request.POST.get("probation_unit_id", "").strip()
    probation_unit = (
        ChurchUnit.raw_objects.filter(
            church=church, is_active=True, id=probation_unit_id
        ).first()
        if probation_unit_id.isdigit()
        else None
    )
    service_unit = probation_unit or m.unit
    same_unit = service_unit.id == m.unit_id

    if m.workforce_member:
        from workforce.models import WorkforceStage

        probationer_stage = WorkforceStage.raw_objects.filter(
            church=church,
            slug=WorkforceStage.SLUG_PROBATIONER,
            is_active=True,
        ).first()
        if probationer_stage and m.workforce_member.stage_id != probationer_stage.id:
            m.workforce_member.stage = probationer_stage
            m.workforce_member.save(update_fields=["stage"])

        if same_unit:
            m.is_probation = True
            if not m.is_active:
                m.is_active = True
            m.save(update_fields=["is_active", "is_probation"])
        else:
            # Temporarily remove the member from the disciplined unit and
            # create/mark the probation service unit as the active assignment.
            m.is_probation = True
            m.is_active = False
            m.save(update_fields=["is_active", "is_probation"])

            UnitMembership.raw_objects.update_or_create(
                church=church,
                workforce_member=m.workforce_member,
                unit=service_unit,
                defaults={"is_probation": True, "is_active": True},
            )
    else:
        m.is_probation = True
        m.save(update_fields=["is_probation"])
    messages.success(request, "Member placed on probation.")
    return redirect(
        "units:detail", slug=service_unit.slug if not same_unit else m.unit.slug
    )


@login_required
@require_POST
def clear_probation(request, membership_id):
    church = getattr(request, "church", None)
    m = get_object_or_404(
        UnitMembership.raw_objects.filter(church=church, is_active=True),
        id=membership_id,
    )
    if not _can_manage_unit(request, m.unit):
        return HttpResponseForbidden()
    if m.workforce_member:
        from workforce.models import WorkforceStage

        probation_qs = UnitMembership.raw_objects.filter(
            church=church,
            workforce_member=m.workforce_member,
            is_probation=True,
        )
        inactive_probation_qs = probation_qs.filter(is_active=False)
        active_probation_qs = probation_qs.filter(is_active=True)
        had_inactive_source = inactive_probation_qs.exists()
        restore_slug = (
            inactive_probation_qs.first().unit.slug
            if had_inactive_source
            else m.unit.slug
        )
        if had_inactive_source:
            inactive_probation_qs.update(is_active=True, is_probation=False)
            active_probation_qs.update(is_active=False, is_probation=False)
        else:
            probation_qs.update(is_probation=False)

        if not UnitMembership.raw_objects.filter(
            church=church,
            workforce_member=m.workforce_member,
            is_active=True,
            is_probation=True,
        ).exists():
            active_stage = WorkforceStage.raw_objects.filter(
                church=church,
                slug=WorkforceStage.SLUG_ACTIVE,
                is_active=True,
            ).first()
            if active_stage and m.workforce_member.stage_id != active_stage.id:
                m.workforce_member.stage = active_stage
                m.workforce_member.save(update_fields=["stage"])
    else:
        m.is_probation = False
        m.save(update_fields=["is_probation"])
    messages.success(request, "Probation cleared.")
    return redirect("units:detail", slug=restore_slug)


@login_required
@require_POST
def clear_for_review(request, membership_id):
    church = getattr(request, "church", None)
    m = get_object_or_404(
        UnitMembership.raw_objects.filter(church=church, is_active=True),
        id=membership_id,
    )
    if not _can_manage_unit(request, m.unit):
        return HttpResponseForbidden()
    m.for_review = False
    m.consecutive_absences = 0
    m.save(update_fields=["for_review", "consecutive_absences"])
    messages.success(request, "Review flag cleared.")
    return redirect("units:detail", slug=m.unit.slug)


# ─────────────────────────────────────────────────────────────────────────────
# Unit attendance
# ─────────────────────────────────────────────────────────────────────────────


@login_required
@require_POST
def mark_unit_attendance(request, slug):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)
    if not _can_manage_unit(request, unit):
        return JsonResponse({"error": "Not allowed."}, status=403)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    session = data.get("session", "Meeting").strip()
    from datetime import date as dateclass
    from django.utils.dateparse import parse_date

    meeting_date = parse_date(data.get("date", "")) or dateclass.today()
    records = data.get("records", [])
    marker = getattr(request, "member", None)
    count = 0

    for rec in records:
        m_id = rec.get("membership_id")
        status = rec.get("status", "absent")
        if status not in ("present", "absent", "excused"):
            continue
        membership = UnitMembership.raw_objects.filter(
            church=church, unit=unit, id=m_id, is_active=True
        ).first()
        if not membership:
            continue
        UnitAttendanceRecord.raw_objects.update_or_create(
            church=church,
            membership=membership,
            unit=unit,
            date=meeting_date,
            session=session,
            defaults={"status": status, "marked_by": marker, "is_active": True},
        )
        count += 1

    return JsonResponse({"success": True, "marked": count})


# ─────────────────────────────────────────────────────────────────────────────
# Campus views
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def campus_list(request):
    return redirect("tenants:campus_settings")


@login_required
def campus_create(request):
    return redirect("tenants:campus_settings")


@login_required
@require_POST
def transfer_to_campus(request):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()

    from accounts.models import ChurchMember

    member = get_object_or_404(
        ChurchMember.raw_objects.filter(church=church, is_active=True),
        id=request.POST.get("member_id", ""),
    )
    campus = get_object_or_404(
        Campus.raw_objects.filter(church=church, is_active=True),
        id=request.POST.get("campus_id", ""),
    )
    is_primary = request.POST.get("is_primary") == "on"

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
    messages.success(request, f"{member} added to {campus.name}.")
    return redirect(request.POST.get("next") or request.META.get("HTTP_REFERER") or "/")


# ─────────────────────────────────────────────────────────────────────────────
# AJAX
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def ajax_unit_members(request, slug):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)

    data = []
    for m in UnitMembership.raw_objects.filter(
        church=church, unit=unit, is_active=True
    ).select_related("workforce_member__member__user"):
        try:
            u = m.workforce_member.member.user
            data.append(
                {
                    "id": m.id,
                    "member_id": m.workforce_member.member.id,
                    "name": u.full_name or u.username,
                    "is_head": m.is_unit_head,
                    "is_probation": m.is_probation,
                    "is_online": u.is_online,
                }
            )
        except AttributeError:
            continue

    return JsonResponse({"members": data})


@login_required
@require_POST
def update_unit_color(request, slug):
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)
    if not _can_manage_unit(request, unit):
        return HttpResponseForbidden()

    color = (request.POST.get("color", "") or "").strip()
    if not color:
        try:
            payload = json.loads(request.body or "{}")
            color = (payload.get("color", "") or "").strip()
        except Exception:
            color = ""

    if not color:
        return JsonResponse({"ok": False, "error": "Color is required."}, status=400)

    unit.color = color
    unit.save(update_fields=["color"])
    return JsonResponse({"ok": True, "color": unit.color})


# ─────────────────────────────────────────────────────────────────────────────
# Unit Functional Role views
# ─────────────────────────────────────────────────────────────────────────────


@login_required
@require_POST
def functional_role_assign(request, slug):
    """
    Assign a functional role to a unit member.

    POST fields:
        role_id          UnitFunctionalRole pk
        membership_id    UnitMembership pk
        assignment_type  permanent | period | event
        start_date       (period only, optional)
        end_date         (period only, optional)
        event_id         (event only, optional)
        notes
    """
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not _can_manage_unit(request, unit) and not _can(
        request, "music.manage_roles", unit=unit
    ):
        if is_ajax:
            return JsonResponse(
                {"ok": False, "error": "Permission denied."}, status=403
            )
        return HttpResponseForbidden()

    role = get_object_or_404(
        UnitFunctionalRole, id=request.POST.get("role_id"), unit=unit, is_active=True
    )
    membership = get_object_or_404(
        UnitMembership, id=request.POST.get("membership_id"), unit=unit, is_active=True
    )
    atype = request.POST.get("assignment_type", "permanent")
    if atype not in ("permanent", "period", "event"):
        atype = "permanent"

    event = None
    if atype == "event":
        eid = request.POST.get("event_id")
        if eid:
            from services.models import Event as _Ev

            event = _Ev.raw_objects.filter(church=church, id=eid).first()

    UnitFunctionalRoleAssignment.objects.create(
        church=church,
        role=role,
        membership=membership,
        assignment_type=atype,
        start_date=request.POST.get("start_date") or None,
        end_date=request.POST.get("end_date") or None,
        event=event,
        notes=request.POST.get("notes", ""),
        assigned_by=getattr(request, "member", None),
        is_active=True,
    )

    if is_ajax:
        name = (
            membership.workforce_member.member.user.get_full_name()
            if membership.workforce_member
            else "Member"
        )
        return JsonResponse({"ok": True, "message": f"{name} assigned as {role.name}."})

    messages.success(request, "Role assigned.")
    return redirect("units:detail", slug=unit.slug)


@login_required
@require_POST
def functional_role_unassign(request, slug, assignment_id):
    """Deactivate a functional role assignment."""
    unit = get_unit_for_request(request, slug)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not _can_manage_unit(request, unit) and not _can(
        request, "music.manage_roles", unit=unit
    ):
        if is_ajax:
            return JsonResponse(
                {"ok": False, "error": "Permission denied."}, status=403
            )
        return HttpResponseForbidden()

    assignment = get_object_or_404(
        UnitFunctionalRoleAssignment, id=assignment_id, role__unit=unit, is_active=True
    )
    assignment.is_active = False
    assignment.save(update_fields=["is_active"])

    if is_ajax:
        return JsonResponse({"ok": True})

    messages.success(request, "Role assignment removed.")
    return redirect("units:detail", slug=unit.slug)


@login_required
@require_POST
def functional_role_create(request, slug):
    """Create a new custom functional role for a unit. Admin/head only."""
    church = getattr(request, "church", None)
    unit = get_unit_for_request(request, slug)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not _can_manage_unit(request, unit):
        if is_ajax:
            return JsonResponse(
                {"ok": False, "error": "Permission denied."}, status=403
            )
        return HttpResponseForbidden()

    name = (request.POST.get("name") or "").strip()
    if not name:
        if is_ajax:
            return JsonResponse({"ok": False, "error": "Name is required."}, status=400)
        messages.error(request, "Name is required.")
        return redirect("units:detail", slug=unit.slug)

    if UnitFunctionalRole.objects.filter(church=church, unit=unit, name=name).exists():
        if is_ajax:
            return JsonResponse(
                {"ok": False, "error": "A role with that name already exists."},
                status=400,
            )
        messages.error(request, "A role with that name already exists.")
        return redirect("units:detail", slug=unit.slug)

    role = UnitFunctionalRole.objects.create(
        church=church,
        unit=unit,
        name=name,
        preset=UnitFunctionalRole.PRESET_CUSTOM,
        description=request.POST.get("description", ""),
        is_active=True,
    )

    if is_ajax:
        return JsonResponse({"ok": True, "role_id": role.id, "role_name": role.name})

    messages.success(request, f"Role '{role.name}' created.")
    return redirect("units:detail", slug=unit.slug)


@login_required
@require_POST
def functional_role_toggle(request, slug, role_id):
    """Toggle active/inactive on a functional role. Admin/head only."""
    unit = get_unit_for_request(request, slug)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not _can_manage_unit(request, unit):
        if is_ajax:
            return JsonResponse(
                {"ok": False, "error": "Permission denied."}, status=403
            )
        return HttpResponseForbidden()

    role = get_object_or_404(UnitFunctionalRole, id=role_id, unit=unit)
    role.is_active = not role.is_active
    role.save(update_fields=["is_active"])

    if is_ajax:
        return JsonResponse({"ok": True, "is_active": role.is_active})

    state = "enabled" if role.is_active else "disabled"
    messages.success(request, f"Role '{role.name}' {state}.")
    return redirect("units:detail", slug=unit.slug)
