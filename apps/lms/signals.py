from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import LMSEnrollment
from guests.models import ApplicationStepProgress, StepRequirement


@receiver(post_save, sender=LMSEnrollment)
def complete_steps_from_lms(sender, instance, **kwargs):

    if instance.status != "passed":
        return

    requirements = StepRequirement.objects.filter(
        requirement_type=StepRequirement.TYPE_LMS,
        lms_course=instance.course,
        church=instance.church,
    )

    for req in requirements:

        ApplicationStepProgress.objects.filter(
            step__requirement=req,
            application__inducted_member=instance.member,
            completed=False,
        ).update(
            completed=True,
            completed_at=timezone.now(),
        )