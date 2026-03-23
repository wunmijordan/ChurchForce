from django.db import models
from core.request_context import get_current_user, get_current_church


class PermissionQuerySet(models.QuerySet):
    """
    Base queryset aware of tenant + permission system.
    """

    permission_view = None       # override per model
    permission_manage = None     # optional

    # -----------------------------------
    # Tenant Scope
    # -----------------------------------

    def for_church(self, church):

        if church is None:
            return self.none()

        return self.filter(church=church)

    def for_request(self, request):
        return self.for_church(getattr(request, "church", None))

    # -----------------------------------
    # Generic helpers
    # -----------------------------------

    def active(self):

        if hasattr(self.model, "is_active"):
            return self.filter(is_active=True)

        return self

    # -----------------------------------
    # Permission Visibility Engine
    # -----------------------------------

    def visible_to(self, user, church=None):

        if not user.is_authenticated:
            return self.none()

        qs = self

        if church:
            qs = qs.for_church(church)

        if user.is_superuser:
            return qs

        # No permission configured → fallback tenant scope
        if not self.permission_view:
            return qs

        resolver = user.permissions(church)

        # global permission
        if resolver.can(self.permission_view):
            return qs

        # unit-scoped permission
        unit_ids = resolver.permission_map.get(
            self.permission_view, {}
        ).get("units", set())

        if not unit_ids:
            return qs.none()

        if hasattr(self.model, "unit_id"):
            return qs.filter(unit_id__in=unit_ids)

        return qs.none()
    

class ChurchQuerySet(PermissionQuerySet):
    def auto_scope(self):
        user = get_current_user()
        church = get_current_church()

        if not user or not church:
            return self.all()

        # If it's an admin/staff in the Django Admin, just filter by Church
        # This prevents the 'visible_to' logic from breaking Admin fields
        if user.is_staff or user.is_superuser:
            return self.for_church(church)

        return self.visible_to(user, church)


class GuestQuerySet(ChurchQuerySet):

    permission_view = "guests.view"

    def visible_to(self, user, church):

        if not user.is_authenticated:
            return self.none()

        qs = self.for_church(church)

        if user.is_superuser:
            return qs

        resolver = user.permissions(church)

        # Global permission
        if resolver.can("guests.view"):
            return qs

        # Unit-scoped permission
        unit_ids = resolver.permission_map.get(
            "guests.view", {}
        ).get("units", set())

        if unit_ids:
            return qs.filter(assigned_to__unit_id__in=unit_ids)

        # Personal fallback (assigned to this workforce member)
        wf = resolver.workforce_member
        if not wf:
            return qs.none()

        return qs.filter(assigned_to__workforce_member=wf)

    # -----------------------------
    # Helpers
    # -----------------------------

    def assigned_to(self, workforce_member):
        return self.filter(assigned_to__workforce_member=workforce_member)

    def recent(self):
        return self.order_by("-created_at")
    

class MessageQuerySet(ChurchQuerySet):

    permission_view = "chat.view"

    def visible_to(self, user, church):

        qs = super().visible_to(user, church)

        resolver = user.permissions(church)
        wf = resolver.workforce_member

        if not wf:
            return qs.none()

        return qs.filter(
            conversation__participants__member=wf.member
        ).distinct()
    

class NotificationQuerySet(ChurchQuerySet):

    def visible_to(self, user, church):

        if not user.is_authenticated:
            return self.none()

        qs = self.for_church(church)

        if user.is_superuser:
            return qs

        return qs.filter(member__user=user)

    def unread(self):
        return self.filter(is_read=False)
