from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from bootstrap.models import ChurchTemplate
from django.utils.functional import cached_property
from django.utils.text import slugify
from core.managers import ScopedChurchManager, RawChurchManager


class TenantBaseModel(models.Model):
    """
    Standalone base for tenant-root models (Church, etc.).

    Cannot inherit from core.TimestampedModel because core imports Church,
    which would create a circular dependency. This class mirrors the fields
    from TimestampedModel and adds is_active â€” kept intentionally minimal.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        abstract = True


class Church(TenantBaseModel):
    """
    Root tenant model. Every piece of data in the system belongs to a Church.

    Access tiers:
        - Trial       â†’ shared DB, root-domain slug routing  (app.com/slug)
        - SaaS        â†’ shared DB, subdomain routing         (slug.app.com)
        - White-label â†’ dedicated DB, custom domain          (client.com)

    Location fields (latitude, longitude) are mandatory â€” they drive the
    geo-fenced clock-in/out logic in accounts/views.py. Every church must
    set these before attendance tracking can be used.
    """

    # Tenant-scoped default manager
    objects = RawChurchManager()

    # Unscoped escape hatch â€” use only in management commands / admin
    raw_objects = RawChurchManager()

    name = models.CharField(max_length=255)
    slogan = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional church slogan shown across the app.",
    )
    slug = models.SlugField(unique=True, db_index=True)
    subdomain = models.SlugField(unique=True, null=True, blank=True, db_index=True)
    custom_domain = models.CharField(
        max_length=255, null=True, blank=True, db_index=True
    )
    logo = models.ImageField(upload_to="org_logos/", blank=True, null=True)

    @property
    def logo_url(self):
        """Logo URL — /media/… in dev, served directly from mediafiles/ in dev."""
        if not self.logo:
            return ""
        try:
            return self.logo.url
        except Exception:
            return ""

    primary_color = models.CharField(max_length=20, default="#206bc4")
    # White-label: set True for churches that have paid to remove the
    # "Powered by ChurchForce" footer credit.
    hide_platform_credit = models.BooleanField(
        default=False,
        help_text="Hide 'Powered by ChurchForce' in the footer for white-label deployments.",
    )
    template = models.ForeignKey(
        ChurchTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # â”€â”€ Location â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Required for geo-fenced attendance (clock-in/out radius check).
    # DecimalField used intentionally â€” avoids floating-point rounding
    # errors that FloatField introduces for coordinate arithmetic.
    # Precision: 6 decimal places â‰ˆ 11cm accuracy at the equator.
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        validators=[
            MinValueValidator(-90),
            MaxValueValidator(90),
        ],
        help_text="Latitude of the church's primary venue. Required for attendance geo-fencing.",
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        validators=[
            MinValueValidator(-180),
            MaxValueValidator(180),
        ],
        help_text="Longitude of the church's primary venue. Required for attendance geo-fencing.",
    )
    attendance_radius_km = models.DecimalField(
        max_digits=5,
        decimal_places=3,
        default=0.100,  # 100 metres â€” sensible default for a church building
        help_text=(
            "Maximum distance in kilometres from the venue coordinates "
            "within which a clock-in or clock-out is accepted. "
            "Default: 0.100 (100 metres)."
        ),
    )

    # â”€â”€ Localisation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Timezone string used by the scheduler and post-login redirect.
    # Defaults to UTC â€” set per church to their local timezone.
    # Valid values: any string accepted by pytz.timezone(),
    # e.g. "Africa/Lagos", "America/New_York", "Europe/London".
    timezone = models.CharField(
        max_length=63,
        default="UTC",
        help_text=(
            "Church local timezone. Used for event scheduling and "
            "dashboard date display. e.g. 'Africa/Lagos'."
        ),
    )

    # ── Multi-campus hierarchy ────────────────────────────────────────────────
    # When a campus is provisioned it becomes a full Church instance.
    # parent_church records the HQ relationship for governance and reporting.
    parent_church = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="campus_churches",
        help_text=(
            "HQ church this campus-church reports to. "
            "Null = this IS the HQ (or a standalone) church."
        ),
    )
    # Short location label, e.g. 'abuja'.
    # Slug routing: gatewaynation-abuja  (trial)
    # Subdomain routing: abuja.gatewaynation  (SaaS)
    # Custom domain routing: abuja.gateway.org  (white-label)
    campus_location_name = models.SlugField(
        max_length=63,
        blank=True,
        help_text=(
            "Short location identifier for this campus-church. "
            "Used to build slug/subdomain relative to the parent church."
        ),
    )
    # HQ-imposed overrides pushed down to this campus-church.
    # Written by HQ admin in the campus modal; read by campus middleware.
    hq_override = models.JSONField(
        default=dict,
        blank=True,
        help_text="HQ-managed feature-flag and restriction overrides for this campus-church.",
    )

    class Meta:
        verbose_name = "church"
        verbose_name_plural = "churches"
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["subdomain"]),
            models.Index(fields=["custom_domain"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["parent_church"]),
        ]

    def __str__(self):
        return self.name

    @property
    def initials(self):
        if self.name:
            return "".join([name[0].upper() for name in self.name.split()[:2]])
        return "?"

    # â”€â”€ Location helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @property
    def coordinates(self):
        """Return (latitude, longitude) as a tuple of floats."""
        return float(self.latitude), float(self.longitude)

    # â”€â”€ Subscription helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    # ── Campus-church helpers ────────────────────────────────────────────────

    @property
    def is_campus_church(self):
        """True if this Church is a campus-church (has a parent HQ church)."""
        return self.parent_church_id is not None

    @property
    def hq_church(self):
        """Walk up the parent chain to find the ultimate HQ church."""
        church = self
        seen = set()
        while church.parent_church_id and church.id not in seen:
            seen.add(church.id)
            church = church.parent_church
        return church

    def get_effective_subscription(self):
        """
        Return the subscription that governs this church.
        Campus-churches inherit the HQ subscription when they have no
        subscription of their own (the common case).
        """
        own = getattr(self, "churchsubscription", None)
        if own is not None:
            return own
        if self.parent_church_id:
            return self.hq_church.get_effective_subscription()
        return None

    def build_campus_slug(self, location_name: str) -> str:
        """
        Build a globally-unique slug for a campus-church.
        Trial routing:  parent-slug-location  (e.g. gatewaynation-abuja)
        """
        return f"{self.slug}-{location_name}"

    def build_campus_subdomain(self, location_name: str) -> str:
        """
        Build a subdomain for a campus-church.
        SaaS routing:  location.parent-subdomain  (e.g. abuja.gatewaynation)
        White-label:   location.parent-custom-domain prefix
        """
        base = self.subdomain or self.slug
        return f"{location_name}.{base}"

    @cached_property
    def subscription(self):
        return self.get_effective_subscription()

    @cached_property
    def plan(self):
        return self.subscription.plan if self.subscription else None

    # â”€â”€ Plan-tier helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @property
    def is_trial(self):
        sub = self.subscription
        return sub.is_trial if sub else True

    @property
    def is_white_label(self):
        plan = self.plan
        return bool(plan and plan.white_label)

    @property
    def is_saas(self):
        plan = self.plan
        return bool(plan and not plan.white_label)


def default_campus_growth_stages():
    """
    Structured campus growth config.
    enabled: whether the feature is shown in the UI.
    stages: ordered list of {name, description} dicts.
    """
    return {
        "enabled": True,
        "stages": [
            {
                "name": "Fellowship Center",
                "description": "The base local congregation.",
                "order": 1,
            },
        ],
    }


def default_campus_workspace_config():
    """
    Default branch workspace configuration.

    Campuses inherit HQ defaults unless overridden locally.
    """
    return {
        "inherit_hq": True,
        "modules": {
            "units": True,
            "workforce": True,
            "guests": True,
            "attendance": True,
            "training": True,
            "lms": True,
            "notifications": True,
        },
        "notes": "",
    }


class ChurchSetting(models.Model):
    """
    Church-level configuration edited by the church admin
    (not Django superuser). One record per church.

    Covers:
        - Guest pipeline: committed threshold and window
        - ID prefixes: customise MBR/GST prefixes per church
        - Localisation: timezone, date format (extends Church.timezone)
        - Display: theme, feature flags

    Location and attendance radius live on Church directly since they
    are required at the model level. ChurchSetting holds the softer
    admin-configurable preferences.

    Auto-created by onboarding.provision_church() and by the
    post_save signal on Church (in tenants/signals.py).
    """

    church = models.OneToOneField(
        Church,
        on_delete=models.CASCADE,
        related_name="settings",
    )

    campus_growth_stages = models.JSONField(
        default=default_campus_growth_stages,
        help_text=(
            "Optional list of campus growth stage labels to show in the UI. "
            "Leave empty to allow free-text entry."
        ),
    )
    # â”€â”€ Guest pipeline â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # How many service attendances within the rolling window qualifies
    # a guest as "Committed" â€” triggering pipeline progression.
    committed_attendance_threshold = models.PositiveSmallIntegerField(
        default=4,
        help_text=(
            "Number of service attendances within the rolling window "
            "that qualifies a guest as Committed. Default: 4."
        ),
    )
    committed_attendance_window_weeks = models.PositiveSmallIntegerField(
        default=6,
        help_text=("Rolling window in weeks for counting attendances. Default: 6."),
    )

    # â”€â”€ Custom ID prefixes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Church can customise the auto-generated ID prefix for guests and members.
    # Must be 2â€“6 uppercase letters. e.g. "GST" â†’ GST000001.
    member_id_prefix = models.CharField(
        max_length=6,
        default="MBR",
        help_text="Prefix for member IDs. e.g. MBR â†’ MBR000001. 2â€“6 uppercase letters.",
    )
    guest_id_prefix = models.CharField(
        max_length=6,
        default="GST",
        help_text="Prefix for guest IDs. e.g. GST â†’ GST000001. 2â€“6 uppercase letters.",
    )

    # â”€â”€ Localisation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    date_format = models.CharField(
        max_length=20,
        default="%d %b %Y",
        help_text=(
            "Python strftime format for displaying dates in the UI. "
            "Default: %d %b %Y (e.g. 14 Jun 2025)."
        ),
    )
    time_format = models.CharField(
        max_length=20,
        default="%H:%M:%S",
        help_text=(
            "Python strftime format for displaying times in the UI. "
            "Default: %H:%M:%S (e.g. 14:30:00)."
        ),
    )

    # â”€â”€ Guest messaging â€” automated thank-you SMS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    thankyou_first_visit_message = models.TextField(
        blank=True,
        default="Hi {name}! Thank you for visiting us today. We hope you felt right at home. We'd love to see you again!",
        help_text="SMS sent to a guest after their first visit. Use {name} for guest name.",
    )
    thankyou_second_visit_message = models.TextField(
        blank=True,
        default="Hi {name}! Great to see you again! Your continued presence means a lot to us.",
        help_text="SMS sent after a guest's second visit. Use {name} for guest name.",
    )
    send_first_visit_thankyou = models.BooleanField(default=True)
    send_second_visit_thankyou = models.BooleanField(default=True)

    # â”€â”€ Feature flags â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Allow the church admin to switch individual features on/off.
    enable_guest_module = models.BooleanField(
        default=True,
        help_text="Show the guest management module to guest unit members.",
    )
    enable_music_module = models.BooleanField(
        default=True,
        help_text="Show the music/setlist module to music unit members.",
    )
    enable_media_module = models.BooleanField(
        default=True,
        help_text="Show the media/production module to media unit members.",
    )
    enable_children_module = models.BooleanField(
        default=False,
        help_text="Show the children ministry module to children unit members.",
    )
    enable_youth_module = models.BooleanField(
        default=False,
        help_text="Show the youth ministry module to youth unit members.",
    )
    enable_teenagers_module = models.BooleanField(
        default=False,
        help_text="Show the teenagers ministry module to teenagers unit members.",
    )
    enable_sms_birthday = models.BooleanField(
        default=True,
        help_text="Send automated birthday SMS to guests and members.",
    )
    enable_sms_bulk = models.BooleanField(
        default=True,
        help_text="Allow manual bulk SMS to guests.",
    )
    enable_welcome_screen = models.BooleanField(
        default=True,
        help_text="Show the welcome/quote screen after login before entering the dashboard.",
    )
    username_prefix = models.CharField(
        max_length=20,
        blank=True,
        help_text="Optional username prefix, e.g. @",
    )
    username_use_church_slug_suffix = models.BooleanField(
        default=False,
        help_text="Append '.<church-slug>' to usernames for church-scoped uniqueness.",
    )
    # ── Church Identity & Values ──────────────────────────────────────
    # Used by AI skills to personalise messages, research, and analysis
    # with the church's own theological emphasis and cultural identity.
    core_values = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "List of church core values/tenets. Each entry: "
            '{"title": "...", "description": "..."}. '
            "Used by AI skills for personalisation across the system."
        ),
    )
    vision_statement = models.TextField(
        blank=True,
        help_text="Church vision statement. Used by AI for context-aware messaging.",
    )
    mission_statement = models.TextField(
        blank=True,
        help_text="Church mission statement.",
    )
    denominational_affiliation = models.CharField(
        max_length=100,
        blank=True,
        help_text="e.g. Pentecostal, Baptist, Anglican, Non-denominational.",
    )

    # -- LMS & onboarding config (Tab 3) ------------------------------------
    # Stored as JSON so new keys can be added without migrations.
    # Keys mirror LMSSettingsForm.LMS_CONFIG_FIELDS.
    lms_config = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "LMS pipeline integration settings. "
            "Keys: induction_course_type, default_passing_score, "
            "promotion_mode, promotion_approval_role, "
            "allow_retakes_default, max_retakes_default, "
            "notify_unit_head_on_completion, notify_admin_on_completion."
        ),
    )

    # -- Workforce config (Tab 4) --------------------------------------------
    # Keys mirror WorkforceSettingsForm.WORKFORCE_CONFIG_FIELDS.
    workforce_config = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Workforce pipeline settings. "
            "Keys: default_workforce_stage, absence_flag_threshold."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "church setting"
        verbose_name_plural = "church settings"

    def __str__(self):
        return f"Settings for {self.church.name}"


from core.models import ChurchOwnedModel


class Campus(ChurchOwnedModel):
    """
    Tenant-owned campus model.

    The underlying table is preserved so this can move into the tenant domain
    without a disruptive data migration in this pass.
    """

    GROWTH_STAGE_CHOICES = [
        ("cell", "Cell Group"),
        ("satellite", "Satellite Campus"),
        ("daughter", "Daughter Church"),
        ("regional", "Regional Campus"),
        ("headquarters", "Headquarters"),
    ]

    name = models.CharField(max_length=255)
    slug = models.SlugField(blank=True)
    description = models.TextField(blank=True)
    address = models.TextField(blank=True)
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    growth_stage = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
        help_text="Optional campus growth stage label (configurable per church).",
    )
    report_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sub_campuses",
        help_text="Parent campus this campus reports to.",
    )
    contributes_to_parent_metrics = models.BooleanField(
        default=True,
        help_text=(
            "If True, this campus guest/attendance counts roll up to the "
            "parent campus metrics report."
        ),
    )
    workspace_config = models.JSONField(
        default=default_campus_workspace_config,
        blank=True,
        help_text="Campus branch workspace overrides and inheritance flags.",
    )
    campus_leader = models.ForeignKey(
        "accounts.ChurchMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="led_campuses",
    )

    # ── Campus-church link ────────────────────────────────────────────────────
    # When campus creation provisions a full Church instance, this FK records it.
    # HQ uses this to navigate to the campus-church for overrides, reporting, etc.
    campus_church = models.OneToOneField(
        Church,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="campus_record",
        help_text=(
            "The full Church instance provisioned for this campus. "
            "Set automatically on campus creation."
        ),
    )

    # Location identifier used to derive campus-church slug/subdomain.
    location_name = models.SlugField(
        max_length=63,
        blank=True,
        help_text="Short location identifier, e.g. 'abuja'. Drives slug/subdomain generation.",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["church", "slug"],
                name="unique_campus_slug_per_church",
            )
        ]
        indexes = [models.Index(fields=["church", "growth_stage"])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class CampusMembership(ChurchOwnedModel):
    """Links a ChurchMember to a Campus."""

    member = models.ForeignKey(
        "accounts.ChurchMember",
        on_delete=models.CASCADE,
        related_name="campus_memberships",
    )
    campus = models.ForeignKey(
        Campus,
        on_delete=models.CASCADE,
        related_name="members",
    )
    joined_at = models.DateField(auto_now_add=True)
    is_primary = models.BooleanField(
        default=False,
        help_text="The member's primary campus (home base).",
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "units_campusmembership"
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
