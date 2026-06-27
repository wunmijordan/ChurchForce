import re, uuid
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.timezone import localdate, now
from cloudinary.models import CloudinaryField
from core.models import ChurchOwnedModel, OrderedChurchModel, SoftDeleteModel
from core.managers import ScopedGuestManager, RawGuestManager
from units.models import UnitMembership
from services.models import Event


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURABLE CHURCH OPTIONS
# ─────────────────────────────────────────────────────────────────────────────


class GuestStatus(OrderedChurchModel):
    """
    Church-configurable guest statuses.

    The pipeline expects these names to exist (case-insensitive) in each
    church's status list. They are seeded by bootstrap/services.py from
    the default template:
        - New Guest    (is_default=True)
        - In-Contact
        - Committed
        - Planted
        - Not Planted

    Churches can rename the display label but the pipeline logic matches
    on the slug field so renames don't break automation.
    """

    SLUG_NEW_GUEST = "new-guest"
    SLUG_IN_CONTACT = "in-contact"
    SLUG_COMMITTED = "committed"
    SLUG_PLANTED = "planted"
    SLUG_NOT_PLANTED = "not-planted"

    name = models.CharField(max_length=100)
    slug = models.SlugField(
        max_length=50,
        help_text=(
            "Used by the pipeline to identify this status. " "Do not change once set."
        ),
    )
    color = models.CharField(max_length=30, default="gray")
    is_default = models.BooleanField(default=False)

    # Terminal statuses stop auto-progression
    is_terminal = models.BooleanField(
        default=False,
        help_text="If True, the pipeline will not auto-progress this guest further.",
    )

    class Meta:
        unique_together = [("church", "name"), ("church", "slug")]
        indexes = [
            models.Index(fields=["church", "order"]),
            models.Index(fields=["church", "slug"]),
        ]

    def __str__(self):
        return self.name


class VisitPurpose(OrderedChurchModel):
    name = models.CharField(max_length=100)

    class Meta:
        unique_together = ("church", "name")
        indexes = [models.Index(fields=["church", "order"])]

    def __str__(self):
        return self.name


class VisitChannel(OrderedChurchModel):
    name = models.CharField(max_length=100)

    class Meta:
        unique_together = ("church", "name")
        indexes = [models.Index(fields=["church", "order"])]

    def __str__(self):
        return self.name


class ChurchService(OrderedChurchModel):
    name = models.CharField(max_length=150)

    class Meta:
        unique_together = ("church", "name")
        indexes = [models.Index(fields=["church", "order"])]

    def __str__(self):
        return self.name


# ─────────────────────────────────────────────────────────────────────────────
# MAIN GUEST MODEL
# ─────────────────────────────────────────────────────────────────────────────


class GuestEntry(ChurchOwnedModel, SoftDeleteModel):

    objects = ScopedGuestManager()
    raw_objects = RawGuestManager()

    TITLE_CHOICES = [
        ("Chief", "Chief"),
        ("Dr.", "Dr."),
        ("Engr.", "Engr."),
        ("Mr.", "Mr."),
        ("Mrs.", "Mrs."),
        ("Ms.", "Ms."),
        ("Pastor", "Pastor"),
        ("Prof.", "Prof."),
    ]
    GENDER_CHOICES = [("Male", "Male"), ("Female", "Female")]
    AGE_RANGE_CHOICES = [
        ("Under 18", "Under 18"),
        ("18–25", "18–25"),
        ("26–35", "26–35"),
        ("36–45", "36–45"),
        ("46 and Above", "46 and Above"),
    ]
    MARITAL_STATUS_CHOICES = [("Single", "Single"), ("Married", "Married")]

    uid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
        help_text="Stable public identifier used in URLs instead of pk.",
    )
    picture = CloudinaryField("image", blank=True, null=True)

    @property
    def picture_url(self):
        """Avatar URL — /media/… in dev, Cloudinary in prod."""
        from core.storage import smart_image_url

        return smart_image_url(self.picture)

    custom_id = models.CharField(max_length=20, blank=True, null=True, editable=False)
    title = models.CharField(max_length=20, choices=TITLE_CHOICES)
    full_name = models.CharField(max_length=100, db_index=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    phone_number = models.CharField(max_length=20, blank=True, null=True, db_index=True)
    email = models.EmailField(blank=True, db_index=True)
    date_of_birth = models.CharField(blank=True, null=True)

    # Birthday index fields — auto-populated from date_of_birth on save.
    # Stored as integers so the daily scheduler can query efficiently:
    #   filter(birthday_month=today.month, birthday_day=today.day)
    birthday_month = models.PositiveSmallIntegerField(
        null=True, blank=True, db_index=True
    )
    birthday_day = models.PositiveSmallIntegerField(
        null=True, blank=True, db_index=True
    )

    age_range = models.CharField(max_length=20, blank=True, choices=AGE_RANGE_CHOICES)
    marital_status = models.CharField(
        max_length=20, blank=True, choices=MARITAL_STATUS_CHOICES
    )
    home_address = models.TextField(blank=True)
    occupation = models.CharField(max_length=100, blank=True)
    date_of_visit = models.DateField(default=localdate, db_index=True)
    purpose_of_visit = models.ForeignKey(
        VisitPurpose, null=True, blank=True, on_delete=models.SET_NULL
    )
    channel_of_visit = models.ForeignKey(
        VisitChannel, null=True, blank=True, on_delete=models.SET_NULL
    )
    service_attended = models.ForeignKey(ChurchService, on_delete=models.PROTECT)
    status = models.ForeignKey(
        GuestStatus,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,  # ← IMPORTANT
    )

    # Assignment
    assigned_to = models.ForeignKey(
        UnitMembership,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_guests",
    )
    assigned_at = models.DateTimeField(null=True, blank=True)

    # Thank-you message flags — set by automated SMS on first and second visits
    first_thankyou_sent = models.BooleanField(default=False)
    second_thankyou_sent = models.BooleanField(default=False)
    second_visit_date = models.DateField(null=True, blank=True, db_index=True)

    # 7-day in-contact reminder tracking
    first_contact_reminder_sent = models.BooleanField(
        default=False,
        help_text="Set when the 3-day pre-reminder is sent to the officer.",
    )
    in_contact_overdue_notified = models.BooleanField(
        default=False,
        help_text="Set when the 7-day overdue notification is sent to the unit head.",
    )

    # Referrer — who invited this guest to the church?
    # referred_by_member links to an existing member (preferred).
    # referred_by_name is a free-text fallback for non-members or guests
    # who invited someone but are not yet in the system.
    # Both are optional — this info is usually captured after first contact.
    referred_by_member = models.ForeignKey(
        UnitMembership,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="referred_guests",
        help_text="Select if the person who invited this guest is already a member.",
    )
    referred_by_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Name of the person who invited this guest (if not a member, or not yet).",
    )

    # Membership conversion — set by induct_member() when guest becomes a member
    converted_to = models.OneToOneField(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="guest_origin",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "created_at"]),
            models.Index(fields=["church", "status"]),
            models.Index(fields=["church", "assigned_to"]),
            models.Index(fields=["church", "date_of_visit"]),
            models.Index(fields=["church", "phone_number"]),
            models.Index(fields=["church", "email"]),
            models.Index(fields=["church", "birthday_month", "birthday_day"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["church", "custom_id"],
                name="unique_guest_custom_id_per_church",
            )
        ]

    def ensure_member_identity(self):
        from guests.services.identity import ensure_member_identity

        return ensure_member_identity(self)

    def clean(self):
        related = [
            getattr(self, "status", None),
            getattr(self, "purpose_of_visit", None),
            getattr(self, "channel_of_visit", None),
            getattr(self, "service_attended", None),
        ]

        for obj in related:
            if obj and obj.church_id != self.church_id:
                raise ValidationError("Configuration must belong to the same church.")

    def save(self, *args, **kwargs):

        if not self.status_id:
            self.status = (
                GuestStatus.objects.filter(church=self.church, is_default=True)
                .order_by("order")
                .first()
            )

            if not self.status:
                raise ValidationError(
                    "No default GuestStatus configured for this church."
                )

        if self.assigned_to and not self.assigned_at:
            self.assigned_at = now()

        self._sync_birthday_index()

        if self.referred_by_member and not self.referred_by_name:
            try:
                user = self.referred_by_member.workforce_member.member.user
                self.referred_by_name = user.full_name or user.username
            except AttributeError:
                pass

        if not self.custom_id:
            settings_obj = getattr(self.church, "settings", None)
            prefix = getattr(settings_obj, "guest_id_prefix", None) or "GST"

            last = (
                GuestEntry.raw_objects.filter(
                    church=self.church, custom_id__startswith=prefix
                )
                .order_by("-custom_id")
                .first()
            )

            last_num = (
                int(re.sub(r"^\D+", "", last.custom_id))
                if last and last.custom_id
                else 0
            )
            self.custom_id = f"{prefix}{last_num + 1:06d}"

        super().save(*args, **kwargs)

    def _sync_birthday_index(self):
        """
        Parse date_of_birth string (e.g. "January 01") and store month/day
        as integers so the birthday scheduler can query efficiently.
        Silently skips if the string can't be parsed.
        """
        import datetime

        dob = self.date_of_birth
        if not dob:
            self.birthday_month = None
            self.birthday_day = None
            return
        for fmt in ("%B %d", "%b %d", "%m/%d", "%d/%m"):
            try:
                parsed = datetime.datetime.strptime(dob.strip(), fmt)
                self.birthday_month = parsed.month
                self.birthday_day = parsed.day
                return
            except ValueError:
                continue
        # Unrecognised format — leave as None
        self.birthday_month = None
        self.birthday_day = None

    @property
    def initials(self):
        if self.full_name:
            return "".join([n[0].upper() for n in self.full_name.split()[:2]])
        return "G"

    @property
    def status_color(self):
        """Template-accessible colour for the guest's current pipeline status."""
        return self.status.color if self.status else "gray"

    def get_status_color(self):
        return self.status_color

    def __str__(self):
        return f"{self.custom_id} - {self.full_name}"


class SocialMediaEntry(ChurchOwnedModel):
    SOCIAL_MEDIA_CHOICES = [
        ("whatsapp", "WhatsApp"),
        ("instagram", "Instagram"),
        ("twitter", "Twitter"),
        ("linkedin", "LinkedIn"),
        ("tiktok", "Tiktok"),
    ]
    guest = models.ForeignKey(
        GuestEntry, on_delete=models.CASCADE, related_name="social_media_accounts"
    )
    platform = models.CharField(max_length=20, choices=SOCIAL_MEDIA_CHOICES)
    handle = models.CharField(max_length=255)

    class Meta:
        indexes = [models.Index(fields=["church", "guest"])]

    def save(self, *args, **kwargs):
        base_urls = {
            "linkedin": "https://www.linkedin.com/in/",
            "whatsapp": "https://wa.me/",
            "instagram": "https://www.instagram.com/",
            "twitter": "https://twitter.com/",
            "tiktok": "https://www.tiktok.com/@",
        }
        if self.platform in base_urls and not self.handle.startswith("http"):
            self.handle = base_urls[self.platform] + self.handle.lstrip("@")
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# FOLLOW-UP REPORT
# ─────────────────────────────────────────────────────────────────────────────


class FollowUpReport(ChurchOwnedModel):
    """
    Weekly report submitted by the assigned follow-up officer.

    Drives two auto-progressions:
        1. contact_answered=True  →  status auto-advances to "In-Contact"
           (if guest is still on "New Guest")
        2. Attendance count within window meets threshold  →  "Committed"
           (handled by flag_committed_guests() in the daily scheduler)

    Manual status override:
        not_planted=True  →  status set to "Not Planted" immediately on save.
        Use when the guest is dormant or has explicitly opted out.
        This is terminal — auto-progression stops.
    """

    CONTACT_TYPE_CHOICES = [
        ("call", "Phone call"),
        ("text", "Text / chat message"),
        ("visit", "Physical visit"),
    ]

    guest = models.ForeignKey(
        GuestEntry, on_delete=models.CASCADE, related_name="reports"
    )
    report_date = models.DateField(default=localdate)
    note = models.TextField()
    assigned_to = models.ForeignKey(
        UnitMembership,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_reports",
    )

    # ── Service attendance ────────────────────────────────────────────
    attended_events = models.ManyToManyField(
        Event,
        blank=True,
        related_name="followup_reports",
        help_text="Events/programmes attended this week.",
    )
    reviewed = models.BooleanField(default=False)

    # ── Contact tracking ──────────────────────────────────────────────
    contact_attempted = models.BooleanField(default=False)
    contact_answered = models.BooleanField(
        default=False,
        help_text="Set True when the guest responds. Triggers 'In-Contact' status.",
    )
    contact_type = models.CharField(
        max_length=10,
        choices=CONTACT_TYPE_CHOICES,
        blank=True,
    )
    contact_attachment = models.FileField(
        upload_to="contact_screenshots/%Y/%m/",
        blank=True,
        null=True,
        help_text="Optional screenshot of a text/chat conversation as evidence.",
    )
    call_evidence = models.ForeignKey(
        "guests.GuestCallEvidence",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="followup_reports",
        help_text="Optional verified call evidence linked to this report.",
    )

    # ── Manual terminal flag ──────────────────────────────────────────
    not_planted = models.BooleanField(
        default=False,
        help_text=(
            "Set True if the guest is dormant or has opted out. "
            "Sets status to 'Not Planted' and stops all auto-progression."
        ),
    )

    class Meta:
        ordering = ["-report_date"]
        unique_together = ["guest", "report_date"]
        indexes = [
            models.Index(fields=["church", "report_date"]),
            models.Index(fields=["church", "assigned_to"]),
            models.Index(fields=["church", "guest", "report_date"]),
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Run status auto-progression AFTER the report is saved
        self._apply_status_progression()

    def _apply_status_progression(self):
        """
        Apply automatic status changes based on this report's fields.
        Evaluated in priority order — not_planted takes precedence over everything.
        """
        guest = self.guest
        church = guest.church

        def get_status(slug):
            return GuestStatus.raw_objects.filter(
                church=church, slug=slug, is_active=True
            ).first()

        current_slug = guest.status.slug if guest.status else ""

        # ── 1. Not Planted (manual, terminal, highest priority) ───────
        if self.not_planted:
            not_planted_status = get_status(GuestStatus.SLUG_NOT_PLANTED)
            if not_planted_status and current_slug != GuestStatus.SLUG_NOT_PLANTED:
                guest.status = not_planted_status
                guest.save(update_fields=["status", "updated_at"])

                # Cancel any active pipeline application
                from guests.models import MembershipApplication

                MembershipApplication.raw_objects.filter(
                    church=church,
                    guest=guest,
                    status__in=[
                        MembershipApplication.STATUS_PENDING,
                        MembershipApplication.STATUS_TRAINING,
                    ],
                ).update(status=MembershipApplication.STATUS_DECLINED)
            return  # terminal — no further progression

        # Stop if the guest is already on a terminal status
        if guest.status and guest.status.is_terminal:
            return

        # ── 2. In-Contact (contact answered, guest still New Guest) ───
        if self.contact_answered and current_slug == GuestStatus.SLUG_NEW_GUEST:
            in_contact_status = get_status(GuestStatus.SLUG_IN_CONTACT)
            if in_contact_status:
                guest.status = in_contact_status
                guest.save(update_fields=["status", "updated_at"])

        # Note: Committed progression is handled by the daily scheduler
        # (flag_committed_guests in guests/pipeline.py) to avoid running
        # expensive attendance queries on every report save.

    def __str__(self):
        return f"{self.guest.full_name} - {self.report_date}"


class GuestCallEvidence(ChurchOwnedModel):
    """
    Verifiable call telemetry linked to a guest and assignee.

    This is meant to power evidence-based status progression (e.g. New Guest ->
    In-Contact) using provider events rather than manual checkboxes.
    """

    STATUS_INITIATED = "initiated"
    STATUS_RINGING = "ringing"
    STATUS_ANSWERED = "answered"
    STATUS_COMPLETED = "completed"
    STATUS_NO_ANSWER = "no_answer"
    STATUS_BUSY = "busy"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_INITIATED, "Initiated"),
        (STATUS_RINGING, "Ringing"),
        (STATUS_ANSWERED, "Answered"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_NO_ANSWER, "No answer"),
        (STATUS_BUSY, "Busy"),
        (STATUS_FAILED, "Failed"),
    ]

    guest = models.ForeignKey(
        GuestEntry,
        on_delete=models.CASCADE,
        related_name="call_evidence",
    )
    assignee = models.ForeignKey(
        "units.UnitMembership",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="guest_call_evidence",
    )
    provider = models.CharField(max_length=30, default="manual", db_index=True)
    provider_call_id = models.CharField(max_length=120, db_index=True)
    from_number = models.CharField(max_length=40, blank=True)
    to_number = models.CharField(max_length=40, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    call_status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_INITIATED,
        db_index=True,
    )
    recording_url = models.URLField(blank=True)
    transcript = models.TextField(blank=True)
    ai_contact_confidence = models.FloatField(null=True, blank=True)
    ai_summary = models.TextField(blank=True)
    verified_contact = models.BooleanField(default=False, db_index=True)
    verification_reason = models.TextField(blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["church", "provider", "provider_call_id"],
                name="unique_guest_call_per_provider",
            )
        ]
        indexes = [
            models.Index(fields=["church", "guest", "-created_at"]),
            models.Index(fields=["church", "provider", "call_status"]),
            models.Index(fields=["church", "verified_contact"]),
        ]

    def __str__(self):
        return f"{self.guest.full_name} [{self.provider}:{self.provider_call_id}]"


class Review(ChurchOwnedModel):
    guest = models.ForeignKey(
        GuestEntry, on_delete=models.CASCADE, related_name="reviews"
    )
    reviewer = models.ForeignKey(UnitMembership, on_delete=models.CASCADE)
    role = models.ForeignKey(
        "permissions.UnitRole", null=True, blank=True, on_delete=models.SET_NULL
    )
    comment = models.TextField()
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies"
    )
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["church", "created_at"]),
            models.Index(fields=["guest", "created_at"]),
        ]

    def __str__(self):
        return f"{self.role} review on {self.guest.full_name}"

    @property
    def has_unread_reviews(self):
        return self.reviews.filter(is_read=False).exists()


# ─────────────────────────────────────────────────────────────────────────────
# MEMBERSHIP PIPELINE MODELS
# Continuation of the guest flow — committed → induction → member.
# All configurable per church. No hardcoded unit or status names.
# ─────────────────────────────────────────────────────────────────────────────
class WorkforceInterest(ChurchOwnedModel):
    """
    Consent + workforce interest form.
    """

    guest = models.OneToOneField(
        "guests.GuestEntry",
        on_delete=models.CASCADE,
        related_name="workforce_interest",
    )
    token = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False, db_index=True
    )

    interested_in_workforce = models.BooleanField(default=True)

    preferred_unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # ───── lifecycle ─────
    invited_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    reminder_1_sent = models.BooleanField(default=False)
    reminder_2_sent = models.BooleanField(default=False)

    submitted_at = models.DateTimeField(null=True, blank=True)
    expired = models.BooleanField(default=False)

    def mark_expiry(self, days=14):
        self.expires_at = timezone.now() + timedelta(days=days)


class OpenInterestLink(ChurchOwnedModel):
    """
    A church-generated, persistent QR-code link for the workforce interest form.

    Unlike WorkforceInterest (which is tied to an existing GuestEntry via the
    guest pipeline), this is a fresh form accessible to anyone — regular church
    members, walk-ins, event attendees — who want to express workforce interest
    without going through the guest pipeline first.

    One church can have multiple links (e.g. per event or location) for tracking.
    """

    token = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False, db_index=True
    )
    label = models.CharField(
        max_length=120,
        blank=True,
        help_text="Optional label e.g. 'Sunday Service QR' to track the source.",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="open_interest_links",
    )
    submission_count = models.PositiveIntegerField(
        default=0,
        help_text="Auto-incremented on each successful submission through this link.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.church.name} — {self.label or self.token}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("guests:open_interest_form", kwargs={"token": str(self.token)})


class MembershipTrack(ChurchOwnedModel):
    """
    Configurable induction pathway. Each church sets their own thresholds.
    A church may have multiple tracks (general, leadership, youth, etc.).
    """

    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    attendance_threshold = models.PositiveIntegerField(
        default=4,
        help_text="Number of services attended within the window to trigger 'Committed'.",
    )
    attendance_window_weeks = models.PositiveIntegerField(
        default=6,
        help_text="Rolling week window for attendance counting. e.g. 4 out of 6 weeks.",
    )
    is_default = models.BooleanField(default=False)
    lms_course = models.ForeignKey(
        "lms.LMSCourse",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="training_tracks",
        help_text="Optional: link this track to a specific LMS course.",
    )

    class Meta:
        unique_together = ("church", "name")

    def __str__(self):
        return f"{self.church.name} — {self.name}"

    def linked_lms_courses(self):
        """
        Return all LMS courses currently attached to this track.

        The reverse relation from LMSCourse.membership_track is the primary
        many-course path. The legacy lms_course field is kept as a fallback
        for older records and for one-course track setups.
        """
        courses = list(self.lms_courses.all())
        if courses:
            return courses
        if self.lms_course_id:
            return [self.lms_course]
        return courses

    def linked_lms_course_ids(self):
        return [course.id for course in self.linked_lms_courses() if course]

    def requires_lms_training(self):
        """
        Return True if this track has any LMS-based requirement.

        Tracks can be course-less if the church decides the training flow
        should rely on steps other than LMS completion.
        """
        if self.linked_lms_courses():
            return True
        return self.steps.filter(
            is_active=True,
            requirement__requirement_type=StepRequirement.TYPE_LMS,
        ).exists()


class StepRequirement(ChurchOwnedModel):
    """
    Defines HOW a step is completed.
    Acts as a pluggable requirement provider.
    """

    TYPE_MANUAL = "manual"
    TYPE_LMS = "lms_course"
    TYPE_ATTENDANCE = "attendance"
    TYPE_AUTO = "auto"

    REQUIREMENT_TYPE_CHOICES = [
        (TYPE_MANUAL, "Manual Approval"),
        (TYPE_LMS, "LMS Course Completion"),
        (TYPE_ATTENDANCE, "Attendance Based"),
        (TYPE_AUTO, "Automatic"),
    ]

    requirement_type = models.CharField(
        max_length=30,
        choices=REQUIREMENT_TYPE_CHOICES,
    )

    # optional references depending on type
    lms_course = models.ForeignKey(
        "lms.LMSCourse",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="step_requirements",
    )
    lms_courses = models.ManyToManyField(
        "lms.LMSCourse",
        blank=True,
        related_name="step_requirement_sets",
        help_text="Optional: attach multiple LMS courses to a single step.",
    )

    attendance_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Required attendance count if attendance-based.",
    )

    auto_complete = models.BooleanField(
        default=False,
        help_text="Automatically complete when reached.",
    )

    name = models.CharField(max_length=150)

    def linked_lms_courses(self):
        if self.lms_courses.exists():
            return self.lms_courses.all()
        if self.lms_course_id:
            return [self.lms_course]
        return []

    def __str__(self):
        return f"{self.name} ({self.requirement_type})"


class MembershipTrackStep(OrderedChurchModel):
    """
    A step within a membership track.
    """

    track = models.ForeignKey(
        MembershipTrack,
        on_delete=models.CASCADE,
        related_name="steps",
    )

    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    is_required = models.BooleanField(default=True)

    requirement = models.ForeignKey(
        StepRequirement,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="track_steps",
        help_text="How this step is completed.",
    )

    class Meta:
        unique_together = ("church", "track", "name")
        ordering = ["order"]

    def __str__(self):
        return f"{self.track.name} → {self.name}"


class MembershipApplication(ChurchOwnedModel):
    """Tracks a guest's journey through the induction pipeline."""

    STATUS_PENDING = "pending"
    STATUS_TRAINING = "in_training"
    STATUS_PASSED = "passed"
    STATUS_INDUCTED = "inducted"
    STATUS_DECLINED = "declined"
    STATUS_WITHDRAWN = "withdrawn"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending review"),
        (STATUS_TRAINING, "In training"),
        (STATUS_PASSED, "Passed"),
        (STATUS_INDUCTED, "Inducted"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_WITHDRAWN, "Withdrawn"),
    ]

    guest = models.ForeignKey(
        GuestEntry, on_delete=models.CASCADE, related_name="membership_applications"
    )
    track = models.ForeignKey(
        MembershipTrack, on_delete=models.PROTECT, related_name="applications"
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    applied_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_applications",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    preferred_unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="preferred_applicants",
        help_text="The unit the applicant has chosen to serve in after induction.",
    )

    inducted_member = models.OneToOneField(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="membership_application",
    )
    inducted_at = models.DateTimeField(null=True, blank=True)
    source_link = models.ForeignKey(
        "guests.OpenInterestLink",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="applications",
        help_text="Set when the application came via an OpenInterestLink (QR/URL).",
    )

    class Meta:
        unique_together = ("church", "guest", "track")
        indexes = [
            models.Index(fields=["church", "status"]),
            models.Index(fields=["church", "track", "status"]),
        ]

    def __str__(self):
        return f"{self.guest} → {self.track.name} [{self.status}]"


class ApplicationStepProgress(ChurchOwnedModel):
    """Completion record for each MembershipTrackStep per application."""

    application = models.ForeignKey(
        MembershipApplication, on_delete=models.CASCADE, related_name="step_progress"
    )
    step = models.ForeignKey(
        MembershipTrackStep, on_delete=models.CASCADE, related_name="progress_records"
    )
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="completed_steps",
    )
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = ("application", "step")

    def __str__(self):
        return f"{'✓' if self.completed else '○'} {self.step.name} — {self.application.guest}"


class TrackRequirement(ChurchOwnedModel):
    track = models.ForeignKey("guests.MembershipTrack", on_delete=models.CASCADE)

    require_lms = models.BooleanField(default=True)
    require_steps = models.BooleanField(default=True)
    require_approval = models.BooleanField(default=True)
