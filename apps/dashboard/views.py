"""
dashboard/views.py

Two dashboard views:

    admin_dashboard  — full analytics view for church admins/leaders.
                       Requires "dashboard.admin" permission.
    dashboard_view   — member-level view showing personal stats,
                       assigned guests, upcoming events.

Multi-tenant changes from single-tenant version:
    - All Team/is_project_admin/is_magnet_admin/is_team_admin references
      removed. Permission checks now use PermissionResolver.can().
    - All GuestEntry/AttendanceRecord/ClockRecord queries scoped to church.
    - GuestEntry.assigned_to is UnitMembership, not CustomUser — queries
      updated accordingly.
    - Status names (Planted, Not Planted etc.) now resolved from GuestStatus
      slugs so renaming a status doesn't break analytics.
    - get_user_color imported from core.utils.colors.
    - Team-based unit/member queries replaced with ChurchUnit/UnitMembership.
    - purpose_of_visit/channel_of_visit/service_attended are now FK fields —
      stats queries use the related name rather than raw string values.
"""

import calendar
import json, datetime
from datetime import timedelta, time, datetime

from django.contrib.auth.decorators import login_required
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Count, Q, Prefetch
from django.db.models.functions import ExtractMonth
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect
from django.utils import timezone
from collections import defaultdict
from accounts.models import CustomUser, ChurchMember
from tenants.models import Church
from tenants.time_utils import format_time_value
from core.utils.colors import resolve_color
from guests.models import GuestEntry, GuestStatus
from units.models import ChurchUnit, UnitMembership
from workforce.models import AttendanceRecord, ClockRecord
from workforce.utils import (
    get_calendar_items,
    get_visible_attendance_records,
    get_visible_clock_records,
    get_available_events_for_user,
    expand_unit_events,
    get_available_units_for_user,
)
from lms.dashboard_mixin import inject_lms_context


def get_user_color(user_id):
    return resolve_color(f"user:{user_id}", variant="hex")


# ─────────────────────────────────────────────────────────────────────────────
# Permission helpers
# ─────────────────────────────────────────────────────────────────────────────


def _can(request, permission):
    if request.user.is_superuser:
        return True
    perms = getattr(request, "permissions", None)
    return bool(perms and perms.can(permission))


def _display_plan_name(plan_name):
    if not plan_name:
        return ""
    return str(plan_name).replace("_", " ").title()


def _is_admin(request):
    """True if user can see the admin dashboard."""
    return request.user.is_superuser or _can(request, "dashboard.admin")


def _can_view_all_guests(request):
    return request.user.is_superuser or _can(request, "guests.view_all")


# ─────────────────────────────────────────────────────────────────────────────
# Widget context helpers — inserted before admin_dashboard
# ─────────────────────────────────────────────────────────────────────────────


def _widget_context(request, church, member=None, unit=None):
    """
    Build shared widget context injected into every dashboard render.
    Catches all exceptions so a missing migration never crashes the dashboard.
    """
    from django.utils import timezone

    is_admin = _is_admin(request)
    wf_announcements = []
    banner_announcements = []
    unit_announcements = []
    my_tasks = []
    analysis_text = ""
    guest_locations = []
    membership_ids = []
    unit_ids = []

    try:
        from accounts.models import ChurchMember
        from units.models import UnitMembership

        if member and not isinstance(member, ChurchMember):
            member = ChurchMember.raw_objects.filter(
                church=church, user=member
            ).first() or getattr(request, "member", None)
        elif not member:
            member = getattr(request, "member", None)

        if member:
            memberships = list(
                UnitMembership.raw_objects.filter(
                    church=church, workforce_member__member=member, is_active=True
                ).select_related("unit")
            )
            membership_ids = [m.id for m in memberships]
            unit_ids = [m.unit_id for m in memberships if m.unit_id]
            if not unit and memberships:
                unit = memberships[0].unit
    except Exception:
        member = getattr(request, "member", None)

    # ── Workforce-wide announcements ─────────────────────────────────────────
    try:
        from workforce.models import WorkforceAnnouncement

        now = timezone.now()
        wf_qs = WorkforceAnnouncement.raw_objects.filter(church=church).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )
        if not is_admin and member:
            wf_qs = wf_qs.filter(
                models.Q(target_units__isnull=True)
                | models.Q(target_units__id__in=unit_ids)
            ).distinct()
        wf_announcements = list(wf_qs.order_by("-is_pinned", "-created_at")[:10])
    except Exception:
        pass

    # ── Unit announcements ───────────────────────────────────────────────────
    try:
        from units.models import UnitAnnouncement

        unit_qs = UnitAnnouncement.raw_objects.filter(church=church)
        if is_admin and not unit_ids:
            unit_qs = unit_qs
        elif unit_ids:
            unit_qs = unit_qs.filter(unit_id__in=unit_ids)
        elif unit:
            unit_qs = unit_qs.filter(unit=unit)
        else:
            unit_qs = unit_qs.none()
        unit_announcements = list(
            unit_qs.select_related("unit").order_by("-is_pinned", "-created_at")[:5]
        )
    except Exception:
        pass

    banner_announcements = sorted(
        [
            *[a for a in wf_announcements if getattr(a, "is_banner", False)],
            *[a for a in unit_announcements if getattr(a, "is_banner", False)],
        ],
        key=lambda ann: getattr(ann, "created_at", timezone.now()),
        reverse=True,
    )

    # ── Tasks ────────────────────────────────────────────────────────────────
    try:
        from workforce.models import WorkforceTask
        from units.models import TaskAssignment

        if member:
            for a in (
                TaskAssignment.objects.filter(
                    church=church, membership_id__in=membership_ids
                )
                .select_related("task__unit")
                .order_by("task__due_date")[:10]
            ):
                my_tasks.append(
                    {
                        "title": a.task.title,
                        "status": a.status,
                        "due_date": a.task.due_date,
                        "is_overdue": a.task.is_overdue,
                        "kind": "unit",
                        "source": (getattr(a.task, "unit", None) and a.task.unit.name)
                        or "Unit",
                        # Extra fields for interactive widget
                        "assignment_id": a.id,
                        "unit_slug": a.task.unit.slug if a.task.unit else None,
                        "completion_pct": a.task.completion_percentage,
                        "acknowledged_at": a.acknowledged_at,
                    }
                )
            for t in (
                WorkforceTask.raw_objects.filter(
                    church=church,
                    status__in=["open", "in_progress", "review"],
                )
                .filter(
                    models.Q(assigned_members__id__in=membership_ids)
                    | models.Q(assigned_units__id__in=unit_ids)
                )
                .distinct()
                .order_by("due_date")[:5]
            ):
                my_tasks.append(
                    {
                        "title": t.title,
                        "status": t.status,
                        "due_date": t.due_date,
                        "is_overdue": t.is_overdue,
                        "kind": "workforce",
                        "source": "Workforce",
                    }
                )
    except Exception:
        pass

    # ── AI insight ───────────────────────────────────────────────────────────
    try:
        from core.ai_skills import dashboard_analysis

        perms = getattr(request, "permissions", None)
        tier_val = (perms.get_tier() if perms else 6) or 6
        metrics = {
            "Tasks pending": sum(1 for t in my_tasks if t["status"] != "done"),
            "Announcements": len(wf_announcements) + len(unit_announcements),
        }
        if unit:
            metrics["Unit"] = unit.name
        analysis_text = (
            dashboard_analysis(
                church=church,
                user_tier=tier_val,
                unit_name=unit.name if unit else "",
                metrics=metrics,
            )
            or ""
        )
    except Exception:
        analysis_text = ""

    return {
        "workforce_announcements": wf_announcements,
        "banner_announcements": banner_announcements,
        "unit_announcements": unit_announcements,
        "my_tasks": my_tasks,
        "analysis_text": analysis_text,
        "widget_title": "Today's Insight",
        "guest_locations": guest_locations,
        "bible_widget": _bible_widget_data(church, member),
    }


def _bible_widget_data(church, member) -> dict:
    """
    Build compact Bible data for the dashboard widget rail.
    Returns a safe dict — all errors caught, never crashes the dashboard.
    """
    try:
        from django.utils import timezone
        from django.db.models import Q
        from bible.models import (
            ReadingPlan,
            ReadingPlanEntry,
            MemberPlanProgress,
            MemoryVerse,
            MemberPlanStreak,
            MemberBadgeAward,
        )
        from bible.services.badges import get_member_stats

        today = timezone.localdate()

        # Today's reading entry
        today_entry = None
        plan_title = ""
        progress_pct = None
        completed_entry_ids = set()

        if member:
            completed_entry_ids = set(
                MemberPlanProgress.raw_objects.filter(
                    church=church, member=member
                ).values_list("entry_id", flat=True)
            )

        active_plan = (
            ReadingPlan.raw_objects.filter(
                church=church,
                is_published=True,
                is_active=True,
                start_date__lte=today,
            )
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
            .order_by("-start_date")
            .first()
        )

        if active_plan:
            plan_title = active_plan.title
            today_entry = active_plan.entries.filter(
                is_active=True, scheduled_date=today
            ).first()
            if not today_entry:
                today_entry = (
                    active_plan.entries.filter(is_active=True)
                    .exclude(id__in=completed_entry_ids)
                    .first()
                )

            total = active_plan.entries.filter(is_active=True).count()
            done = len(
                [
                    eid
                    for eid in completed_entry_ids
                    if active_plan.entries.filter(id=eid).exists()
                ]
            )
            progress_pct = round((done / total * 100) if total else 0)

        # Memory verse
        memory_verse = (
            MemoryVerse.raw_objects.filter(
                church=church,
                is_published=True,
                is_active=True,
                active_date__lte=today,
            )
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
            .order_by("-active_date")
            .first()
        )

        # Streak + badges
        current_streak = 0
        badges = []
        if member:
            for s in MemberPlanStreak.raw_objects.filter(church=church, member=member):
                if s.current_streak > current_streak:
                    current_streak = s.current_streak
            badges = [
                {"icon": a.badge.icon_emoji, "name": a.badge.name}
                for a in MemberBadgeAward.raw_objects.filter(
                    church=church, member=member
                )
                .select_related("badge")
                .order_by("-awarded_at")[:4]
            ]

        return {
            "today_entry": today_entry,
            "plan_title": plan_title,
            "progress_pct": progress_pct,
            "memory_verse": memory_verse,
            "current_streak": current_streak,
            "badges": badges,
        }
    except Exception:
        return {
            "today_entry": None,
            "plan_title": "",
            "progress_pct": None,
            "memory_verse": None,
            "current_streak": 0,
            "badges": [],
        }


def _guest_location_data(church, guest_qs):
    """Return top catchment areas from guest home_address field."""
    from collections import Counter

    addresses = list(
        guest_qs.exclude(home_address="").values_list("home_address", flat=True)[:500]
    )
    areas = []
    for addr in addresses:
        parts = [p.strip() for p in addr.replace(",", "\n").split("\n") if p.strip()]
        if len(parts) >= 2:
            areas.append(parts[-2])
        elif parts:
            areas.append(parts[0])
    counter = Counter(areas)
    total = sum(counter.values()) or 1
    return [
        {"area": area, "count": count, "percent": round(count / total * 100, 1)}
        for area, count in counter.most_common(10)
        if area
    ]


def _serialize_available_events(events, today):
    upcoming = []
    weekday_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }

    for event in events:
        if event.date and event.date >= today:
            upcoming.append(
                {
                    "id": event.id,
                    "name": event.name,
                    "event_type": event.event_type,
                    "attendance_mode": event.attendance_mode,
                    "time": event.time.isoformat() if event.time else None,
                    "date": event.date,
                    "unit_id": getattr(event.unit, "id", None),
                    "unit_name": (
                        getattr(event.unit, "name", "ChurchForce")
                        if event.unit
                        else "ChurchForce"
                    ),
                    "unit_color_hex": getattr(event.unit, "color_hex", "#f59f00"),
                    "event_image": event.event_image.url if event.event_image else None,
                    "registrable": bool(event.registration_link),
                    "registration_link": event.registration_link,
                    "postponed": event.postponed,
                    "is_recurring_weekly": event.is_recurring_weekly,
                }
            )
            continue

        if event.is_recurring_weekly and event.day_of_week:
            weekday = weekday_map.get((event.day_of_week or "").lower())
            if weekday is None:
                continue
            days_until = (weekday - today.weekday()) % 7
            next_date = today + timedelta(days=days_until)
            upcoming.append(
                {
                    "id": event.id,
                    "name": event.name,
                    "event_type": event.event_type,
                    "attendance_mode": event.attendance_mode,
                    "time": event.time.isoformat() if event.time else None,
                    "date": next_date,
                    "unit_id": getattr(event.unit, "id", None),
                    "unit_name": (
                        getattr(event.unit, "name", "ChurchForce")
                        if event.unit
                        else "ChurchForce"
                    ),
                    "unit_color_hex": getattr(event.unit, "color_hex", "#f59f00"),
                    "event_image": event.event_image.url if event.event_image else None,
                    "registrable": bool(event.registration_link),
                    "registration_link": event.registration_link,
                    "postponed": event.postponed,
                    "is_recurring_weekly": True,
                }
            )

    return sorted(upcoming, key=lambda e: (e["date"], e.get("time") or ""))


# ─────────────────────────────────────────────────────────────────────────────
# Shared analytics helpers
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_status_id(church, slug):
    """Return PK of a GuestStatus by slug, or None."""
    status = GuestStatus.raw_objects.filter(
        church=church, slug=slug, is_active=True
    ).first()
    return status.pk if status else None


def _guest_status_counts(qs, church):
    """
    Return a dict of {status_name: count} and individual named counts
    for statuses that templates reference by slug.
    """
    status_data = (
        qs.values("status__name", "status__slug")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    by_slug = {row["status__slug"]: row["count"] for row in status_data}
    by_name = {row["status__name"] or "Unknown": row["count"] for row in status_data}
    labels = list(by_name.keys())
    counts = list(by_name.values())

    return {
        "status_labels": labels,
        "status_counts": counts,
        "planted_count": by_slug.get("planted", 0),
        "not_planted_count": by_slug.get("not-planted", 0),
        "committed_count": by_slug.get("committed", 0),
        "in_contact_count": by_slug.get("in-contact", 0),
        "new_guest_count": by_slug.get("new-guest", 0),
    }


def _purpose_stats(qs):
    """Return per-purpose counts from configurable VisitPurpose FK."""
    purpose_data = (
        qs.values("purpose_of_visit__name")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    total = sum(row["count"] for row in purpose_data) or 1
    return {
        row["purpose_of_visit__name"]
        or "Not specified": {
            "count": row["count"],
            "percentage": round(row["count"] / total * 100, 1),
        }
        for row in purpose_data
    }


def _channel_progress(qs):
    """Return channel breakdown list for templates."""
    channel_data = (
        qs.values("channel_of_visit__name")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    total = sum(row["count"] for row in channel_data) or 1
    return [
        {
            "label": row["channel_of_visit__name"] or "Unknown",
            "count": row["count"],
            "percent": round(row["count"] / total * 100, 2),
        }
        for row in channel_data
    ]


def _service_stats(qs):
    """Return service attendance breakdown."""
    service_data = (
        qs.values("service_attended__name")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    labels = [row["service_attended__name"] or "Not Specified" for row in service_data]
    counts = [row["count"] for row in service_data]
    total = sum(counts) or 1

    most_attended = labels[0] if labels else "No Data"
    most_count = counts[0] if counts else 0

    return {
        "service_labels": labels,
        "service_counts": counts,
        "most_attended_service": most_attended,
        "most_attended_count": most_count,
        "attendance_rate": round(most_count / total * 100, 1),
    }


def _monthly_summary(qs, year):
    """Return per-month guest counts for a given year."""
    month_counts_qs = (
        qs.filter(date_of_visit__year=year)
        .annotate(month=ExtractMonth("date_of_visit"))
        .values("month")
        .annotate(count=Count("id"))
        .order_by("month")
    )
    counts = {m: 0 for m in range(1, 13)}
    for entry in month_counts_qs:
        counts[entry["month"]] = entry["count"]

    max_count = max(counts.values(), default=0)
    min_count = min(counts.values(), default=0)
    avg_count = sum(counts.values()) // 12

    def pct(c):
        return round(c / max_count * 100, 1) if max_count else 0

    max_month = next(
        (calendar.month_name[m] for m, c in counts.items() if c == max_count), "N/A"
    )
    min_month = next(
        (calendar.month_name[m] for m, c in counts.items() if c == min_count), "N/A"
    )

    return {
        "max_month": max_month,
        "max_count": max_count,
        "max_percent": pct(max_count),
        "min_month": min_month,
        "min_count": min_count,
        "min_percent": pct(min_count),
        "avg_count": avg_count,
        "avg_percent": pct(avg_count),
        "total_count": qs.filter(date_of_visit__year=year).count(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Admin dashboard
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def admin_dashboard(request):
    """
    Full analytics dashboard for church admins and leaders.
    Requires: dashboard.admin permission (or superuser).

    Template: accounts/admin_dashboard.html
    """
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _is_admin(request):
        return HttpResponseForbidden(
            "You do not have permission to view the admin dashboard."
        )

    user = request.user
    today = timezone.localdate()
    current_year = today.year
    last_30_days = today - timedelta(days=30)

    # ── Base queryset — all guests for this church ────────────────────
    queryset = GuestEntry.raw_objects.filter(
        church=church,
        is_active=True,
        is_deleted=False,
    )

    # ── Year selection ────────────────────────────────────────────────
    available_years = [d.year for d in queryset.dates("date_of_visit", "year")]
    try:
        year = int(request.GET.get("year", current_year))
    except (TypeError, ValueError):
        year = current_year

    # ── Analytics ────────────────────────────────────────────────────
    summary_data = _monthly_summary(queryset, year)
    service_data = _service_stats(queryset)
    status_data = _guest_status_counts(queryset, church)
    purpose_stats = _purpose_stats(queryset)
    channel_prog = _channel_progress(queryset)

    # ── Monthly growth ────────────────────────────────────────────────
    first_day_this_month = today.replace(day=1)
    last_month_end = first_day_this_month - timedelta(days=1)
    first_day_last_month = last_month_end.replace(day=1)

    current_month_count = queryset.filter(
        date_of_visit__gte=first_day_this_month,
        date_of_visit__lte=today,
    ).count()
    last_month_count = queryset.filter(
        date_of_visit__gte=first_day_last_month,
        date_of_visit__lte=last_month_end,
    ).count()

    if last_month_count:
        increase_rate = round(
            (current_month_count - last_month_count) / last_month_count * 100, 1
        )
    else:
        increase_rate = 100 if current_month_count else 0

    # ── Unit members ──────────────────────────────────────────────────
    units = ChurchUnit.raw_objects.filter(church=church, is_active=True)
    all_memberships = list(
        UnitMembership.raw_objects.filter(church=church, is_active=True)
        .select_related("unit", "workforce_member__member__user")
        .prefetch_related("roles__role")
    )

    # Build fallback list (unique users)
    other_users = []
    seen_ids = set()

    for m in all_memberships:
        try:
            u = m.workforce_member.member.user
            if u and not u.is_superuser and u.id not in seen_ids:
                u.color = get_user_color(u.id)
                other_users.append(u)
                seen_ids.add(u.id)
        except AttributeError:
            continue

    # Group memberships by type WITHOUT mutating original
    unit_type_labels = {
        "group": "Groups",
        "unit": "Units",
    }

    grouped_member_pairs = []

    for unit_type in ("unit", "group"):
        filtered = [
            m for m in all_memberships if m.unit and m.unit.unit_type == unit_type
        ]

        if filtered:
            grouped_member_pairs.append(
                (
                    {
                        "name": unit_type_labels.get(unit_type, unit_type.title()),
                        "color_class": (
                            "bg-indigo-lt" if unit_type == "unit" else "bg-purple-lt"
                        ),
                        "unit_type": unit_type,
                    },
                    filtered,
                )
            )

    # 🔥 Deduplicate users and attach multiple units
    unit_member_pairs = []

    for group_meta, group_memberships in grouped_member_pairs:
        user_map = defaultdict(list)

        for m in group_memberships:
            try:
                user_obj = m.workforce_member.member.user
                if not user_obj or user_obj.is_superuser:
                    continue

                user_obj.color = get_user_color(user_obj.id)

                # avoid duplicate unit entries
                if m.unit not in user_map[user_obj]:
                    user_map[user_obj].append(m.unit)

            except AttributeError:
                continue

        cleaned_members = [
            {
                "user": user,
                "units": units,  # 🔥 multiple units per user
            }
            for user, units in user_map.items()
        ]

        if cleaned_members:
            unit_member_pairs.append((group_meta, cleaned_members))

    # ── Attendance ────────────────────────────────────────────────────
    records = get_visible_attendance_records(user, since_date=last_30_days)
    clock_records = get_visible_clock_records(user, since_date=last_30_days)
    clock_map = {(c.user_id, c.event_id, c.date): c for c in clock_records}

    for r in records:
        clock = clock_map.get((r.user_id, r.event_id, r.date))
        r.clock_in_time = format_time_value(clock.clock_in, church) if clock else None
        r.clock_out_time = format_time_value(clock.clock_out, church) if clock else None

    total_clock_in = clock_records.filter(clock_in__isnull=False).count()
    total_clock_out = clock_records.filter(clock_out__isnull=False).count()
    today_record = clock_records.filter(date=today).first()

    # ── Events ────────────────────────────────────────────────────────
    calendar_items = get_calendar_items(user)
    all_events = get_available_events_for_user(user)
    upcoming_events = _serialize_available_events(all_events, today)
    start_of_week = today - timedelta(days=(today.isoweekday() % 7))
    end_of_week = start_of_week + timedelta(days=6)
    weekly_events = [
        e for e in upcoming_events if start_of_week <= e["date"] <= end_of_week
    ]
    for e in weekly_events:
        e["datetime_iso"] = f"{e['date']}T{e.get('time', '00:00:00')}"

    # ── Church members list ───────────────────────────────────────────
    all_members = (
        CustomUser.objects.filter(
            church_memberships__church=church,
            church_memberships__is_active=True,
            is_active=True,
            is_superuser=False,
        )
        .distinct()
        .order_by("full_name")
    )

    # ── Permission context for templates ─────────────────────────────
    context_user_permissions = json.dumps(
        {
            "is_admin": _is_admin(request),
            "can_view_all_guests": _can_view_all_guests(request),
            "can_manage_users": _can(request, "accounts.manage_users"),
            "can_manage_events": _can(request, "events.manage_all"),
        },
        cls=DjangoJSONEncoder,
    )

    bible_provider_analytics = {}
    try:
        from bible.services.analytics import provider_analytics

        bible_provider_analytics = provider_analytics(church, days=7)
    except Exception:
        bible_provider_analytics = {}

    context = {
        # year selector
        "available_years": available_years,
        "current_year": current_year,
        # analytics
        "summary_data": summary_data,
        **service_data,
        **status_data,
        "channel_progress": channel_prog,
        "purpose_stats": purpose_stats,
        "total_guests": queryset.count(),
        "increase_rate": increase_rate,
        "percent_change": increase_rate,
        # unit
        "users": all_members,
        "other_users": other_users,
        "user_units": units,
        "unit_member_pairs": unit_member_pairs,
        # attendance
        "records": records,
        "can_view_attendance_summary": _can(request, "attendance.view_all"),
        "clock_records": clock_records,
        "total_clock_in": total_clock_in,
        "total_clock_out": total_clock_out,
        "today_clock_in": getattr(today_record, "clock_in", None),
        "today_clock_out": getattr(today_record, "clock_out", None),
        # events
        "calendar_items": calendar_items,
        "available_events": upcoming_events,
        "next_events_for_week": weekly_events,
        "assignable_memberships": list(
            UnitMembership.raw_objects.filter(church=church, is_active=True)
            .select_related("unit", "workforce_member__member__user")
            .order_by("unit__name", "workforce_member__member__user__full_name")
        ),
        "available_units": get_available_units_for_user(user),
        "can_create_guest": bool(
            ChurchUnit.raw_objects.filter(
                church=church,
                is_active=True,
                guest_management=True,
            ).exists()
        ),
        # permissions
        "context_user_permissions": context_user_permissions,
        "bible_provider_analytics": bible_provider_analytics,
        "show_filters": False,
        "page_title": "Admin Dashboard",
        **_widget_context(
            request,
            church,
            member=member if "member" in dir() else getattr(request, "member", None),
        ),
    }

    return render(request, "dashboard/admin_dashboard.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# Superuser church management
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def superuser_dashboard(request):
    """
    Operator control panel — only accessible on founder.workforce.church
    OR by a Django superuser on any host.
    Provides full cross-tenant visibility and plan management.
    """
    if not request.user.is_superuser:
        return HttpResponseForbidden("Superuser access required.")

    from billing.models import SubscriptionPlan, ChurchSubscription, PaymentRecord
    from billing.services import grant_founders_plan

    msg_ok = None
    msg_err = None

    # ── POST actions ──────────────────────────────────────────────────────────
    if request.method == "POST":
        action = request.POST.get("action", "")
        church_id = request.POST.get("church_id", "")

        target = Church.raw_objects.filter(id=church_id).first() if church_id else None

        if not target and action not in ("seed_plans",):
            msg_err = "Church not found."

        elif action == "grant_plan":
            plan_key = request.POST.get("plan_key", "").strip()
            # Lifetime founders plans
            if plan_key in ("founders_saas_lifetime", "founders_white_label_lifetime"):
                tier = "saas" if "saas" in plan_key else "white_label"
                grant_founders_plan(target, tier)
                msg_ok = f"Founders lifetime plan granted to {target.name}."
            else:
                # Regular paid plan — assign with expiry
                from django.utils import timezone
                from datetime import timedelta

                plan_obj, _ = SubscriptionPlan.objects.get_or_create(
                    name=plan_key,
                    defaults={
                        "monthly_price_ngn": 0,
                        "multi_campus": plan_key != "trial",
                    },
                )
                days = int(request.POST.get("days", 30))
                sub = ChurchSubscription.objects.filter(church=target).first()
                is_trial = plan_key == "trial"
                now_ = timezone.now()
                exp = now_ + timedelta(days=days)
                if sub:
                    sub.plan = plan_obj
                    sub.is_trial = is_trial
                    sub.is_active = True
                    sub.expires_at = None if is_trial else exp
                    sub.trial_expires_at = exp if is_trial else None
                    sub.save()
                else:
                    ChurchSubscription.objects.create(
                        church=target,
                        plan=plan_obj,
                        is_trial=is_trial,
                        is_active=True,
                        expires_at=None if is_trial else exp,
                        trial_expires_at=exp if is_trial else None,
                    )
                msg_ok = f"{plan_key} plan assigned to {target.name} for {days} days."

        elif action == "toggle_active":
            target.is_active = not target.is_active
            target.save(update_fields=["is_active"])
            state = "activated" if target.is_active else "deactivated"
            msg_ok = f"{target.name} {state}."

        elif action == "set_subdomain":
            sub = request.POST.get("subdomain", "").strip().lower()
            if (
                sub
                and Church.raw_objects.filter(subdomain=sub)
                .exclude(id=target.id)
                .exists()
            ):
                msg_err = f"Subdomain '{sub}' is already taken."
            else:
                target.subdomain = sub or None
                target.save(update_fields=["subdomain"])
                msg_ok = f"Subdomain set to '{sub}' for {target.name}."

        elif action == "set_custom_domain":
            dom = request.POST.get("custom_domain", "").strip().lower()
            target.custom_domain = dom or None
            target.save(update_fields=["custom_domain"])
            msg_ok = f"Custom domain updated for {target.name}."

        elif action == "seed_plans":
            # Seed all SubscriptionPlan records if missing
            from billing.management.commands.dev_grant_plan import PLAN_DEFAULTS

            seeded = []
            for name, defaults in PLAN_DEFAULTS.items():
                _, created = SubscriptionPlan.objects.get_or_create(
                    name=name, defaults=defaults
                )
                if created:
                    seeded.append(name)
            msg_ok = f"Plans seeded: {', '.join(seeded) or 'all already present'}."

        elif action == "extend":
            from django.utils import timezone
            from datetime import timedelta

            days = int(request.POST.get("days", 30))
            sub = ChurchSubscription.objects.filter(church=target).first()
            if sub:
                base = sub.expires_at or timezone.now()
                sub.expires_at = base + timedelta(days=days)
                sub.is_active = True
                sub.save(update_fields=["expires_at", "is_active"])
                msg_ok = f"Extended {target.name} by {days} days."
            else:
                msg_err = "No subscription found to extend."

    # ── Query ─────────────────────────────────────────────────────────────────
    scope = request.GET.get("scope", "all")
    status = request.GET.get("status", "active")
    query = (request.GET.get("q") or "").strip()

    qs = Church.raw_objects.all()
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)
    if query:
        qs = qs.filter(
            Q(name__icontains=query)
            | Q(slug__icontains=query)
            | Q(subdomain__icontains=query)
            | Q(custom_domain__icontains=query)
        )

    # Annotate subscription data
    from django.utils import timezone as tz

    churches = []
    for c in qs.order_by("-created_at"):
        sub = ChurchSubscription.objects.filter(church=c).first()
        plan_name = sub.plan.name if sub else "—"
        is_valid = sub.is_valid() if sub else False
        days_left = sub.days_until_expiry() if sub else None
        last_payment = (
            PaymentRecord.objects.filter(church=c, status="success")
            .order_by("-created_at")
            .first()
        )
        churches.append(
            {
                "obj": c,
                "sub": sub,
                "plan_name": plan_name,
                "is_valid": is_valid,
                "days_left": days_left,
                "last_payment": last_payment,
            }
        )

    all_plans = SubscriptionPlan.objects.all().order_by("name")

    # ── Stats ─────────────────────────────────────────────────────────────────
    from accounts.models import ChurchMember

    stats = {
        "total_churches": Church.raw_objects.count(),
        "active_churches": Church.raw_objects.filter(is_active=True).count(),
        "total_members": ChurchMember.raw_objects.filter(is_active=True).count(),
        "paid_churches": ChurchSubscription.objects.filter(
            is_trial=False, is_active=True
        ).count(),
        "trial_churches": ChurchSubscription.objects.filter(is_trial=True).count(),
        "revenue_total": PaymentRecord.objects.filter(status="success").aggregate(
            t=models.Sum("amount_ngn")
        )["t"]
        or 0,
    }

    context = {
        "page_title": "Operator Control Panel",
        "scope": scope,
        "status": status,
        "query": query,
        "churches": churches,
        "total_count": len(churches),
        "all_plans": all_plans,
        "msg_ok": msg_ok,
        "msg_err": msg_err,
        "stats": stats,
    }
    return render(request, "dashboard/superuser_dashboard.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# Member dashboard
# ─────────────────────────────────────────────────────────────────────────────
@login_required
def dashboard_view(request):
    """
    Personal dashboard for any authenticated workforce member.
    Shows personal guest assignments, attendance, and upcoming events.

    Template: guests/dashboard.html
    """
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    if request.GET.get("mode") != "regular":
        member = getattr(request, "member", None)
        if member:
            from workforce.models import WorkforceTraineeProfile

            if WorkforceTraineeProfile.raw_objects.filter(
                church=church,
                member=member,
                reason="induction",
                is_active=True,
            ).exists():
                return redirect("dashboard:trainee_dashboard")

    user = request.user
    today = timezone.localdate()
    current_year = today.year
    last_30_days = today - timedelta(days=30)

    # ── Resolve this user's UnitMembership IDs ────────────────────────
    # Used to scope guest assignments to the current user
    membership_ids = list(
        UnitMembership.raw_objects.filter(
            church=church,
            workforce_member__member__user=user,
            is_active=True,
        ).values_list("id", flat=True)
    )

    # ── Guest queryset ────────────────────────────────────────────────
    # Admins see all church guests; others see only their assigned ones
    base_qs = GuestEntry.raw_objects.filter(
        church=church, is_active=True, is_deleted=False
    )

    if _can_view_all_guests(request):
        queryset = base_qs
    else:
        queryset = base_qs.filter(assigned_to_id__in=membership_ids)

    # ── Available years (full church scope for year selector) ─────────
    available_years = [d.year for d in base_qs.dates("date_of_visit", "year")]

    # ── Summary JSON (reuse guests.views helper via direct call) ──────
    try:
        from guests.views import guest_entry_summary
        import copy

        fake_request = copy.copy(request)
        fake_request.GET = request.GET.copy()
        fake_request.GET["year"] = str(current_year)
        summary_json = guest_entry_summary(fake_request)
        summary_data = json.loads(summary_json.content.decode())
    except Exception:
        summary_data = {}

    # ── Analytics ────────────────────────────────────────────────────
    service_data = _service_stats(queryset)
    status_data = _guest_status_counts(queryset, church)
    purpose_stats = _purpose_stats(queryset)
    channel_prog = _channel_progress(queryset)

    # ── Monthly growth metrics ────────────────────────────────────────
    first_day_this_month = today.replace(day=1)
    last_month_end = first_day_this_month - timedelta(days=1)
    first_day_last_month = last_month_end.replace(day=1)

    planted_status_id = _resolve_status_id(church, "planted")

    metrics = queryset.aggregate(
        total_guests=Count("id"),
        current_month_count=Count(
            "id",
            filter=Q(
                date_of_visit__gte=first_day_this_month,
                date_of_visit__lte=today,
            ),
        ),
        last_month_count=Count(
            "id",
            filter=Q(
                date_of_visit__gte=first_day_last_month,
                date_of_visit__lte=last_month_end,
            ),
        ),
        user_total=Count("id", filter=Q(assigned_to_id__in=membership_ids)),
        user_current_month=Count(
            "id",
            filter=Q(
                assigned_to_id__in=membership_ids,
                date_of_visit__gte=first_day_this_month,
                date_of_visit__lte=today,
            ),
        ),
        user_last_month=Count(
            "id",
            filter=Q(
                assigned_to_id__in=membership_ids,
                date_of_visit__gte=first_day_last_month,
                date_of_visit__lte=last_month_end,
            ),
        ),
        user_planted_total=Count(
            "id",
            filter=Q(
                assigned_to_id__in=membership_ids,
                **({} if not planted_status_id else {"status_id": planted_status_id}),
            ),
        ),
        user_planted_current=Count(
            "id",
            filter=Q(
                assigned_to_id__in=membership_ids,
                date_of_visit__gte=first_day_this_month,
                date_of_visit__lte=today,
                **({} if not planted_status_id else {"status_id": planted_status_id}),
            ),
        ),
        user_planted_last=Count(
            "id",
            filter=Q(
                assigned_to_id__in=membership_ids,
                date_of_visit__gte=first_day_last_month,
                date_of_visit__lte=last_month_end,
                **({} if not planted_status_id else {"status_id": planted_status_id}),
            ),
        ),
    )

    def growth_rate(current, previous):
        if not previous:
            return 100 if current else 0
        return round((current - previous) / previous * 100, 1)

    def abs_diff(current, previous):
        if not previous:
            return (100 if current else 0), True
        diff = (current - previous) / previous * 100
        return round(abs(diff), 1), diff >= 0

    increase_rate = growth_rate(
        metrics["current_month_count"], metrics["last_month_count"]
    )
    user_diff_pct, user_diff_pos = abs_diff(
        metrics["user_current_month"], metrics["user_last_month"]
    )
    planted_growth = (
        round(metrics["user_planted_total"] / metrics["user_total"] * 100, 1)
        if metrics["user_total"]
        else 0
    )
    planted_change = growth_rate(
        metrics["user_planted_current"], metrics["user_planted_last"]
    )

    # ── Unit members (same-unit colleagues) ───────────────────────────
    if _can(request, "unit.view_all"):
        user_units = ChurchUnit.raw_objects.filter(
            church=church, is_active=True
        ).distinct()
    else:
        user_units = ChurchUnit.raw_objects.filter(
            church=church,
            memberships__workforce_member__member__user=user,
            is_active=True,
        ).distinct()

    other_memberships = (
        UnitMembership.raw_objects.filter(
            church=church, unit__in=user_units, is_active=True
        )
        .exclude(workforce_member__member__user=user)
        .select_related("unit", "workforce_member__member__user")
        .distinct()
    )
    other_users = []
    seen_ids = set()
    for m in other_memberships:
        try:
            u = m.workforce_member.member.user
            if u.id not in seen_ids and not u.is_superuser:
                u.color = get_user_color(u.id)
                other_users.append(u)
                seen_ids.add(u.id)
        except AttributeError:
            pass

    unit_type_labels = {
        "group": "Groups",
        "unit": "Units",
    }
    grouped_member_pairs = []
    for unit_type in ("unit", "group"):
        memberships = [
            m for m in other_memberships if m.unit and m.unit.unit_type == unit_type
        ]
        if memberships:
            grouped_member_pairs.append(
                (
                    {
                        "name": unit_type_labels.get(unit_type, unit_type.title()),
                        "color_class": (
                            "bg-indigo-lt" if unit_type == "unit" else "bg-purple-lt"
                        ),
                        "unit_type": unit_type,
                    },
                    memberships,
                )
            )

    # 🔥 Deduplicate users and attach multiple units
    unit_member_pairs = []

    for group_meta, group_memberships in grouped_member_pairs:
        user_map = defaultdict(list)

        for m in group_memberships:
            try:
                user_obj = m.workforce_member.member.user
                if not user_obj or user_obj.is_superuser:
                    continue

                user_obj.color = get_user_color(user_obj.id)

                # avoid duplicate unit entries
                if m.unit not in user_map[user_obj]:
                    user_map[user_obj].append(m.unit)

            except AttributeError:
                continue

        cleaned_members = [
            {
                "user": user,
                "units": units,  # 🔥 multiple units per user
            }
            for user, units in user_map.items()
        ]

        if cleaned_members:
            unit_member_pairs.append((group_meta, cleaned_members))

    # ── Attendance ────────────────────────────────────────────────────
    clock_records = get_visible_clock_records(user, since_date=last_30_days)
    total_clock_in = clock_records.filter(clock_in__isnull=False).count()
    total_clock_out = clock_records.filter(clock_out__isnull=False).count()
    today_record = clock_records.filter(date=today).first()

    # ── Events ────────────────────────────────────────────────────────
    calendar_items = get_calendar_items(user)
    all_events = get_available_events_for_user(user)
    upcoming_events = _serialize_available_events(all_events, today)
    start_of_week = today - timedelta(days=(today.isoweekday() % 7))
    end_of_week = start_of_week + timedelta(days=6)
    weekly_events = [
        e for e in upcoming_events if start_of_week <= e["date"] <= end_of_week
    ]
    for e in weekly_events:
        e["datetime_iso"] = f"{e['date']}T{e.get('time', '00:00:00')}"

    context = {
        "show_filters": False,
        "available_years": available_years,
        "current_year": current_year,
        "summary_data": summary_data,
        # analytics
        **service_data,
        **status_data,
        "channel_progress": channel_prog,
        "channel_stats": [
            {
                "channel": row.get("label", "Unknown"),
                "count": row.get("count", 0),
                "percent": row.get("percent", 0),
            }
            for row in channel_prog
        ],
        "purpose_stats": purpose_stats,
        "total_guests": metrics["total_guests"],
        "increase_rate": increase_rate,
        "percent_change": increase_rate,
        # personal metrics
        "user_total_guest_entries": metrics["user_total"],
        "user_guest_entry_diff_percent": user_diff_pct,
        "user_guest_entry_diff_positive": user_diff_pos,
        "user_planted_total": metrics["user_planted_total"],
        "planted_growth_rate": planted_growth,
        "planted_growth_change": planted_change,
        # unit
        "other_users": other_users,
        "user_units": user_units,
        # Build unit_member_pairs: list of (unit, [enriched_member_dicts])
        "unit_member_pairs": unit_member_pairs,
        # Guest statuses with per-church counts for the status breakdown cards
        "guest_statuses": (
            [
                {
                    "name": s.name,
                    "color": s.color,
                    "slug": s.slug,
                    "count": (
                        queryset.filter(status=s).count() if "queryset" in dir() else 0
                    ),
                }
                for s in GuestStatus.raw_objects.filter(
                    church=church, is_active=True
                ).order_by("order")
            ]
            if "GuestStatus" in dir()
            else []
        ),
        "plan_name_display": _display_plan_name(
            getattr(getattr(church, "churchsubscription", None), "plan", None).name
            if getattr(church, "churchsubscription", None)
            and getattr(church.churchsubscription, "plan", None)
            else ""
        ),
        # attendance
        "clock_records": clock_records,
        "total_clock_in": total_clock_in,
        "total_clock_out": total_clock_out,
        "today_clock_in": getattr(today_record, "clock_in", None),
        "today_clock_out": getattr(today_record, "clock_out", None),
        # events
        "calendar_items": calendar_items,
        "available_events": upcoming_events,
        "next_events_for_week": weekly_events,
        "page_title": "Member Dashboard",
        "assignable_memberships": [],
        "can_create_guest": bool(
            ChurchUnit.raw_objects.filter(
                church=church,
                is_active=True,
                guest_management=True,
                memberships__workforce_member__member__user=user,
                memberships__is_active=True,
            ).exists()
            and _can(request, "guests.create")
        ),
        **_widget_context(request, church, member=getattr(request, "member", None)),
    }
    inject_lms_context(context, request)
    return render(request, "dashboard/dashboard.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# Trainee dashboard — for WorkforceTraineeProfile members
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def trainee_dashboard(request):
    """
    Personal dashboard for WorkforceTraineeProfile members.

    Mirrors the regular member dashboard but scoped to:
    - Training events only (for attendance marking)
    - Their preferred unit or probation unit (no full workforce tools)
    - LMS enrollment progress (using AttendanceRecord for session gates)
    - Induction step progress
    - Upcoming training sessions linked via LMSSession → Event
    """
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not church or not member:
        return HttpResponseForbidden()

    from workforce.models import WorkforceTraineeProfile, LeaderTask, AttendanceRecord
    from guests.models import MembershipApplication, ApplicationStepProgress
    from lms.models import LMSSubmission, LMSSession, LMSEnrollment
    from units.models import UnitMembership
    from services.models import Event

    trainee = (
        WorkforceTraineeProfile.raw_objects.filter(
            church=church,
            member=member,
            reason="induction",
            is_active=True,
        )
        .select_related(
            "lms_enrollment__course",
            "preferred_unit",
            "probation_unit",
            "original_unit",
        )
        .first()
    )

    if not trainee:
        return redirect("lms:course_list")

    today = timezone.localdate()

    # ── Application and step progress ──────────────────────────────────────
    application = (
        MembershipApplication.raw_objects.filter(
            church=church,
            guest__converted_to=member,
            is_active=True,
        )
        .select_related("track")
        .order_by("-applied_at")
        .first()
    )
    step_progress = []
    if application:
        step_progress = list(
            ApplicationStepProgress.raw_objects.filter(
                church=church,
                application=application,
                is_active=True,
            )
            .select_related("step__requirement")
            .order_by("step__order")
        )

    # ── Trainee's UnitMembership (for attendance) ──────────────────────────
    trainee_membership = (
        UnitMembership.raw_objects.filter(
            church=church,
            trainee_profile=trainee,
            is_active=True,
        )
        .select_related("unit")
        .first()
    )

    # ── LMS enrollment progress ────────────────────────────────────────────
    enrollment = trainee.lms_enrollment
    modules = []

    if enrollment:
        raw_modules = enrollment.course.modules.filter(is_active=True).order_by("order")
        submissions = {
            s.module_id: s
            for s in LMSSubmission.raw_objects.filter(
                church=church, enrollment=enrollment
            ).order_by("-retake_count")
        }

        # Per-module attendance gate: mirrors lms/views.py course_detail logic.
        # For each module, find sessions that cover it (specific or blanket),
        # then check if the trainee has an AttendanceRecord for any of those events.
        # Only count sessions whose linked event still exists and is active.
        course_sessions = LMSSession.raw_objects.filter(
            church=church,
            course=enrollment.course,
            is_active=True,
            event__isnull=False,
            event__is_active=True,
        ).prefetch_related("modules_covered")

        # Pre-fetch all trainee attendance for this course's events (one query).
        # AttendanceRecord.user is ChurchMember — use trainee.member, not trainee_membership.
        attended_event_ids = set()
        if course_sessions.exists():
            attended_event_ids = set(
                AttendanceRecord.raw_objects.filter(
                    church=church,
                    user=trainee.member,
                    event_id__in=course_sessions.values_list("event_id", flat=True),
                    status__in=("present", "late", "excused"),
                ).values_list("event_id", flat=True)
            )

        # Blanket sessions (no specific modules): attending any of these
        # unlocks all modules. No sessions means nothing is unlocked for members.
        blanket_attended = False
        for s in course_sessions:
            if not s.modules_covered.exists() and s.event_id in attended_event_ids:
                blanket_attended = True
                break

        def _module_unlocked(mod):
            """True if the trainee has attended a session covering this module."""
            if not course_sessions.exists():
                return False
            if blanket_attended:
                return True
            # Check sessions specifically tagged to this module
            for s in course_sessions:
                if s.modules_covered.exists():
                    if (
                        mod in s.modules_covered.all()
                        and s.event_id in attended_event_ids
                    ):
                        return True
            return False

        def _get_covering_sessions(mod):
            """Return list of sessions that cover this module."""
            covering = []
            for s in course_sessions:
                # Blanket session (no specific modules) covers all
                if not s.modules_covered.exists():
                    covering.append(s)
                elif mod in s.modules_covered.all():
                    covering.append(s)
            return covering

        for mod in raw_modules:
            sub = submissions.get(mod.id)
            is_locked = (
                enrollment.course.strict_sequence
                and enrollment.current_module
                and mod.order > enrollment.current_module.order
            )
            attendance_satisfied = _module_unlocked(mod)
            covering_sessions = _get_covering_sessions(mod)
            modules.append(
                {
                    "module": mod,
                    "submission": sub,
                    "is_locked": is_locked,
                    "can_submit": not is_locked and attendance_satisfied,
                    "is_attendance_gate": True,
                    "attendance_satisfied": attendance_satisfied,
                    "covering_sessions": covering_sessions,
                    "lock_reason": (
                        "No training session has been scheduled for this module yet."
                        if not covering_sessions
                        else (
                            "Attend the session covering this module to unlock it."
                            if not attendance_satisfied
                            else (
                                "Finish the current module to unlock this one."
                                if is_locked
                                else ""
                            )
                        )
                    ),
                }
            )

    # ── Upcoming training sessions ─────────────────────────────────────────
    upcoming_sessions = []
    if enrollment:
        upcoming_sessions = list(
            LMSSession.raw_objects.filter(
                church=church,
                course=enrollment.course,
                is_active=True,
                event__date__gte=today,
            )
            .select_related("event")
            .order_by("event__date", "event__time")[:5]
        )
        for session in upcoming_sessions:
            session.scheduled_at = (
                timezone.make_aware(
                    datetime.combine(
                        session.event.date,
                        session.event.time or time.min,
                    )
                )
                if session.event and session.event.date
                else None
            )
            session.venue = (
                session.event.unit.name
                if session.event and session.event.unit
                else church.name
            )
            session.delivery_mode = (
                (session.event.attendance_mode or "").lower() if session.event else ""
            )

    # ── Upcoming events — trainees only see Training events for their course ──
    # Trainees are privy ONLY to Training events explicitly linked to the
    # guest-pipeline LMS course they are enrolled in (set in Settings →
    # Trainings). General workforce events or other unit-based trainings
    # are NOT shown to them.
    available_events = []
    pipeline_course_id = enrollment.course_id if enrollment else None

    event_qs_base = Event.raw_objects.filter(
        church=church,
        is_active=True,
        event_type="Training",
    ).filter(models.Q(date__gte=today) | models.Q(is_recurring_weekly=True))

    if pipeline_course_id:
        # Only Training events explicitly linked to the trainee's enrolled course
        event_qs = event_qs_base.filter(lms_course_id=pipeline_course_id).order_by(
            "date", "time"
        )[:10]
    else:
        # No enrollment — show nothing; trainee hasn't been assigned a course yet
        event_qs = Event.raw_objects.none()

    # Attendance records for this trainee membership
    attendance_map = {}
    if member:
        records = AttendanceRecord.raw_objects.filter(
            church=church,
            user=member,
            date__gte=today - timedelta(days=30),
        ).select_related("event")
        attendance_map = {r.event_id: r for r in records}

    # Check if trainee has attended any course sessions (for course_attended flag)
    course_attended = False
    if enrollment and member and course_sessions.exists():
        course_attended = bool(
            AttendanceRecord.raw_objects.filter(
                church=church,
                user=member,
                event_id__in=course_sessions.values_list("event_id", flat=True),
                status__in=("present", "late", "excused"),
            ).exists()
        )

    for event in event_qs:
        available_events.append(
            {
                "event": event,
                "attendance": attendance_map.get(event.id),
                "is_training": event.event_type == "Training",
                "is_linked": (
                    enrollment and event.lms_course_id == enrollment.course_id
                ),
            }
        )

    # ── Promotion state ────────────────────────────────────────────────────
    is_eligible = trainee.is_eligible_for_promotion()
    pending_approval = LeaderTask.raw_objects.filter(
        church=church,
        trainee=trainee,
        task_type="promotion_review",
        completed=False,
        is_active=True,
    ).exists()

    # ── Regular dashboard context (for block.super in trainee template) ────
    user = request.user
    last_30_days = today - timedelta(days=30)

    # Build trainee-aware memberships so unit/group strips still render.
    trainee_memberships = list(
        UnitMembership.raw_objects.filter(
            church=church,
            is_active=True,
        )
        .filter(
            models.Q(workforce_member__member__user=user)
            | models.Q(trainee_profile=trainee)
        )
        .select_related(
            "unit", "workforce_member__member__user", "trainee_profile__member__user"
        )
    )
    visible_unit_ids = [m.unit_id for m in trainee_memberships if m.unit_id]
    user_units = ChurchUnit.raw_objects.filter(
        church=church, is_active=True, id__in=visible_unit_ids
    ).distinct()

    other_memberships = (
        UnitMembership.raw_objects.filter(
            church=church, unit__in=user_units, is_active=True
        )
        .exclude(workforce_member__member__user=user)
        .exclude(trainee_profile=trainee)
        .select_related("unit", "workforce_member__member__user")
        .distinct()
    )
    other_users = []
    seen_ids = set()
    for m in other_memberships:
        try:
            u = m.workforce_member.member.user
            if u and u.id not in seen_ids and not u.is_superuser:
                u.color = get_user_color(u.id)
                other_users.append(u)
                seen_ids.add(u.id)
        except AttributeError:
            continue

    unit_type_labels = {"group": "Groups", "unit": "Units"}
    grouped_member_pairs = []
    for unit_type in ("unit", "group"):
        memberships = [
            m for m in other_memberships if m.unit and m.unit.unit_type == unit_type
        ]
        if memberships:
            grouped_member_pairs.append(
                (
                    {
                        "name": unit_type_labels.get(unit_type, unit_type.title()),
                        "color_class": (
                            "bg-indigo-lt" if unit_type == "unit" else "bg-purple-lt"
                        ),
                        "unit_type": unit_type,
                    },
                    memberships,
                )
            )

    regular_events = get_available_events_for_user(user)
    upcoming_events = _serialize_available_events(regular_events, today)
    start_of_week = today - timedelta(days=(today.isoweekday() % 7))
    end_of_week = start_of_week + timedelta(days=6)
    weekly_events = [
        e for e in upcoming_events if start_of_week <= e["date"] <= end_of_week
    ]
    for e in weekly_events:
        e["datetime_iso"] = f"{e['date']}T{e.get('time', '00:00:00')}"

    clock_records = get_visible_clock_records(user, since_date=last_30_days)
    total_clock_in = clock_records.filter(clock_in__isnull=False).count()
    total_clock_out = clock_records.filter(clock_out__isnull=False).count()
    today_record = clock_records.filter(date=today).first()

    # ── All LMS enrollments for this member (multi-course) ────────────────
    # trainee.lms_enrollment tracks only the induction course.
    # Members can be enrolled in additional unit_training / workforce courses.
    all_enrollments = []
    for enr in (
        LMSEnrollment.raw_objects.filter(church=church, member=member, is_active=True)
        .select_related("course", "current_module")
        .order_by("course__title")
    ):
        enr.module_count = enr.course.modules.filter(is_active=True).count()
        enr.passed_count = LMSSubmission.raw_objects.filter(
            church=church, enrollment=enr, status="passed", is_active=True
        ).count()
        all_enrollments.append(enr)

    context = {
        "trainee": trainee,
        "trainee_membership": trainee_membership,
        "application": application,
        "step_progress": step_progress,
        "enrollment": enrollment,
        "all_enrollments": all_enrollments,
        "modules": modules,
        "course_attended": course_attended if enrollment else True,
        "upcoming_sessions": upcoming_sessions,
        "training_events": available_events,
        "attendance_map": attendance_map,
        "is_eligible": is_eligible,
        "pending_approval": pending_approval,
        "today": today,
        "page_title": "Trainee Dashboard",
        # regular dashboard segment (block.super)
        "other_users": other_users,
        "user_units": user_units,
        "unit_member_pairs": [
            (
                group_meta,
                [
                    {"user": m.workforce_member.member.user, "unit": m.unit}
                    for m in memberships
                    if m.workforce_member and m.workforce_member.member
                ],
            )
            for group_meta, memberships in grouped_member_pairs
        ],
        "calendar_items": get_calendar_items(user),
        "available_events": upcoming_events,
        "next_events_for_week": weekly_events,
        "clock_records": clock_records,
        "total_clock_in": total_clock_in,
        "total_clock_out": total_clock_out,
        "today_clock_in": getattr(today_record, "clock_in", None),
        "today_clock_out": getattr(today_record, "clock_out", None),
        "can_create_guest": bool(
            ChurchUnit.raw_objects.filter(
                church=church,
                is_active=True,
                guest_management=True,
                id__in=visible_unit_ids,
            ).exists()
            and _can(request, "guests.create")
        ),
        **_widget_context(request, church, member=member),
    }
    inject_lms_context(context, request)
    return render(request, "dashboard/trainee_dashboard.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# Evangelism Research (AJAX — guest management unit heads/admins only)
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def evangelism_research_view(request):
    """
    AJAX endpoint: returns AI-generated missionary research brief.
    Only accessible to users who can view all guests or are admins.
    """
    from django.http import JsonResponse

    church = getattr(request, "church", None)
    if not church:
        return JsonResponse({"ok": False, "error": "No church context"}, status=403)

    if not (_is_admin(request) or _can_view_all_guests(request)):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    # Build sanitised guest pattern analytics
    from guests.models import GuestEntry, GuestStatus

    today = timezone.localdate()
    cutoff = today - timedelta(days=90)
    qs = GuestEntry.raw_objects.filter(
        church=church,
        is_active=True,
        is_deleted=False,
        date_of_visit__gte=cutoff,
    )

    total = qs.count()
    status_dist = dict(
        qs.values_list("status__slug")
        .annotate(n=Count("id"))
        .values_list("status__slug", "n")
    )
    channel_dist = dict(
        qs.values_list("channel_of_visit__name")
        .annotate(n=Count("id"))
        .values_list("channel_of_visit__name", "n")
    )
    purpose_dist = dict(
        qs.values_list("purpose_of_visit__name")
        .annotate(n=Count("id"))
        .values_list("purpose_of_visit__name", "n")
    )
    monthly = list(
        qs.annotate(m=models.functions.TruncMonth("date_of_visit"))
        .values("m")
        .annotate(n=Count("id"))
        .order_by("m")
        .values_list("m", "n")
    )

    patterns = {
        "Total guests (90 days)": total,
        "Status breakdown": str(status_dist),
        "Channels": str(channel_dist),
        "Visit purposes": str(purpose_dist),
        "Monthly trend": str(
            [(str(m[0].strftime("%b %Y")), m[1]) for m in monthly if m[0]]
        ),
        "Location data": str(_guest_location_data(church, qs)[:5]),
    }

    from core.ai_skills import evangelism_research

    report = evangelism_research(church, patterns)

    if not report:
        return JsonResponse(
            {"ok": False, "error": "AI unavailable. Try again shortly."}
        )

    return JsonResponse({"ok": True, "report": report})


@login_required
def workforce_announce_create(request):
    """Create a workforce-wide announcement (admin/sub-admin only)."""
    from django.http import JsonResponse
    from workforce.models import WorkforceAnnouncement

    church = getattr(request, "church", None)
    if not church or not (_is_admin(request) or _can(request, "messaging.send_bulk")):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    title = request.POST.get("title", "").strip()
    body = request.POST.get("body", "").strip()
    priority = request.POST.get("priority", "normal")
    is_banner = request.POST.get("is_banner") == "on"
    is_pinned = request.POST.get("is_pinned") == "on"
    expires_str = request.POST.get("expires_at", "").strip()
    unit_ids = request.POST.getlist("unit_ids")
    member_ids = request.POST.getlist("member_ids")

    if not title or not body:
        return JsonResponse({"ok": False, "error": "Title and body required."})

    member = getattr(request, "member", None)
    from django.utils import timezone as tz
    import datetime

    expires_at = None
    if expires_str:
        try:
            expires_at = tz.make_aware(datetime.datetime.fromisoformat(expires_str))
        except ValueError:
            pass

    ann = WorkforceAnnouncement.raw_objects.create(
        church=church,
        title=title,
        body=body,
        priority=priority,
        is_banner=is_banner,
        is_pinned=is_pinned,
        expires_at=expires_at,
        created_by=member,
    )
    if "all" not in unit_ids and "all" not in member_ids:
        from units.models import ChurchUnit, UnitMembership

        explicit_unit_ids = set(
            ChurchUnit.raw_objects.filter(church=church, id__in=unit_ids).values_list(
                "id", flat=True
            )
        )
        member_unit_ids = set(
            UnitMembership.raw_objects.filter(
                church=church, id__in=member_ids
            ).values_list("unit_id", flat=True)
        )
        target_ids = [uid for uid in (explicit_unit_ids | member_unit_ids) if uid]
        if target_ids:
            ann.target_units.set(
                ChurchUnit.raw_objects.filter(church=church, id__in=target_ids)
            )
    return JsonResponse({"ok": True, "id": ann.pk, "title": ann.title})


@login_required
def workforce_task_create(request):
    """Create a workforce-wide task (admin/sub-admin only)."""
    from django.http import JsonResponse
    from workforce.models import WorkforceTask

    church = getattr(request, "church", None)
    if not church or not (_is_admin(request) or _can(request, "messaging.send_bulk")):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    title = request.POST.get("title", "").strip()
    desc = request.POST.get("description", "").strip()
    priority = request.POST.get("priority", "normal")
    due_str = request.POST.get("due_date", "").strip()
    unit_ids = request.POST.getlist("unit_ids")
    member_ids = request.POST.getlist("member_ids")

    if not title:
        return JsonResponse({"ok": False, "error": "Title required."})

    member = getattr(request, "member", None)
    import datetime

    due_date = None
    if due_str:
        try:
            due_date = datetime.date.fromisoformat(due_str)
        except ValueError:
            pass

    task = WorkforceTask.raw_objects.create(
        church=church,
        title=title,
        description=desc,
        priority=priority,
        due_date=due_date,
        created_by=member,
    )
    if unit_ids and "all" not in unit_ids:
        from units.models import ChurchUnit

        task.assigned_units.set(
            ChurchUnit.raw_objects.filter(church=church, id__in=unit_ids)
        )
    if member_ids and "all" not in member_ids:
        from units.models import UnitMembership

        task.assigned_members.set(
            UnitMembership.raw_objects.filter(church=church, id__in=member_ids)
        )
    return JsonResponse({"ok": True, "id": task.pk, "title": task.title})
