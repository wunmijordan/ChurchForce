from django.db import transaction


@transaction.atomic
def evaluate_trainee_progress(trainee):
    """
    Evaluate trainee readiness.

    DOES NOT promote.
    Creates leader approval task instead.
    """

    from workforce.models import (
        TraineePromotionEvaluation,
        LeaderTask,
    )

    if not trainee.is_active:
        return None

    # ── Requirement checks ─────────────────────────────

    lms_completed = False
    probation_completed = False

    if trainee.reason == "induction":
        if trainee.lms_enrollment:
            lms_completed = trainee.lms_enrollment.status in (
                "passed",
                "certified",
            )

    if trainee.reason == "probation":
        from django.utils import timezone

        if trainee.probation_ends_at:
            probation_completed = (
                timezone.now().date() >= trainee.probation_ends_at
            )

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

    return evaluation