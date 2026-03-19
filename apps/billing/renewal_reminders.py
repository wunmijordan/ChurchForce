"""
billing/renewal_reminders.py

Sends renewal reminder notifications to churches whose subscriptions
are expiring in 7, 3, or 1 day(s).

Registered into the core scheduler by register_billing_jobs(), which
is called from core/scheduler.py:start(). Never registers jobs at
module import time — that would fire during migrations and testing.

Also callable directly as a management command:
    python manage.py send_renewal_reminders

Reminders appear as in-app notifications via the existing notifications
system — no email dependency.
"""

import logging
from datetime import timedelta
from django.utils import timezone

logger = logging.getLogger(__name__)

# Days before expiry at which to send a reminder
REMINDER_WINDOWS = [7, 3, 1]


# ─────────────────────────────────────────────────────────────────────────────
# Main job function
# ─────────────────────────────────────────────────────────────────────────────

def send_renewal_reminders():
    """
    Check all active non-lifetime subscriptions and send in-app reminders
    at the 7-day, 3-day, and 1-day windows before expiry.

    Idempotent — reminder flags on ChurchSubscription are set after
    sending so the same reminder is never sent twice in a cycle.
    Flags are cleared on successful payment (handled in webhook.py).
    """
    from billing.models import ChurchSubscription

    now = timezone.now()

    subscriptions = ChurchSubscription.objects.filter(
        is_active=True,
        expires_at__isnull=False,
    ).exclude(
        plan__name__in=(
            "founders_saas_lifetime",
            "founders_white_label_lifetime",
        )
    ).select_related("church", "plan")

    sent_count = 0

    for sub in subscriptions:
        days_left = (sub.expires_at.date() - now.date()).days

        for days in REMINDER_WINDOWS:
            flag = f"reminder_{days}d_sent"

            if getattr(sub, flag, False):
                continue  # Already sent for this window this cycle

            if 0 <= days_left <= days:
                _send_reminder_notification(sub, days_left)

                if hasattr(sub, flag):
                    setattr(sub, flag, True)
                    sub.save(update_fields=[flag, "updated_at"])

                sent_count += 1
                logger.info(
                    "Renewal reminder sent: %s — %d day(s) left",
                    sub.church.name, days_left,
                )
                break  # Only one reminder per run per subscription

    logger.info("Renewal reminders: %d sent.", sent_count)
    return sent_count


# ─────────────────────────────────────────────────────────────────────────────
# Notification delivery
# ─────────────────────────────────────────────────────────────────────────────

def _send_reminder_notification(sub, days_left):
    """
    Create in-app Notification records for all active members of the church.
    """
    from notifications.models import Notification
    from accounts.models import ChurchMember

    church = sub.church
    plan_label = sub.plan.get_name_display()

    if days_left == 0:
        title = f"Your {plan_label} subscription expires today"
        description = "Renew now to avoid losing access to ChurchForce."
    elif days_left == 1:
        title = "Your subscription expires tomorrow"
        description = (
            f"Your {plan_label} subscription expires tomorrow. "
            "Renew now to keep your access uninterrupted."
        )
    else:
        title = f"Your subscription expires in {days_left} days"
        description = (
            f"Your {plan_label} subscription expires in {days_left} days. "
            "Renew before it lapses to avoid service interruption."
        )

    members = ChurchMember.raw_objects.filter(
        church=church,
        is_active=True,
    ).select_related("user")

    notifications = [
        Notification(
            church=church,
            member=member,
            title=title,
            description=description,
            is_urgent=(days_left <= 1),
            is_active=True,
        )
        for member in members
    ]

    if notifications:
        Notification.objects.bulk_create(notifications, ignore_conflicts=True)


# ─────────────────────────────────────────────────────────────────────────────
# Job registration — called by core/scheduler.py:start()
# ─────────────────────────────────────────────────────────────────────────────

def register_billing_jobs(scheduler):
    """Register all billing cron jobs into the shared scheduler."""

    scheduler.add_job(
        send_renewal_reminders,
        trigger="cron", hour=8, minute=0,
        id="daily_renewal_reminders",
        replace_existing=True,
    )
    print("✅ [Scheduler] Billing jobs registered.")