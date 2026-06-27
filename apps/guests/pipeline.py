"""
guests/pipeline.py

Guest → Workforce Membership Pipeline

FLOW:

New Guest
    ↓  contact_answered=True in FollowUpReport  [event-driven, immediate]
In-Contact
    ↓  attendance threshold met  [event-driven on every FollowUpReport save]
       + checked by daily cron as safety net
Committed  →  WorkforceInterest form sent
    ↓  guest submits interest form
MembershipApplication (pending → in_training)
    ↓  WorkforceTraineeProfile created, LMS course auto-enrolled
Training (modules + attendance gates)
    ↓  all required steps complete → evaluate_trainee_progress
(Admin approval via LeaderTask)
    ↓  finalize_induction_from_trainee
Planted  +  WorkforceMember (stage=Probationer)

Key design choices:
- check_commitment_for_guest() uses a single aggregated SQL query, not Python loops.
  It runs synchronously on every FollowUpReport post_save so commitment is detected
  the moment it is earned, not the next morning.
- The daily cron (flag_committed_guests) is kept as a safety net for any reports
  that were bulk-imported or whose signals were suppressed.
- All pipeline state transitions are atomic.
"""

from django.db import transaction, connection
from django.utils import timezone
from datetime import timedelta


# ============================================================
# COMMITMENT DETECTION — event-driven, pure SQL aggregation
# ============================================================


def check_commitment_for_guest(guest, *, threshold=None, window_weeks=None):
    """
    Check whether a single guest has reached the commitment threshold.
    Called synchronously on every FollowUpReport post_save.

    Uses a single COUNT(DISTINCT) SQL query — no Python-side loops over
    report rows, no N+1 queries.

    Returns True if the guest was just advanced to Committed, False otherwise.
    """
    from guests.models import GuestStatus, MembershipTrack

    church = guest.church

    # Already past commitment — nothing to do
    if guest.status and guest.status.slug not in (
        GuestStatus.SLUG_NEW_GUEST,
        GuestStatus.SLUG_IN_CONTACT,
    ):
        return False

    if guest.status and guest.status.is_terminal:
        return False

    # Resolve thresholds from church's default track
    if threshold is None or window_weeks is None:
        track = MembershipTrack.raw_objects.filter(
            church=church, is_default=True, is_active=True
        ).first()
        threshold = threshold or (track.attendance_threshold if track else 4)
        window_weeks = window_weeks or (track.attendance_window_weeks if track else 6)

    window_start = (timezone.now() - timedelta(weeks=window_weeks)).date()

    # Single aggregated SQL — count distinct commitment-eligible events
    # attended by this guest within the rolling window.
    sql = """
        SELECT COUNT(DISTINCT fr_ev."event_id")
        FROM   guests_followupreport_attended_events fr_ev
        JOIN   guests_followupreport fr
               ON fr."id" = fr_ev."followupreport_id"
        JOIN   services_event ev
               ON ev."id" = fr_ev."event_id"
        WHERE  fr."guest_id"    = %s
          AND  fr."church_id"   = %s
          AND  fr."report_date" >= %s
          AND  ev."count_towards_commitment" = TRUE
          AND  ev."is_active"   = TRUE
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [guest.pk, church.pk, window_start])
        count = cursor.fetchone()[0]

    if count < threshold:
        return False

    # Threshold met — advance to Committed
    committed_status = GuestStatus.raw_objects.filter(
        church=church, slug=GuestStatus.SLUG_COMMITTED, is_active=True
    ).first()
    if not committed_status:
        return False

    guest.status = committed_status
    guest.save(update_fields=["status", "updated_at"])

    # Send workforce interest form immediately
    from guests.services.interest import send_workforce_interest_form

    send_workforce_interest_form(guest)

    return True


# ============================================================
# BATCH COMMITMENT DETECTION (daily scheduler safety net)
# Uses a single GROUP BY query across all candidate guests.
# ============================================================


def flag_committed_guests(church, threshold=None, window_weeks=None):
    """
    Bulk commitment detection for all in-scope guests in a church.
    Run daily by the scheduler as a safety net.

    Uses a single GROUP BY aggregation query rather than iterating guests
    in Python — O(1) database round-trips regardless of guest count.

    Returns: number of guests advanced to Committed.
    """
    from guests.models import GuestEntry, GuestStatus, MembershipTrack

    # Resolve thresholds
    track = MembershipTrack.raw_objects.filter(
        church=church, is_default=True, is_active=True
    ).first()
    threshold = threshold or (track.attendance_threshold if track else 4)
    window_weeks = window_weeks or (track.attendance_window_weeks if track else 6)

    committed_status = GuestStatus.raw_objects.filter(
        church=church, slug=GuestStatus.SLUG_COMMITTED, is_active=True
    ).first()
    if not committed_status:
        return 0

    in_scope_slugs = [GuestStatus.SLUG_NEW_GUEST, GuestStatus.SLUG_IN_CONTACT]
    in_scope_status_ids = list(
        GuestStatus.raw_objects.filter(
            church=church, slug__in=in_scope_slugs, is_active=True
        ).values_list("id", flat=True)
    )
    if not in_scope_status_ids:
        return 0

    window_start = (timezone.now() - timedelta(weeks=window_weeks)).date()

    # Single aggregation query — group by guest, count distinct commitment events
    sql = """
        SELECT fr."guest_id", COUNT(DISTINCT fr_ev."event_id") AS attend_count
        FROM   guests_followupreport_attended_events fr_ev
        JOIN   guests_followupreport fr
               ON fr."id" = fr_ev."followupreport_id"
        JOIN   guests_guestentry g
               ON g."id" = fr."guest_id"
        JOIN   services_event ev
               ON ev."id" = fr_ev."event_id"
        WHERE  fr."church_id"   = %s
          AND  fr."report_date" >= %s
          AND  g."status_id"   = ANY(%s)
          AND  g."is_active"   = TRUE
          AND  g."is_deleted"  = FALSE
          AND  g."converted_to_id" IS NULL
          AND  ev."count_towards_commitment" = TRUE
          AND  ev."is_active"  = TRUE
        GROUP  BY fr."guest_id"
        HAVING COUNT(DISTINCT fr_ev."event_id") >= %s
    """
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            [
                church.pk,
                window_start,
                in_scope_status_ids,
                threshold,
            ],
        )
        ready_guest_ids = [row[0] for row in cursor.fetchall()]

    if not ready_guest_ids:
        return 0

    # Fetch and advance only the qualifying guests
    from guests.services.interest import send_workforce_interest_form

    count = 0
    guests = GuestEntry.raw_objects.filter(
        id__in=ready_guest_ids, church=church
    ).select_related("status")

    for guest in guests:
        # Skip if already past committed (e.g. already handled by event-driven check)
        if guest.status_id == committed_status.id:
            continue
        if guest.status and guest.status.is_terminal:
            continue

        guest.status = committed_status
        guest.save(update_fields=["status", "updated_at"])
        send_workforce_interest_form(guest)
        count += 1

    return count


# ============================================================
# APPLICATION CREATION
# ============================================================


def create_application(*, church, guest, track=None, notes=""):
    """Create a MembershipApplication for a committed guest."""
    from guests.models import MembershipApplication, MembershipTrack

    if not track:
        track = MembershipTrack.raw_objects.filter(
            church=church, is_default=True, is_active=True
        ).first()

    if not track:
        raise ValueError("No default membership track configured.")

    active_statuses = [
        MembershipApplication.STATUS_PENDING,
        MembershipApplication.STATUS_TRAINING,
    ]
    if MembershipApplication.raw_objects.filter(
        church=church, guest=guest, track=track, status__in=active_statuses
    ).exists():
        raise ValueError("Guest already has an active application.")

    return MembershipApplication.raw_objects.create(
        church=church,
        guest=guest,
        track=track,
        status=MembershipApplication.STATUS_PENDING,
        notes=notes,
        is_active=True,
    )


# ============================================================
# WORKFORCE INTEREST SUBMISSION
# ============================================================


@transaction.atomic
def submit_workforce_interest(*, guest, preferred_unit):
    """
    Called when a guest submits the workforce interest form.
    Creates the MembershipApplication and WorkforceTraineeProfile,
    then starts training (auto-enrolls in induction LMS course).
    """
    from guests.models import WorkforceInterest
    from workforce.models import WorkforceTraineeProfile

    # Record interest
    WorkforceInterest.raw_objects.update_or_create(
        church=guest.church,
        guest=guest,
        defaults={"preferred_unit": preferred_unit},
    )

    # Create application
    application = create_application(church=guest.church, guest=guest)

    # Ensure the guest has a ChurchMember identity
    member = guest.ensure_member_identity()

    # Create trainee profile (ChurchMember state — not yet WorkforceMember)
    WorkforceTraineeProfile.raw_objects.get_or_create(
        church=guest.church,
        member=member,
        defaults={
            "reason": "induction",
            "preferred_unit": preferred_unit,
            "is_active": True,
        },
    )

    # Start training → auto-enroll in LMS course
    start_training(application)

    return application


# ============================================================
# TRAINING START
# ============================================================


@transaction.atomic
def start_training(application):
    """
    Mark application as in_training and auto-enroll trainee in the
    induction LMS course linked to the membership track.
    """
    from workforce.models import WorkforceTraineeProfile
    from lms.models import LMSCourse, LMSEnrollment
    from guests.models import MembershipApplication

    church = application.church
    member = application.guest.ensure_member_identity()

    trainee = WorkforceTraineeProfile.raw_objects.filter(
        church=church, member=member, is_active=True
    ).first()

    if not trainee:
        raise ValueError(f"No active trainee profile for member {member} in {church}.")

    # Find the induction course for this track
    induction_course = LMSCourse.raw_objects.filter(
        church=church,
        course_type="induction",
        membership_track=application.track,
        is_active=True,
    ).first()

    if induction_course and not trainee.lms_enrollment_id:
        first_module = (
            induction_course.modules.filter(is_active=True).order_by("order").first()
        )
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

    # Seed ApplicationStepProgress records for each track step, even when the
    # church intentionally runs the track without an LMS course.
    _seed_step_progress(application)

    application.status = MembershipApplication.STATUS_TRAINING
    application.save(update_fields=["status", "updated_at"])

    return trainee


def _seed_step_progress(application):
    """
    Create ApplicationStepProgress records for all steps in the track,
    so the trainee can track progress through each requirement.
    """
    from guests.models import ApplicationStepProgress

    for step in application.track.steps.filter(is_active=True).order_by("order"):
        ApplicationStepProgress.raw_objects.get_or_create(
            church=application.church,
            application=application,
            step=step,
            defaults={"completed": False, "is_active": True},
        )


# ============================================================
# STEP COMPLETION
# ============================================================


def complete_step(progress_record, completed_by, notes=""):
    """
    Mark an ApplicationStepProgress complete.
    If all required steps are done, advance application to PASSED
    and trigger promotion evaluation.
    """
    from guests.models import MembershipApplication, ApplicationStepProgress
    from workforce.services.evaluator import evaluate_trainee_progress

    progress_record.completed = True
    progress_record.completed_at = timezone.now()
    progress_record.completed_by = completed_by
    progress_record.notes = notes
    progress_record.save(
        update_fields=[
            "completed",
            "completed_at",
            "completed_by",
            "notes",
            "updated_at",
        ]
    )

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

        # Trigger promotion evaluation for the linked trainee
        from workforce.models import WorkforceTraineeProfile

        trainees = WorkforceTraineeProfile.raw_objects.filter(
            church=application.church,
            member=application.guest.ensure_member_identity(),
            is_active=True,
        )
        for trainee in trainees:
            evaluate_trainee_progress(trainee)

    return progress_record


# ============================================================
# FINAL INDUCTION (admin-controlled promotion)
# ============================================================


@transaction.atomic
def finalize_induction_from_trainee(application, inducted_by):
    """
    Admin-controlled final promotion:
    WorkforceTraineeProfile → WorkforceMember (stage=Probationer).
    Marks the guest as Planted.
    """
    from workforce.models import WorkforceTraineeProfile
    from guests.models import MembershipApplication, GuestStatus

    if application.status != MembershipApplication.STATUS_PASSED:
        raise ValueError("Application must be in PASSED status before induction.")

    member = application.guest.ensure_member_identity()
    trainee = WorkforceTraineeProfile.raw_objects.filter(
        church=application.church, member=member, is_active=True
    ).first()

    if not trainee:
        raise ValueError(f"No active trainee profile for {member}.")

    workforce_member = trainee.promote_to_workforce(promoted_by_member=inducted_by)

    application.status = MembershipApplication.STATUS_INDUCTED
    application.inducted_member = member
    application.inducted_at = timezone.now()
    application.save(
        update_fields=["status", "inducted_member", "inducted_at", "updated_at"]
    )

    # Advance guest status to Planted
    planted_status = GuestStatus.raw_objects.filter(
        church=application.church, slug=GuestStatus.SLUG_PLANTED, is_active=True
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
        raise ValueError("Cannot decline an inducted application.")
    application.status = MembershipApplication.STATUS_DECLINED
    application.reviewed_by = reviewed_by
    application.reviewed_at = timezone.now()
    application.notes = notes
    application.save(
        update_fields=["status", "reviewed_by", "reviewed_at", "notes", "updated_at"]
    )
    return application


def withdraw_application(application):
    from guests.models import MembershipApplication

    if application.status == MembershipApplication.STATUS_INDUCTED:
        raise ValueError("Cannot withdraw an inducted application.")
    application.status = MembershipApplication.STATUS_WITHDRAWN
    application.save(update_fields=["status", "updated_at"])
    return application


# ============================================================
# WORKFORCE INTEREST LIFECYCLE (reminders + expiry)
# ============================================================


def process_workforce_interest_lifecycle(church):
    """Daily job — send reminders and expire stale interest forms."""
    from guests.models import WorkforceInterest
    from guests.services.interest import send_interest_reminder

    now = timezone.now()
    processed = 0

    interests = WorkforceInterest.raw_objects.filter(
        church=church, submitted_at__isnull=True, expired=False
    )
    for interest in interests:
        days = (now - interest.invited_at).days

        if days >= 3 and not interest.reminder_1_sent:
            send_interest_reminder(interest, reminder=1)
            interest.reminder_1_sent = True
        elif days >= 7 and not interest.reminder_2_sent:
            send_interest_reminder(interest, reminder=2)
            interest.reminder_2_sent = True
        elif interest.expires_at and now >= interest.expires_at:
            interest.expired = True

        interest.save(
            update_fields=[
                "reminder_1_sent",
                "reminder_2_sent",
                "expired",
                "updated_at",
            ]
        )
        processed += 1

    return processed
