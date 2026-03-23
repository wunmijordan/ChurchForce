from django.db import models

from core.models import ChurchOwnedModel
from units.models import ChurchUnit


class TeenProfile(ChurchOwnedModel):
    unit = models.ForeignKey(
        ChurchUnit,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="teen_profiles",
    )
    member = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="teen_profiles",
    )
    guest = models.ForeignKey(
        "guests.GuestEntry",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="teen_profiles",
        help_text="Teen still in guest-to-member pipeline.",
    )
    full_name = models.CharField(max_length=150)
    school = models.CharField(max_length=150, blank=True)
    interests = models.TextField(blank=True)
    guardian_member = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="teen_guardians",
    )
    guardian_guest = models.ForeignKey(
        "guests.GuestEntry",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="teen_guardians",
    )
    guardian_name = models.CharField(max_length=150, blank=True)
    guardian_phone = models.CharField(max_length=30, blank=True)
    guardian_contact = models.CharField(max_length=150, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["church", "unit"]),
            models.Index(fields=["church", "full_name"]),
        ]
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name


class MentorAssignment(ChurchOwnedModel):
    teen = models.ForeignKey(
        TeenProfile,
        on_delete=models.CASCADE,
        related_name="mentors",
    )
    mentor = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="teen_mentorships",
    )
    start_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["church", "mentor"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        mentor_name = self.mentor.user.full_name if self.mentor and hasattr(self.mentor, "user") else "Mentor"
        return f"{self.teen} - {mentor_name}"


class TrendPulse(ChurchOwnedModel):
    topic = models.CharField(max_length=200)
    summary = models.TextField(blank=True)
    reported_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="teen_trend_pulses",
    )

    class Meta:
        indexes = [
            models.Index(fields=["church", "topic"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return self.topic





