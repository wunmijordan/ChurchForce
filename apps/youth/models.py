from django.db import models

from core.models import ChurchOwnedModel
from units.models import ChurchUnit


class YouthProfile(ChurchOwnedModel):
    unit = models.ForeignKey(
        ChurchUnit,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="youth_profiles",
    )
    member = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="youth_profiles",
    )
    full_name = models.CharField(max_length=150)
    school = models.CharField(max_length=150, blank=True)
    career_interest = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["church", "unit"]),
            models.Index(fields=["church", "full_name"]),
        ]
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name


class CareerGoal(ChurchOwnedModel):
    STATUS_CHOICES = [
        ("planned", "Planned"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
    ]

    youth = models.ForeignKey(
        YouthProfile,
        on_delete=models.CASCADE,
        related_name="career_goals",
    )
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="planned")
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["church", "status"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class ImpactProject(ChurchOwnedModel):
    STATUS_CHOICES = [
        ("planned", "Planned"),
        ("active", "Active"),
        ("completed", "Completed"),
    ]

    unit = models.ForeignKey(
        ChurchUnit,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="youth_projects",
    )
    title = models.CharField(max_length=200)
    focus_area = models.CharField(max_length=150, blank=True)
    start_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="planned")
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["church", "unit"]),
            models.Index(fields=["church", "status"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
