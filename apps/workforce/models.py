from django.db import models
from django.conf import settings
from accounts.models import ChurchMember
from django.db import transaction
from django.utils import timezone
from django.utils.timezone import now
from guests.models import GuestEntry
from units.models import ChurchUnit
from core.models import ChurchOwnedModel, OrderedChurchModel, SoftDeleteModel

# CHURCH_COORDS removed — coordinates now live on Church.latitude / Church.longitude
# Use church.coordinates property or accounts.views._church_coordinates(church)


class WorkforceStage(OrderedChurchModel):
    """
    Progression stages for workforce members.

    Four system stages are always seeded and locked in position:
        INDUCTEE    (order 0) — auto from guest pipeline; trainee period begins.
        PROBATIONER (order 1) — post-training promotion or disciplinary action.
        ACTIVE      (order 2) — final automated stage after probation clears.
        LEADER      (order 3) — manually assigned by admin.

    Custom stages are inserted after LEADER and are freely reorderable.
    The first three (Inductee, Probationer, Active) are order-locked.
    """

    # Inductee is NOT a WorkforceStage — it is a WorkforceTraineeProfile state.
    # WorkforceMember stages begin at Probationer (after training completion).
    SLUG_PROBATIONER = "probationer"
    SLUG_ACTIVE = "active"
    SLUG_LEADER = "leader"

    SYSTEM_SLUGS = {SLUG_PROBATIONER, SLUG_ACTIVE, SLUG_LEADER}
    ORDER_LOCKED_SLUGS = {SLUG_PROBATIONER, SLUG_ACTIVE}

    name = models.CharField(max_length=100)
    slug = models.SlugField(
        max_length=50,
        blank=True,
        help_text="System identifier. Set on system stages; auto-generated for custom stages.",
    )
    description = models.TextField(blank=True)
    is_default = models.BooleanField(
        default=False,
        help_text="New workforce members land here on promotion. Only Inductee should be True.",
    )
    is_locked = models.BooleanField(
        default=False,
        help_text="System stages cannot be renamed or deleted.",
    )
    is_order_locked = models.BooleanField(
        default=False,
        help_text="Stage cannot be reordered (first three system stages).",
    )

    class Meta:
        unique_together = ("church", "name")
        indexes = [
            models.Index(fields=["church", "slug"]),
        ]

    def __str__(self):
        return self.name


class WorkforceRole(OrderedChurchModel):
    """
    A workforce-wide role assigned to members across a church.

    tier maps to the permission tier hierarchy:
        1 = Church Admin   — sovereign access to all church resources
        2 = Sub-Admin      — cross-unit operations, no sovereign rights
        3 = Overseer       — handled via unit roles, not workforce roles
        4 = Unit / Group Head — handled via unit roles, not workforce roles
        5 = Assistant      — handled via unit roles, not workforce roles
        6 = Custom         — ad-hoc permissions for specific activities

    scope:
        "global" — permissions apply church-wide
        "unit"   — permissions apply only within assigned unit memberships

    The permissions JSONField stores {key: True} expanded from tier at creation.
    Admins can further customise individual keys in church settings.
    """

    TIER_CHOICES = [
        (1, "Church Admin"),
        (2, "Sub-Admin"),
        (3, "Assistant Pastor"),
        (7, "Custom Role"),
    ]
    SCOPE_CHOICES = [
        ("global", "Global (church-wide)"),
        ("unit", "Unit-scoped"),
    ]

    name = models.CharField(max_length=100)
    permissions = models.JSONField(default=dict, blank=True)

    # Tier drives default permission expansion and resolver shortcuts.
    tier = models.PositiveSmallIntegerField(
        choices=TIER_CHOICES,
        default=7,
        db_index=True,
        help_text=(
            "Permission tier: 1=Admin, 2=Sub-Admin, 3=Assistant Pastor, 7=Custom. "
            "Unit leadership tiers (4-7) are handled by unit/group roles."
        ),
    )
    scope = models.CharField(
        max_length=10,
        choices=SCOPE_CHOICES,
        default="global",
        help_text="'global' = church-wide workforce permission set.",
    )

    # Kept for backwards compat — tier is now authoritative
    is_leadership = models.BooleanField(
        default=False,
        help_text="Legacy flag — auto-synced from tier. Tier is authoritative.",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Default role assigned to all workforce members.",
    )
    is_locked = models.BooleanField(
        default=False,
        help_text="System roles cannot be deleted.",
    )

    class Meta:
        unique_together = ("church", "name")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        from permissions.registry import permissions_for_tier

        # Auto-sync is_leadership from tier for backwards compat
        self.is_leadership = self.tier in (1, 2, 3)
        self.scope = "global"

        if self.pk:
            previous = (
                WorkforceRole.raw_objects.filter(pk=self.pk).values("tier").first()
            )
            if previous and previous["tier"] != self.tier:
                self.permissions = permissions_for_tier(self.tier)
        elif not self.permissions:
            self.permissions = permissions_for_tier(self.tier)
        super().save(*args, **kwargs)


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
        return f"{self.member}"


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
        ("induction", "New member induction"),
        ("probation", "Disciplinary probation"),
        ("transfer", "Campus/unit transfer (awaiting re-assignment)"),
    ]

    member = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="trainee_profiles",
    )
    reason = models.CharField(
        max_length=20,
        choices=REASON_CHOICES,
        default="induction",
        db_index=True,
    )
    preferred_unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="preferred_trainees",
        help_text="The unit the trainee has indicated they wish to join.",
    )
    lms_enrollment = models.ForeignKey(
        "lms.LMSEnrollment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="trainee_profiles",
        help_text="The induction LMS course enrollment for this trainee.",
    )
    probation_ends_at = models.DateField(
        null=True,
        blank=True,
        help_text="For disciplinary probation — the date probation is due to end.",
    )
    # Probation unit assignment (manual, by admin)
    # For induction: the unit the admin assigns for probation service.
    #   preferred_unit is the REQUESTED unit from the interest form —
    #   probation_unit is where they're ACTUALLY assigned during probation.
    #   On probation completion → auto-assigned to preferred_unit.
    # For disciplinary: probation_unit is the temporary demotion unit.
    #   original_unit is where they'll be restored when probation ends.
    probation_unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="probation_trainees",
        help_text=(
            "Unit assigned for the probation period. "
            "Induction: admin assigns this; member auto-moves to preferred_unit on completion. "
            "Disciplinary: temporary unit; member restores to original_unit on completion."
        ),
    )
    original_unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pending_return_trainees",
        help_text=(
            "Disciplinary only — the unit the member returns to after probation ends. "
            "Auto-populated when a WorkforceMember is demoted to probation."
        ),
    )
    notes = models.TextField(blank=True)

    # Lifecycle
    promoted_at = models.DateTimeField(null=True, blank=True)
    promoted_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
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
    def get_active_application(self):
        """
        Return the active MembershipApplication for this trainee, or None.
        Used by attempt_final_promotion() in promotion_engine.py.
        """
        try:
            from guests.models import MembershipApplication

            return (
                MembershipApplication.raw_objects.filter(
                    church=self.church,
                    guest__converted_to=self.member,
                    status__in=[
                        MembershipApplication.STATUS_TRAINING,
                        MembershipApplication.STATUS_PASSED,
                    ],
                    is_active=True,
                )
                .order_by("-applied_at")
                .first()
            )
        except Exception:
            return None

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

        # New workforce members always start at Probationer stage.
        # Inductee is NOT a stage — it is the WorkforceTraineeProfile state.
        stage = (
            WorkforceStage.raw_objects.filter(
                church=self.church,
                slug=WorkforceStage.SLUG_PROBATIONER,
                is_active=True,
            ).first()
            or WorkforceStage.raw_objects.filter(
                church=self.church,
                is_active=True,
            )
            .order_by("order")
            .first()
        )

        if not stage:
            raise ValueError(
                "No WorkforceStage configured. Add at least a Probationer stage."
            )

        # Assign custom_id now that they are a proper workforce member
        self.member.assign_custom_id()

        # Create workforce member
        wf = WorkforceMember.raw_objects.create(
            church=self.church,
            member=self.member,
            stage=stage,
            is_active=True,
        )

        # Assign probation unit if set by admin, otherwise no unit assignment yet.
        # preferred_unit is NOT auto-assigned here — the member serves probation
        # first, then gets auto-assigned to preferred_unit when probation ends
        # (via the daily probation check or manual admin action).
        target_unit = self.probation_unit or None
        if target_unit:
            UnitMembership.raw_objects.get_or_create(
                church=self.church,
                workforce_member=wf,
                unit=target_unit,
                defaults={
                    "is_probation": True,  # probation flag on the membership
                    "is_active": True,
                },
            )

        # Close trainee lifecycle
        self.promoted_at = timezone.now()
        self.promoted_by = promoted_by_member
        self.is_active = False

        self.save(
            update_fields=[
                "promoted_at",
                "promoted_by",
                "is_active",
            ]
        )

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
        default=False, help_text="If true, promotion happens automatically when ready."
    )

    is_default = models.BooleanField(default=True)


class LeaderTask(ChurchOwnedModel):
    """
    A pending promotion approval task.

    Created when a trainee completes all requirements and needs admin sign-off.
    Actioned via the promotion_queue view in workforce/views.py.

    assigned_role: optional — if set, only members holding that WorkforceRole
    should action it. If None, any member with workforce.promotions.review can action it.
    """

    TASK_TYPES = [
        ("promotion_review", "Promotion Review"),
        ("approval_required", "Approval Required"),
    ]

    task_type = models.CharField(max_length=30, choices=TASK_TYPES)

    trainee = models.ForeignKey(
        "workforce.WorkforceTraineeProfile",
        on_delete=models.CASCADE,
        related_name="leader_tasks",
    )
    # Optional: restrict this task to a specific WorkforceRole.
    # None = any church admin / sub-admin can handle it.
    assigned_role = models.ForeignKey(
        "workforce.WorkforceRole",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_tasks",
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    completed = models.BooleanField(default=False, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="completed_leader_tasks",
    )
    rejection_reason = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["church", "completed"]),
            models.Index(fields=["church", "task_type"]),
        ]

    def __str__(self):
        return f"[{self.task_type}] {self.title}"


class ChatMessage(ChurchOwnedModel):
    """
    Shared chat model across all workforce units.

    sender / seen_by / read_by / pinned_by / deleted_by are all keyed on
    ChurchMember — the lowest common identity that every active member holds,
    regardless of whether they have been placed in a ChurchUnit yet.

    This means trainees, admins without a unit assignment, and any future
    member type can all send and receive messages without needing a
    UnitMembership first.
    """

    sender = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="sent_chats",
        db_index=True,
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="replies",
        db_index=True,
    )
    message = models.TextField(blank=True, null=True)
    voice_note = models.FileField(upload_to="chat_voice/", blank=True, null=True)
    guest_card = models.ForeignKey(
        GuestEntry,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chat_messages",
        db_index=True,
    )
    seen_by = models.ManyToManyField(
        "accounts.ChurchMember", related_name="seen_messages", blank=True
    )
    read_by = models.ManyToManyField(
        "accounts.ChurchMember", related_name="read_messages", blank=True
    )
    file = models.CharField(max_length=5000, blank=True, null=True)
    file_type = models.CharField(max_length=5000, blank=True, null=True)
    file_name = models.CharField(max_length=5000, blank=True, null=True)
    link_url = models.CharField(max_length=5000, blank=True, null=True)
    link_title = models.CharField(max_length=5000, blank=True, null=True)
    link_description = models.CharField(max_length=5000, blank=True, null=True)
    link_image = models.TextField(blank=True, null=True)
    pinned = models.BooleanField(default=False, db_index=True)
    pinned_at = models.DateTimeField(null=True, blank=True, db_index=True)
    pinned_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pinned_messages",
    )

    # Edit / delete / forward
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deleted_messages",
    )
    edited_at = models.DateTimeField(null=True, blank=True)
    original_message = models.TextField(blank=True)  # stores pre-edit text

    # Forward: reference the original message this was forwarded from
    forwarded_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="forwards",
        help_text="Set when this message is a forward of another.",
    )
    # Room: which ChatRoom this message belongs to (replaces implicit unit scope)
    room = models.ForeignKey(
        "units.ChatRoom",
        null=True,
        blank=True,
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
        sender_name = ""
        try:
            sender_name = self.sender.user.full_name or self.sender.user.username
        except Exception:
            sender_name = str(self.sender_id)
        return f"{sender_name}: {(self.message or '')[:30]}"

    @property
    def is_expired(self):
        if not self.pinned or not self.pinned_at:
            return False
        return (now() - self.pinned_at).days > 14

    def is_seen_by_all(self):
        """
        Check whether all ChurchMembers in this church have seen the message.
        """
        church_member_count = (
            ChurchMember.raw_objects.filter(
                church=self.church,
                is_active=True,
            )
            .exclude(id=self.sender_id)
            .count()
        )
        return self.seen_by.count() >= church_member_count


class ChatMessageReaction(ChurchOwnedModel):
    """One reaction emoji per member per chat message (same keys as feeds / reactions.js)."""

    message = models.ForeignKey(
        ChatMessage,
        on_delete=models.CASCADE,
        related_name="reactions",
        db_index=True,
    )
    member = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="chat_message_reactions",
        db_index=True,
    )
    reaction = models.CharField(max_length=24, db_index=True)

    class Meta:
        unique_together = ("message", "member")
        indexes = [
            models.Index(fields=["message", "reaction"]),
            models.Index(fields=["church", "message"]),
        ]

    def __str__(self):
        return f"{self.reaction} on msg {self.message_id}"


class ChatSettings(ChurchOwnedModel):
    """
    Per-church chat configuration.
    One row per church (get_or_create pattern).
    """

    # Which global tiers appear in rooms with no explicit members
    # Stored as a list of tier integers, e.g. [1, 2, 3]
    default_tier_list = models.JSONField(default=list, blank=True)

    # Per-room tier overrides: {str(room_id): [tier_int, ...]}
    room_tier_overrides = models.JSONField(default=dict, blank=True)

    # Chat background
    BG_CHOICES = [
        ("logo", "Church Logo (watermark)"),
        ("upload", "Custom image"),
        ("none", "No background"),
    ]
    bg_type = models.CharField(max_length=16, choices=BG_CHOICES, default="logo")
    bg_image = models.ImageField(upload_to="chat_bg/", blank=True, null=True)

    class Meta:
        verbose_name = "Chat Settings"

    def __str__(self):
        return f"ChatSettings for {self.church}"


class AttendanceRecord(ChurchOwnedModel):
    STATUS_CHOICES = [
        ("present", "Present"),
        ("late", "Late"),
        ("excused", "Excused"),
        ("absent", "Absent"),
    ]
    LOCATION_CHOICES = [
        ("onsite", "Onsite"),
        ("offsite", "Offsite"),
        ("virtual", "Virtual"),
    ]
    # user is a ChurchMember — the lowest common identity covering trainees,
    # workforce members, and admins alike.
    user = models.ForeignKey(
        ChurchMember, on_delete=models.CASCADE, related_name="attendance_records"
    )
    event = models.ForeignKey(
        "services.Event", on_delete=models.CASCADE, related_name="attendance_records"
    )
    date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="absent")
    location_tag = models.CharField(
        max_length=10,
        choices=LOCATION_CHOICES,
        blank=True,
        default="",
        help_text="Where the attendance was recorded from for hybrid events.",
    )
    remarks = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "event", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.user} — {self.event} ({self.date})"


class ClockRecord(ChurchOwnedModel):
    # user is a ChurchMember — consistent with AttendanceRecord
    user = models.ForeignKey(
        ChurchMember, on_delete=models.CASCADE, related_name="clock_records"
    )
    event = models.ForeignKey(
        "services.Event",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="clock_records",
    )
    clock_in = models.DateTimeField(null=True, blank=True)
    clock_out = models.DateTimeField(null=True, blank=True)
    date = models.DateField(default=timezone.localdate)

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
    user = models.ForeignKey(WorkforceMember, on_delete=models.CASCADE, db_index=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    date = models.DateField()
    time = models.TimeField(null=True, blank=True)
    is_done = models.BooleanField(default=False)

    class Meta:
        ordering = ["date", "time"]

    def __str__(self):
        return f"{self.user} — {self.title} ({self.date})"


class UserActivity(ChurchOwnedModel):
    ACTIVITY_TYPES = [
        ("followup", "Follow-up"),
        ("message", "Message Sent"),
        ("guest_view", "Viewed Guest"),
        ("call", "Called Guest"),
        ("report", "Submitted Report"),
        ("other", "Other"),
    ]
    user = models.ForeignKey(
        ChurchMember, on_delete=models.CASCADE, related_name="activities", db_index=True
    )
    activity_type = models.CharField(max_length=50, choices=ACTIVITY_TYPES)
    guest_id = models.CharField(max_length=50, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "User Activities"

    def __str__(self):
        return f"{self.user} — {self.activity_type} ({self.created_at.strftime('%Y-%m-%d')})"


# ── Workforce-wide Announcements ──────────────────────────────────────────────


class WorkforceAnnouncement(ChurchOwnedModel):
    """
    Church-wide announcement visible to the entire workforce (or a subset).

    Unlike UnitAnnouncement (which is unit-scoped), this is published by
    admins/sub-admins and appears on every member's dashboard.

    target_units: if empty, visible to all workforce members.
    is_banner:    show as a scrolling banner across the top of the dashboard.
    """

    PRIORITY_CHOICES = [
        ("low", "Low"),
        ("normal", "Normal"),
        ("high", "High"),
        ("urgent", "Urgent"),
    ]

    title = models.CharField(max_length=200)
    body = models.TextField()
    priority = models.CharField(
        max_length=10, choices=PRIORITY_CHOICES, default="normal", db_index=True
    )
    is_banner = models.BooleanField(
        default=False, help_text="Show as scrolling banner on dashboard."
    )
    is_pinned = models.BooleanField(default=False, db_index=True)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Auto-hide after this date/time."
    )
    # Targeting
    target_units = models.ManyToManyField(
        "units.ChurchUnit",
        blank=True,
        related_name="workforce_announcements",
        help_text="Leave empty = visible to all workforce members.",
    )
    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="workforce_announcements",
    )

    class Meta:
        ordering = ["-is_pinned", "-priority", "-created_at"]
        indexes = [
            models.Index(fields=["church", "is_pinned", "-created_at"]),
            models.Index(fields=["church", "expires_at"]),
        ]

    def __str__(self):
        return self.title

    @property
    def is_active(self):
        from django.utils import timezone

        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True


class WorkforceTask(ChurchOwnedModel):
    """
    Task assigned by admin/sub-admin to a unit, group, or individual member.

    Unlike UnitTask (managed by unit heads), WorkforceTasks are created at
    the church/workforce level and can span multiple units.
    """

    PRIORITY_CHOICES = [
        ("low", "Low"),
        ("normal", "Normal"),
        ("high", "High"),
        ("urgent", "Urgent"),
    ]
    STATUS_CHOICES = [
        ("open", "Open"),
        ("in_progress", "In Progress"),
        ("review", "In Review"),
        ("done", "Done"),
        ("cancelled", "Cancelled"),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    priority = models.CharField(
        max_length=10, choices=PRIORITY_CHOICES, default="normal"
    )
    status = models.CharField(
        max_length=15, choices=STATUS_CHOICES, default="open", db_index=True
    )
    due_date = models.DateField(null=True, blank=True)

    # Assignment: unit(s), or individual member(s), or both
    assigned_units = models.ManyToManyField(
        "units.ChurchUnit", blank=True, related_name="workforce_tasks"
    )
    assigned_members = models.ManyToManyField(
        "units.UnitMembership", blank=True, related_name="workforce_tasks"
    )

    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_workforce_tasks",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "status", "-created_at"]),
            models.Index(fields=["church", "due_date"]),
        ]

    def __str__(self):
        return self.title

    @property
    def is_overdue(self):
        from django.utils import timezone

        return bool(
            self.due_date
            and self.due_date < timezone.localdate()
            and self.status not in ("done", "cancelled")
        )
