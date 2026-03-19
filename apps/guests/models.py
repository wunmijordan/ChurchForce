import re
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.timezone import localdate, now
from cloudinary.models import CloudinaryField
from core.models import ChurchOwnedModel, OrderedChurchModel, SoftDeleteModel
from core.managers import ScopedGuestManager, RawGuestManager
from units.models import UnitMembership


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

    SLUG_NEW_GUEST    = "new-guest"
    SLUG_IN_CONTACT   = "in-contact"
    SLUG_COMMITTED    = "committed"
    SLUG_PLANTED      = "planted"
    SLUG_NOT_PLANTED  = "not-planted"

    name = models.CharField(max_length=100)
    slug = models.SlugField(
        max_length=50,
        help_text=(
            "Used by the pipeline to identify this status. "
            "Do not change once set."
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
        ('Chief', 'Chief'), ('Dr.', 'Dr.'), ('Engr.', 'Engr.'),
        ('Mr.', 'Mr.'), ('Mrs.', 'Mrs.'), ('Ms.', 'Ms.'),
        ('Pastor', 'Pastor'), ('Prof.', 'Prof.')
    ]
    GENDER_CHOICES = [('Male', 'Male'), ('Female', 'Female')]
    AGE_RANGE_CHOICES = [
        ("Under 18", "Under 18"), ("18–25", "18–25"), ("26–35", "26–35"),
        ("36–45", "36–45"), ("46 and Above", "46 and Above"),
    ]
    MARITAL_STATUS_CHOICES = [('Single', 'Single'), ('Married', 'Married')]

    picture           = CloudinaryField("image", blank=True, null=True)
    custom_id         = models.CharField(max_length=20, blank=True, null=True, editable=False)
    title             = models.CharField(max_length=20, choices=TITLE_CHOICES)
    full_name         = models.CharField(max_length=100, db_index=True)
    gender            = models.CharField(max_length=10, choices=GENDER_CHOICES)
    phone_number      = models.CharField(max_length=20, blank=True, null=True, db_index=True)
    email             = models.EmailField(blank=True, db_index=True)
    date_of_birth     = models.CharField(blank=True, null=True)

    # Birthday index fields — auto-populated from date_of_birth on save.
    # Stored as integers so the daily scheduler can query efficiently:
    #   filter(birthday_month=today.month, birthday_day=today.day)
    birthday_month    = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    birthday_day      = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)

    age_range         = models.CharField(max_length=20, blank=True, choices=AGE_RANGE_CHOICES)
    marital_status    = models.CharField(max_length=20, blank=True, choices=MARITAL_STATUS_CHOICES)
    home_address      = models.TextField(blank=True)
    occupation        = models.CharField(max_length=100, blank=True)
    date_of_visit     = models.DateField(default=localdate, db_index=True)
    purpose_of_visit  = models.ForeignKey(VisitPurpose, null=True, blank=True, on_delete=models.SET_NULL)
    channel_of_visit  = models.ForeignKey(VisitChannel, null=True, blank=True, on_delete=models.SET_NULL)
    service_attended  = models.ForeignKey(ChurchService, on_delete=models.PROTECT)
    status            = models.ForeignKey(GuestStatus, on_delete=models.PROTECT)

    # Assignment
    assigned_to  = models.ForeignKey(
        UnitMembership, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_guests",
    )
    assigned_at  = models.DateTimeField(null=True, blank=True)

    # Thank-you message flags — set by automated SMS on first and second visits
    first_thankyou_sent   = models.BooleanField(default=False)
    second_thankyou_sent  = models.BooleanField(default=False)
    second_visit_date     = models.DateField(null=True, blank=True, db_index=True)

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
        null=True, blank=True,
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
        null=True, blank=True,
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

    def clean(self):
        for obj in [self.status, self.purpose_of_visit, self.channel_of_visit, self.service_attended]:
            if obj and obj.church_id != self.church_id:
                raise ValidationError("Configuration must belong to the same church.")

    def save(self, *args, **kwargs):
        if self.assigned_to and not self.assigned_at:
            self.assigned_at = now()

        # Auto-populate birthday index fields from date_of_birth string
        self._sync_birthday_index()

        # Auto-populate referred_by_name from member if not already set
        if self.referred_by_member and not self.referred_by_name:
            try:
                user = self.referred_by_member.workforce_member.member.user
                self.referred_by_name = user.full_name or user.username
            except AttributeError:
                pass

        if not self.custom_id:
            # Read prefix from ChurchSetting, falling back to GNG
            try:
                prefix = self.church.settings.guest_id_prefix or "GNG"
            except Exception:
                prefix = "GNG"
            last = (
                GuestEntry.raw_objects
                .filter(church=self.church, custom_id__startswith=prefix)
                .order_by("-custom_id")
                .first()
            )
            last_num = int(re.sub(r"^\D+", "", last.custom_id)) if last and last.custom_id else 0
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
            self.birthday_day   = None
            return
        for fmt in ("%B %d", "%b %d", "%m/%d", "%d/%m"):
            try:
                parsed = datetime.datetime.strptime(dob.strip(), fmt)
                self.birthday_month = parsed.month
                self.birthday_day   = parsed.day
                return
            except ValueError:
                continue
        # Unrecognised format — leave as None
        self.birthday_month = None
        self.birthday_day   = None

    @property
    def initials(self):
        if self.full_name:
            return "".join([n[0].upper() for n in self.full_name.split()[:2]])
        return "G"

    def get_status_color(self):
        return self.status.color if self.status else "gray"

    def __str__(self):
        return f"{self.custom_id} - {self.full_name}"


class SocialMediaEntry(ChurchOwnedModel):
    SOCIAL_MEDIA_CHOICES = [
        ('whatsapp', 'WhatsApp'), ('instagram', 'Instagram'),
        ('twitter', 'Twitter'), ('linkedin', 'LinkedIn'), ('tiktok', 'Tiktok'),
    ]
    guest    = models.ForeignKey(GuestEntry, on_delete=models.CASCADE, related_name='social_media_accounts')
    platform = models.CharField(max_length=20, choices=SOCIAL_MEDIA_CHOICES)
    handle   = models.CharField(max_length=255)

    class Meta:
        indexes = [models.Index(fields=["church", "guest"])]

    def save(self, *args, **kwargs):
        base_urls = {
            'linkedin': 'https://www.linkedin.com/in/', 'whatsapp': 'https://wa.me/',
            'instagram': 'https://www.instagram.com/', 'twitter': 'https://twitter.com/',
            'tiktok': 'https://www.tiktok.com/@',
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
        ("call",  "Phone call"),
        ("text",  "Text / chat message"),
        ("visit", "Physical visit"),
    ]

    guest         = models.ForeignKey(GuestEntry, on_delete=models.CASCADE, related_name='reports')
    report_date   = models.DateField(default=localdate)
    note          = models.TextField()
    assigned_to   = models.ForeignKey(
        UnitMembership, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='assigned_reports',
    )

    # ── Service attendance ────────────────────────────────────────────
    service_sunday  = models.BooleanField(default=False)
    service_midweek = models.BooleanField(default=False)
    service_others  = models.BooleanField(default=False)
    reviewed        = models.BooleanField(default=False)

    # ── Contact tracking ──────────────────────────────────────────────
    contact_attempted  = models.BooleanField(default=False)
    contact_answered   = models.BooleanField(
        default=False,
        help_text="Set True when the guest responds. Triggers 'In-Contact' status.",
    )
    contact_type       = models.CharField(
        max_length=10, choices=CONTACT_TYPE_CHOICES, blank=True,
    )
    contact_attachment = models.FileField(
        upload_to="contact_screenshots/%Y/%m/",
        blank=True, null=True,
        help_text="Optional screenshot of a text/chat conversation as evidence.",
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


class Review(ChurchOwnedModel):
    guest    = models.ForeignKey(GuestEntry, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(UnitMembership, on_delete=models.CASCADE)
    role     = models.ForeignKey("permissions.UnitRole", null=True, blank=True, on_delete=models.SET_NULL)
    comment  = models.TextField()
    parent   = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies")
    is_read  = models.BooleanField(default=False)

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

class MembershipTrack(ChurchOwnedModel):
    """
    Configurable induction pathway. Each church sets their own thresholds.
    A church may have multiple tracks (general, leadership, youth, etc.).
    """
    name                   = models.CharField(max_length=150)
    description            = models.TextField(blank=True)
    attendance_threshold   = models.PositiveIntegerField(
        default=4,
        help_text="Number of services attended within the window to trigger 'Committed'.",
    )
    attendance_window_weeks = models.PositiveIntegerField(
        default=6,
        help_text="Rolling week window for attendance counting. e.g. 4 out of 6 weeks.",
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        unique_together = ("church", "name")

    def __str__(self):
        return f"{self.church.name} — {self.name}"


class MembershipTrackStep(OrderedChurchModel):
    """A single configurable step in a MembershipTrack."""
    track       = models.ForeignKey(MembershipTrack, on_delete=models.CASCADE, related_name="steps")
    name        = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_required = models.BooleanField(default=True)

    class Meta:
        unique_together = ("church", "track", "name")
        ordering = ["order"]

    def __str__(self):
        return f"{self.track.name} → {self.name}"


class MembershipApplication(ChurchOwnedModel):
    """Tracks a guest's journey through the induction pipeline."""

    STATUS_PENDING   = "pending"
    STATUS_TRAINING  = "in_training"
    STATUS_PASSED    = "passed"
    STATUS_INDUCTED  = "inducted"
    STATUS_DECLINED  = "declined"
    STATUS_WITHDRAWN = "withdrawn"

    STATUS_CHOICES = [
        (STATUS_PENDING,   "Pending review"),
        (STATUS_TRAINING,  "In training"),
        (STATUS_PASSED,    "Passed"),
        (STATUS_INDUCTED,  "Inducted"),
        (STATUS_DECLINED,  "Declined"),
        (STATUS_WITHDRAWN, "Withdrawn"),
    ]

    guest        = models.ForeignKey(GuestEntry, on_delete=models.CASCADE, related_name="membership_applications")
    track        = models.ForeignKey(MembershipTrack, on_delete=models.PROTECT, related_name="applications")
    status       = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    applied_at   = models.DateTimeField(auto_now_add=True)
    reviewed_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reviewed_applications",
    )
    reviewed_at  = models.DateTimeField(null=True, blank=True)
    notes        = models.TextField(blank=True)

    preferred_unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="preferred_applicants",
        help_text="The unit the applicant has chosen to serve in after induction.",
    )

    inducted_member = models.OneToOneField(
        "accounts.ChurchMember", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="membership_application",
    )
    inducted_at = models.DateTimeField(null=True, blank=True)

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
    application  = models.ForeignKey(MembershipApplication, on_delete=models.CASCADE, related_name="step_progress")
    step         = models.ForeignKey(MembershipTrackStep, on_delete=models.CASCADE, related_name="progress_records")
    completed    = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="completed_steps",
    )
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = ("application", "step")

    def __str__(self):
        return f"{'✓' if self.completed else '○'} {self.step.name} — {self.application.guest}"