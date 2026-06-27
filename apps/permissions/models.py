from django.db import models
from django.conf import settings
from core.models import ChurchOwnedModel, OrderedChurchModel
from units.models import ChurchUnit


class UnitRole(OrderedChurchModel):
    """
    A role scoped to a specific unit/group.

    tier mirrors WorkforceRole.tier but applies only within the unit:
        3 = Overseer      — manages this unit as an assigned overseer
        4 = Unit Head     — full control of this unit
        5 = Assistant     — limited write + read in this unit
        6 = Custom        — ad-hoc unit-scoped permissions

    Tiers 1 and 2 (Admin, Sub-Admin) are never unit-scoped —
    those are handled via WorkforceRole with scope='global'.
    """

    TIER_CHOICES = [
        (4, "Overseer"),
        (5, "Unit / Group Head"),
        (6, "Assistant Head"),
        (7, "Custom Role"),
    ]

    unit = models.ForeignKey(ChurchUnit, on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=100)
    permissions = models.JSONField(default=dict)
    tier = models.PositiveSmallIntegerField(
        choices=TIER_CHOICES,
        default=7,
        db_index=True,
        help_text="Permission tier within this unit: 4=Overseer, 5=Head, 6=Assistant, 7=Custom.",
    )
    is_leadership = models.BooleanField(
        default=False,
        help_text="Legacy flag — auto-synced from tier.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["church", "unit", "name"], name="unique_unit_role_per_unit"
            )
        ]
        indexes = [
            models.Index(fields=["church", "unit"]),
            models.Index(fields=["church", "unit", "tier"]),
        ]
        ordering = ["order"]

    def __str__(self):
        return f"{self.unit.name} → {self.name}"

    def save(self, *args, **kwargs):
        self.is_leadership = self.tier in (4, 5, 6)
        super().save(*args, **kwargs)


class MembershipRole(ChurchOwnedModel):
    membership = models.ForeignKey(
        "units.UnitMembership", on_delete=models.CASCADE, related_name="roles"
    )
    role = models.ForeignKey(UnitRole, on_delete=models.CASCADE)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:

        constraints = [
            models.UniqueConstraint(
                fields=["membership", "role"], name="unique_role_per_membership"
            )
        ]

    def __str__(self):
        return f"{self.membership} → {self.role}"
