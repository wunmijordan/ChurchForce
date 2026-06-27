import re
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils.functional import cached_property
from core.models import ChurchOwnedModel
from core.utils.colors import get_member_color


class CustomUser(AbstractUser):
    # Use FileField for both dev and prod - simpler migration handling
    # In prod, you can use a custom storage that syncs to Cloudinary
    # Or keep using CloudinaryField but handle offline errors gracefully
    image = models.FileField(upload_to="user_images/%Y/%m/", blank=True, null=True)
    cover_image = models.FileField(
        upload_to="cover_images/%Y/%m/", blank=True, null=True
    )

    TITLE_CHOICES = [
        ("Bro.", "Bro."),
        ("Min.", "Min."),
        ("Mr.", "Mr."),
        ("Mrs.", "Mrs."),
        ("Pastor", "Pastor"),
        ("Sis.", "Sis."),
    ]
    MARITAL_STATUS_CHOICES = [
        ("Married", "Married"),
        ("Single", "Single"),
    ]

    full_name = models.CharField(max_length=255, blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    title = models.CharField(
        max_length=50, choices=TITLE_CHOICES, blank=True, null=True
    )
    marital_status = models.CharField(
        max_length=20, choices=MARITAL_STATUS_CHOICES, blank=True, null=True
    )
    address = models.TextField(blank=True, null=True)
    date_of_birth = models.CharField(max_length=50, blank=True, null=True)

    # Birthday index fields — auto-populated from date_of_birth on save.
    # Allows the daily birthday scheduler to query efficiently.
    birthday_month = models.PositiveSmallIntegerField(
        null=True, blank=True, db_index=True
    )
    birthday_day = models.PositiveSmallIntegerField(
        null=True, blank=True, db_index=True
    )

    is_online = models.BooleanField(default=False)
    last_active = models.DateTimeField(null=True, blank=True)

    @property
    def display_username(self):
        return self.username

    def save(self, *args, **kwargs):
        self._sync_birthday_index()
        super().save(*args, **kwargs)

    def _sync_birthday_index(self):
        """Auto-populate birthday_month/day from date_of_birth string."""
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
        self.birthday_month = None
        self.birthday_day = None

    def __str__(self):
        return f"{self.title} {self.full_name or self.username}".strip()

    @property
    def initials(self):
        if self.full_name:
            return "".join([name[0].upper() for name in self.full_name.split()[:2]])
        return self.username[0].upper() if self.username else "?"

    def permissions(self, church):
        from permissions.services.resolver import PermissionResolver

        if not hasattr(self, "_permission_cache"):
            self._permission_cache = {}

        if church.id not in self._permission_cache:
            self._permission_cache[church.id] = PermissionResolver(self, church)

        return self._permission_cache[church.id]

    def can(self, permission, church, unit=None):
        return self.permissions(church).can(permission, unit)

    @property
    def guest_count(self):
        return self.assigned_guests.count() if hasattr(self, "assigned_guests") else 0

    @property
    def units(self):
        """Return all units where this user has membership."""
        from units.models import UnitMembership

        return UnitMembership.objects.filter(workforce_member__member__user=self)


class ChurchMember(ChurchOwnedModel):
    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="church_memberships"
    )
    is_admin = models.BooleanField(
        default=False,
        help_text="Designates whether this member has administrative access to this church.",
    )
    joined_at = models.DateField(auto_now_add=True)
    hint_shown = models.BooleanField(
        default=False,
        help_text="True once the first-login setup hint has been dismissed.",
    )

    # Church-scoped member number, auto-generated on first save.
    # Format: <prefix><6-digit-number> e.g. MBR000001
    # Prefix is configurable per church via ChurchSetting (member_id_prefix).

    custom_id = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        editable=False,
        db_index=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["church", "user"], name="unique_user_per_church"
            ),
            models.UniqueConstraint(
                fields=["church", "custom_id"],
                name="unique_member_custom_id_per_church",
            ),
        ]

        indexes = [
            models.Index(fields=["church", "user"]),
            models.Index(fields=["church", "custom_id"]),
        ]

    # Set this to True before saving a trainee/pipeline-created member to
    # defer custom_id generation until the member actually joins the workforce.
    _skip_custom_id = False

    def save(self, *args, **kwargs):
        skip = kwargs.pop("skip_custom_id", self.__class__._skip_custom_id)
        if not self.custom_id and not skip:
            # Read prefix from ChurchSetting if available, default to MBR
            settings_obj = getattr(self.church, "settings", None)
            prefix = getattr(settings_obj, "member_id_prefix", None) or "MBR"
            last = (
                ChurchMember.raw_objects.filter(
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

    def assign_custom_id(self):
        """
        Explicitly generate and save custom_id.
        Called by WorkforceTraineeProfile.promote_to_workforce().
        """
        if self.custom_id:
            return  # already has one
        settings_obj = getattr(self.church, "settings", None)
        prefix = getattr(settings_obj, "member_id_prefix", None) or "MBR"
        last = (
            ChurchMember.raw_objects.filter(
                church=self.church, custom_id__startswith=prefix
            )
            .order_by("-custom_id")
            .first()
        )
        last_num = (
            int(re.sub(r"^\D+", "", last.custom_id)) if last and last.custom_id else 0
        )
        self.custom_id = f"{prefix}{last_num + 1:06d}"
        self.save(update_fields=["custom_id"])

    def __str__(self):
        return f"{self.user}"

    @cached_property
    def color_data(self):
        return get_member_color(self.id, variant="both")

    @cached_property
    def color_class(self):
        return self.color_data["class"]

    @cached_property
    def color_hex(self):
        return self.color_data["hex"]

    @cached_property
    def color(self):
        """
        Alias for hex (used in charts/JS).
        """
        return self.color_hex


class MemberInvitation(models.Model):
    """
    An invitation to join the Church's Workforce.

    Flow:
        1. Admin creates invitation → email sent with unique token URL
        2. Invitee clicks link → pre-filled registration form
        3. On form submit → CustomUser + ChurchMember created
        4. invited_at set → invitation marked used

    Token is a UUID — single-use, expires after 7 days by default.
    This model does NOT inherit ChurchOwnedModel (no scoped manager needed
    here — invitations are created by admins and read by unauthenticated users).
    """

    import uuid as _uuid

    church = models.ForeignKey(
        "tenants.Church",
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    email = models.EmailField(db_index=True)
    full_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Pre-fill the invitee's name on the form.",
    )
    token = models.UUIDField(
        default=_uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_invitations",
    )
    # Optional: pre-assign to a unit on acceptance
    suggested_unit = models.ForeignKey(
        "units.ChurchUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invitations",
    )
    message = models.TextField(
        blank=True,
        help_text="Personal message included in the invitation email.",
    )
    expires_at = models.DateTimeField(
        help_text="Invitation expires after this date.",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accepted_invitations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["church", "email"]),
            models.Index(fields=["token"]),
        ]

    def __str__(self):
        return f"Invite: {self.email} to {self.church.name}"

    @property
    def is_expired(self):
        from django.utils import timezone

        return timezone.now() > self.expires_at

    @property
    def is_used(self):
        return self.accepted_at is not None

    @property
    def is_valid(self):
        return not self.is_expired and not self.is_used

    def get_accept_url(self):
        from django.urls import reverse

        return reverse("accounts:accept_invitation", kwargs={"token": str(self.token)})
