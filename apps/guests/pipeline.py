"""
guests/pipeline.py

Guest → Workforce Membership Pipeline (LEVEL-3 ARCHITECTURE)

FLOW:

New Guest
    ↓ attendance threshold met
Committed
    ↓ workforce interest form
Membership Application
    ↓ training started
Workforce Trainee
    ↓ evaluation satisfied
(Admin approval)
    ↓
promote_to_workforce()
    ↓
Planted Member
"""

from django.db import transaction, models
from django.utils import timezone
from datetime import timedelta


# ============================================================
# COMMITTED DETECTION (Scheduler)
# ============================================================

def flag_committed_guests(church, threshold=None, window_weeks=None):
    """
    Scheduler job.

    Detects committed guests and sends workforce interest form.
    """

    from guests.models import (
        GuestEntry,
        GuestStatus,
        MembershipTrack,
        FollowUpReport,
    )
    from guests.services.interest import send_workforce_interest_form

    track = MembershipTrack.raw_objects.filter(
        church=church,
        is_default=True,
        is_active=True,
    ).first()

    if not track:
        return 0

    threshold = threshold or track.attendance_threshold
    window_weeks = window_weeks or track.attendance_window_weeks

    committed_status = GuestStatus.raw_objects.filter(
        church=church,
        slug=GuestStatus.SLUG_COMMITTED,
        is_active=True,
    ).first()

    if not committed_status:
        return 0

    window_start = timezone.now().date() - timedelta(weeks=window_weeks)

    candidates = GuestEntry.raw_objects.filter(
        church=church,
        is_active=True,
        converted_to__isnull=True,
        status__slug__in=[
            GuestStatus.SLUG_NEW_GUEST,
            GuestStatus.SLUG_IN_CONTACT,
        ],
    )

    count = 0

    for guest in candidates:

        attendance = (
            FollowUpReport.raw_objects.filter(
                church=church,
                guest=guest,
                report_date__gte=window_start,
            )
            .filter(
                models.Q(service_sunday=True)
                | models.Q(service_midweek=True)
            )
            .count()
        )

        if attendance >= threshold:
            guest.status = committed_status
            guest.save(update_fields=["status", "updated_at"])

            # send workforce interest consent
            send_workforce_interest_form(guest)

            count += 1

    return count


# ============================================================
# APPLICATION CREATION
# ============================================================

def create_application(*, church, guest, track=None, notes=""):
    """
    Creates membership application.
    """

    from guests.models import MembershipApplication, MembershipTrack

    if not track:
        track = MembershipTrack.raw_objects.filter(
            church=church,
            is_default=True,
            is_active=True,
        ).first()

    if not track:
        raise ValueError("No default membership track configured.")

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
        raise ValueError("Guest already has active application.")

    return MembershipApplication.raw_objects.create(
        church=church,
        guest=guest,
        track=track,
        status=MembershipApplication.STATUS_PENDING,
        is_active=True,
        notes=notes,
    )


# ============================================================
# WORKFORCE INTEREST SUBMISSION
# ============================================================

@transaction.atomic
def submit_workforce_interest(*, guest, preferred_unit):

    """
    Called when guest submits interest form.
    """

    from guests.models import WorkforceInterest
    from workforce.models import WorkforceTraineeProfile

    # save interest
    WorkforceInterest.raw_objects.update_or_create(
        church=guest.church,
        guest=guest,
        defaults={"preferred_unit": preferred_unit},
    )

    # create application
    application = create_application(
        church=guest.church,
        guest=guest,
    )

    # ensure identity
    member = guest.ensure_member_identity()

    # create trainee profile
    WorkforceTraineeProfile.raw_objects.get_or_create(
        church=guest.church,
        member=member,
        defaults={
            "reason": "induction",
            "preferred_unit": preferred_unit,
            "is_active": True,
        },
    )

    # start onboarding
    start_training(application)

    return application


# ============================================================
# TRAINING START
# ============================================================

@transaction.atomic
def start_training(application):

    from workforce.models import WorkforceTraineeProfile
    from lms.models import LMSCourse, LMSEnrollment

    church = application.church
    guest = application.guest

    member = guest.ensure_member_identity()

    trainee = WorkforceTraineeProfile.raw_objects.get(
        church=church,
        member=member,
        is_active=True,
    )

    induction_course = LMSCourse.raw_objects.filter(
        church=church,
        course_type="induction",
        membership_track=application.track,
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
                "status": "active",
                "current_module": first_module,
                "is_active": True,
            },
        )

        trainee.lms_enrollment = enrollment
        trainee.save(update_fields=["lms_enrollment"])

    application.status = application.STATUS_TRAINING
    application.save(update_fields=["status", "updated_at"])

    return trainee


# ============================================================
# STEP COMPLETION
# ============================================================

def complete_step(progress_record, completed_by, notes=""):

    from guests.models import MembershipApplication, ApplicationStepProgress

    progress_record.completed = True
    progress_record.completed_at = timezone.now()
    progress_record.completed_by = completed_by
    progress_record.notes = notes

    progress_record.save(update_fields=[
        "completed",
        "completed_at",
        "completed_by",
        "notes",
        "updated_at",
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


# ============================================================
# FINAL INDUCTION (ADMIN ACTION)
# ============================================================

@transaction.atomic
def finalize_induction_from_trainee(application, inducted_by):

    """
    ADMIN CONTROLLED PROMOTION.
    """

    from workforce.models import WorkforceTraineeProfile
    from guests.models import MembershipApplication, GuestStatus

    if application.status != MembershipApplication.STATUS_PASSED:
        raise ValueError("Application must be PASSED.")

    member = application.guest.ensure_member_identity()

    trainee = WorkforceTraineeProfile.raw_objects.get(
        church=application.church,
        member=member,
        is_active=True,
    )

    workforce_member = trainee.promote_to_workforce(
        promoted_by_member=inducted_by
    )

    application.status = MembershipApplication.STATUS_INDUCTED
    application.inducted_member = member
    application.inducted_at = timezone.now()

    application.save(update_fields=[
        "status",
        "inducted_member",
        "inducted_at",
        "updated_at",
    ])

    # plant guest
    planted_status = GuestStatus.raw_objects.filter(
        church=application.church,
        slug=GuestStatus.SLUG_PLANTED,
        is_active=True,
    ).first()

    if planted_status:
        guest = application.guest
        guest.status = planted_status
        guest.save(update_fields=["status", "updated_at"])

    return workforce_member


# ============================================================
# DECLINE / WITHDRAW
# ============================================================

def decline_application(application, reviewed_by, notes=""):

    from guests.models import MembershipApplication

    if application.status == MembershipApplication.STATUS_INDUCTED:
        raise ValueError("Cannot decline inducted application.")

    application.status = MembershipApplication.STATUS_DECLINED
    application.reviewed_by = reviewed_by
    application.reviewed_at = timezone.now()
    application.notes = notes

    application.save(update_fields=[
        "status",
        "reviewed_by",
        "reviewed_at",
        "notes",
        "updated_at",
    ])

    return application


def withdraw_application(application):

    from guests.models import MembershipApplication

    if application.status == MembershipApplication.STATUS_INDUCTED:
        raise ValueError("Cannot withdraw inducted application.")

    application.status = MembershipApplication.STATUS_WITHDRAWN
    application.save(update_fields=["status", "updated_at"])

    return application