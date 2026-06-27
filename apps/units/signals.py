"""
units/signals.py

Post-save signals for ChurchUnit and UnitMembership.

ensure_unit_roles:
    Creates default tier-3 (Unit Head) and tier-5 (Member) UnitRole records
    for every new or updated unit, with permissions derived from the registry.
    Module flags (guest_management, music_module, etc.) add extra permission keys.

ensure_membership_roles:
    Auto-assigns the tier-5 Member UnitRole to every new active UnitMembership,
    so module access is granted by membership alone.
    If is_unit_head=True, also assigns the tier-3 Unit Head UnitRole.
    If is_assistant=True, also assigns the tier-4 Assistant UnitRole.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

# Module-specific permission keys that are granted when the unit has
# that module enabled. These supplement the registry-based tier defaults.
MODULE_PERMISSIONS = {
    "guest_management": {
        "guests.view": True,
        "guests.report": True,
    },
    "music_module": {"music.view": True},
    "media_module": {"media.view": True},
    "children_module": {"children.view": True},
    "youth_module": {"youth.view": True},
    "teenagers_module": {"teenagers.view": True},
}

# Base permissions every unit member gets (regardless of module flags)
UNIT_BASE_PERMISSIONS = {
    "chat.view": True,
    "attendance.clock": True,
    "training.view": True,
    "dashboard.view": True,
}

# Additional permissions for unit management tier
UNIT_HEAD_EXTRA_PERMISSIONS = {
    "chat.manage": True,
    "unit.manage_members": True,
    "attendance.mark": True,
    "attendance.view": True,
    "events.manage_unit": True,
    "training.manage": True,
    "training.approve_absence": True,
    "training.conduct_interview": True,
    "messaging.send": True,
}

UNIT_ASSISTANT_EXTRA_PERMISSIONS = {
    "attendance.mark": True,
    "attendance.view": True,
    "training.approve_absence": True,
}


def _unit_permissions(unit, extra=None):
    """Build the permission dict for a unit, merging module flags and extras."""
    perms = dict(UNIT_BASE_PERMISSIONS)
    for field, pset in MODULE_PERMISSIONS.items():
        if getattr(unit, field, False):
            perms.update(pset)
    if extra:
        perms.update(extra)
    return perms


@receiver(post_save, sender="units.ChurchUnit")
def create_default_chat_room(sender, instance, created, **kwargs):
    """Auto-create a default ChatRoom for a new ChurchUnit."""
    if not created:
        return
    from units.models import ChatRoom

    ChatRoom.raw_objects.get_or_create(
        church=instance.church,
        unit=instance,
        is_default=True,
        defaults={
            "name": instance.name,
            "room_type": "unit",
            "is_active": True,
        },
    )


@receiver(post_save, sender="units.ChurchUnit")
def ensure_unit_roles(sender, instance, **kwargs):
    """
    Ensure tier-appropriate UnitRole records exist for this unit
    and keep permissions in sync with module flags.

    Roles created (by tier, not by name):
        tier 3 — Unit Head:     full unit management + module access
        tier 4 — Assistant:     limited write + module access
        tier 5 — Member:        base access + module access

    Admins can rename these roles freely. Tier is authoritative.
    """
    from permissions.models import UnitRole
    from permissions.registry import (
        TIER_OVERSEER,
        TIER_UNIT_HEAD,
        TIER_ASSISTANT,
        TIER_CUSTOM,
    )

    roles_spec = [
        # (tier, default_name, order, extra_perms)
        (TIER_OVERSEER, "Overseer", 1, UNIT_HEAD_EXTRA_PERMISSIONS),
        (TIER_UNIT_HEAD, "Unit Head", 2, UNIT_HEAD_EXTRA_PERMISSIONS),
        (TIER_ASSISTANT, "Assistant", 3, UNIT_ASSISTANT_EXTRA_PERMISSIONS),
        (TIER_CUSTOM, "Member", 4, {}),
    ]

    for tier, default_name, order, extra in roles_spec:
        perms = _unit_permissions(instance, extra)

        # Check if a role with this tier already exists for this unit
        existing = UnitRole.raw_objects.filter(
            church=instance.church, unit=instance, tier=tier
        ).first()

        if existing:
            # Refresh permissions in case module flags changed
            existing.permissions = perms
            existing.save(update_fields=["permissions"])
        else:
            UnitRole.raw_objects.create(
                church=instance.church,
                unit=instance,
                name=default_name,
                tier=tier,
                permissions=perms,
                is_leadership=(tier in (TIER_OVERSEER, TIER_UNIT_HEAD, TIER_ASSISTANT)),
                order=order,
                is_active=True,
            )


@receiver(post_save, sender="units.UnitMembership")
def ensure_membership_roles(sender, instance, **kwargs):
    """
    Auto-assign UnitRoles to memberships based on tier and structural flags.

    - Every active member gets the tier-5 (Member) role.
    - is_unit_head=True  → also gets the tier-3 (Unit Head) role.
    - is_assistant=True  → also gets the tier-4 (Assistant) role.
    - is_unit_head=False → tier-3 role removed if present.
    - is_assistant=False → tier-4 role removed if present.
    """
    if not instance.is_active:
        return

    from permissions.models import UnitRole, MembershipRole
    from permissions.registry import TIER_UNIT_HEAD, TIER_ASSISTANT, TIER_CUSTOM

    church = instance.church
    unit = instance.unit

    def _get_role(tier):
        return UnitRole.raw_objects.filter(
            church=church, unit=unit, tier=tier, is_active=True
        ).first()

    def _assign(role):
        if role:
            MembershipRole.raw_objects.get_or_create(
                church=church, membership=instance, role=role
            )

    def _revoke(tier):
        role = _get_role(tier)
        if role:
            MembershipRole.raw_objects.filter(
                church=church, membership=instance, role=role
            ).delete()

    # Always assign base member role
    _assign(_get_role(TIER_CUSTOM))

    # Unit head
    if instance.is_unit_head:
        _assign(_get_role(TIER_UNIT_HEAD))
    else:
        _revoke(TIER_UNIT_HEAD)

    # Assistant
    is_assistant = getattr(instance, "is_assistant", False)
    if is_assistant:
        _assign(_get_role(TIER_ASSISTANT))
    else:
        _revoke(TIER_ASSISTANT)
