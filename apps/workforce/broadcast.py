"""
workforce/broadcast.py

WebSocket broadcast helpers for attendance and event notifications.

Multi-tenant fixes from single-tenant version:
    - broadcast_event: stateless broadcasted_events set replaced with
      per-church channel group names so events from different churches
      don't collide.
    - broadcast_attendance_summary: was querying AttendanceRecord,
      ClockRecord, and CustomUser across ALL tenants with no church
      filter. Now accepts a church argument and scopes all queries.
    - AttendanceRecord.user is UnitMembership — user display fields
      now accessed via the correct relationship chain.
"""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from datetime import timedelta
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────────────────
# Event broadcast
# ─────────────────────────────────────────────────────────────────────────────

def broadcast_event(event):
    """
    Broadcast an event start trigger to the attendance channel group.

    Uses a per-church channel group (attendance_{church_id}) to ensure
    events from different tenants never cross.

    The old global broadcasted_events set was a memory leak and caused
    cross-tenant collisions — removed entirely. APScheduler ensures
    each event fires only once per scheduled run.
    """
    channel_layer = get_channel_layer()

    church_id = event.church_id

    async_to_sync(channel_layer.group_send)(
        f"attendance_{church_id}",
        {
            "type": "send_event",
            "data": {
                "id":      event.id,
                "name":    event.name,
                "date":    str(event.date),
                "time":    str(event.time),
                "team":    event.team.name if event.team else None,
                "team_id": event.team.id   if event.team else None,
            },
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Attendance summary broadcast
# ─────────────────────────────────────────────────────────────────────────────

def broadcast_attendance_summary(church):
    """
    Broadcast attendance summary for a specific church.

    Sends:
        1. Admin table summary → attendance_{church_id} group
        2. Per-member dashboard summary → attendance_user_{user_id} group

    Must receive the church explicitly — never queries across all tenants.

    AttendanceRecord.user is a UnitMembership FK. User display fields
    are accessed via membership.workforce_member.member.user.
    """
    from workforce.models import AttendanceRecord, ClockRecord
    from units.models import UnitMembership

    channel_layer = get_channel_layer()
    today = timezone.localdate()
    last_30_days = today - timedelta(days=30)

    # ── 1. Admin table: recent attendance records for this church ─────
    records = (
        AttendanceRecord.raw_objects
        .filter(church=church, date__gte=last_30_days)
        .select_related(
            "event",
            "event__team",
            "user__workforce_member__member__user",
        )
        .order_by("-date")[:30]
    )

    clock_records = ClockRecord.raw_objects.filter(
        church=church, date__gte=last_30_days
    )
    clock_map = {
        (c.user_id, c.event_id, c.date): c
        for c in clock_records
    }

    serialized_records = []
    for r in records:
        clock = clock_map.get((r.user_id, r.event_id, r.date))
        clock_in  = clock.clock_in.strftime("%H:%M")  if clock and clock.clock_in  else None
        clock_out = clock.clock_out.strftime("%H:%M") if clock and clock.clock_out else None

        # Navigate UnitMembership → WorkforceMember → ChurchMember → User
        try:
            member_user = r.user.workforce_member.member.user
            full_name   = member_user.full_name or member_user.username
            title       = member_user.title or ""
        except AttributeError:
            full_name = "Unknown"
            title     = ""

        serialized_records.append({
            "date": r.date.strftime("%Y-%m-%d"),
            "event": {
                "name": r.event.name if r.event else "—",
                "id":   r.event.id   if r.event else None,
                "team": {
                    "name": r.event.team.name if r.event and r.event.team else None,
                    "id":   r.event.team.id   if r.event and r.event.team else None,
                },
            },
            "user": {
                "id":        r.user_id,
                "title":     title,
                "full_name": full_name,
            },
            "status":          r.status,
            "remarks":         r.remarks or "—",
            "clock_in_time":   clock_in  or "—",
            "clock_out_time":  clock_out or "—",
        })

    async_to_sync(channel_layer.group_send)(
        f"attendance_{church.id}",
        {
            "type": "send_summary",
            "data": {"records": serialized_records},
        },
    )

    # ── 2. Per-member dashboard summaries ────────────────────────────
    # Iterate only active UnitMemberships for this church — never all users
    memberships = (
        UnitMembership.raw_objects
        .filter(church=church, is_active=True)
        .select_related("workforce_member__member__user")
    )

    for membership in memberships:
        try:
            user = membership.workforce_member.member.user
        except AttributeError:
            continue

        if not user.is_active or user.is_superuser:
            continue

        user_records = AttendanceRecord.raw_objects.filter(
            church=church,
            user=membership,
            date__gte=last_30_days,
        )
        present = user_records.filter(status="present").count()
        excused = user_records.filter(status="excused").count()
        absent  = user_records.filter(status="absent").count()

        today_clock = ClockRecord.raw_objects.filter(
            church=church, user=membership, date=today
        ).first()
        clocked_in = today_clock.is_clocked_in if today_clock else False

        total_clock_in  = ClockRecord.raw_objects.filter(church=church, user=membership, clock_in__isnull=False).count()
        total_clock_out = ClockRecord.raw_objects.filter(church=church, user=membership, clock_out__isnull=False).count()

        async_to_sync(channel_layer.group_send)(
            f"attendance_user_{user.id}",
            {
                "type": "dashboard_summary",
                "data": {
                    "user_id": user.id,
                    "summary": {
                        "present": present,
                        "excused": excused,
                        "absent":  absent,
                    },
                    "today": {
                        "clocked_in": clocked_in,
                        "clock_in":  today_clock.clock_in.strftime("%H:%M")  if today_clock and today_clock.clock_in  else None,
                        "clock_out": today_clock.clock_out.strftime("%H:%M") if today_clock and today_clock.clock_out else None,
                    },
                    "totals": {
                        "clock_in":  total_clock_in,
                        "clock_out": total_clock_out,
                    },
                },
            },
        )
