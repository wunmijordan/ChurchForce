from django.db import models
from tenants.models import Church
from .managers import ScopedChurchManager, RawChurchManager


class BaseModel(models.Model):
    """
    Base model for all project models.
    Provides a BigAutoField PK and a hook for future extensibility.
    """

    id = models.BigAutoField(primary_key=True)

    class Meta:
        abstract = True


class TimestampedModel(BaseModel):
    """
    Automatically tracks creation and last-update timestamps.

    NOTE: Church (the root tenant model) cannot inherit this class because
    core.models imports Church, creating a circular dependency. Church instead
    inherits tenants.models.TenantBaseModel, which mirrors these fields.
    All other models should prefer TimestampedModel or ChurchOwnedModel.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ChurchOwnedModel(TimestampedModel):
    """
    Base for every model that belongs to a Church tenant.

    Provides:
        - church FK (CASCADE) with auto-scoped manager
        - is_active flag
        - composite indexes on (church, created_at) and (church, is_active)

    Always query through .objects — it auto-scopes to the current tenant via
    contextvars. Use .raw_objects only for cross-tenant admin operations.
    """

    church = models.ForeignKey(
        Church,
        on_delete=models.CASCADE,
        db_index=True,
        related_name="%(app_label)s_%(class)s_set",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    # Tenant-scoped default manager
    objects = ScopedChurchManager()

    # Unscoped escape hatch — use only in management commands / admin
    raw_objects = RawChurchManager()

    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=["church", "-created_at"]),
            models.Index(fields=["church", "is_active"]),
        ]


class OrderedChurchModel(ChurchOwnedModel):
    """
    Church-owned models that need explicit ordering (e.g. pipeline stages,
    service lists, unit roles).
    """

    order = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ["order"]


class SoftDeleteModel(BaseModel):
    """
    Mixin for soft deletion. Combine with ChurchOwnedModel or TimestampedModel
    when you need to hide records without permanently destroying them.

    Usage:
        class MyModel(ChurchOwnedModel, SoftDeleteModel): ...

    Always filter on is_deleted=False in your querysets unless you specifically
    need to surface deleted records.
    """

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
