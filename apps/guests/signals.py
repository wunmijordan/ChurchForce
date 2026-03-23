from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender="guests.MembershipApplication")
def create_trainee_profile_on_induction(sender, instance, **kwargs):
    """
    When a MembershipApplication transitions to 'inducted' status,
    auto-create a WorkforceTraineeProfile for the new ChurchMember.

    This gives the new member access to:
        - The induction LMS course
        - Church-wide notices
        - Their preferred unit (probationary)

    They are upgraded to WorkforceMember on LMS completion.
    """
    if instance.status != "inducted" or not instance.inducted_member:
        return

    try:
        from workforce.models import WorkforceTraineeProfile
        from lms.models import LMSCourse, LMSEnrollment

        church = instance.church
        member = instance.inducted_member
        preferred_unit = instance.preferred_unit

        # Create trainee profile
        trainee, created = WorkforceTraineeProfile.raw_objects.get_or_create(
            church=church,
            member=member,
            defaults={
                "reason":         "induction",
                "preferred_unit": preferred_unit,
                "is_active":      True,
            },
        )

        # Auto-enroll in the induction LMS course for this track
        if created:
            induction_course = LMSCourse.raw_objects.filter(
                church=church,
                course_type="induction",
                membership_track=instance.track,
                is_active=True,
            ).first()

            if induction_course:
                first_module = induction_course.modules.filter(
                    is_active=True
                ).order_by("order").first()

                enrollment, _ = LMSEnrollment.raw_objects.get_or_create(
                    church=church,
                    member=member,
                    course=induction_course,
                    defaults={
                        "status":         "active",
                        "current_module": first_module,
                        "is_active":      True,
                    },
                )
                # Link enrollment to trainee profile
                trainee.lms_enrollment = enrollment
                trainee.save(update_fields=["lms_enrollment"])

    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(
            "create_trainee_profile_on_induction failed for application %s: %s",
            instance.pk, exc,
        )
