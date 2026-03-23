from django.db import models
from django.utils import timezone

from accounts.models import ChurchMember
from core.models import ChurchOwnedModel
from core.managers import ScopedNotificationManager, RawNotificationManager


class Notification(ChurchOwnedModel):
    """
    In-app notification for a church member.

    Scoped to church via ChurchOwnedModel.
    Recipient is a ChurchMember (not CustomUser) — one member can belong
    to multiple churches and notifications are per-church.

    send_at: optional future datetime for deferred notifications.
             If set, the scheduler fires the WebSocket push at that time.
             If null, the notification is shown immediately on creation.
    """
    objects     = ScopedNotificationManager()
    raw_objects = RawNotificationManager()

    member      = models.ForeignKey(
        ChurchMember,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title       = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    link        = models.URLField(blank=True, null=True)
    is_read     = models.BooleanField(default=False, db_index=True)
    is_urgent   = models.BooleanField(default=False)
    is_success  = models.BooleanField(default=False)
    is_starred  = models.BooleanField(default=False)

    # Deferred delivery — if set, scheduler fires the push at this time.
    # Null = deliver immediately on creation.
    send_at     = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="If set, the push notification is deferred until this time.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "member", "-created_at"]),
            models.Index(fields=["church", "is_read"]),
            models.Index(fields=["send_at"]),
        ]

    def __str__(self):
        return f"{self.title} → {self.member}"


class UserSettings(ChurchOwnedModel):
    """Notification preferences for a church member."""

    SOUND_CHOICES = [
        ("chime1", "Chime 1"),
        ("chime2", "Chime 2"),
        ("chime3", "Chime 3"),
        ("chime4", "Chime 4"),
        ("chime5", "Chime 5"),
        ("chime6", "Chime 6"),
        ("chime7", "Chime 7"),
        ("chime8", "Chime 8"),
    ]

    member              = models.OneToOneField(
        ChurchMember,
        on_delete=models.CASCADE,
        related_name="settings",
    )
    notification_sound  = models.CharField(
        max_length=20, choices=SOUND_CHOICES, default="chime1"
    )
    vibration_enabled   = models.BooleanField(default=True)

    def __str__(self):
        return f"Settings for {self.member}"


class PushSubscription(ChurchOwnedModel):
    """Web Push subscription data for a church member."""

    member            = models.ForeignKey(
        ChurchMember,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
    )
    subscription_data = models.JSONField(help_text="Endpoint and VAPID keys from the browser.")

    def __str__(self):
        return f"PushSubscription for {self.member}"
