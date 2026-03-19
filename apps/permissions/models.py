from django.db import models
from django.conf import settings
from core.models import ChurchOwnedModel, OrderedChurchModel
from units.models import ChurchUnit


class UnitRole(OrderedChurchModel):
    unit = models.ForeignKey(
        ChurchUnit,
        on_delete=models.CASCADE,
        related_name="roles"
    )
    name = models.CharField(max_length=100)
    permissions = models.JSONField(default=dict)
    is_leadership = models.BooleanField(default=False)

    class Meta:

        constraints = [
            models.UniqueConstraint(
                fields=["church", "unit", "name"],
                name="unique_unit_role_per_unit"
            )
        ]

        indexes = [
            models.Index(fields=["church", "unit"]),
        ]

        ordering = ["order"]

    def __str__(self):
        return f"{self.unit.name} → {self.name}"


class MembershipRole(ChurchOwnedModel):
    membership = models.ForeignKey(
        "units.UnitMembership",
        on_delete=models.CASCADE,
        related_name="roles"
    )
    role = models.ForeignKey(
        UnitRole,
        on_delete=models.CASCADE
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:

        constraints = [
            models.UniqueConstraint(
                fields=["membership", "role"],
                name="unique_role_per_membership"
            )
        ]

    def __str__(self):
        return f"{self.membership} → {self.role}"
