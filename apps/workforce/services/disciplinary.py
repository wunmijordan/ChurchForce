"""
workforce/services/disciplinary.py

Disciplinary probation downgrade and restoration services.

INDUCTION PROBATION FLOW:
    Guest completes training → promote_to_workforce()
        → WorkforceMember created (stage=Probationer)
        → Admin assigns probation_unit via assign_probation_unit()
        → Member serves probation in that unit (is_probation=True on UnitMembership)
        → On probation_ends_at: complete_probation() auto-moves member to preferred_unit

DISCIPLINARY PROBATION FLOW:
    Admin calls demote_to_probation(workforce_member, probation_unit, ends_at)
        → WorkforceMember.stage → Probationer
        → WorkforceTraineeProfile created (reason='probation')
          with original_unit = member's current primary unit
        → New UnitMembership in probation_unit (is_probation=True)
        → Original UnitMembership deactivated (or kept for record)
        → On probation_ends_at: complete_probation() restores to original_unit
"""

from django.db import transaction
from django.utils import timezone


@transaction.atomic
def assign_probation_unit(trainee, probation_unit, assigned_by=None):
    """
    Admin assigns a unit for an inductee to serve their probation in.

    Called after promote_to_workforce() — gives the newly promoted WorkforceMember
    a unit assignment for the probation period.

    The member's preferred_unit (from the interest form) is NOT assigned here —
    it's where they move AFTER probation ends.
    """
    from units.models import UnitMembership

    wf = trainee.member.workforce_profiles.filter(
        church=trainee.church, is_active=True
    ).first()
    if not wf:
        raise ValueError(f"No active WorkforceMember found for {trainee.member}")

    # Update trainee profile with probation unit
    trainee.probation_unit = probation_unit
    trainee.save(update_fields=["probation_unit"])

    # Create or update the probationary UnitMembership
    membership, created = UnitMembership.raw_objects.get_or_create(
        church=trainee.church,
        workforce_member=wf,
        unit=probation_unit,
        defaults={"is_probation": True, "is_active": True},
    )
    if not created and not membership.is_probation:
        membership.is_probation = True
        membership.save(update_fields=["is_probation"])

    return membership


@transaction.atomic
def demote_to_probation(
    workforce_member, probation_unit, ends_at, demoted_by=None, notes=""
):
    """
    Disciplinary demotion: downgrade a WorkforceMember to probation.

    Steps:
    1. Stage → Probationer
    2. Record their current primary unit as original_unit
    3. Create WorkforceTraineeProfile(reason='probation', original_unit=...)
    4. Deactivate all current UnitMemberships (or mark is_probation=True)
    5. Create new UnitMembership in probation_unit (is_probation=True)
    6. Send SMS notification

    Returns the new WorkforceTraineeProfile.
    """
    from workforce.models import WorkforceStage, WorkforceTraineeProfile
    from units.models import UnitMembership

    church = workforce_member.church
    member = workforce_member.member

    # Find current primary unit (first active non-probation unit membership)
    current_membership = (
        UnitMembership.raw_objects.filter(
            church=church,
            workforce_member=workforce_member,
            is_probation=False,
            is_active=True,
        )
        .order_by("joined_at")
        .first()
    )
    original_unit = current_membership.unit if current_membership else None

    # Move stage to Probationer
    probationer_stage = WorkforceStage.raw_objects.filter(
        church=church, slug=WorkforceStage.SLUG_PROBATIONER, is_active=True
    ).first()
    if not probationer_stage:
        raise ValueError("No Probationer stage configured.")
    workforce_member.stage = probationer_stage
    workforce_member.save(update_fields=["stage"])

    # Deactivate all current unit memberships
    UnitMembership.raw_objects.filter(
        church=church, workforce_member=workforce_member, is_active=True
    ).update(is_active=False)

    # Create probation unit membership
    UnitMembership.raw_objects.create(
        church=church,
        workforce_member=workforce_member,
        unit=probation_unit,
        is_probation=True,
        is_active=True,
    )

    # Create or update WorkforceTraineeProfile for probation tracking
    trainee, _ = WorkforceTraineeProfile.raw_objects.update_or_create(
        church=church,
        member=member,
        defaults={
            "reason": "probation",
            "probation_unit": probation_unit,
            "original_unit": original_unit,
            "probation_ends_at": ends_at,
            "notes": notes,
            "is_active": True,
            "promoted_at": None,
            "promoted_by": None,
        },
    )

    # Notify member via SMS
    _notify_demotion(member, church, probation_unit, ends_at)

    return trainee


@transaction.atomic
def complete_probation(trainee):
    """
    Called when probation_ends_at is reached (daily scheduler) or manually.

    INDUCTION path (reason='induction'):
        - Moves member from probation_unit to preferred_unit
        - Clears is_probation flag
        - Stage → Active

    DISCIPLINARY path (reason='probation'):
        - Deactivates probation unit membership
        - Restores original_unit membership
        - Clears is_probation flag
        - Stage → Active

    Returns the final UnitMembership.
    """
    from workforce.models import WorkforceStage
    from units.models import UnitMembership

    church = trainee.church
    wf = trainee.member.workforce_profiles.filter(church=church, is_active=True).first()
    if not wf:
        return None

    # Determine target unit
    if trainee.reason == "induction":
        target_unit = trainee.preferred_unit
    else:
        target_unit = trainee.original_unit

    # Deactivate probation membership
    UnitMembership.raw_objects.filter(
        church=church,
        workforce_member=wf,
        is_probation=True,
        is_active=True,
    ).update(is_active=False)

    # Assign or reactivate target unit membership
    final_membership = None
    if target_unit:
        final_membership, _ = UnitMembership.raw_objects.update_or_create(
            church=church,
            workforce_member=wf,
            unit=target_unit,
            defaults={"is_probation": False, "is_active": True},
        )

    # Advance stage to Active
    active_stage = WorkforceStage.raw_objects.filter(
        church=church, slug=WorkforceStage.SLUG_ACTIVE, is_active=True
    ).first()
    if active_stage:
        wf.stage = active_stage
        wf.save(update_fields=["stage"])

    # Close trainee profile
    trainee.is_active = False
    trainee.promoted_at = timezone.now()
    trainee.save(update_fields=["is_active", "promoted_at"])

    # Notify member
    _notify_probation_complete(trainee.member, church, target_unit)

    return final_membership


def _notify_demotion(member, church, probation_unit, ends_at):
    """Send SMS to demoted member."""
    try:
        from messaging.sms import send_sms

        phone = member.user.phone_number
        if not phone:
            return
        unit_name = probation_unit.name if probation_unit else "a designated unit"
        end_str = (
            ends_at.strftime("%d %b %Y")
            if ends_at
            else "a period determined by leadership"
        )
        send_sms(
            phone=phone,
            message=(
                f"Hi {member.user.full_name or 'there'}, "
                f"your membership has been placed on a disciplinary probation period "
                f"ending {end_str}. You will serve in {unit_name} during this period. "
                f"Please speak with your unit head for more details."
            ),
            church=church,
            category="other",
            recipient_name=str(member),
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).error("demote SMS failed: %s", exc)


def _notify_probation_complete(member, church, target_unit):
    """Send SMS to member when probation completes."""
    try:
        from messaging.sms import send_sms

        phone = member.user.phone_number
        if not phone:
            return
        unit_name = target_unit.name if target_unit else "your assigned unit"
        send_sms(
            phone=phone,
            message=(
                f"Congratulations {member.user.full_name or 'there'}! "
                f"Your probation period is complete. "
                f"You are now assigned to {unit_name} as an active member. "
                f"Welcome fully to the team!"
            ),
            church=church,
            category="other",
            recipient_name=str(member),
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).error("probation complete SMS failed: %s", exc)
