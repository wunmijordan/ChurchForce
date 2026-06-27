from django.db import models
from .querysets import ChurchQuerySet, GuestQuerySet, NotificationQuerySet


class ScopedChurchManager(
    models.Manager.from_queryset(ChurchQuerySet)
):
    def get_queryset(self):
        qs = super().get_queryset()

        # Only auto-scope when explicitly enabled
        try:
            return qs.auto_scope()
        except Exception:
            return qs


class RawChurchManager(
    models.Manager.from_queryset(ChurchQuerySet)
):
    def get_queryset(self):
        return super().get_queryset()


class ScopedGuestManager(
    models.Manager.from_queryset(GuestQuerySet)
):
    def get_queryset(self):
        qs = super().get_queryset()

        # Only auto-scope when explicitly enabled
        try:
            return qs.auto_scope()
        except Exception:
            return qs


class RawGuestManager(
    models.Manager.from_queryset(GuestQuerySet)
):
    def get_queryset(self):
        return super().get_queryset()


class ScopedNotificationManager(
    models.Manager.from_queryset(NotificationQuerySet)
):
    def get_queryset(self):
        qs = super().get_queryset()

        # Only auto-scope when explicitly enabled
        try:
            return qs.auto_scope()
        except Exception:
            return qs


class RawNotificationManager(
    models.Manager.from_queryset(NotificationQuerySet)
):
    def get_queryset(self):
        return super().get_queryset()
