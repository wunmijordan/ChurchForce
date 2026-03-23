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

from core.models import ChurchOwnedModel, OrderedChurchModel
from core.utils.colors import get_unit_color, resolve_color


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
        ("unit",  "Unit / Department"),
        ("group", "Group / Team"),
    ]

    name        = models.CharField(max_length=255)
    slug        = models.SlugField(blank=True)
    description = models.TextField(blank=True)
    icon        = models.CharField(max_length=50, blank=True)
    color       = models.CharField(max_length=20, default="blue")
    unit_type   = models.CharField(
        max_length=10, choices=UNIT_TYPE_CHOICES, default="unit", db_index=True,
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

    is_default  = models.BooleanField(
        default=False,
        help_text="New members are automatically assigned to default units.",
    )

    # Reporting hierarchy — self-referential
    report_to = models.ForeignKey(
        "self",
        null=True, blank=True,
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
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @property
    def member_count(self):
        return self.memberships.filter(is_active=True).count()

    @property
    def is_unit(self):
        return self.unit_type == "unit"

    @property
    def is_group(self):
        return self.unit_type == "group"

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
            self.memberships
            .filter(is_active=True, is_unit_head=True)
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
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="unit_memberships",
        help_text="Full workforce member. Null if member is still a trainee.",
    )
    trainee_profile = models.ForeignKey(
        "workforce.WorkforceTraineeProfile",
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="unit_memberships",
        help_text="Trainee profile, used during probation or induction.",
    )
    unit       = models.ForeignKey(
        ChurchUnit,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    joined_at  = models.DateField(auto_now_add=True)

    # Status flags
    is_probation  = models.BooleanField(
        default=False,
        help_text="Member is on probation — limited to trainee-tier access.",
    )
    is_unit_head  = models.BooleanField(
        default=False,
        help_text="This member is the head of the unit.",
    )

    # Attendance tracking
    consecutive_absences = models.PositiveSmallIntegerField(
        default=0,
        help_text="Resets on any attendance; triggers review at 2.",
    )
    for_review = models.BooleanField(
        default=False, db_index=True,
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
        member = (
            self.workforce_member or self.trainee_profile or "Unknown"
        )
        return f"{member} → {self.unit}"

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
            head_member = head.workforce_member.member if head.workforce_member else None
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
            logging.getLogger(__name__).warning("_notify_unit_head_of_absences failed: %s", exc)


class UnitAttendanceRecord(ChurchOwnedModel):
    """
    Attendance record for a unit meeting, task, or group session.
    Distinct from service-level AttendanceRecord in workforce.models.
    """

    STATUS_CHOICES = [
        ("present", "Present"),
        ("absent",  "Absent"),
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
    date        = models.DateField(db_index=True)
    status      = models.CharField(max_length=10, choices=STATUS_CHOICES, default="absent")
    session     = models.CharField(
        max_length=255, blank=True,
        help_text="e.g. 'Weekly rehearsal', 'Task: Sound check', 'Leadership meeting'",
    )
    marked_by   = models.ForeignKey(
        "accounts.ChurchMember",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="unit_attendance_marks",
    )
    notes       = models.TextField(blank=True)

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


class ChatRoom(ChurchOwnedModel):
    """
    A chat room within the church.

    Rooms are created from existing ChurchUnit (unit or group) records.
    One unit → one primary room. Additional rooms can be created per unit
    for sub-topics.

    room_type:
        'unit'    — default room for a unit/group (auto-created with unit)
        'project' — temporary room for a cross-unit project
        'private' — 1-1 private conversation (modal-based, no history outside modal)

    Private rooms are created on request and require approval from a room admin.
    """

    ROOM_TYPE_CHOICES = [
        ("unit",    "Unit Room"),
        ("project", "Project Room"),
        ("private", "Private Conversation"),
    ]

    name      = models.CharField(max_length=255)
    unit      = models.ForeignKey(
        ChurchUnit,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="chat_rooms",
        help_text="The unit or group this room belongs to.",
    )
    room_type = models.CharField(
        max_length=10, choices=ROOM_TYPE_CHOICES, default="unit", db_index=True,
    )
    is_default = models.BooleanField(
        default=False,
        help_text="The primary room for a unit. Auto-created when a unit is created.",
    )
    description = models.TextField(blank=True)
    created_by  = models.ForeignKey(
        "accounts.ChurchMember",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="created_rooms",
    )

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["church", "unit"]),
            models.Index(fields=["church", "room_type"]),
        ]

    def __str__(self):
        return f"{self.name} [{self.get_room_type_display()}]"


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

    room      = models.ForeignKey(
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
# Campus models
# ─────────────────────────────────────────────────────────────────────────────

class Campus(ChurchOwnedModel):
    """
    A campus or branch of a church.

    Campuses can form a hierarchy (satellite campus → regional campus → HQ).
    report_to is a self-FK for this. At the top level, report_to is None
    (the main/headquarter campus).

    growth_stage is an optional label (e.g. 'Daughter Church', 'Satellite',
    'Regional', 'Headquarters') — configurable per church if the church uses
    a multi-tier hierarchy model.

    Members can belong to multiple campuses simultaneously (e.g. staff who
    oversee multiple locations), but a member can only be in ONE church.
    """

    GROWTH_STAGE_CHOICES = [
        ("cell",         "Cell Group"),
        ("satellite",    "Satellite Campus"),
        ("daughter",     "Daughter Church"),
        ("regional",     "Regional Campus"),
        ("headquarters", "Headquarters"),
    ]

    name         = models.CharField(max_length=255)
    slug         = models.SlugField(blank=True)
    description  = models.TextField(blank=True)
    address      = models.TextField(blank=True)
    latitude     = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
    )
    longitude    = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
    )
    growth_stage = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
        help_text="Optional campus growth stage label (configurable per church).",
    )
    report_to    = models.ForeignKey(
        "self",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="sub_campuses",
        help_text="Parent campus this campus reports to.",
    )
    # Metrics roll up to parent campus if use_rollup_metrics=True on parent
    contributes_to_parent_metrics = models.BooleanField(
        default=True,
        help_text=(
            "If True, this campus guest/attendance counts roll up to the "
            "parent campus metrics report."
        ),
    )
    campus_leader = models.ForeignKey(
        "accounts.ChurchMember",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="led_campuses",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["church", "slug"],
                name="unique_campus_slug_per_church",
            )
        ]
        indexes = [
            models.Index(fields=["church", "growth_stage"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class CampusMembership(ChurchOwnedModel):
    """
    Links a ChurchMember to a Campus.
    A member can be in multiple campuses.
    A member can only be in one church (enforced at ChurchMember level).
    """

    member    = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="campus_memberships",
    )
    campus    = models.ForeignKey(
        Campus,
        on_delete=models.CASCADE,
        related_name="members",
    )
    joined_at = models.DateField(auto_now_add=True)
    is_primary = models.BooleanField(
        default=False,
        help_text="The member's primary campus (home base).",
    )
    notes     = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["member", "campus"],
                name="unique_campus_membership",
            )
        ]
        indexes = [
            models.Index(fields=["church", "campus"]),
            models.Index(fields=["church", "member"]),
        ]

    def __str__(self):
        return f"{self.member} @ {self.campus}"



