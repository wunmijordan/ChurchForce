from django.db.models.signals import post_save
from django.dispatch import receiver

MODULE_PERMISSIONS = {
    "guest_management": {
        "guests.view": True,
        "guests.view_all": True,
        "guests.create": True,
        "guests.manage_all": True,
        "guests.manage_unassigned": True,
        "guests.update_status": True,
        "guests.assign_all": True,
        "guests.assign_unit": True,
        "guests.report": True,
        "guests.delete": True,
        "guests.import": True,
        "guests.export": True,
    },
    "music_module": {
        "music.view": True,
        "music.manage": True,
    },
    "media_module": {
        "media.view": True,
        "media.manage": True,
    },
    "children_module": {
        "children.view": True,
        "children.manage": True,
    },
    "youth_module": {
        "youth.view": True,
        "youth.manage": True,
    },
    "teenagers_module": {
        "teenagers.view": True,
        "teenagers.manage": True,
    },
}

UNIT_BASE_PERMISSIONS = {"chat.view": True}
UNIT_HEAD_PERMISSIONS = {"chat.manage": True}


def _unit_permissions(unit):
    perms = dict(UNIT_BASE_PERMISSIONS)
    for field, pset in MODULE_PERMISSIONS.items():
        if getattr(unit, field, False):
            perms.update(pset)
    return perms


@receiver(post_save, sender="units.ChurchUnit")
def create_default_chat_room(sender, instance, created, **kwargs):
    """
    Auto-create a default ChatRoom for a new ChurchUnit.
    The room name mirrors the unit name.
    """
    if not created:
        return
    from units.models import ChatRoom
    ChatRoom.raw_objects.get_or_create(
        church=instance.church,
        unit=instance,
        is_default=True,
        defaults={
            "name":      instance.name,
            "room_type": "unit",
            "is_active": True,
        },
    )


@receiver(post_save, sender="units.ChurchUnit")
def ensure_unit_roles(sender, instance, **kwargs):
    """
    Ensure default UnitRole records exist for this unit and keep
    permissions in sync with module flags.
    """
    from permissions.models import UnitRole

    base_perms = _unit_permissions(instance)
    roles = [
        ("Member", False, 2, base_perms),
        ("Unit Head", True, 1, {**base_perms, **UNIT_HEAD_PERMISSIONS}),
    ]

    for name, is_leadership, order, perms in roles:
        UnitRole.raw_objects.update_or_create(
            church=instance.church,
            unit=instance,
            name=name,
            defaults={
                "permissions": perms,
                "is_leadership": is_leadership,
                "order": order,
                "is_active": True,
            },
        )


@receiver(post_save, sender="units.UnitMembership")
def ensure_membership_roles(sender, instance, **kwargs):
    """
    Auto-assign the unit's default roles to new memberships so
    module access is granted by membership.
    """
    if not instance.is_active:
        return
    from permissions.models import UnitRole, MembershipRole

    member_role = UnitRole.raw_objects.filter(
        church=instance.church, unit=instance.unit, name__iexact="member"
    ).first()
    if member_role:
        MembershipRole.raw_objects.get_or_create(
            church=instance.church, membership=instance, role=member_role
        )

    head_role = UnitRole.raw_objects.filter(
        church=instance.church, unit=instance.unit, name__iexact="unit head"
    ).first()
    if head_role:
        if instance.is_unit_head:
            MembershipRole.raw_objects.get_or_create(
                church=instance.church, membership=instance, role=head_role
            )
        else:
            MembershipRole.raw_objects.filter(
                church=instance.church, membership=instance, role=head_role
            ).delete()



