"""
guests/pipeline.py

Service layer for the guest-to-workforce membership pipeline.
Models live in guests/models.py — only functions here.

Auto-progression summary:
    New Guest
        ↓  FollowUpReport.contact_answered=True  (on report save)
    In-Contact
        ↓  attendance threshold met within window  (daily scheduler)
    Committed
        ↓  create_application() + approve_application() + complete_step()
    Passed
        ↓  induct_member()  →  promote_guest_to_member()
    Planted  (guest status updated automatically)

Manual overrides:
    FollowUpReport.not_planted=True  →  Not Planted (terminal, cancels pipeline)
    decline_application()            →  Declined
    withdraw_application()           →  Withdrawn
"""

from django.db import models, transaction
from django.utils import timezone
from datetime import timedelta


# ─────────────────────────────────────────────────────────────────────────────
# Flag committed guests (daily scheduler job)
# ─────────────────────────────────────────────────────────────────────────────

def flag_committed_guests(church):
    """
    Scan all active, non-terminal guests for this church and flag those
    who have met the attendance threshold within the rolling window as
    'Committed'.

    Threshold and window are per-church, configured on MembershipTrack:
        attendance_threshold    — e.g. 4 services
        attendance_window_weeks — e.g. within the last 6 weeks

    Attendance is counted from FollowUpReport records where
    service_sunday=True or service_midweek=True.

    Returns the number of guests newly flagged as Committed.
    """
    from guests.models import (
        GuestEntry, GuestStatus, MembershipTrack, FollowUpReport
    )

    track = MembershipTrack.raw_objects.filter(
        church=church,
        is_default=True,
        is_active=True,
    ).first()

    if not track or track.attendance_threshold == 0:
        return 0

    committed_status = GuestStatus.raw_objects.filter(
        church=church,
        slug=GuestStatus.SLUG_COMMITTED,
        is_active=True,
    ).first()

    if not committed_status:
        return 0

    # Rolling window start
    window_start = timezone.now().date() - timedelta(weeks=track.attendance_window_weeks)

    # Only consider guests who are In-Contact or New-Guest
    # (not already Committed, Planted, Not Planted, or Inducted)
    eligible_slugs = [GuestStatus.SLUG_NEW_GUEST, GuestStatus.SLUG_IN_CONTACT]

    candidates = GuestEntry.raw_objects.filter(
        church=church,
        is_active=True,
        converted_to__isnull=True,
        status__slug__in=eligible_slugs,
    )

    count = 0
    for guest in candidates:
        attendance = FollowUpReport.raw_objects.filter(
            church=church,
            guest=guest,
            report_date__gte=window_start,
        ).filter(
            models.Q(service_sunday=True) | models.Q(service_midweek=True)
        ).count()

        if attendance >= track.attendance_threshold:
            guest.status = committed_status
            guest.save(update_fields=["status", "updated_at"])
            count += 1

    return count


# ─────────────────────────────────────────────────────────────────────────────
# Application management
# ─────────────────────────────────────────────────────────────────────────────

def create_application(*, church, guest, track=None, notes=""):
    """
    Create a MembershipApplication for a committed guest.
    Uses the church's default track if none specified.

    Raises ValueError if the guest already has an active application
    on the same track, or if the guest is on a terminal status.
    """
    from guests.models import MembershipApplication, MembershipTrack, GuestStatus

    # Guard: terminal status (Not Planted)
    if guest.status and guest.status.is_terminal:
        raise ValueError(
            f"{guest} has a terminal status ('{guest.status.name}') "
            "and cannot be added to the membership pipeline."
        )

    if not track:
        track = MembershipTrack.raw_objects.filter(
            church=church,
            is_default=True,
            is_active=True,
        ).first()

    if not track:
        raise ValueError(
            "No default membership track configured for this church. "
            "Set one up in Settings → Membership Tracks."
        )

    active_statuses = [
        MembershipApplication.STATUS_PENDING,
        MembershipApplication.STATUS_TRAINING,
    ]

    if MembershipApplication.raw_objects.filter(
        church=church,
        guest=guest,
        track=track,
        status__in=active_statuses,
    ).exists():
        raise ValueError(
            f"{guest} already has an active application for '{track.name}'."
        )

    return MembershipApplication.raw_objects.create(
        church=church,
        guest=guest,
        track=track,
        status=MembershipApplication.STATUS_PENDING,
        notes=notes,
        is_active=True,
    )


@transaction.atomic
def approve_application(application, reviewed_by):
    """
    Admin approves the application and starts training.
    Creates step progress records for each track step.
    Moves status → in_training.
    """
    from guests.models import MembershipApplication, ApplicationStepProgress

    if application.status != MembershipApplication.STATUS_PENDING:
        raise ValueError("Only pending applications can be approved.")

    steps = application.track.steps.filter(is_active=True).order_by("order")
    for step in steps:
        ApplicationStepProgress.raw_objects.get_or_create(
            church=application.church,
            application=application,
            step=step,
            defaults={"completed": False, "is_active": True},
        )

    # Update guest status to reflect training has begun
    # Churches seed a "Committed" status which holds during training.
    # The guest stays Committed until fully inducted (→ Planted).

    application.status = MembershipApplication.STATUS_TRAINING
    application.reviewed_by = reviewed_by
    application.reviewed_at = timezone.now()
    application.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])

    return application


def complete_step(progress_record, completed_by, notes=""):
    """
    Mark a single track step as completed.
    Automatically advances application to 'passed' when all required
    steps are done.
    """
    from guests.models import MembershipApplication, ApplicationStepProgress

    progress_record.completed = True
    progress_record.completed_at = timezone.now()
    progress_record.completed_by = completed_by
    progress_record.notes = notes
    progress_record.save(update_fields=[
        "completed", "completed_at", "completed_by", "notes", "updated_at"
    ])

    application = progress_record.application

    all_required_done = not ApplicationStepProgress.raw_objects.filter(
        church=application.church,
        application=application,
        step__is_required=True,
        completed=False,
    ).exists()

    if all_required_done:
        application.status = MembershipApplication.STATUS_PASSED
        application.save(update_fields=["status", "updated_at"])

    return progress_record


@transaction.atomic
def induct_member(
    application,
    inducted_by,
    workforce_stage=None,
    workforce_role=None,
    unit=None,
    unit_role=None,
):
    """
    Final pipeline step: admin confirms induction.

    1. Calls promote_guest_to_member() — single source of truth for
       converting a guest into a ChurchMember + WorkforceMember.
    2. Marks application as inducted.
    3. Updates guest status to 'Planted'.

    Returns the result dict from promote_guest_to_member:
        {user, member, workforce_member, username, temp_password}

    The calling view must display the generated credentials to the admin
    for sharing with the new member — they are shown once only.
    """
    from guests.models import MembershipApplication, GuestStatus
    from accounts.member_service import promote_guest_to_member

    if application.status != MembershipApplication.STATUS_PASSED:
        raise ValueError(
            "Application must be in 'passed' status before induction. "
            "Ensure all required steps are completed first."
        )

    # Delegate member creation entirely to member_service
    result = promote_guest_to_member(
        guest_entry=application.guest,
        church=application.church,
        workforce_stage=workforce_stage,
        workforce_role=workforce_role,
        unit=unit,
        unit_role=unit_role,
        created_by=inducted_by,
    )

    # Mark application as inducted
    application.status = MembershipApplication.STATUS_INDUCTED
    application.inducted_member = result["member"]
    application.inducted_at = timezone.now()
    application.save(update_fields=[
        "status", "inducted_member", "inducted_at", "updated_at"
    ])

    # Update guest status to Planted
    planted_status = GuestStatus.raw_objects.filter(
        church=application.church,
        slug=GuestStatus.SLUG_PLANTED,
        is_active=True,
    ).first()

    if planted_status:
        guest = application.guest
        guest.status = planted_status
        guest.save(update_fields=["status", "updated_at"])

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Decline / withdraw
# ─────────────────────────────────────────────────────────────────────────────

def decline_application(application, reviewed_by, notes=""):
    """Admin declines the application."""
    from guests.models import MembershipApplication

    if application.status == MembershipApplication.STATUS_INDUCTED:
        raise ValueError("Cannot decline an already inducted application.")

    application.status = MembershipApplication.STATUS_DECLINED
    application.reviewed_by = reviewed_by
    application.reviewed_at = timezone.now()
    if notes:
        application.notes = notes
    application.save(update_fields=[
        "status", "reviewed_by", "reviewed_at", "notes", "updated_at"
    ])
    return application


def withdraw_application(application):
    """Guest or admin withdraws the application."""
    from guests.models import MembershipApplication

    if application.status == MembershipApplication.STATUS_INDUCTED:
        raise ValueError("Cannot withdraw a completed induction.")

    application.status = MembershipApplication.STATUS_WITHDRAWN
    application.save(update_fields=["status", "updated_at"])
    return application