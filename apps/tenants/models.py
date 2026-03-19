from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from bootstrap.models import ChurchTemplate
from django.utils.functional import cached_property


class TenantBaseModel(models.Model):
    """
    Standalone base for tenant-root models (Church, etc.).

    Cannot inherit from core.TimestampedModel because core imports Church,
    which would create a circular dependency. This class mirrors the fields
    from TimestampedModel and adds is_active — kept intentionally minimal.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active  = models.BooleanField(default=True, db_index=True)

    class Meta:
        abstract = True


class Church(TenantBaseModel):
    """
    Root tenant model. Every piece of data in the system belongs to a Church.

    Access tiers:
        - Trial       → shared DB, root-domain slug routing  (app.com/slug)
        - SaaS        → shared DB, subdomain routing         (slug.app.com)
        - White-label → dedicated DB, custom domain          (client.com)

    Location fields (latitude, longitude) are mandatory — they drive the
    geo-fenced clock-in/out logic in accounts/views.py. Every church must
    set these before attendance tracking can be used.
    """

    name         = models.CharField(max_length=255)
    slug         = models.SlugField(unique=True, db_index=True)
    subdomain    = models.SlugField(unique=True, null=True, blank=True, db_index=True)
    custom_domain = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    logo          = models.ImageField(upload_to="org_logos/", blank=True, null=True)
    primary_color = models.CharField(max_length=20, default="#206bc4")
    # White-label: set True for churches that have paid to remove the
    # "Powered by ChurchForce" footer credit.
    hide_platform_credit = models.BooleanField(
        default=False,
        help_text="Hide 'Powered by ChurchForce' in the footer for white-label deployments.",
    )
    template      = models.ForeignKey(
        ChurchTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # ── Location ──────────────────────────────────────────────────────
    # Required for geo-fenced attendance (clock-in/out radius check).
    # DecimalField used intentionally — avoids floating-point rounding
    # errors that FloatField introduces for coordinate arithmetic.
    # Precision: 6 decimal places ≈ 11cm accuracy at the equator.
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
        default=0.100,  # 100 metres — sensible default for a church building
        help_text=(
            "Maximum distance in kilometres from the venue coordinates "
            "within which a clock-in or clock-out is accepted. "
            "Default: 0.100 (100 metres)."
        ),
    )

    # ── Localisation ──────────────────────────────────────────────────
    # Timezone string used by the scheduler and post-login redirect.
    # Defaults to UTC — set per church to their local timezone.
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

    class Meta:
        verbose_name = "church"
        verbose_name_plural = "churches"
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["subdomain"]),
            models.Index(fields=["custom_domain"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.name

    # ── Location helper ───────────────────────────────────────────────

    @property
    def coordinates(self):
        """Return (latitude, longitude) as a tuple of floats."""
        return float(self.latitude), float(self.longitude)

    # ── Subscription helpers ──────────────────────────────────────────

    @cached_property
    def subscription(self):
        return getattr(self, "churchsubscription", None)

    @cached_property
    def plan(self):
        return self.subscription.plan if self.subscription else None

    # ── Plan-tier helpers ─────────────────────────────────────────────

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


class ChurchSetting(models.Model):
    """
    Church-level configuration edited by the church admin
    (not Django superuser). One record per church.

    Covers:
        - Guest pipeline: committed threshold and window
        - ID prefixes: customise MBR/GNG prefixes per church
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

    # ── Guest pipeline ──────────────────────────────────────────────────
    # How many service attendances within the rolling window qualifies
    # a guest as "Committed" — triggering pipeline progression.
    committed_attendance_threshold = models.PositiveSmallIntegerField(
        default=4,
        help_text=(
            "Number of service attendances within the rolling window "
            "that qualifies a guest as Committed. Default: 4."
        ),
    )
    committed_attendance_window_weeks = models.PositiveSmallIntegerField(
        default=6,
        help_text=(
            "Rolling window in weeks for counting attendances. Default: 6."
        ),
    )

    # ── Custom ID prefixes ────────────────────────────────────────────
    # Church can customise the auto-generated ID prefix for guests and members.
    # Must be 2–6 uppercase letters. e.g. "GNG" → GNG000001.
    member_id_prefix = models.CharField(
        max_length=6,
        default="MBR",
        help_text="Prefix for member IDs. e.g. MBR → MBR000001. 2–6 uppercase letters.",
    )
    guest_id_prefix = models.CharField(
        max_length=6,
        default="GNG",
        help_text="Prefix for guest IDs. e.g. GNG → GNG000001. 2–6 uppercase letters.",
    )

    # ── Localisation ──────────────────────────────────────────────────
    date_format = models.CharField(
        max_length=20,
        default="%d %b %Y",
        help_text=(
            "Python strftime format for displaying dates in the UI. "
            "Default: %d %b %Y (e.g. 14 Jun 2025)."
        ),
    )

    # ── Guest messaging — automated thank-you SMS ────────────────────
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
    send_first_visit_thankyou  = models.BooleanField(default=True)
    send_second_visit_thankyou = models.BooleanField(default=True)

    # ── Feature flags ─────────────────────────────────────────────────
    # Allow the church admin to switch individual features on/off.
    enable_music_module  = models.BooleanField(
        default=True,
        help_text="Show the music/setlist module to music unit members.",
    )
    enable_media_module  = models.BooleanField(
        default=True,
        help_text="Show the media/production module to media unit members.",
    )
    enable_sms_birthday  = models.BooleanField(
        default=True,
        help_text="Send automated birthday SMS to guests and members.",
    )
    enable_sms_bulk      = models.BooleanField(
        default=True,
        help_text="Allow manual bulk SMS to guests.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "church setting"
        verbose_name_plural = "church settings"

    def __str__(self):
        return f"Settings for {self.church.name}"