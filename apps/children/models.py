from django.db import models

from core.models import ChurchOwnedModel
from units.models import ChurchUnit


class ChildProfile(ChurchOwnedModel):
    GENDER_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
        ("other", "Other"),
    ]

    unit = models.ForeignKey(
        ChurchUnit,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children_profiles",
        help_text="Children ministry unit responsible for this child.",
    )
    guardian_member = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children_guardians",
        help_text="Parent/guardian who is a church member.",
    )
    guardian_guest = models.ForeignKey(
        "guests.GuestEntry",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children_guardians",
        help_text="Parent/guardian still in the guest pipeline.",
    )
    guardian_name = models.CharField(max_length=150, blank=True)
    guardian_phone = models.CharField(max_length=30, blank=True)
    guardian_email = models.EmailField(blank=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    birth_date = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True)
    grade = models.CharField(max_length=50, blank=True)
    allergies = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["church", "unit"]),
            models.Index(fields=["church", "last_name"]),
        ]
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()


class ParentContact(ChurchOwnedModel):
    child = models.ForeignKey(
        ChildProfile,
        on_delete=models.CASCADE,
        related_name="parents",
    )
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    relationship = models.CharField(max_length=50, blank=True)
    is_primary = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["church", "child"]),
            models.Index(fields=["church", "is_primary"]),
        ]
        ordering = ["-is_primary", "full_name"]

    def __str__(self):
        return f"{self.full_name} ({self.relationship or 'Parent'})"


class CheckInSession(ChurchOwnedModel):
    unit = models.ForeignKey(
        ChurchUnit,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children_checkin_sessions",
    )
    title = models.CharField(max_length=150)
    session_date = models.DateField(db_index=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children_checkin_sessions",
    )

    class Meta:
        indexes = [
            models.Index(fields=["church", "session_date"]),
        ]
        ordering = ["-session_date"]

    def __str__(self):
        return f"{self.title} ({self.session_date})"


class CheckInRecord(ChurchOwnedModel):
    STATUS_CHOICES = [
        ("checked_in", "Checked In"),
        ("checked_out", "Checked Out"),
    ]

    child = models.ForeignKey(
        ChildProfile,
        on_delete=models.CASCADE,
        related_name="checkins",
    )
    session = models.ForeignKey(
        CheckInSession,
        on_delete=models.CASCADE,
        related_name="records",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="checked_in")
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_out_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children_checkins_in",
    )
    checked_out_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children_checkins_out",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["church", "child", "session"],
                name="unique_child_checkin_per_session",
            )
        ]
        indexes = [
            models.Index(fields=["church", "session"]),
            models.Index(fields=["church", "child"]),
        ]

    def __str__(self):
        return f"{self.child} @ {self.session}"



