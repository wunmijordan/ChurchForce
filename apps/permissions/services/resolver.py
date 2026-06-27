"""
permissions/services/resolver.py

Tiered permission resolver for ChurchForce.

Resolution order (first match wins):
  0. Superuser          → all permissions, all tenants
  1. Church Admin       → all permissions within their church
                          (ChurchMember.is_admin=True OR WorkforceRole.tier==1)
  2. Sub-Admin          → global-scope perms from WorkforceRole.tier==2
  3. Assistant Pastor   → global-scope perms from WorkforceRole.tier==3
  4-6. Unit roles       → unit-scoped only, never global bleed
  7. Custom             → explicit JSONField perms, unit-scoped only unless scope="global"

GLOBAL BLEED PREVENTION
───────────────────────
Only WorkforceRole with scope="global" AND tier <= TIER_ASSISTANT_PASTOR (3)
can grant global=True on a permission. Everything else (tiers 4-7, any UnitRole)
is silently unit-scoped even if the permissions JSONField contains global keys.
"""

from functools import cached_property
from django.core.exceptions import SynchronousOnlyOperation

from permissions.registry import (
    PERMISSIONS,
    TIER_ADMIN,
    TIER_SUB_ADMIN,
    TIER_ASSISTANT_PASTOR,
    TIER_OVERSEER,
    TIER_UNIT_HEAD,
    TIER_ASSISTANT,
    TIER_CUSTOM,
    TIER_LABELS,
    permissions_for_tier,
)


class PermissionResolver:
    """Resolves effective permissions for a user within a specific church."""

    def __init__(self, user, church):
        self.user = user
        self.church = church

    @cached_property
    def church_member(self):
        if not getattr(self, "user", None) or not getattr(self, "church", None):
            return None
        try:
            return (
                self.user.church_memberships.filter(church=self.church, is_active=True)
                .select_related("church")
                .first()
            )
        except SynchronousOnlyOperation:
            return None

    @cached_property
    def workforce_member(self):
        member = self.church_member
        if not member:
            return None
        return member.workforce_profiles.filter(is_active=True).first()

    @cached_property
    def _effective_global_tier(self):
        """
        Lowest (most powerful) global tier from the user's WorkforceRole assignments.
        Only scope='global' roles count. Returns None if no global role held.
        """
        wf = self.workforce_member
        if not wf:
            return None
        tiers = [
            a.role.tier
            for a in wf.roles.select_related("role").filter(is_active=True)
            if getattr(a.role, "scope", "unit") == "global"
        ]
        return min(tiers) if tiers else None

    @cached_property
    def permission_map(self):
        pmap = {}

        # 0. Superuser
        if self.user.is_superuser:
            pmap["*"] = {"global": True, "units": set()}
            return pmap

        # 1. Church Admin (is_admin flag OR WorkforceRole tier <= 1)
        is_admin_flag = bool(
            self.church_member and getattr(self.church_member, "is_admin", False)
        )
        global_tier = self._effective_global_tier
        if is_admin_flag or (global_tier is not None and global_tier <= TIER_ADMIN):
            pmap["*"] = {"global": True, "units": set()}
            return pmap

        wf = self.workforce_member
        if not wf:
            return pmap

        # Baseline: every active workforce member gets dashboard + clock globally
        for key in ("dashboard.view", "attendance.clock"):
            pmap.setdefault(key, {"global": False, "units": set()})["global"] = True

        memberships = list(
            wf.unit_memberships.filter(is_active=True)
            .select_related("unit")
            .prefetch_related("roles__role")
        )
        direct_unit_ids = {m.unit_id for m in memberships}

        # 2-3. WorkforceRole assignments (global-scope tiers 2 & 3)
        for assignment in wf.roles.select_related("role").filter(is_active=True):
            role = assignment.role
            role_tier = getattr(role, "tier", TIER_CUSTOM)
            role_scope = getattr(role, "scope", "unit")

            if role_scope == "global":
                if role_tier <= TIER_ASSISTANT_PASTOR:
                    # Tiers 1-3 with scope=global expand global perms
                    effective_perms = permissions_for_tier(role_tier)
                    for key, allowed in (role.permissions or {}).items():
                        effective_perms[key] = allowed
                    for key, allowed in effective_perms.items():
                        if not allowed:
                            continue
                        meta = PERMISSIONS.get(key, {})
                        entry = pmap.setdefault(key, {"global": False, "units": set()})
                        if meta.get("scope") == "global":
                            entry["global"] = True
                        else:
                            entry["units"].update(direct_unit_ids)
                else:
                    # Tier 7 with scope=global is a misconfiguration — demote
                    role_scope = "unit"

            if role_scope != "global":
                # Unit-scoped WorkforceRole: only grant unit-scope perms
                unit_only_perms = {
                    k: True
                    for k in permissions_for_tier(role_tier)
                    if PERMISSIONS.get(k, {}).get("scope") == "unit"
                }
                for key, allowed in (role.permissions or {}).items():
                    if PERMISSIONS.get(key, {}).get("scope") == "unit":
                        unit_only_perms[key] = allowed
                for key, allowed in unit_only_perms.items():
                    if not allowed:
                        continue
                    entry = pmap.setdefault(key, {"global": False, "units": set()})
                    entry["units"].update(direct_unit_ids)

        # 4-6. UnitMembership structural tiers + explicit UnitRole assignments
        for membership in memberships:
            unit_id = membership.unit_id

            # Structural tier from membership flags
            # TIER_OVERSEER (4) is expressed via a UnitRole with tier=4
            if membership.is_unit_head:
                structural_tier = TIER_UNIT_HEAD
            elif getattr(membership, "is_assistant", False):
                structural_tier = TIER_ASSISTANT
            else:
                structural_tier = None

            if structural_tier is not None:
                for key in permissions_for_tier(structural_tier):
                    if PERMISSIONS.get(key, {}).get("scope") == "unit":
                        entry = pmap.setdefault(key, {"global": False, "units": set()})
                        entry["units"].add(unit_id)

            # Explicit UnitRole assignments
            for role_assignment in membership.roles.all():
                role = role_assignment.role
                role_tier = getattr(role, "tier", TIER_CUSTOM)

                if role_tier <= TIER_ASSISTANT:
                    effective_perms = {
                        k: True
                        for k in permissions_for_tier(role_tier)
                        if PERMISSIONS.get(k, {}).get("scope") == "unit"
                    }
                else:
                    effective_perms = {}

                for key, allowed in (role.permissions or {}).items():
                    if PERMISSIONS.get(key, {}).get("scope") == "unit":
                        effective_perms[key] = allowed

                for key, allowed in effective_perms.items():
                    if not allowed:
                        continue
                    entry = pmap.setdefault(key, {"global": False, "units": set()})
                    entry["units"].add(unit_id)

        return pmap

    def can(self, permission, unit=None):
        if "*" in self.permission_map:
            return True
        data = self.permission_map.get(permission)
        if not data:
            return False
        if data["global"]:
            return True
        if unit:
            return unit.id in data["units"]
        return bool(data["units"])

    def get_tier(self):
        if self.user.is_superuser:
            return 0
        if "*" in self.permission_map:
            return TIER_ADMIN
        return self._effective_global_tier

    def get_tier_label(self):
        t = self.get_tier()
        return TIER_LABELS.get(t, "Member") if t is not None else "Member"

    def unit_ids_for(self, permission):
        from units.models import ChurchUnit

        if "*" in self.permission_map:
            return set(
                ChurchUnit.raw_objects.filter(
                    church=self.church, is_active=True
                ).values_list("id", flat=True)
            )
        data = self.permission_map.get(permission)
        if not data:
            return set()
        if data["global"]:
            return set(
                ChurchUnit.raw_objects.filter(
                    church=self.church, is_active=True
                ).values_list("id", flat=True)
            )
        return set(data["units"])

    def managed_unit_ids(self):
        return self.unit_ids_for("unit.manage_members")

    def is_unit_manager(self, unit):
        return self.can("unit.manage_members", unit=unit)

    def effective_permissions_for_unit(self, unit):
        result = {}
        if "*" in self.permission_map:
            return {k: True for k in PERMISSIONS}
        for key, data in self.permission_map.items():
            if data["global"] or unit.id in data["units"]:
                result[key] = True
        return result
