from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


@receiver(user_logged_in)
def auto_generate_attendance(sender, request, user, **kwargs):
    """
    When a user logs in, create absent attendance records for today's
    events if they don't already exist.

    Scoped to the current church — never touches other tenants' events.
    Uses UnitMembership as the attendance identity, consistent with
    AttendanceRecord.user FK.
    """
    from services.models import Event
    from workforce.models import AttendanceRecord
    from units.models import UnitMembership

    church = getattr(request, "church", None)
    if not church:
        return

    today = timezone.localdate()

    # Find this user's UnitMembership(s) in this church
    memberships = UnitMembership.raw_objects.filter(
        church=church,
        workforce_member__member__user=user,
        is_active=True,
    )

    if not memberships.exists():
        return

    # Today's events for this church
    events_today = Event.raw_objects.filter(
        church=church,
        is_active=True,
    )

    for event in events_today:
        # Skip weekly events not scheduled for today
        if (
            event.day_of_week
            and event.day_of_week.lower() != today.strftime("%A").lower()
        ):
            continue

        for membership in memberships:
            AttendanceRecord.raw_objects.get_or_create(
                church=church,
                user=membership,
                event=event,
                date=today,
                defaults={"status": "absent", "remarks": ""},
            )


@receiver(post_save, sender="services.Event")
def reschedule_on_event_save(sender, instance, **kwargs):
    """
    When an event is saved, refresh the scheduler so the new or
    updated event gets a broadcast job scheduled.
    """
    try:
        from workforce.scheduler_jobs import schedule_event_notifications
        schedule_event_notifications()
    except Exception as exc:
        print(f"❌ [Signal] reschedule_on_event_save failed: {exc}")