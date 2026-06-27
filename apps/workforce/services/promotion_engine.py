# workforce/services/promotion_engine.py

from django.db import transaction


@transaction.atomic
def attempt_final_promotion(trainee, *, triggered_by=None):
    """
    Promote trainee IF AND ONLY IF all conditions are satisfied.
    Safe to call repeatedly.
    """

    from workforce.services.readiness import evaluate_trainee_readiness
    from guests.pipeline import finalize_induction_from_trainee

    # Refresh truth snapshot
    snapshot = evaluate_trainee_readiness(trainee)

    if not snapshot.is_ready:
        return None

    application = trainee.get_active_application()

    if not application:
        return None

    # Already promoted guard
    if application.status == application.STATUS_INDUCTED:
        return None

    return finalize_induction_from_trainee(
        application=application,
        inducted_by=triggered_by,
    )
