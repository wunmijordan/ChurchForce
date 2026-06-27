import uuid
from datetime import datetime, time, timedelta

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
from django.utils import timezone


class Event(ChurchOwnedModel):

    EVENT_TYPES = [
        ("Service", "Service"),
        ("Meeting", "Meeting"),
        ("Training", "Training"),
        ("Rehearsal", "Rehearsal"),
        ("Outreach", "Outreach"),
        ("Other", "Other"),
    ]

    ATTENDANCE_MODE_CHOICES = [
        ("Physical", "Physical"),
        ("Virtual", "Virtual"),
        ("Hybrid", "Hybrid"),
    ]

    DAY_CHOICES = [
        ("Sunday", "Sunday"),
        ("Monday", "Monday"),
        ("Tuesday", "Tuesday"),
        ("Wednesday", "Wednesday"),
        ("Thursday", "Thursday"),
        ("Friday", "Friday"),
        ("Saturday", "Saturday"),
    ]

    uid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
        help_text="Stable public identifier used in URLs instead of pk.",
    )
    name = models.CharField(max_length=255)
    event_type = models.CharField(max_length=50, choices=EVENT_TYPES)
    # Guest pipeline commitment gate.
    # When True, attendance at this event is counted toward a guest's
    # commitment threshold (e.g. 4 services in 6 weeks → Committed status).
    # Typically set on main weekly services, not on unit meetings or training.
    count_towards_commitment = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            "Count attendance at this event toward a guest's commitment "
            "threshold. Enable for main services; disable for unit meetings, "
            "training days, or one-off events."
        ),
    )
    attendance_mode = models.CharField(
        max_length=10, choices=ATTENDANCE_MODE_CHOICES, default="Physical"
    )
    day_of_week = models.CharField(
        max_length=20, choices=DAY_CHOICES, null=True, blank=True
    )
    date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    time = models.TimeField(null=True, blank=True)
    attendance_closes_at = models.TimeField(
        null=True,
        blank=True,
        help_text="Optional end time used to close attendance access.",
    )
    duration_days = models.PositiveIntegerField(
        default=1, help_text="Number of days this event lasts."
    )
    is_recurring_weekly = models.BooleanField(default=False)
    postponed = models.BooleanField(default=False)
    event_image = CloudinaryField("event_image", folder="events", blank=True, null=True)

    @property
    def event_image_url(self):
        """Event image URL — /media/… in dev, Cloudinary in prod."""
        from core.storage import smart_image_url

        return smart_image_url(self.event_image)

    registrable = models.BooleanField(
        default=False,
        help_text="Allow members to register for this event.",
    )
    registration_link = models.URLField(max_length=500, blank=True, null=True)
    trainee_access = models.BooleanField(
        default=False,
        help_text=(
            "When checked, trainees in the guest pipeline can see and mark "
            "attendance for this event. Uncheck to hide it from trainees."
        ),
    )
    unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
        help_text="Leave blank for church-wide events visible to all units.",
    )
    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_events",
    )
    # For Training events: link to the LMS course this session belongs to.
    lms_course = models.ForeignKey(
        "lms.LMSCourse",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="training_events",
        help_text=(
            "For Training events only — link to the LMS course this session covers. "
            "Trainee attendance at this event unlocks the course for them."
        ),
    )
    # Which modules this training event (class) covers.
    # Empty = attendance unlocks ALL modules in the linked course.
    # Set specific modules to unlock only those on attendance.
    # Kept in sync with LMSSession.modules_covered via signal.
    modules_covered = models.ManyToManyField(
        "lms.LMSModule",
        blank=True,
        related_name="training_events",
        help_text=(
            "For Training events — which course modules this class covers. "
            "Leave empty to unlock all modules on attendance. "
            "Select specific modules to unlock only those."
        ),
    )
    # For Rehearsal events: link to the Service/Program event this rehearsal prepares for.
    # This is stored on Event so it persists and can be edited, then synced to RehearsalSession.
    prepares_for_service = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rehearsal_preparations",
        help_text=(
            "For Rehearsal events only — link to the Event this rehearsal "
            "is preparing for. This connection is visible on the rehearsal board."
        ),
    )

    class Meta:
        verbose_name = "Event"
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

    def attendance_end_datetime(self, *, reference_date=None):
        event_date = reference_date or self.date
        if not event_date:
            return None
        if self.attendance_closes_at:
            end_dt = datetime.combine(event_date, self.attendance_closes_at)
            if self.time and self.attendance_closes_at < self.time:
                end_dt += timedelta(days=1)
            return timezone.make_aware(end_dt)
        if self.time:
            return timezone.make_aware(
                datetime.combine(event_date, self.time) + timedelta(hours=2)
            )
        return timezone.make_aware(datetime.combine(event_date, time.max))

    def attendance_is_open(self, *, now=None, reference_date=None):
        current = now or timezone.localtime()
        end_dt = self.attendance_end_datetime(reference_date=reference_date)
        if not end_dt:
            return True
        return current <= end_dt
