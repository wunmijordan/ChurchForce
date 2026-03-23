"""
workforce/scheduler_jobs.py

Workforce-specific scheduled jobs:
    - Event broadcast notifications (fixed + weekly recurring)
    - Deferred push notifications

These functions are registered into the core scheduler by
core/scheduler.py:start() — they never start their own scheduler.

Import the shared scheduler instance with:
    from core.scheduler import scheduler
"""

from datetime import datetime, timedelta
from django.utils import timezone
from django.utils.timezone import make_aware


weekday_map = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}


# ─────────────────────────────────────────────────────────────────────────────
# Job functions
# ─────────────────────────────────────────────────────────────────────────────

def fire_event_broadcast(event_id, church_id):
    """
    Re-fetch event and broadcast to its church's members.
    Silently skips if the event was cancelled or deactivated since scheduling.
    """
    try:
        from services.models import Event
        from workforce.broadcast import broadcast_event

        event = Event.raw_objects.filter(
            id=event_id,
            church_id=church_id,
            is_active=True,
        ).first()

        if event:
            broadcast_event(event)
        else:
            print(f"⏩ [Scheduler] Event {event_id} no longer active — skipped.")

    except Exception as exc:
        print(f"❌ [Scheduler] fire_event_broadcast({event_id}, church={church_id}): {exc}")


def fire_push_notification(notification_id, church_id):
    """
    Re-fetch notification and broadcast it.
    Silently skips if already read/delivered since scheduling.
    """
    try:
        from notifications.models import Notification
        from notifications.broadcast import broadcast_notification

        notif = Notification.raw_objects.filter(
            id=notification_id,
            church_id=church_id,
            is_read=False,
        ).first()

        if notif:
            broadcast_notification(notif)
        else:
            print(f"⏩ [Scheduler] Notification {notification_id} already delivered — skipped.")

    except Exception as exc:
        print(f"❌ [Scheduler] fire_push_notification({notification_id}, church={church_id}): {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Scheduling functions (called on startup + daily cron)
# ─────────────────────────────────────────────────────────────────────────────

def get_next_occurrence(day_of_week: str, event_time):
    """Return next localized datetime for a weekly recurring event."""
    today = timezone.localdate()
    now = timezone.localtime()
    target_weekday = weekday_map[day_of_week.lower()]
    delta = (target_weekday - today.weekday() + 7) % 7

    if delta == 0:
        event_dt_today = make_aware(datetime.combine(today, event_time))
        if event_dt_today <= now:
            delta = 7

    next_date = today + timedelta(days=delta)
    return make_aware(datetime.combine(next_date, event_time))


def schedule_event_notifications():
    """
    Re-schedule all upcoming event broadcasts across all active churches.
    Called on startup and daily at 00:10 by the cron job registered in
    register_workforce_jobs().
    """
    from core.scheduler import scheduler

    print("🟡 [Scheduler] schedule_event_notifications() called")
    try:
        from services.models import Event
        from tenants.models import Church

        for job in scheduler.get_jobs():
            if job.id.startswith("event_"):
                scheduler.remove_job(job.id)

        now = timezone.now()
        churches = Church.raw_objects.filter(is_active=True)
        total = 0

        for church in churches:
            for event in Event.raw_objects.filter(church=church, is_active=True):

                if event.date:
                    full_dt = make_aware(datetime.combine(event.date, event.time))
                    if full_dt <= now:
                        continue
                    run_time = max(full_dt - timedelta(seconds=45), now + timedelta(seconds=5))
                    scheduler.add_job(
                        fire_event_broadcast,
                        trigger="date", run_date=run_time,
                        args=[event.id, church.id],
                        id=f"event_fixed_{church.id}_{event.id}",
                        replace_existing=True, misfire_grace_time=60,
                    )
                    total += 1
                    continue

                if getattr(event, "is_recurring_weekly", False) and event.day_of_week:
                    next_dt = get_next_occurrence(event.day_of_week, event.time)
                    if next_dt <= now:
                        continue
                    scheduler.add_job(
                        fire_event_broadcast,
                        trigger="date", run_date=next_dt - timedelta(seconds=45),
                        args=[event.id, church.id],
                        id=f"event_weekly_{church.id}_{event.id}",
                        replace_existing=True, misfire_grace_time=60,
                    )
                    total += 1

        print(f"✅ [Scheduler] {total} event broadcasts scheduled across {churches.count()} churches.")

    except Exception as exc:
        print(f"❌ [Scheduler] schedule_event_notifications: {exc}")


def schedule_push_notifications():
    """
    Schedule deferred push notifications (Notification.send_at set to future).
    Immediate notifications are handled by signals/views — not here.
    Called on startup and daily at 00:15.
    """
    from core.scheduler import scheduler

    print("🟡 [Scheduler] schedule_push_notifications() called")
    try:
        from notifications.models import Notification

        now = timezone.now()
        pending = Notification.raw_objects.filter(
            is_read=False,
            send_at__isnull=False,
            send_at__gt=now,
        )

        total = 0
        for notif in pending:
            scheduler.add_job(
                fire_push_notification,
                trigger="date", run_date=notif.send_at,
                args=[notif.id, notif.church_id],
                id=f"notif_{notif.church_id}_{notif.id}",
                replace_existing=True, misfire_grace_time=60,
            )
            total += 1

        print(f"✅ [Scheduler] {total} deferred push notifications scheduled.")

    except Exception as exc:
        print(f"❌ [Scheduler] schedule_push_notifications: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Job registration — called by core/scheduler.py:start()
# ─────────────────────────────────────────────────────────────────────────────

def register_workforce_jobs(scheduler):
    """Register all workforce cron jobs into the shared scheduler."""

    scheduler.add_job(
        schedule_event_notifications,
        trigger="cron", hour=0, minute=10,
        id="daily_event_reschedule",
        replace_existing=True,
    )
    scheduler.add_job(
        schedule_push_notifications,
        trigger="cron", hour=0, minute=15,
        id="daily_push_reschedule",
        replace_existing=True,
    )
    print("✅ [Scheduler] Workforce jobs registered.")


def evaluate_all_trainees(church):
    from workforce.models import WorkforceTraineeProfile
    from workforce.services.evaluator import evaluate_trainee_progress

    trainees = WorkforceTraineeProfile.raw_objects.filter(
        church=church,
        is_active=True,
    )

    for trainee in trainees:
        evaluate_trainee_progress(trainee)
