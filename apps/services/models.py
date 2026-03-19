"""
services/models.py

Event is the single model for all scheduled activities — services,
meetings, rehearsals, trainings, etc.

ServiceEvent has been removed. Music and media apps link directly to
Event via FK, which is the correct design since every church activity
is already an Event.

Event.unit (FK to ChurchUnit, nullable) determines which unit's
calendar an event appears on. Null = church-wide event visible to all.
"""

from django.db import models
from cloudinary.models import CloudinaryField
from core.models import ChurchOwnedModel


class Event(ChurchOwnedModel):

    EVENT_TYPES = [
        ("Service",  "Service"),
        ("Meeting",  "Meeting"),
        ("Training", "Training"),
        ("Rehearsal","Rehearsal"),
        ("Outreach", "Outreach"),
        ("Other",    "Other"),
    ]

    ATTENDANCE_MODE_CHOICES = [
        ("Physical", "Physical"),
        ("Virtual",  "Virtual"),
        ("Hybrid",   "Hybrid"),
    ]

    DAY_CHOICES = [
        ("Sunday",    "Sunday"),
        ("Monday",    "Monday"),
        ("Tuesday",   "Tuesday"),
        ("Wednesday", "Wednesday"),
        ("Thursday",  "Thursday"),
        ("Friday",    "Friday"),
        ("Saturday",  "Saturday"),
    ]

    name            = models.CharField(max_length=255)
    event_type      = models.CharField(max_length=50, choices=EVENT_TYPES)
    attendance_mode = models.CharField(
        max_length=10, choices=ATTENDANCE_MODE_CHOICES, default="Physical"
    )
    day_of_week     = models.CharField(
        max_length=20, choices=DAY_CHOICES, null=True, blank=True
    )
    date            = models.DateField(null=True, blank=True)
    end_date        = models.DateField(null=True, blank=True)
    time            = models.TimeField(null=True, blank=True)
    duration_days   = models.PositiveIntegerField(
        default=1, help_text="Number of days this event lasts."
    )
    is_recurring_weekly = models.BooleanField(default=False)
    postponed       = models.BooleanField(default=False)
    event_image     = CloudinaryField(
        "event_image", folder="events", blank=True, null=True
    )
    registrable     = models.BooleanField(
        default=False,
        help_text="Allow members to register for this event.",
    )
    registration_link = models.URLField(
        max_length=500, blank=True, null=True
    )
    unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
        help_text="Leave blank for church-wide events visible to all units.",
    )
    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="created_events",
    )

    class Meta:
        verbose_name        = "Event"
        verbose_name_plural = "Events"
        ordering = ["date", "day_of_week", "time"]
        indexes = [
            models.Index(fields=["church", "name"]),
            models.Index(fields=["church", "event_type"]),
            models.Index(fields=["church", "date"]),
            models.Index(fields=["church", "unit"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_event_type_display()})"