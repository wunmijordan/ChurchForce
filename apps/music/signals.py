"""
music/signals.py

Auto-sync: when an Event with event_type="Rehearsal" is created or updated,
ensure a corresponding RehearsalSession exists in the music module.

This keeps Event (the scheduling truth) and RehearsalSession (the music
module's operational record) in sync without either app depending on the
other's internal save logic.
"""

import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_save, sender="services.Event")
def sync_rehearsal_session_on_event_save(sender, instance, created, **kwargs):
    """
    When a Rehearsal event is created or updated:
      - create a RehearsalSession if none exists yet
      - update unit FK if the event's unit changed

    When a non-Rehearsal event is saved: no-op.
    """
    if instance.event_type != "Rehearsal":
        return

    from music.models import RehearsalSession

    session, was_created = RehearsalSession.raw_objects.get_or_create(
        church=instance.church,
        event=instance,
        defaults={
            "unit": instance.unit,
            "notes": "",
            "completed": False,
        },
    )

    # If the event's unit changed, keep the session in sync
    if not was_created and session.unit != instance.unit:
        session.unit = instance.unit
        session.save(update_fields=["unit"])

    if was_created:
        logger.info(
            "RehearsalSession auto-created for Event '%s' (church=%s)",
            instance.name,
            instance.church_id,
        )


@receiver(post_delete, sender="services.Event")
def cleanup_rehearsal_session_on_event_delete(sender, instance, **kwargs):
    """
    RehearsalSession has event FK with CASCADE, so deletion is automatic.
    This signal is here as a hook for any future cleanup logic (e.g. logging).
    """
    if instance.event_type == "Rehearsal":
        logger.info(
            "Event '%s' deleted — associated RehearsalSession will cascade.",
            instance.name,
        )
