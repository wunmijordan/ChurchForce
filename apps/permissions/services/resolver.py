from functools import cached_property


class PermissionResolver:
    """
    Resolves permissions for a user within a specific church tenant.

    Builds a permission_map from:
        1. WorkforceRole assignments  — global permissions (church-wide)
        2. UnitRole assignments       — unit-scoped permissions

    The map structure:
        {
            "permission.key": {
                "global": True/False,   # True = allowed anywhere in the church
                "units":  {unit_id, …}  # unit IDs where this permission applies
            }
        }

    Usage:
        resolver = PermissionResolver(user, church)
        resolver.can("guests.view")               # global or any unit
        resolver.can("guests.view", unit=unit_obj) # scoped to specific unit

    Instances are created lazily by ChurchContextMiddleware and cached
    on request.permissions for the duration of the request.
    """

    def __init__(self, user, church):
        self.user   = user
        self.church = church

    # ── Membership resolution ─────────────────────────────────────────

    @cached_property
    def church_member(self):
        """ChurchMember for this user in this church, or None."""
        return (
            self.user.church_memberships
            .filter(church=self.church, is_active=True)
            .select_related("church")
            .first()
        )

    @cached_property
    def workforce_member(self):
        """WorkforceMember profile for this church member, or None."""
        member = self.church_member
        if not member:
            return None
        return (
            member.workforce_profiles
            .filter(is_active=True)
            .first()
        )

    # ── Permission map builder ────────────────────────────────────────

    @cached_property
    def permission_map(self):
        """
        Build and cache the full permission map for this user+church.
        Called at most once per request via cached_property.
        """
        permission_map = {}

        # Superuser bypasses all permission checks
        if self.user.is_superuser:
            permission_map["*"] = {"global": True, "units": set()}
            return permission_map

        # Church admin bypasses all permission checks (unit-agnostic)
        if self.church_member and getattr(self.church_member, "is_admin", False):
            permission_map["*"] = {"global": True, "units": set()}
            return permission_map

        wf = self.workforce_member
        if not wf:
            return permission_map

        # ── Global workforce role permissions ─────────────────────────
        for assignment in wf.roles.select_related("role").filter(is_active=True):
            for perm, allowed in (assignment.role.permissions or {}).items():
                if not allowed:
                    continue
                entry = permission_map.setdefault(perm, {"global": False, "units": set()})
                entry["global"] = True

        # ── Unit-scoped role permissions ──────────────────────────────
        memberships = (
            wf.unit_memberships
            .filter(is_active=True)
            .select_related("unit")
            .prefetch_related("roles__role")
        )

        for membership in memberships:
            unit_id = membership.unit_id
            for role_assignment in membership.roles.all():
                for perm, allowed in (role_assignment.role.permissions or {}).items():
                    if not allowed:
                        continue
                    entry = permission_map.setdefault(perm, {"global": False, "units": set()})
                    entry["units"].add(unit_id)

        return permission_map

    # ── Permission check ──────────────────────────────────────────────

    def can(self, permission, unit=None):
        """
        Check whether the user has the given permission.

        Args:
            permission: dot-separated permission string e.g. "guests.view"
            unit:       optional ChurchUnit — if provided, also checks
                        unit-scoped permission for that specific unit.

        Returns:
            True if the user has the permission globally or in the
            specified unit (or any unit if unit is None).
        """
        # Superuser
        if "*" in self.permission_map:
            return True

        data = self.permission_map.get(permission)
        if not data:
            return False

        # Global permission
        if data["global"]:
            return True

        # Unit-scoped check
        if unit:
            return unit.id in data["units"]

        # Has permission in at least one unit
        return bool(data["units"])

    # ── Convenience helpers ───────────────────────────────────────────

    def unit_ids_for(self, permission):
        """
        Return the set of unit IDs where the user has the given permission.
        Returns an empty set if the permission is not granted anywhere.
        """
        if "*" in self.permission_map:
            # Superuser — return all unit IDs for this church
            from units.models import ChurchUnit
            return set(
                ChurchUnit.raw_objects.filter(church=self.church, is_active=True)
                .values_list("id", flat=True)
            )

        data = self.permission_map.get(permission)
        if not data:
            return set()
        if data["global"]:
            from units.models import ChurchUnit
            return set(
                ChurchUnit.raw_objects.filter(church=self.church, is_active=True)
                .values_list("id", flat=True)
            )
        return data["units"]

