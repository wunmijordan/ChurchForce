from django.db.models.signals import post_save
from django.dispatch import receiver


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