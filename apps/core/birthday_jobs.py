"""
core/birthday_jobs.py

Daily birthday checker for guests and workforce members.

Runs at 08:00 every morning via the core scheduler.
Creates in-app Notification records for today's birthdays.

SMS sending is intentionally left as a stub — wire it to the bulk
SMS platform when the messaging app is ready. The notification record
serves as the in-app trigger until then.

Registered into the core scheduler by register_birthday_jobs(),
called from core/scheduler.py:start().
"""

import logging
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Birthday check functions
# ─────────────────────────────────────────────────────────────────────────────

def send_guest_birthday_notifications():
    """
    Find all guests across all active churches whose birthday is today
    and create in-app notifications for their assigned follow-up officer.

    Birthday matching uses birthday_month and birthday_day integer fields
    which are auto-populated from date_of_birth on save — this avoids
    parsing the free-text date string on every query.
    """
    from tenants.models import Church
    from guests.models import GuestEntry
    from accounts.models import ChurchMember
    from notifications.models import Notification

    today = timezone.localdate()
    total = 0

    churches = Church.raw_objects.filter(is_active=True)
    for church in churches:
        guests = GuestEntry.raw_objects.filter(
            church=church,
            is_active=True,
            is_deleted=False,
            birthday_month=today.month,
            birthday_day=today.day,
        ).select_related(
            "assigned_to__workforce_member__member",
            "status",
        )

        for guest in guests:
            # Notify the assigned follow-up officer if one exists
            assigned_membership = guest.assigned_to
            if assigned_membership:
                try:
                    officer_member = assigned_membership.workforce_member.member
                    Notification.objects.create(
                        church=church,
                        member=officer_member,
                        title=f"🎂 Birthday: {guest.full_name}",
                        description=(
                            f"{guest.full_name} has a birthday today! "
                            f"Consider reaching out with a birthday message."
                        ),
                        is_active=True,
                    )
                    total += 1
                except AttributeError:
                    pass

            # Send birthday SMS via configured provider
            if guest.phone_number:
                try:
                    from messaging.sms import send_sms
                    guest_name = f"{guest.title} {guest.full_name}".strip() if guest.title else guest.full_name
                    send_sms(
                        phone=guest.phone_number,
                        message=f"Happy birthday, {guest_name}! 🎂 Wishing you a wonderful day.",
                        church=church,
                        category="birthday",
                        recipient_name=guest.full_name,
                        guest=guest,
                    )
                except Exception as exc:
                    logger.warning("Birthday SMS failed for guest %s: %s", guest, exc)

    logger.info("Guest birthday notifications sent: %d", total)
    return total


def send_member_birthday_notifications():
    """
    Find all church members whose birthday is today and:
    1. Create an in-app notification for the church admin(s).
    2. Stub for SMS — wire when messaging app is ready.

    Birthday matching uses birthday_month and birthday_day on CustomUser
    which are auto-populated from date_of_birth on save.
    """
    from tenants.models import Church
    from accounts.models import CustomUser, ChurchMember
    from notifications.models import Notification

    today = timezone.localdate()
    total = 0

    churches = Church.raw_objects.filter(is_active=True)
    for church in churches:
        # Find members with birthdays today
        birthday_users = CustomUser.objects.filter(
            church_memberships__church=church,
            church_memberships__is_active=True,
            birthday_month=today.month,
            birthday_day=today.day,
            is_active=True,
        ).distinct()

        for user in birthday_users:
            member = ChurchMember.raw_objects.filter(
                church=church, user=user, is_active=True
            ).first()

            if not member:
                continue

            # Self-notification — shown on their own dashboard
            Notification.objects.create(
                church=church,
                member=member,
                title="🎂 Happy Birthday!",
                description=(
                    f"Wishing you a wonderful birthday, {user.full_name or user.username}! "
                    f"The team celebrates you today."
                ),
                is_active=True,
            )
            total += 1

            # Send birthday SMS via configured provider
            if user.phone_number:
                try:
                    from messaging.sms import send_sms
                    member_name = f"{user.title} {user.full_name}".strip() if user.title else (user.full_name or user.username)
                    send_sms(
                        phone=user.phone_number,
                        message=f"Happy birthday, {member_name}! 🎂 The team celebrates you today.",
                        church=church,
                        category="birthday",
                        recipient_name=user.full_name or user.username,
                    )
                except Exception as exc:
                    logger.warning("Birthday SMS failed for member %s: %s", user, exc)

    logger.info("Member birthday notifications sent: %d", total)
    return total


def run_birthday_notifications():
    """Single entry point called by the scheduler."""
    guest_count  = send_guest_birthday_notifications()
    member_count = send_member_birthday_notifications()
    logger.info(
        "Birthday notifications complete — guests: %d, members: %d",
        guest_count, member_count,
    )
    return guest_count + member_count


# ─────────────────────────────────────────────────────────────────────────────
# Job registration — called by core/scheduler.py:start()
# ─────────────────────────────────────────────────────────────────────────────

def register_birthday_jobs(scheduler):
    """Register birthday notification job into the shared scheduler."""

    scheduler.add_job(
        run_birthday_notifications,
        trigger="cron",
        hour=8,
        minute=0,
        id="daily_birthday_notifications",
        replace_existing=True,
    )
    print("✅ [Scheduler] Birthday jobs registered.")