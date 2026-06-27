from django.db.models import Q
from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver
from django.utils import timezone

from .models import LMSCourse, LMSEnrollment, LMSModule
from .services import (
    sync_course_audience,
    sync_empty_current_modules,
    sync_member_courses,
    sync_unit_membership_courses,
)
from guests.models import ApplicationStepProgress, StepRequirement
from services.models import Event


@receiver(post_save, sender=LMSEnrollment)
def complete_steps_from_lms(sender, instance, **kwargs):
    if instance.status != "passed":
        return

    requirements = StepRequirement.objects.filter(
        requirement_type=StepRequirement.TYPE_LMS,
        church=instance.church,
    ).filter(Q(lms_course=instance.course) | Q(lms_courses=instance.course)).distinct()

    for req in requirements:
        ApplicationStepProgress.objects.filter(
            step__requirement=req,
            application__inducted_member=instance.member,
            completed=False,
        ).update(
            completed=True,
            completed_at=timezone.now(),
        )


@receiver(post_save, sender=LMSCourse)
def sync_enrollments_for_course_audience(sender, instance, **kwargs):
    sync_course_audience(instance)


@receiver(post_save, sender=LMSModule)
def sync_enrollment_current_module_for_new_module(sender, instance, **kwargs):
    if getattr(instance, "is_active", True):
        sync_empty_current_modules(instance.course)


@receiver(post_save, sender="workforce.WorkforceMember")
def sync_workforce_courses_for_new_member(sender, instance, **kwargs):
    if getattr(instance, "is_active", True):
        sync_member_courses(instance.member, instance.church)


@receiver(post_save, sender="units.UnitMembership")
def sync_unit_courses_for_membership(sender, instance, **kwargs):
    if getattr(instance, "is_active", True):
        sync_unit_membership_courses(instance)


@receiver(post_save, sender="services.Event")
def sync_lms_session_on_event_save(sender, instance, **kwargs):
    """
    Auto-create or update an LMSSession whenever a Training event is saved
    with an lms_course set.  If the course is removed, the session is deleted.
    """
    if instance.event_type != "Training" or not instance.lms_course_id:
        # If a Training event had its course removed, clean up the session
        try:
            instance.lms_session.delete()
        except Exception:
            pass
        return

    from lms.models import LMSSession

    session, created = LMSSession.raw_objects.get_or_create(
        church=instance.church,
        event=instance,
        defaults={
            "course": instance.lms_course,
            "title": instance.name,
            "notes": "",
            "is_active": True,
        },
    )
    update_fields = []
    if session.course_id != instance.lms_course_id:
        session.course = instance.lms_course
        update_fields.append("course")
    if session.title != instance.name:
        session.title = instance.name
        update_fields.append("title")
    if not getattr(session, "is_active", True):
        session.is_active = True
        update_fields.append("is_active")
    if update_fields:
        session.save(update_fields=update_fields)

    # Sync modules_covered M2M from Event → LMSSession.
    # This is called AFTER save() so Event.modules_covered M2M is already set
    # (form.save_m2m() is called before _sync_session_modules() in views).
    # We also handle it here for any programmatic saves.
    try:
        session.modules_covered.set(instance.modules_covered.all())
    except Exception:
        pass


@receiver(m2m_changed, sender=Event.modules_covered.through)
def sync_lms_session_modules_on_m2m_change(sender, instance, action, pk_set, **kwargs):
    """
    Keep LMSSession.modules_covered in sync whenever Event.modules_covered
    is changed via the M2M manager (add/remove/clear/set).
    This handles programmatic changes outside of the form flow.
    """
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    if not isinstance(instance, type) and hasattr(instance, "lms_session"):
        if instance.event_type != "Training":
            return
        try:
            session = instance.lms_session
            if session:
                session.modules_covered.set(instance.modules_covered.all())
        except Exception:
            pass


@receiver(post_delete, sender="services.Event")
def deactivate_lms_session_on_event_delete(sender, instance, **kwargs):
    if instance.event_type != "Training":
        return
    try:
        instance.lms_session.delete()
    except Exception:
        return
