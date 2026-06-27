from django.db import transaction


@transaction.atomic
def evaluate_trainee_progress(trainee):
    """
    Evaluate trainee readiness.

    DOES NOT promote directly.
    Creates leader approval task instead.
    """

    from workforce.models import (
        TraineePromotionEvaluation,
        LeaderTask,
    )
    from workforce.services.promotion_engine import attempt_final_promotion
    from guests.models import MembershipApplication

    if not trainee.is_active:
        return None

    # ── Requirement checks ─────────────────────────────

    lms_completed = False
    probation_completed = False
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

    if trainee.reason == "induction":
        if application and not application.track.requires_lms_training():
            lms_completed = True
        elif trainee.lms_enrollment:
            lms_completed = trainee.lms_enrollment.status in (
                "passed",
                "certified",
            )

    if trainee.reason == "probation":
        from django.utils import timezone

        if trainee.probation_ends_at:
            probation_completed = timezone.now().date() >= trainee.probation_ends_at

    all_met = lms_completed or probation_completed

    evaluation = TraineePromotionEvaluation.raw_objects.create(
        church=trainee.church,
        trainee=trainee,
        lms_completed=lms_completed,
        probation_completed=probation_completed,
        all_requirements_met=all_met,
    )

    # ── Create approval task ───────────────────────────

    if all_met:
        LeaderTask.raw_objects.get_or_create(
            church=trainee.church,
            trainee=trainee,
            task_type="promotion_review",
            is_active=True,
            defaults={
                "title": f"Approve promotion for {trainee.member}",
                "description": "All trainee requirements satisfied.",
            },
        )

    # ✅ FINAL STEP — orchestration check
    # Safe to call repeatedly (idempotent)
    attempt_final_promotion(trainee)

    return evaluation
