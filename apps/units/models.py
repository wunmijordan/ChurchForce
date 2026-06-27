"""
units/models.py

ChurchUnit covers both traditional units (departments) and cross-unit
groups. The distinction is unit_type: 'unit' vs 'group'.

    Unit   — a department (Music, Media, Magnet/Guests, Ushering, Security…)
              Members belong to ONE primary unit at a time.

    Group  — a cross-unit cohort (prayer team, project team, leadership circle…)
              Members can be in MANY groups simultaneously, and can be drawn
              from different units. Groups are not mutually exclusive with units.

UnitMembership covers both. A member's primary unit is their 'unit' type
membership. Group memberships are additive.

UnitHierarchy: every unit/group can optionally report_to a parent unit,
forming a tree up to the pastoral level. This drives the notification
chain when attendance flags are raised.

Probation: a UnitMembership can be marked is_probation=True. This restricts
the member to the WorkforceTraineeProfile tier, granting LMS access and
church-wide notices but NOT full unit tools.

UnitAttendanceRecord: tracks presence at unit meetings/tasks (separate
from service attendance). Two consecutive absences triggers notification
to the unit head and marks the membership for_review=True.
"""

from django.db import models
from django.utils.functional import cached_property
from django.utils.text import slugify
from django.utils import timezone
from core.models import ChurchOwnedModel, OrderedChurchModel
from core.utils.colors import get_unit_color, resolve_color
from tenants.models import Campus, CampusMembership


class ChurchUnit(ChurchOwnedModel):
    """
    A unit (department) or cross-unit group within a church.

    unit_type distinguishes the two:
        'unit'  — a structured department; members have a single primary unit
        'group' — an ad-hoc or cross-unit cohort; additive membership

    report_to enables a hierarchy: unit → zone leader → pastor.
    The notification chain for attendance flags follows this path.
    """

    UNIT_TYPE_CHOICES = [
        ("unit", "Unit / Department"),
        ("group", "Group / Team"),
    ]

    name = models.CharField(max_length=255)
    slug = models.SlugField(
        blank=True, editable=False, help_text="Auto-generated from name if left blank."
    )
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True)
    color = models.CharField(max_length=20, default="blue")
    unit_type = models.CharField(
        max_length=10,
        choices=UNIT_TYPE_CHOICES,
        default="unit",
        db_index=True,
        help_text="'Unit' for departments; 'Group' for cross-unit cohorts.",
    )
    guest_management = models.BooleanField(
        default=False,
        help_text="Enable guest management features for this unit.",
    )
    music_module = models.BooleanField(
        default=False,
        help_text="Enable music module features for this unit.",
    )
    media_module = models.BooleanField(
        default=False,
        help_text="Enable media module features for this unit.",
    )
    children_module = models.BooleanField(
        default=False,
        help_text="Enable children ministry features for this unit.",
    )
    youth_module = models.BooleanField(
        default=False,
        help_text="Enable youth ministry features for this unit.",
    )
    teenagers_module = models.BooleanField(
        default=False,
        help_text="Enable teenagers ministry features for this unit.",
    )

    is_default = models.BooleanField(
        default=False,
        help_text="New members are automatically assigned to default units.",
    )

    # Reporting hierarchy — self-referential
    report_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sub_units",
        help_text="Parent unit this unit reports to (e.g. a zone or pastoral office).",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["church", "slug"],
                name="unique_unit_slug_per_church",
            )
        ]
        indexes = [
            models.Index(fields=["church", "name"]),
            models.Index(fields=["church", "unit_type"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """
        Generate a church-scoped unique slug.
        Also seeds UnitFunctionalRoles on first save or when music_module is toggled on.
        """
        # Track music_module toggle so we can seed roles after save
        _music_just_enabled = False
        _is_new = not bool(self.pk)
        if not _is_new:
            try:
                prev = (
                    ChurchUnit.objects.filter(pk=self.pk).values("music_module").first()
                )
                if prev and not prev["music_module"] and self.music_module:
                    _music_just_enabled = True
            except Exception:
                pass

        if not self.slug:
            base_slug = slugify(self.name) or "unit"
            slug = base_slug
            counter = 2
            while (
                ChurchUnit.objects.filter(church=self.church, slug=slug)
                .exclude(pk=self.pk)
                .exists()
            ):
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug

        super().save(*args, **kwargs)

        # Post-save: seed functional roles (import deferred to avoid circular)
        try:
            from units.models import UnitFunctionalRole as _UFR

            if _is_new or _music_just_enabled:
                _UFR.seed_for_unit(self)
            else:
                # Ensure at least Secretary exists on every unit
                _UFR.objects.get_or_create(
                    church=self.church,
                    unit=self,
                    preset=_UFR.PRESET_SECRETARY,
                    defaults={"name": "Secretary", "is_active": True},
                )
        except Exception:
            pass

    @property
    def member_count(self):
        return self.memberships.filter(is_active=True).count()

    @property
    def is_unit(self):
        return self.unit_type == "Unit"

    @property
    def is_group(self):
        return self.unit_type == "Group"

    @cached_property
    def color_class(self):
        if self.color:
            return f"bg-{self.color} text-white"
        return get_unit_color(self.id or self.name, variant="class")

    @cached_property
    def color_hex(self):
        if self.color:
            return resolve_color(self.color, variant="hex")
        return get_unit_color(self.id or self.name, variant="hex")

    @cached_property
    def color_data(self):
        return {"class": self.color_class, "hex": self.color_hex}

    def get_unit_head(self):
        """Return the UnitMembership of the head of this unit, or None."""
        return (
            self.memberships.filter(is_active=True, is_unit_head=True)
            .select_related("workforce_member__member__user")
            .first()
        )

    def get_reporting_chain(self):
        """Return list of units from this unit up to the root."""
        chain = []
        current = self
        seen = set()
        while current and current.pk not in seen:
            chain.append(current)
            seen.add(current.pk)
            current = current.report_to
        return chain


class UnitMembership(ChurchOwnedModel):
    """
    Links a WorkforceMember (or WorkforceTraineeProfile) to a unit or group.

    is_probation: True when a member is undergoing probationary period in
    this unit. Their profile is downgraded to WorkforceTraineeProfile tier
    until probation ends.

    is_unit_head: True for the head of a unit. Used for notification routing
    and permission escalation.

    consecutive_absences: reset to 0 on any attendance, incremented on each
    absence. Reaching 2 triggers a notification to the unit head.

    for_review: set True when consecutive_absences reaches 2. Cleared by
    the unit head or admin after follow-up.
    """

    workforce_member = models.ForeignKey(
        "workforce.WorkforceMember",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="unit_memberships",
        help_text="Full workforce member. Null if member is still a trainee.",
    )
    trainee_profile = models.ForeignKey(
        "workforce.WorkforceTraineeProfile",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="unit_memberships",
        help_text="Trainee profile, used during probation or induction.",
    )
    unit = models.ForeignKey(
        ChurchUnit,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    joined_at = models.DateField(auto_now_add=True)

    # Status flags
    is_probation = models.BooleanField(
        default=False,
        help_text="Member is on probation — limited to trainee-tier access.",
    )
    is_unit_head = models.BooleanField(
        default=False,
        help_text="This member is the head of the unit.",
    )
    is_assistant = models.BooleanField(
        default=False,
        help_text=(
            "This member assists the unit head. Gets tier-4 (Assistant) permissions "
            "automatically via the resolver, even without an explicit WorkforceRole."
        ),
    )

    # Attendance tracking
    consecutive_absences = models.PositiveSmallIntegerField(
        default=0,
        help_text="Resets on any attendance; triggers review at 2.",
    )
    for_review = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Set True when 2 consecutive absences are recorded.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workforce_member", "unit"],
                name="unique_unit_membership",
                condition=models.Q(workforce_member__isnull=False),
            ),
            models.UniqueConstraint(
                fields=["trainee_profile", "unit"],
                name="unique_trainee_unit_membership",
                condition=models.Q(trainee_profile__isnull=False),
            ),
        ]
        indexes = [
            models.Index(fields=["church", "unit"]),
            models.Index(fields=["church", "workforce_member"]),
            models.Index(fields=["church", "is_probation"]),
            models.Index(fields=["church", "for_review"]),
        ]

    def __str__(self):
        member = self.workforce_member or self.trainee_profile or "Unknown"
        return f"{member}"

    def record_attendance(self, present: bool):
        """
        Record attendance for this membership.
        Resets consecutive_absences on presence; increments on absence.
        Triggers for_review flag and notification at 2 consecutive absences.
        """
        if present:
            self.consecutive_absences = 0
            self.for_review = False
            self.save(update_fields=["consecutive_absences", "for_review"])
        else:
            self.consecutive_absences += 1
            if self.consecutive_absences >= 2:
                self.for_review = True
                self.save(update_fields=["consecutive_absences", "for_review"])
                self._notify_unit_head_of_absences()
            else:
                self.save(update_fields=["consecutive_absences"])

    def _notify_unit_head_of_absences(self):
        """Notify the unit head when a member reaches 2 consecutive absences."""
        try:
            head = self.unit.get_unit_head()
            if not head:
                return
            head_member = (
                head.workforce_member.member if head.workforce_member else None
            )
            if not head_member:
                return
            from notifications.utils import notify_members

            name = str(self.workforce_member or self.trainee_profile or "A member")
            notify_members(
                [head_member],
                title=f"⚠️ Attendance alert: {name}",
                description=(
                    f"{name} has missed {self.consecutive_absences} consecutive "
                    f"meetings in {self.unit.name} and has been flagged for review."
                ),
                church=self.church,
                is_urgent=True,
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "_notify_unit_head_of_absences failed: %s", exc
            )


class UnitAttendanceRecord(ChurchOwnedModel):
    """
    Attendance record for a unit meeting, task, or group session.
    Distinct from service-level AttendanceRecord in workforce.models.
    """

    STATUS_CHOICES = [
        ("present", "Present"),
        ("absent", "Absent"),
        ("excused", "Excused"),
    ]

    membership = models.ForeignKey(
        UnitMembership,
        on_delete=models.CASCADE,
        related_name="unit_attendance_records",
    )
    unit = models.ForeignKey(
        ChurchUnit,
        on_delete=models.CASCADE,
        related_name="attendance_records",
    )
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="absent")
    session = models.CharField(
        max_length=255,
        blank=True,
        help_text="e.g. 'Weekly rehearsal', 'Task: Sound check', 'Leadership meeting'",
    )
    marked_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="unit_attendance_marks",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date"]
        unique_together = ("membership", "date", "session")
        indexes = [
            models.Index(fields=["church", "unit", "date"]),
            models.Index(fields=["church", "membership", "-date"]),
        ]

    def __str__(self):
        return f"{self.membership} — {self.date} [{self.status}]"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Propagate attendance to the membership's consecutive_absences tracker
        self.membership.record_attendance(present=(self.status == "present"))


class UnitAnnouncement(ChurchOwnedModel):
    """
    Announcements scoped to a unit or group.
    """

    unit = models.ForeignKey(
        ChurchUnit,
        on_delete=models.CASCADE,
        related_name="announcements",
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="unit_announcements",
    )
    is_pinned = models.BooleanField(default=False, db_index=True)
    pinned_at = models.DateTimeField(null=True, blank=True)
    is_banner = models.BooleanField(
        default=False,
        help_text="Show as scrolling banner on dashboard.",
    )

    class Meta:
        ordering = ["-is_pinned", "-created_at"]
        indexes = [
            models.Index(fields=["church", "unit", "-created_at"]),
            models.Index(fields=["church", "unit", "is_pinned"]),
        ]

    def __str__(self):
        return f"{self.unit} — {self.title}"

    def save(self, *args, **kwargs):
        if self.is_pinned and not self.pinned_at:
            from django.utils import timezone

            self.pinned_at = timezone.now()
        super().save(*args, **kwargs)


class UnitTask(ChurchOwnedModel):
    """
    Tasks scoped to a unit or group.
    Can be assigned to a single member or multiple members.
    """

    TASK_TYPE_CHOICES = [
        ("single", "Single Assignee"),
        ("group", "Group Task"),
    ]

    unit = models.ForeignKey(
        "units.ChurchUnit",
        on_delete=models.CASCADE,
        related_name="tasks",
    )

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    # NEW — assignment mode
    task_type = models.CharField(
        max_length=10,
        choices=TASK_TYPE_CHOICES,
        default="single",
        db_index=True,
    )

    # EXISTING (single assignment)
    assigned_to = models.ForeignKey(
        "units.UnitMembership",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="single_assignee_tasks",
    )

    # NEW — group assignment
    assignees = models.ManyToManyField(
        "units.UnitMembership",
        blank=True,
        related_name="group_tasks",
    )

    due_date = models.DateField(null=True, blank=True)

    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_unit_tasks",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "unit", "task_type"]),
            models.Index(fields=["church", "unit", "-created_at"]),
        ]

    def __str__(self):
        return self.title

    # --------------------------------------------------
    # COMPUTED WORKFLOW STATUS
    # --------------------------------------------------

    @property
    def computed_status(self):
        """
        Aggregated task status derived from assignments.
        """

        assignments = self.assignments.all()

        if not assignments.exists():
            return "open"

        if all(a.status == "done" for a in assignments):
            return "done"

        if any(a.status == "in_progress" for a in assignments):
            return "in_progress"

        return "open"

    # --------------------------------------------------
    # HELPER FLAGS
    # --------------------------------------------------

    @property
    def is_overdue(self):
        if not self.due_date:
            return False

        return self.due_date < timezone.now().date() and self.computed_status != "done"

    @property
    def completion_percentage(self):
        assignments = self.assignments.all()

        total = assignments.count()
        if total == 0:
            return 0

        done = assignments.filter(status="done").count()
        return int((done / total) * 100)


class TaskAssignment(ChurchOwnedModel):
    """
    Tracks each assignee's interaction with a task.
    """

    STATUS_CHOICES = [
        ("open", "Open"),
        ("in_progress", "In Progress"),
        ("done", "Done"),
    ]

    task = models.ForeignKey(
        "units.UnitTask",
        on_delete=models.CASCADE,
        related_name="assignments",
    )

    membership = models.ForeignKey(
        "units.UnitMembership",
        on_delete=models.CASCADE,
        related_name="task_assignments",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="open",
        db_index=True,
    )

    acknowledged_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    completion_note = models.TextField(blank=True)

    class Meta:
        unique_together = ("task", "membership")
        indexes = [
            models.Index(fields=["church", "status"]),
        ]

    def __str__(self):
        return f"{self.membership} → {self.task}"


class UnitReport(ChurchOwnedModel):
    """
    Member-submitted unit/group report for services, tasks, or trainings.

    Reports can be routed to the immediate head, escalated up the
    unit hierarchy, and optionally copied to a Pastor (tier-1 global role).
    """

    CATEGORY_CHOICES = [
        ("service", "Service Report"),
        ("task", "Task Report"),
        ("training", "Training Report"),
    ]

    STATUS_CHOICES = [
        ("submitted", "Submitted"),
        ("reviewed", "Reviewed"),
        ("escalated", "Escalated"),
        ("closed", "Closed"),
    ]

    unit = models.ForeignKey(
        ChurchUnit,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    submitted_by = models.ForeignKey(
        "units.UnitMembership",
        on_delete=models.CASCADE,
        related_name="submitted_reports",
    )
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        db_index=True,
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    related_task = models.ForeignKey(
        "units.UnitTask",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports",
        help_text="Optional link to a specific unit task.",
    )
    report_to_unit = models.ForeignKey(
        ChurchUnit,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="incoming_reports",
        help_text="Next unit/group in the reporting chain.",
    )
    copy_pastor = models.BooleanField(
        default=False,
        help_text="When enabled, notify active Pastor role holders globally.",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="submitted",
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_unit_reports",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    escalation_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "unit", "-created_at"]),
            models.Index(fields=["church", "status"]),
            models.Index(fields=["church", "category"]),
        ]

    def __str__(self):
        return f"{self.unit.name} • {self.title}"


class ChatRoom(ChurchOwnedModel):
    """
    A chat room within the church.

    Rooms are created from existing ChurchUnit (unit or group) records.
    One unit → one primary room (is_default=True). Additional sub-rooms can
    be created per unit for sub-topics (room_type='project', is_default=False).

    Sub-rooms are EXCLUSIVE to the modal — they never appear in the main sidebar.
    Their membership is tracked via the `members` M2M, drawn from ChurchMember
    records of the parent room's unit.

    room_type:
        'unit'    — default room for a unit/group (auto-created with unit)
        'project' — sub-room / temporary room for a topic within a unit
        'private' — 1-1 private conversation (modal-based, no history outside modal)

    parent_room:
        Set on sub-rooms (project type). Points to the parent unit room.
        Null on all top-level (unit) rooms.

    members:
        Explicit member list for sub-rooms. For unit rooms this is left empty
        (membership is derived from UnitMembership). Sub-room creator can add
        any ChurchMember from the parent room's unit.
    """

    ROOM_TYPE_CHOICES = [
        ("unit", "Unit Room"),
        ("project", "Project Room"),
        ("private", "Private Conversation"),
    ]

    name = models.CharField(max_length=255)
    unit = models.ForeignKey(
        ChurchUnit,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="chat_rooms",
        help_text="The unit or group this room belongs to.",
    )
    room_type = models.CharField(
        max_length=10,
        choices=ROOM_TYPE_CHOICES,
        default="unit",
        db_index=True,
    )
    is_default = models.BooleanField(
        default=False,
        help_text="The primary room for a unit. Auto-created when a unit is created.",
    )
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_rooms",
    )

    # Sub-room hierarchy — null on top-level unit rooms
    parent_room = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="sub_rooms",
        help_text="Set on sub-rooms (project type); null on top-level unit rooms.",
    )

    # Explicit membership for sub-rooms, drawn from the parent unit's members.
    # Top-level unit rooms derive membership from UnitMembership instead.
    members = models.ManyToManyField(
        "accounts.ChurchMember",
        blank=True,
        related_name="joined_chat_rooms",
        help_text="Explicit member list for sub-rooms. Leave empty for unit rooms.",
    )

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["church", "unit"]),
            models.Index(fields=["church", "room_type"]),
            models.Index(fields=["church", "parent_room"]),
        ]

    def __str__(self):
        return f"{self.name} [{self.get_room_type_display()}]"

    @property
    def is_sub_room(self):
        return self.parent_room_id is not None

    @property
    def chat_tier_overrides(self):
        from workforce.models import ChatSettings

        cs = ChatSettings.objects.filter(church=self.church).first()
        if not cs:
            return []
        return cs.room_tier_overrides.get(str(self.pk), [])

    def get_accessible_members(self):
        """
        Returns ChurchMember qs of everyone who can see this room.
        - Unit rooms: all active UnitMembership holders for the room's unit.
        - Sub-rooms:  the explicit `members` M2M (set at creation / via modal).
        """
        from accounts.models import ChurchMember

        if self.is_sub_room:
            return self.members.filter(is_active=True)
        if self.unit:
            return ChurchMember.raw_objects.filter(
                church=self.church,
                is_active=True,
                workforce_member__unit_memberships__unit=self.unit,
                workforce_member__unit_memberships__is_active=True,
            ).distinct()
        return ChurchMember.raw_objects.filter(church=self.church, is_active=True)


class PrivateChatRequest(ChurchOwnedModel):
    """
    Tracks an active private side-conversation between two members
    who are both present in the same ChatRoom.

    No approval required — any member can initiate a private chat
    with another member in a room they are both in. The conversation
    opens as a modal overlay within the room view.

    The session is ephemeral (modal-based). This record exists only
    to coordinate the WebSocket pairing and to allow either party to
    close / dismiss the conversation.

    room       — the shared room both members are currently in.
    requester  — the member who clicked the other person's avatar.
    recipient  — the member whose avatar was clicked.
    is_active  — True while the private chat modal is open. Set False
                 when either party closes it.
    """

    room = models.ForeignKey(
        ChatRoom, on_delete=models.CASCADE, related_name="private_sessions"
    )
    requester = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="initiated_private_chats",
    )
    recipient = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="received_private_chats",
    )

    class Meta:
        # Only one active session per pair per room at a time
        unique_together = ("room", "requester", "recipient")
        indexes = [models.Index(fields=["church", "is_active"])]

    def __str__(self):
        return f"{self.requester} ↔ {self.recipient} in {self.room}"


# ─────────────────────────────────────────────────────────────────────────────
# Unit Functional Roles
# ─────────────────────────────────────────────────────────────────────────────


class UnitFunctionalRole(ChurchOwnedModel):
    """
    A configurable functional role within any unit or group.

    Functional roles describe WHAT a member does inside a unit — they are
    entirely separate from permission tiers (which control system access).

    DESIGN PRINCIPLE
    ────────────────
    The model is the same for all units. The difference is only in which
    presets are auto-seeded when a unit is created or updated:

      Music-enabled units (music_module=True)
        → Seeded with all MUSIC_PRESETS (incl. Secretary) on first music enable.

      All other units/groups
        → Seeded with GENERAL_PRESETS (Secretary only) on creation.

    Both sets are fully configurable: admins/unit-heads can rename roles,
    toggle them off, or add completely custom ones (preset='custom').
    Only the Secretary preset is shared between the two seed sets — music
    units get it as part of the music bundle, general units get it alone.

    ASSIGNMENT
    ──────────
    Members get roles via UnitFunctionalRoleAssignment, which supports:
        'permanent'  — standing role (e.g. regular guitarist)
        'period'     — for a date range (start_date → end_date)
        'event'      — for one specific event only

    Multiple assignments per member are allowed simultaneously.
    """

    # Preset slugs
    PRESET_MUSIC_DIRECTOR = "music_director"
    PRESET_GUITARIST = "guitarist"
    PRESET_BASSIST = "bassist"
    PRESET_DRUMMER = "drummer"
    PRESET_VOCALIST = "vocalist"
    PRESET_BACKUP_VOCALIST = "backup_vocalist"
    PRESET_SECRETARY = "secretary"  # universal — in both seed sets
    PRESET_CUSTOM = "custom"

    PRESET_CHOICES = [
        (PRESET_MUSIC_DIRECTOR, "Music Director"),
        (PRESET_GUITARIST, "Guitarist"),
        (PRESET_BASSIST, "Bassist"),
        (PRESET_DRUMMER, "Drummer"),
        (PRESET_VOCALIST, "Vocalist"),
        (PRESET_BACKUP_VOCALIST, "Backup Vocalist"),
        (PRESET_SECRETARY, "Secretary"),
        (PRESET_CUSTOM, "Custom"),
    ]

    # Auto-seeded for music-enabled units (includes Secretary)
    MUSIC_PRESETS = [
        (PRESET_MUSIC_DIRECTOR, "Music Director"),
        (PRESET_GUITARIST, "Guitarist"),
        (PRESET_BASSIST, "Bassist"),
        (PRESET_DRUMMER, "Drummer"),
        (PRESET_VOCALIST, "Vocalist"),
        (PRESET_BACKUP_VOCALIST, "Backup Vocalist"),
        (PRESET_SECRETARY, "Secretary"),
    ]

    # Auto-seeded for ALL units/groups (music units get the full bundle above)
    GENERAL_PRESETS = [
        (PRESET_SECRETARY, "Secretary"),
    ]

    unit = models.ForeignKey(
        "units.ChurchUnit",
        on_delete=models.CASCADE,
        related_name="functional_roles",
    )
    name = models.CharField(max_length=100)
    preset = models.CharField(
        max_length=30,
        choices=PRESET_CHOICES,
        default=PRESET_CUSTOM,
        db_index=True,
        help_text="Built-in preset slug, or 'custom' for user-defined roles.",
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["church", "unit", "name"],
                name="unique_functional_role_per_unit",
            )
        ]
        indexes = [
            models.Index(fields=["church", "unit"]),
            models.Index(fields=["church", "unit", "preset"]),
            models.Index(fields=["church", "unit", "is_active"]),
        ]

    def __str__(self):
        return f"{self.unit.name} / {self.name}"

    @classmethod
    def seed_for_unit(cls, unit):
        """
        Idempotent seed: create missing default roles for a unit.

        Music units  → MUSIC_PRESETS  (all 7, including Secretary).
        Other units  → GENERAL_PRESETS (Secretary only).

        Safe to call multiple times — only creates roles that don't exist.
        Returns list of newly created instances.
        """
        presets = cls.MUSIC_PRESETS if unit.music_module else cls.GENERAL_PRESETS
        created = []
        for preset_slug, default_name in presets:
            role, was_created = cls.objects.get_or_create(
                church=unit.church,
                unit=unit,
                preset=preset_slug,
                defaults={"name": default_name, "is_active": True},
            )
            if was_created:
                created.append(role)
        return created

    @classmethod
    def backfill_music_roles(cls, unit):
        """Add missing music presets when music_module is toggled ON."""
        return cls.seed_for_unit(unit)


class UnitFunctionalRoleAssignment(ChurchOwnedModel):
    """
    Assigns a UnitFunctionalRole to a UnitMembership.

    assignment_type controls duration:
        'permanent'  — ongoing standing role
        'period'     — active for start_date → end_date (inclusive)
        'event'      — active for a single event only

    A member can hold multiple simultaneous assignments across different roles
    or the same role with different types.
    """

    ASSIGNMENT_TYPE_CHOICES = [
        ("permanent", "Permanent"),
        ("period", "For a Period"),
        ("event", "For an Event"),
    ]

    role = models.ForeignKey(
        UnitFunctionalRole,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    membership = models.ForeignKey(
        "units.UnitMembership",
        on_delete=models.CASCADE,
        related_name="functional_role_assignments",
    )
    assignment_type = models.CharField(
        max_length=15,
        choices=ASSIGNMENT_TYPE_CHOICES,
        default="permanent",
        db_index=True,
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    event = models.ForeignKey(
        "services.Event",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="functional_role_assignments",
    )
    notes = models.TextField(blank=True)
    assigned_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="functional_role_assignments_made",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "membership"]),
            models.Index(fields=["church", "role"]),
            models.Index(fields=["church", "event"]),
            models.Index(fields=["church", "is_active"]),
            models.Index(fields=["church", "membership", "is_active"]),
        ]

    def __str__(self):
        return f"{self.membership} → {self.role.name} [{self.assignment_type}]"

    @property
    def is_current(self):
        from django.utils import timezone

        today = timezone.now().date()
        if not self.is_active:
            return False
        if self.assignment_type == "permanent":
            return True
        if self.assignment_type == "period":
            return (not self.start_date or self.start_date <= today) and (
                not self.end_date or self.end_date >= today
            )
        return True  # event — caller checks event.date
