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
import json
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Count, Q, Prefetch
from django.db.models.functions import ExtractMonth
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone

from accounts.models import CustomUser, ChurchMember
from tenants.models import Church
from core.utils.colors import resolve_color
from guests.models import GuestEntry, GuestStatus
from units.models import ChurchUnit, UnitMembership
from workforce.models import AttendanceRecord, ClockRecord
from workforce.utils import (
    get_calendar_items,
    get_visible_attendance_records,
    get_visible_clock_records,
    get_available_events_for_user,
    expand_team_events,
    get_available_units_for_user,
)


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


def _is_admin(request):
    """True if user can see the admin dashboard."""
    return request.user.is_superuser or _can(request, "dashboard.admin")


def _can_view_all_guests(request):
    return request.user.is_superuser or _can(request, "guests.view_all")


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
    by_slug   = {row["status__slug"]: row["count"] for row in status_data}
    by_name   = {row["status__name"] or "Unknown": row["count"] for row in status_data}
    labels    = list(by_name.keys())
    counts    = list(by_name.values())

    return {
        "status_labels":          labels,
        "status_counts":          counts,
        "planted_count":          by_slug.get("planted", 0),
        "not_planted_count":      by_slug.get("not-planted", 0),
        "committed_count":        by_slug.get("committed", 0),
        "in_contact_count":       by_slug.get("in-contact", 0),
        "new_guest_count":        by_slug.get("new-guest", 0),
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
        row["purpose_of_visit__name"] or "Not specified": {
            "count":      row["count"],
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
            "label":   row["channel_of_visit__name"] or "Unknown",
            "count":   row["count"],
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
    total  = sum(counts) or 1

    most_attended = labels[0] if labels else "No Data"
    most_count    = counts[0] if counts else 0

    return {
        "service_labels":        labels,
        "service_counts":        counts,
        "most_attended_service": most_attended,
        "most_attended_count":   most_count,
        "attendance_rate":       round(most_count / total * 100, 1),
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

    max_month = next((calendar.month_name[m] for m, c in counts.items() if c == max_count), "N/A")
    min_month = next((calendar.month_name[m] for m, c in counts.items() if c == min_count), "N/A")

    return {
        "max_month":   max_month,
        "max_count":   max_count,
        "max_percent": pct(max_count),
        "min_month":   min_month,
        "min_count":   min_count,
        "min_percent": pct(min_count),
        "avg_count":   avg_count,
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
        return HttpResponseForbidden("You do not have permission to view the admin dashboard.")

    user         = request.user
    today        = timezone.localdate()
    current_year = today.year
    last_30_days = today - timedelta(days=30)

    # ── Base queryset — all guests for this church ────────────────────
    queryset = GuestEntry.raw_objects.filter(
        church=church,
        is_active=True,
        is_deleted=False,
    )

    # ── Year selection ────────────────────────────────────────────────
    available_years = [
        d.year for d in queryset.dates("date_of_visit", "year")
    ]
    try:
        year = int(request.GET.get("year", current_year))
    except (TypeError, ValueError):
        year = current_year

    # ── Analytics ────────────────────────────────────────────────────
    summary_data   = _monthly_summary(queryset, year)
    service_data   = _service_stats(queryset)
    status_data    = _guest_status_counts(queryset, church)
    purpose_stats  = _purpose_stats(queryset)
    channel_prog   = _channel_progress(queryset)

    # ── Monthly growth ────────────────────────────────────────────────
    first_day_this_month  = today.replace(day=1)
    last_month_end        = first_day_this_month - timedelta(days=1)
    first_day_last_month  = last_month_end.replace(day=1)

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
    memberships = (
        UnitMembership.raw_objects
        .filter(church=church, is_active=True)
        .select_related("unit", "workforce_member__member__user")
        .prefetch_related("roles__role")
    )
    other_users = []
    for m in memberships:
        try:
            u = m.workforce_member.member.user
            if u.id != user.id and not u.is_superuser:
                u.color = get_user_color(u.id)
                other_users.append(u)
        except AttributeError:
            pass

    unit_member_pairs = [
        (
            unit,
            [
                m for m in memberships
                if m.unit_id == unit.id
            ]
        )
        for unit in units
    ]

    # ── Attendance ────────────────────────────────────────────────────
    records      = get_visible_attendance_records(user, since_date=last_30_days)
    clock_records = get_visible_clock_records(user, since_date=last_30_days)
    clock_map     = {(c.user_id, c.event_id, c.date): c for c in clock_records}

    for r in records:
        clock = clock_map.get((r.user_id, r.event_id, r.date))
        r.clock_in_time  = clock.clock_in.strftime("%H:%M")  if clock and clock.clock_in  else None
        r.clock_out_time = clock.clock_out.strftime("%H:%M") if clock and clock.clock_out else None

    total_clock_in  = clock_records.filter(clock_in__isnull=False).count()
    total_clock_out = clock_records.filter(clock_out__isnull=False).count()
    today_record    = clock_records.filter(date=today).first()

    # ── Events ────────────────────────────────────────────────────────
    calendar_items   = get_calendar_items(user)
    all_events       = get_available_events_for_user(user)
    upcoming_events  = sorted(
        [e for e in all_events if e["date"] >= today],
        key=lambda e: (e["date"], e.get("time", "")),
    )
    start_of_week = today - timedelta(days=(today.isoweekday() % 7))
    end_of_week   = start_of_week + timedelta(days=6)
    weekly_events = [
        e for e in upcoming_events
        if start_of_week <= e["date"] <= end_of_week
    ]
    for e in weekly_events:
        e["datetime_iso"] = f"{e['date']}T{e.get('time', '00:00:00')}"

    # ── Church members list ───────────────────────────────────────────
    all_members = CustomUser.objects.filter(
        church_memberships__church=church,
        church_memberships__is_active=True,
        is_active=True,
        is_superuser=False,
    ).distinct().order_by("full_name")

    # ── Permission context for templates ─────────────────────────────
    context_user_permissions = json.dumps({
        "is_admin":           _is_admin(request),
        "can_view_all_guests": _can_view_all_guests(request),
        "can_manage_users":   _can(request, "accounts.manage_users"),
        "can_manage_events":  _can(request, "events.manage_all"),
    }, cls=DjangoJSONEncoder)

    context = {
        # year selector
        "available_years":    available_years,
        "current_year":       current_year,

        # analytics
        "summary_data":       summary_data,
        **service_data,
        **status_data,
        "channel_progress":   channel_prog,
        "purpose_stats":      purpose_stats,
        "total_guests":       queryset.count(),
        "increase_rate":      increase_rate,
        "percent_change":     increase_rate,

        # team
        "users":              all_members,
        "other_users":        other_users,
        "user_units":         units,
        "unit_member_pairs":  unit_member_pairs,

        # attendance
        "records":            records,
        "clock_records":      clock_records,
        "total_clock_in":     total_clock_in,
        "total_clock_out":    total_clock_out,
        "today_clock_in":     getattr(today_record, "clock_in",  None),
        "today_clock_out":    getattr(today_record, "clock_out", None),

        # events
        "calendar_items":         calendar_items,
        "available_events":       upcoming_events,
        "next_events_for_week":   weekly_events,
        "available_units":        get_available_units_for_user(user),

        # permissions
        "context_user_permissions": context_user_permissions,
        "show_filters":           False,
        "page_title":             "Admin Dashboard",
    }

    return render(request, "dashboard/admin_dashboard.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# Member dashboard
# ─────────────────────────────────────────────────────────────────────────────# ─────────────────────────────────────────────────────────────────────────────
# Superuser church management
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def superuser_dashboard(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Superuser access required.")

    scope = request.GET.get("scope", "scoped")
    status = request.GET.get("status", "active")
    query = (request.GET.get("q") or "").strip()

    if scope == "all":
        qs = Church.raw_objects.all()
    else:
        qs = Church.objects.all()

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

    churches = qs.order_by("name")

    context = {
        "page_title": "Church Management",
        "scope": scope,
        "status": status,
        "query": query,
        "churches": churches,
        "total_count": churches.count(),
    }

    return render(request, "dashboard/superuser_dashboard.html", context)



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

    user         = request.user
    today        = timezone.localdate()
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
    available_years = [
        d.year for d in base_qs.dates("date_of_visit", "year")
    ]

    # ── Summary JSON (reuse guests.views helper via direct call) ──────
    try:
        from guests.views import guest_entry_summary
        import copy
        fake_request      = copy.copy(request)
        fake_request.GET  = request.GET.copy()
        fake_request.GET["year"] = str(current_year)
        summary_json = guest_entry_summary(fake_request)
        summary_data = json.loads(summary_json.content.decode())
    except Exception:
        summary_data = {}

    # ── Analytics ────────────────────────────────────────────────────
    service_data  = _service_stats(queryset)
    status_data   = _guest_status_counts(queryset, church)
    purpose_stats = _purpose_stats(queryset)
    channel_prog  = _channel_progress(queryset)

    # ── Monthly growth metrics ────────────────────────────────────────
    first_day_this_month = today.replace(day=1)
    last_month_end       = first_day_this_month - timedelta(days=1)
    first_day_last_month = last_month_end.replace(day=1)

    planted_status_id = _resolve_status_id(church, "planted")

    metrics = queryset.aggregate(
        total_guests=Count("id"),
        current_month_count=Count("id", filter=Q(
            date_of_visit__gte=first_day_this_month,
            date_of_visit__lte=today,
        )),
        last_month_count=Count("id", filter=Q(
            date_of_visit__gte=first_day_last_month,
            date_of_visit__lte=last_month_end,
        )),
        user_total=Count("id", filter=Q(assigned_to_id__in=membership_ids)),
        user_current_month=Count("id", filter=Q(
            assigned_to_id__in=membership_ids,
            date_of_visit__gte=first_day_this_month,
            date_of_visit__lte=today,
        )),
        user_last_month=Count("id", filter=Q(
            assigned_to_id__in=membership_ids,
            date_of_visit__gte=first_day_last_month,
            date_of_visit__lte=last_month_end,
        )),
        user_planted_total=Count("id", filter=Q(
            assigned_to_id__in=membership_ids,
            **({} if not planted_status_id else {"status_id": planted_status_id}),
        )),
        user_planted_current=Count("id", filter=Q(
            assigned_to_id__in=membership_ids,
            date_of_visit__gte=first_day_this_month,
            date_of_visit__lte=today,
            **({} if not planted_status_id else {"status_id": planted_status_id}),
        )),
        user_planted_last=Count("id", filter=Q(
            assigned_to_id__in=membership_ids,
            date_of_visit__gte=first_day_last_month,
            date_of_visit__lte=last_month_end,
            **({} if not planted_status_id else {"status_id": planted_status_id}),
        )),
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

    increase_rate   = growth_rate(metrics["current_month_count"], metrics["last_month_count"])
    user_diff_pct, user_diff_pos = abs_diff(metrics["user_current_month"], metrics["user_last_month"])
    planted_growth  = round(metrics["user_planted_total"] / metrics["user_total"] * 100, 1) if metrics["user_total"] else 0
    planted_change  = growth_rate(metrics["user_planted_current"], metrics["user_planted_last"])

    # ── Unit members (same-unit colleagues) ───────────────────────────
    user_units = ChurchUnit.raw_objects.filter(
        church=church,
        memberships__workforce_member__member__user=user,
        is_active=True,
    ).distinct()

    other_memberships = (
        UnitMembership.raw_objects
        .filter(church=church, unit__in=user_units, is_active=True)
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

    unit_member_pairs = [
        (unit, [m for m in other_memberships if m.unit_id == unit.id])
        for unit in user_units
    ]

    # ── Attendance ────────────────────────────────────────────────────
    clock_records   = get_visible_clock_records(user, since_date=last_30_days)
    total_clock_in  = clock_records.filter(clock_in__isnull=False).count()
    total_clock_out = clock_records.filter(clock_out__isnull=False).count()
    today_record    = clock_records.filter(date=today).first()

    # ── Events ────────────────────────────────────────────────────────
    calendar_items  = get_calendar_items(user)
    all_events      = get_available_events_for_user(user)
    upcoming_events = sorted(
        [e for e in all_events if e["date"] >= today],
        key=lambda e: (e["date"], e.get("time", "")),
    )
    start_of_week = today - timedelta(days=(today.isoweekday() % 7))
    end_of_week   = start_of_week + timedelta(days=6)
    weekly_events = [
        e for e in upcoming_events
        if start_of_week <= e["date"] <= end_of_week
    ]
    for e in weekly_events:
        e["datetime_iso"] = f"{e['date']}T{e.get('time', '00:00:00')}"

    context = {
        "show_filters":    False,
        "available_years": available_years,
        "current_year":    current_year,
        "summary_data":    summary_data,

        # analytics
        **service_data,
        **status_data,
        "channel_progress": channel_prog,
        "purpose_stats":    purpose_stats,
        "total_guests":     metrics["total_guests"],
        "increase_rate":    increase_rate,
        "percent_change":   increase_rate,

        # personal metrics
        "user_total_guest_entries":      metrics["user_total"],
        "user_guest_entry_diff_percent": user_diff_pct,
        "user_guest_entry_diff_positive": user_diff_pos,
        "user_planted_total":            metrics["user_planted_total"],
        "planted_growth_rate":           planted_growth,
        "planted_growth_change":         planted_change,

        # team
        "other_users":       other_users,
        "user_teams":        user_units,
        "user_units":        user_units,
        # Build team_member_pairs: list of (unit, [enriched_member_dicts])
        "team_member_pairs": [
            (unit, [
                {"user": m.workforce_member.member.user}
                for m in other_memberships
                if m.unit_id == unit.id and m.workforce_member and m.workforce_member.member
            ])
            for unit in user_units
        ],
        # Guest statuses with per-church counts for the status breakdown cards
        "guest_statuses": [
            {
                "name":       s.name,
                "color":      s.color,
                "slug":       s.slug,
                "count": queryset.filter(status=s).count() if "queryset" in dir() else 0,
            }
            for s in GuestStatus.raw_objects.filter(church=church, is_active=True).order_by("order")
        ] if "GuestStatus" in dir() else [],
        "team_member_pairs": unit_member_pairs,

        # attendance
        "clock_records":   clock_records,
        "total_clock_in":  total_clock_in,
        "total_clock_out": total_clock_out,
        "today_clock_in":  getattr(today_record, "clock_in",  None),
        "today_clock_out": getattr(today_record, "clock_out", None),

        # events
        "calendar_items":       calendar_items,
        "available_events":     upcoming_events,
        "next_events_for_week": weekly_events,

        "page_title": "Dashboard",
    }

    return render(request, "dashboard/dashboard.html", context)


