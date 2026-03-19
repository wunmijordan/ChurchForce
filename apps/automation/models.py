"""
automation/models.py

Church-scoped audit and automation log.

Every significant automated action in the system writes a record here:
    - Scheduler job outcomes (birthday SMS sent, renewal reminders, committed flags)
    - Webhook events received and processed
    - System-level errors that affect a church
    - Admin-triggered bulk operations

This gives church admins and superusers a searchable history of what
the system has done on their behalf, without digging through server logs.

AutomationLog rows are append-only — never update or delete them.
"""

from django.db import models
from core.models import ChurchOwnedModel


class AutomationLog(ChurchOwnedModel):
    """
    Immutable record of a single automated event for a church.

    category: broad grouping for filtering in the admin/dashboard
    event_type: specific event name, e.g. "birthday_sms_sent"
    status: outcome of the event
    payload: arbitrary JSON data for debugging (IDs, counts, errors)
    """

    CATEGORY_CHOICES = [
        ("scheduler",   "Scheduler Job"),
        ("webhook",     "Webhook Event"),
        ("signal",      "Signal"),
        ("billing",     "Billing"),
        ("messaging",   "Messaging"),
        ("pipeline",    "Guest Pipeline"),
        ("system",      "System"),
    ]

    STATUS_CHOICES = [
        ("success", "Success"),
        ("partial", "Partial"),
        ("failed",  "Failed"),
        ("info",    "Info"),
    ]

    category   = models.CharField(
        max_length=20, choices=CATEGORY_CHOICES,
        default="system", db_index=True,
    )
    event_type = models.CharField(
        max_length=100, db_index=True,
        help_text="e.g. 'birthday_sms_sent', 'charge.success', 'committed_guests_flagged'",
    )
    status     = models.CharField(
        max_length=20, choices=STATUS_CHOICES,
        default="info", db_index=True,
    )
    summary    = models.CharField(
        max_length=500, blank=True,
        help_text="Human-readable one-line summary shown in the admin list.",
    )
    payload    = models.JSONField(
        default=dict, blank=True,
        help_text="Structured data for debugging — IDs, counts, error details.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "category", "-created_at"]),
            models.Index(fields=["church", "status"]),
            models.Index(fields=["church", "event_type"]),
        ]

    def __str__(self):
        return f"[{self.category}] {self.event_type} — {self.status}"