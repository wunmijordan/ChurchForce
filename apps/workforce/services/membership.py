"""
workforce/services/membership.py

Checks whether all required MembershipTrackStep records have been
completed for a given ChurchMember's active induction application.

Called by evaluate_trainee_readiness() in readiness.py.
"""


def check_membership_steps(member):
    """
    Return True if all required ApplicationStepProgress records
    for this member's active induction application are completed.

    Returns True if:
        - No application exists (not step-gated)
        - All required steps are marked complete
    Returns False if any required step is still pending.
    """
    try:
        from guests.models import MembershipApplication, ApplicationStepProgress

        application = (
            MembershipApplication.raw_objects.filter(
                church=member.church,
                guest__converted_to=member,
                status__in=[
                    MembershipApplication.STATUS_TRAINING,
                    MembershipApplication.STATUS_PASSED,
                ],
                is_active=True,
            )
            .order_by("-applied_at")
            .first()
        )

        if not application:
            return True  # No application — not step-gated

        pending_required = ApplicationStepProgress.raw_objects.filter(
            church=member.church,
            application=application,
            step__is_required=True,
            completed=False,
            is_active=True,
        ).exists()

        return not pending_required

    except Exception:
        return True  # Fail open — don't block promotion on service errors
