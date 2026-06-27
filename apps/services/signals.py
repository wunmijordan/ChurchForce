# In apps/services/signals.py  (create file if it doesn't exist)
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender="services.Event")
def sync_lms_session(sender, instance, created, **kwargs):
    """
    When a Training event with lms_course is saved, ensure an LMSSession
    exists that links them. This is the bridge between scheduling an event
    and the LMS attendance gate.
    """
    if instance.event_type != "Training" or not instance.lms_course_id:
        return

    try:
        from lms.models import LMSSession

        LMSSession.raw_objects.get_or_create(
            church=instance.church,
            event=instance,
            defaults={
                "course_id": instance.lms_course_id,
                "title": instance.name,
                "notes": "",
                "is_active": True,
            },
        )
    except Exception:
        pass  # Never break event save due to LMS side-effect
