from django.db import models
from django.conf import settings
from accounts.models import ChurchMember
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import now
from guests.models import GuestEntry
from units.models import ChurchUnit, UnitMembership
from core.models import ChurchOwnedModel, OrderedChurchModel, SoftDeleteModel


# CHURCH_COORDS removed — coordinates now live on Church.latitude / Church.longitude
# Use church.coordinates property or accounts.views._church_coordinates(church)


class WorkforceStage(OrderedChurchModel):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    # is_default: auto-assigned to newly promoted trainees
    is_default = models.BooleanField(
        default=False,
        help_text="New workforce members land here on promotion. Only one stage can be default.",
    )
    # is_locked: system stages (Inductee) cannot be renamed or deleted
    is_locked = models.BooleanField(
        default=False,
        help_text="System stages cannot be renamed or deleted.",
    )

    class Meta:
        unique_together = ("church", "name")

    def __str__(self):
        return self.name


class WorkforceRole(OrderedChurchModel):
    name = models.CharField(max_length=100)
    permissions = models.JSONField(default=dict, blank=True)
    is_leadership = models.BooleanField(default=False)
    # is_default: the base role for all workforce members
    is_default = models.BooleanField(
        default=False,
        help_text="Default role assigned to all workforce members.",
    )
    # is_locked: system roles (Member) cannot be deleted
    is_locked = models.BooleanField(
        default=False,
        help_text="System roles cannot be deleted.",
    )

    class Meta:
        unique_together = ("church", "name")

    def __str__(self):
        return self.name


class WorkforceMember(ChurchOwnedModel):
    member = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="workforce_profiles",
    )
    joined_at = models.DateField(auto_now_add=True)
    stage = models.ForeignKey(
        WorkforceStage,
        on_delete=models.PROTECT,
    )
    stage_updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["church", "member"],
                name="unique_workforce_member",
            )
        ]
        indexes = [
            models.Index(fields=["church", "stage"]),
        ]

    def __str__(self):
        return f"{self.member} ({self.stage})"


class WorkforceMembershipRole(ChurchOwnedModel):
    workforce_member = models.ForeignKey(
        WorkforceMember,
        on_delete=models.CASCADE,
        related_name="roles",
    )
    role = models.ForeignKey(WorkforceRole, on_delete=models.CASCADE)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("workforce_member", "role")




class WorkforceTraineeProfile(ChurchOwnedModel):
    """
    A limited-access profile for members who are:
        1. In the induction/probation period after becoming a member
        2. On disciplinary probation (downgraded from WorkforceMember)

    Grants access to:
        - LMS courses (induction + church-wide training)
        - Church-wide notices and announcements
        - Unit assignment (probationary)

    Does NOT grant access to:
        - Full unit tools (chat, attendance marking, guest management)
        - Role-based permissions from UnitRole

    Upgrades to WorkforceMember when:
        - Induction LMS course is completed with a passing score
        - Probation period ends (set by unit head or admin)

    Downgrades from WorkforceMember when:
        - is_probation=True is set on UnitMembership
    """

    REASON_CHOICES = [
        ("induction",    "New member induction"),
        ("probation",    "Disciplinary probation"),
        ("transfer",     "Campus/unit transfer (awaiting re-assignment)"),
    ]

    member            = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="trainee_profiles",
    )
    reason            = models.CharField(
        max_length=20, choices=REASON_CHOICES, default="induction", db_index=True,
    )
    preferred_unit    = models.ForeignKey(
        "units.ChurchUnit",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="preferred_trainees",
        help_text="The unit the trainee has indicated they wish to join.",
    )
    lms_enrollment    = models.ForeignKey(
        "lms.LMSEnrollment",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="trainee_profiles",
        help_text="The induction LMS course enrollment for this trainee.",
    )
    probation_ends_at = models.DateField(
        null=True, blank=True,
        help_text="For disciplinary probation — the date probation is due to end.",
    )
    notes             = models.TextField(blank=True)

    # Lifecycle
    promoted_at       = models.DateTimeField(null=True, blank=True)
    promoted_by       = models.ForeignKey(
        "accounts.ChurchMember",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="promoted_trainees",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["church", "member"],
                name="unique_trainee_per_church_member",
            )
        ]
        indexes = [
            models.Index(fields=["church", "reason"]),
            models.Index(fields=["church", "is_active"]),
        ]

    def __str__(self):
        return f"Trainee: {self.member} [{self.get_reason_display()}]"
    
    def is_eligible_for_promotion(self):
        """
        Central promotion eligibility logic.
        LMS is only ONE possible requirement.
        """

        # Induction flow
        if self.reason == "induction":
            if not self.lms_enrollment:
                return False

            return self.lms_enrollment.status in (
                "passed",
                "certified",
            )

        # Disciplinary probation
        if self.reason == "probation":
            if self.probation_ends_at:
                return timezone.now().date() >= self.probation_ends_at

        return True

    @transaction.atomic
    def promote_to_workforce(self, promoted_by_member=None):
        """
        Upgrade trainee → WorkforceMember.

        THIS is the single authority for workforce promotion.
        """

        from workforce.models import WorkforceMember, WorkforceStage
        from units.models import UnitMembership

        if not self.is_active:
            raise ValueError("Trainee profile already inactive.")

        if not self.is_eligible_for_promotion():
            raise ValueError("Trainee not eligible for promotion.")

        stage = WorkforceStage.raw_objects.filter(
            church=self.church,
            is_active=True,
        ).order_by("order").first()

        if not stage:
            raise ValueError("No WorkforceStage configured.")

        # Create workforce member
        wf = WorkforceMember.raw_objects.create(
            church=self.church,
            member=self.member,
            stage=stage,
            is_active=True,
        )

        # Assign preferred unit automatically
        if self.preferred_unit:
            UnitMembership.raw_objects.get_or_create(
                church=self.church,
                workforce_member=wf,
                unit=self.preferred_unit,
                defaults={
                    "is_probation": False,
                    "is_active": True,
                },
            )

        # Close trainee lifecycle
        self.promoted_at = timezone.now()
        self.promoted_by = promoted_by_member
        self.is_active = False

        self.save(update_fields=[
            "promoted_at",
            "promoted_by",
            "is_active",
        ])

        return wf


class TraineePromotionEvaluation(ChurchOwnedModel):
    """
    Snapshot of trainee readiness evaluation.
    """

    trainee = models.ForeignKey(
        "workforce.WorkforceTraineeProfile",
        on_delete=models.CASCADE,
        related_name="evaluations",
    )

    lms_completed = models.BooleanField(default=False)
    probation_completed = models.BooleanField(default=False)

    # extensible future checks
    all_requirements_met = models.BooleanField(default=False)

    evaluated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-evaluated_at"]


class WorkforceReadinessSnapshot(ChurchOwnedModel):
    """
    Cached evaluation of trainee readiness.
    Rebuilt automatically by evaluator.
    """

    trainee = models.OneToOneField(
        "workforce.WorkforceTraineeProfile",
        on_delete=models.CASCADE,
        related_name="readiness_snapshot",
    )

    # ---- requirement results ----
    lms_completed = models.BooleanField(default=False)
    required_steps_completed = models.BooleanField(default=False)
    probation_complete = models.BooleanField(default=False)
    approval_received = models.BooleanField(default=False)

    # ---- aggregate ----
    is_ready = models.BooleanField(default=False)

    evaluated_at = models.DateTimeField(auto_now=True)

    details = models.JSONField(default=dict, blank=True)


class PromotionApprovalRole(ChurchOwnedModel):
    role = models.ForeignKey("permissions.MembershipRole", on_delete=models.CASCADE)

    order = models.PositiveSmallIntegerField(default=1)
    is_required = models.BooleanField(default=True)


class PromotionRule(ChurchOwnedModel):
    name = models.CharField(max_length=120)

    require_lms_completion = models.BooleanField(default=True)
    require_all_steps = models.BooleanField(default=True)
    require_probation_end = models.BooleanField(default=False)
    require_admin_approval = models.BooleanField(default=True)

    auto_promote = models.BooleanField(
        default=False,
        help_text="If true, promotion happens automatically when ready."
    )

    is_default = models.BooleanField(default=True)


class LeaderTask(ChurchOwnedModel):
    TASK_TYPES = [
        ("promotion_review", "Promotion Review"),
        ("approval_required", "Approval Required"),
    ]

    task_type = models.CharField(max_length=30, choices=TASK_TYPES)

    trainee = models.ForeignKey(
        "workforce.WorkforceTraineeProfile",
        on_delete=models.CASCADE,
    )
    assigned_role = models.ForeignKey(
        "permissions.MembershipRole",
        on_delete=models.CASCADE,
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)


class ChatMessage(ChurchOwnedModel):
    """
    Shared chat model across all workforce units.
    Supports message threads, guest cards, and pinned messages.
    """
    sender = models.ForeignKey(
        UnitMembership,
        on_delete=models.CASCADE,
        related_name="sent_chats",
        db_index=True,
    )
    parent = models.ForeignKey(
        "self",
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="replies",
        db_index=True,
    )
    message      = models.TextField(blank=True, null=True)
    voice_note   = models.FileField(upload_to="chat_voice/", blank=True, null=True)
    guest_card   = models.ForeignKey(
        GuestEntry,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="chat_messages",
        db_index=True,
    )
    seen_by = models.ManyToManyField(
        UnitMembership, related_name="seen_messages", blank=True
    )
    read_by = models.ManyToManyField(
        UnitMembership, related_name="read_messages", blank=True
    )
    file             = models.CharField(max_length=5000, blank=True, null=True)
    file_type        = models.CharField(max_length=5000, blank=True, null=True)
    file_name        = models.CharField(max_length=5000, blank=True, null=True)
    link_url         = models.CharField(max_length=5000, blank=True, null=True)
    link_title       = models.CharField(max_length=5000, blank=True, null=True)
    link_description = models.CharField(max_length=5000, blank=True, null=True)
    link_image       = models.TextField(blank=True, null=True)
    pinned    = models.BooleanField(default=False, db_index=True)
    pinned_at = models.DateTimeField(null=True, blank=True, db_index=True)
    pinned_by = models.ForeignKey(
        UnitMembership,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="pinned_messages",
    )

    # Edit / delete / forward
    is_deleted   = models.BooleanField(default=False, db_index=True)
    deleted_at   = models.DateTimeField(null=True, blank=True)
    deleted_by   = models.ForeignKey(
        UnitMembership,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="deleted_messages",
    )
    edited_at    = models.DateTimeField(null=True, blank=True)
    original_message = models.TextField(blank=True)   # stores pre-edit text

    # Forward: reference the original message this was forwarded from
    forwarded_from = models.ForeignKey(
        "self",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="forwards",
        help_text="Set when this message is a forward of another.",
    )
    # Room: which ChatRoom this message belongs to (replaces implicit unit scope)
    room = models.ForeignKey(
        "units.ChatRoom",
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="messages",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["sender", "created_at"]),
            models.Index(fields=["guest_card", "created_at"]),
        ]

    def __str__(self):
        return f"{self.sender}: {(self.message or '')[:30]}"

    @property
    def is_expired(self):
        if not self.pinned or not self.pinned_at:
            return False
        return (now() - self.pinned_at).days > 14

    def is_seen_by_all(self):
        """
        Check whether all unit members for this church have seen the message.
        Scoped to church — does not count users from other tenants.
        """
        from units.models import UnitMembership
        church_member_count = UnitMembership.raw_objects.filter(
            church=self.church,
            is_active=True,
        ).exclude(id=self.sender_id).count()
        return self.seen_by.count() >= church_member_count


class AttendanceRecord(ChurchOwnedModel):
    STATUS_CHOICES = [
        ("present", "Present"),
        ("late",    "Late"),
        ("excused", "Excused"),
        ("absent",  "Absent"),
    ]
    # user is a UnitMembership — the unit-scoped identity of the workforce member
    user   = models.ForeignKey(UnitMembership, on_delete=models.CASCADE, related_name="attendance_records")
    event  = models.ForeignKey("services.Event", on_delete=models.CASCADE, related_name="attendance_records")
    date   = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="absent")
    remarks   = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "event", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.user} — {self.event} ({self.date})"


class ClockRecord(ChurchOwnedModel):
    # user is a UnitMembership — consistent with AttendanceRecord
    user  = models.ForeignKey(UnitMembership, on_delete=models.CASCADE, related_name="clock_records")
    event = models.ForeignKey(
        "services.Event",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="clock_records",
    )
    clock_in  = models.DateTimeField(null=True, blank=True)
    clock_out = models.DateTimeField(null=True, blank=True)
    date      = models.DateField(default=timezone.localdate)

    class Meta:
        unique_together = ("user", "date", "event")
        ordering = ["-date"]

    @property
    def is_clocked_in(self):
        return bool(self.clock_in and not self.clock_out)

    def mark_clock_in(self):
        self.clock_in = timezone.now()
        self.is_active = True
        self.save(update_fields=["clock_in", "is_active"])

    def mark_clock_out(self):
        self.clock_out = timezone.now()
        self.is_active = False
        self.save(update_fields=["clock_out", "is_active"])


class PersonalReminder(ChurchOwnedModel):
    user        = models.ForeignKey(WorkforceMember, on_delete=models.CASCADE, db_index=True)
    title       = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    date        = models.DateField()
    time        = models.TimeField(null=True, blank=True)
    is_done     = models.BooleanField(default=False)

    class Meta:
        ordering = ["date", "time"]

    def __str__(self):
        return f"{self.user} — {self.title} ({self.date})"


class UserActivity(ChurchOwnedModel):
    ACTIVITY_TYPES = [
        ("followup",   "Follow-up"),
        ("message",    "Message Sent"),
        ("guest_view", "Viewed Guest"),
        ("call",       "Called Guest"),
        ("report",     "Submitted Report"),
        ("other",      "Other"),
    ]
    user          = models.ForeignKey(ChurchMember, on_delete=models.CASCADE, related_name="activities", db_index=True)
    activity_type = models.CharField(max_length=50, choices=ACTIVITY_TYPES)
    guest_id      = models.CharField(max_length=50, blank=True, null=True)
    description   = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "User Activities"

    def __str__(self):
        return f"{self.user} — {self.activity_type} ({self.created_at.strftime('%Y-%m-%d')})"
