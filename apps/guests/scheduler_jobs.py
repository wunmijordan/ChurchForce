"""
guests/scheduler_jobs.py

Guest pipeline scheduled jobs:
    - flag_committed_guests: runs daily, checks attendance thresholds
      per church and advances eligible guests to 'Committed' status.

Registered into the core scheduler by register_guest_jobs(), which
is called from core/scheduler.py:start().
"""

import logging

logger = logging.getLogger(__name__)


def run_flag_committed_guests():
    """
    Daily safety-net scan for committed guests.

    This is a backup to the event-driven check_commitment_for_guest() signal
    which fires on every FollowUpReport save. This job catches any edge cases
    where signals were suppressed (e.g. bulk imports, management commands).

    Uses flag_committed_guests() which issues a single GROUP BY aggregation
    query per church — O(1) DB round-trips regardless of guest count.
    """
    from tenants.models import Church
    from guests.pipeline import flag_committed_guests

    churches = Church.raw_objects.filter(is_active=True)
    total_flagged = 0

    for church in churches:
        try:
            church_settings = getattr(church, "settings", None)
            threshold = getattr(church_settings, "committed_attendance_threshold", None)
            window = getattr(church_settings, "committed_attendance_window_weeks", None)

            kwargs = {}
            if threshold is not None:
                kwargs["threshold"] = threshold
            if window is not None:
                kwargs["window_weeks"] = window

            count = flag_committed_guests(church, **kwargs)
            if count:
                total_flagged += count
                logger.info(
                    "Committed guests (cron) flagged: %d for %s", count, church.name
                )

        except Exception as exc:
            logger.error(
                "flag_committed_guests cron failed for %s: %s", church.name, exc
            )

    logger.info("Daily cron: total committed guests flagged: %d", total_flagged)
    return total_flagged


def run_followup_reminders():
    """
    Daily job that checks for guests who haven't been contacted yet.

    Timeline per guest (from assignment date):
        Day 4 — 3-day pre-reminder sent to the assigned officer
        Day 7 — overdue notification sent to unit head (officer + head if different)
    """
    from tenants.models import Church
    from guests.models import GuestEntry, GuestStatus
    from django.utils import timezone

    today = timezone.localdate()
    day4 = today - timezone.timedelta(days=4)
    day7 = today - timezone.timedelta(days=7)
    total = 0

    churches = Church.raw_objects.filter(is_active=True)
    for church in churches:
        # Guests with no contact_answered report yet
        new_status_id = (
            GuestStatus.raw_objects.filter(church=church, slug="new-guest")
            .values_list("id", flat=True)
            .first()
        )

        if not new_status_id:
            continue

        uncontacted = GuestEntry.raw_objects.filter(
            church=church,
            is_active=True,
            is_deleted=False,
            status_id=new_status_id,
            assigned_to__isnull=False,
            assigned_at__isnull=False,
        ).select_related(
            "assigned_to__workforce_member__member",
            "assigned_to__unit",
        )

        for guest in uncontacted:
            try:
                assigned_date = guest.assigned_at.date() if guest.assigned_at else None
                if not assigned_date:
                    continue

                days_since = (today - assigned_date).days

                # Day 4 — remind the officer
                if days_since >= 4 and not guest.first_contact_reminder_sent:
                    _send_contact_reminder(guest, church, "officer")
                    guest.first_contact_reminder_sent = True
                    guest.save(update_fields=["first_contact_reminder_sent"])
                    total += 1

                # Day 7 — notify unit head + officer
                elif days_since >= 7 and not guest.in_contact_overdue_notified:
                    _send_contact_reminder(guest, church, "head")
                    guest.in_contact_overdue_notified = True
                    guest.save(update_fields=["in_contact_overdue_notified"])
                    total += 1

            except Exception as exc:
                import logging

                logging.getLogger(__name__).error(
                    "followup_reminder failed for guest %s: %s", guest.id, exc
                )

    return total


def _send_contact_reminder(guest, church, level):
    """Send in-contact reminder notifications."""
    from notifications.utils import notify_members
    from units.models import UnitMembership
    from django.utils import timezone

    name = guest.full_name
    officer_member = None
    head_member = None

    try:
        membership = guest.assigned_to
        officer_member = membership.workforce_member.member
        head = membership.unit.get_unit_head() if membership.unit else None
        if head and head.workforce_member:
            head_member = head.workforce_member.member
    except AttributeError:
        pass

    if level == "officer" and officer_member:
        notify_members(
            [officer_member],
            title=f"📋 Follow-up reminder: {name}",
            description=(
                f"{name} was assigned to you {(timezone.localdate() - guest.assigned_at.date()).days} days ago "
                f"and has not been contacted yet. Please make first contact within the next 3 days."
            ),
            church=church,
            is_urgent=False,
        )

    if level == "head":
        recipients = []
        if officer_member:
            recipients.append(officer_member)
        if head_member and head_member != officer_member:
            recipients.append(head_member)
        if recipients:
            days = (timezone.localdate() - guest.assigned_at.date()).days
            notify_members(
                recipients,
                title=f"⚠️ No contact made: {name} ({days} days)",
                description=(
                    f"{name} has been assigned for {days} days with no contact recorded. "
                    f"Unit head review recommended."
                ),
                church=church,
                is_urgent=True,
            )


def run_thankyou_messages():
    """
    Daily job that sends automated thank-you SMS to newly registered
    guests and guests who made a second visit.
    """
    from tenants.models import Church
    from guests.models import GuestEntry
    from messaging.sms import send_sms
    from django.utils import timezone

    today = timezone.localdate()
    yesterday = today - timezone.timedelta(days=1)
    total = 0

    churches = Church.raw_objects.filter(is_active=True)
    for church in churches:
        church_settings = getattr(church, "settings", None)
        if not church_settings:
            continue

        # First-visit thank-you (registered yesterday, not yet sent)
        if church_settings.send_first_visit_thankyou:
            first_guests = GuestEntry.raw_objects.filter(
                church=church,
                is_active=True,
                is_deleted=False,
                first_thankyou_sent=False,
                date_of_visit=yesterday,
                phone_number__isnull=False,
            ).exclude(phone_number="")

            for guest in first_guests:
                msg = church_settings.thankyou_first_visit_message.format(
                    name=guest.full_name
                )
                send_sms(
                    phone=guest.phone_number,
                    message=msg,
                    church=church,
                    category="other",
                    recipient_name=guest.full_name,
                    guest=guest,
                )
                guest.first_thankyou_sent = True
                guest.save(update_fields=["first_thankyou_sent"])
                total += 1

        # Second-visit thank-you
        if church_settings.send_second_visit_thankyou:
            second_guests = GuestEntry.raw_objects.filter(
                church=church,
                is_active=True,
                is_deleted=False,
                second_thankyou_sent=False,
                second_visit_date=yesterday,
                phone_number__isnull=False,
            ).exclude(phone_number="")

            for guest in second_guests:
                msg = church_settings.thankyou_second_visit_message.format(
                    name=guest.full_name
                )
                send_sms(
                    phone=guest.phone_number,
                    message=msg,
                    church=church,
                    category="other",
                    recipient_name=guest.full_name,
                    guest=guest,
                )
                guest.second_thankyou_sent = True
                guest.save(update_fields=["second_thankyou_sent"])
                total += 1

    return total


def register_guest_jobs(scheduler):
    """Register all guest pipeline cron jobs into the shared scheduler."""

    scheduler.add_job(
        run_flag_committed_guests,
        trigger="cron",
        hour=1,
        minute=0,
        id="daily_flag_committed_guests",
        replace_existing=True,
    )
    scheduler.add_job(
        run_followup_reminders,
        trigger="cron",
        hour=9,
        minute=0,
        id="daily_followup_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        run_thankyou_messages,
        trigger="cron",
        hour=9,
        minute=30,
        id="daily_thankyou_messages",
        replace_existing=True,
    )
    print("✅ [Scheduler] Guest pipeline jobs registered.")
