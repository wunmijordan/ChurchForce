"""
messaging/models.py

Two models:

    GuestMessage  — records a bulk or individual manual message sent to guests.
                    Sender is a UnitMembership (church-scoped workforce member).
                    Recipients are GuestEntry records.

    SMSLog        — immutable audit record for every SMS dispatch attempt,
                    whether manual or automated (birthday, reminder etc.).
                    Never delete rows from this table.
"""

from django.db import models
from django.utils.timezone import now

from core.models import ChurchOwnedModel
from guests.models import GuestEntry
from units.models import UnitMembership


class GuestMessage(ChurchOwnedModel):
    """
    A manual message sent to one or more guests by a workforce member.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("sent",    "Sent"),
        ("failed",  "Failed"),
    ]

    sender = models.ForeignKey(
        UnitMembership,
        on_delete=models.CASCADE,
        related_name="sent_guest_messages",
        help_text="The workforce member who sent this message.",
    )
    recipients = models.ManyToManyField(
        GuestEntry,
        related_name="received_messages",
        blank=True,
    )
    subject  = models.CharField(max_length=255, blank=True)
    body     = models.TextField()
    status   = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True
    )
    sent_at  = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        try:
            user = self.sender.workforce_member.member.user
            name = user.full_name or user.username
        except AttributeError:
            name = "Unknown"
        return f"{name} — {self.subject or 'No Subject'}"

    def send(self):
        """
        Mark as sent and create MessageLog entries for each recipient.
        Actual SMS dispatch is handled by messaging.sms.send_sms().
        """
        self.sent_at = now()
        self.status  = "sent"
        self.save(update_fields=["sent_at", "status"])

        for guest in self.recipients.all():
            MessageLog.raw_objects.create(
                church=self.church,
                message=self,
                guest=guest,
                status="sent",
                phone=guest.phone_number or "",
            )


class MessageLog(ChurchOwnedModel):
    """
    Immutable audit log for every SMS dispatch attempt.

    message  — nullable because automated messages (birthday, reminder)
               have no parent GuestMessage record.
    category — classifies the trigger source for reporting.
    """

    CATEGORY_CHOICES = [
        ("manual",   "Manual bulk message"),
        ("birthday", "Birthday message"),
        ("reminder", "Follow-up reminder"),
        ("other",    "Other automated"),
    ]
    STATUS_CHOICES = [
        ("sent",    "Sent"),
        ("failed",  "Failed"),
        ("pending", "Pending"),
    ]

    message = models.ForeignKey(
        GuestMessage,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="logs",
    )
    guest = models.ForeignKey(
        GuestEntry,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="message_logs",
    )
    # Phone stored separately so logs survive guest deletion
    phone       = models.CharField(max_length=30, blank=True)
    recipient_name = models.CharField(max_length=255, blank=True)
    body        = models.TextField(blank=True)
    status      = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    category    = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="manual", db_index=True)
    provider    = models.CharField(
        max_length=50, blank=True,
        help_text="SMS provider used (termii, africastalking, twilio, etc.)",
    )
    provider_reference = models.CharField(
        max_length=255, blank=True,
        help_text="Provider's message ID for delivery status lookup.",
    )
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "status"]),
            models.Index(fields=["church", "category"]),
            models.Index(fields=["church", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.recipient_name or self.phone} — {self.status} [{self.category}]"

    def save(self, *args, **kwargs):
        # Auto-populate church from parent message if not set
        if not self.church_id and self.message_id:
            self.church = self.message.church
        super().save(*args, **kwargs)