# workforce/services/readiness.py

from django.utils import timezone
from workforce.services.membership import check_membership_steps
from workforce.services.approvals import check_required_approvals

def evaluate_trainee_readiness(trainee):
    from workforce.models import WorkforceReadinessSnapshot
    from guests.models import MembershipApplication

    application = (
        MembershipApplication.raw_objects.filter(
            church=trainee.church,
            inducted_member=trainee.member,
            status__in=(
                MembershipApplication.STATUS_TRAINING,
                MembershipApplication.STATUS_PASSED,
            ),
            is_active=True,
        )
        .select_related("track")
        .first()
    )

    lms_ok = True
    if application and application.track.requires_lms_training():
        lms_ok = bool(
            trainee.lms_enrollment
            and trainee.lms_enrollment.is_completed
        )

    probation_ok = (
        not trainee.probation_ends_at
        or trainee.probation_ends_at <= timezone.now().date()
    )

    steps_ok = check_membership_steps(trainee.member)
    approval_ok = check_required_approvals(trainee)

    ready = all([
        lms_ok,
        probation_ok,
        steps_ok,
        approval_ok,
    ])

    snapshot, _ = WorkforceReadinessSnapshot.objects.update_or_create(
        trainee=trainee,
        defaults={
            "lms_completed": lms_ok,
            "probation_complete": probation_ok,
            "required_steps_completed": steps_ok,
            "approval_received": approval_ok,
            "is_ready": ready,
        },
    )

    return snapshot
