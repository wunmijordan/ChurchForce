"""
tenants/forms.py

Church-admin-facing settings forms. All tabs in the church settings
page correspond to a section here.

Tabs / sections:
    1. General          â€” Church profile, branding, geo, timezone
    2. Guest Pipeline   â€” Committed threshold, lookup tables (VisitChannel,
                          VisitPurpose, ChurchService) inline CRUD
    3. LMS & Onboarding â€” Which courses count, passing score, promotion rules
    4. Workforce        â€” Stages, roles, unit roles
    5. Notifications    â€” SMS templates, feature flags
    6. ID & Display     â€” Prefixes, date format, campus stages
"""

from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from django.utils.text import slugify
from accounts.models import CustomUser
from tenants.models import Church, ChurchSetting

FC = {"class": "form-control"}
FS = {"class": "form-select"}
FC_SM = {"class": "form-control form-control-sm"}
CHK = {"class": "form-check-input me-2"}


def _campus_hq_settings(church):
    hq = getattr(church, "hq_church", None)
    if not hq:
        return None
    return getattr(hq, "settings", None)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Signup form (public, not settings)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class ChurchSignupForm(forms.Form):
    """Minimal self-serve signup. Plan selection happens post-signup."""

    church_name = forms.CharField(
        max_length=255,
        label="Organisation name",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Grace Chapel", **FC}),
    )
    url_handle = forms.SlugField(
        max_length=63,
        label="URL handle",
        help_text="Letters, numbers and hyphens only. This becomes your address: workforce.church/your-handle/",
        widget=forms.TextInput(attrs={"placeholder": "grace-chapel", **FC}),
    )
    full_name = forms.CharField(
        max_length=255,
        label="Your full name",
        widget=forms.TextInput(attrs={"placeholder": "John Doe", **FC}),
    )
    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"placeholder": "john@gracechapel.org", **FC}),
    )
    password = forms.CharField(
        label="Password",
        min_length=8,
        widget=forms.PasswordInput(attrs=FC),
    )
    password_confirm = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs=FC),
    )

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email and CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "A user with this email already exists. Please log in or use a different email."
            )
        return email

    def clean_url_handle(self):
        value = slugify(self.cleaned_data.get("url_handle", ""))
        if not value:
            raise forms.ValidationError("Please enter a valid URL handle.")
        ok, error = self.is_url_handle_available(value)
        if not ok:
            raise forms.ValidationError(error)
        return value

    @staticmethod
    def is_url_handle_available(value):
        value = slugify(value or "")
        if not value:
            return False, "Please enter a valid URL handle."
        if (
            Church.raw_objects.filter(slug=value).exists()
            or Church.raw_objects.filter(subdomain=value).exists()
        ):
            return (
                False,
                f"The URL 'workforce.church/{value}' is already taken. Please try a different name.",
            )
        reserved = ["admin", "api", "billing", "support", "app", "dev"]
        if value in reserved:
            return False, "This URL handle is reserved. Please choose another."
        return True, ""

    def clean_password(self):
        password = self.cleaned_data.get("password")
        try:
            validate_password(password)
        except Exception as e:
            raise forms.ValidationError(list(e.messages))
        return password

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("password_confirm")
        if p1 and p2 and p1 != p2:
            self.add_error("password_confirm", "Passwords do not match.")
        return cleaned


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Tab 1 â€” General
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class GeneralSettingsForm(forms.Form):
    """Church profile, branding, location."""

    name = forms.CharField(
        max_length=255,
        label="Organisation name",
        widget=forms.TextInput(attrs=FC),
    )
    slogan = forms.CharField(
        required=False,
        max_length=255,
        label="Church Slogan",
        widget=forms.TextInput(
            attrs={**FC, "placeholder": "e.g. Building Lives, Changing Nations"}
        ),
        help_text="Displayed on login page and accessible as {{ church.slogan }} in templates.",
    )
    primary_color = forms.CharField(
        max_length=20,
        label="Brand colour",
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-color",
                "type": "color",
                "colorpick-eyedropper-active": "true",
            }
        ),
    )
    logo = forms.ImageField(
        required=False,
        label="Logo",
        widget=forms.ClearableFileInput(attrs=FC),
    )
    timezone = forms.CharField(
        max_length=63,
        label="Timezone",
        widget=forms.TextInput(attrs={"placeholder": "Africa/Lagos", **FC}),
        help_text="e.g. Africa/Lagos, America/New_York, Europe/London",
    )
    latitude = forms.DecimalField(
        max_digits=9,
        decimal_places=6,
        label="Latitude",
        widget=forms.NumberInput(attrs={"step": "0.000001", **FC}),
        help_text="Right-click venue on Google Maps to get coordinates.",
    )
    longitude = forms.DecimalField(
        max_digits=9,
        decimal_places=6,
        label="Longitude",
        widget=forms.NumberInput(attrs={"step": "0.000001", **FC}),
    )
    attendance_radius_km = forms.DecimalField(
        max_digits=5,
        decimal_places=3,
        label="Clock-in radius (km)",
        widget=forms.NumberInput(attrs={"step": "0.001", **FC}),
        help_text="Geo-fence radius for attendance clock-in/out.",
    )
    hide_platform_credit = forms.BooleanField(
        required=False,
        label="Hide 'Powered by ChurchForce'",
        widget=forms.CheckboxInput(attrs=CHK),
        help_text="Remove ChurchForce branding from the footer (white-label).",
    )
    enable_welcome_screen = forms.BooleanField(
        required=False,
        label="Show welcome screen after login",
        widget=forms.CheckboxInput(attrs=CHK),
    )

    # ── Church identity / Core values ──────────────────────────────────
    vision_statement = forms.CharField(
        required=False,
        label="Vision Statement",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        help_text="Your church's vision. Used by AI for personalisation.",
    )
    mission_statement = forms.CharField(
        required=False,
        label="Mission Statement",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )
    denominational_affiliation = forms.CharField(
        required=False,
        max_length=100,
        label="Denomination / Affiliation",
        widget=forms.TextInput(
            attrs={**FC, "placeholder": "e.g. Pentecostal, Baptist, Non-denominational"}
        ),
    )
    # ── Username formatting ────────────────────────────────────────────
    username_prefix = forms.CharField(
        required=False,
        max_length=5,
        label="Username Prefix",
        widget=forms.TextInput(attrs={**FC, "placeholder": "@", "maxlength": "5"}),
        help_text=(
            "Optional character(s) shown before Usernames at login and in credentials. "
            "e.g. '@'. "
            "Leave blank to disable."
        ),
    )
    username_use_church_slug_suffix = forms.BooleanField(
        required=False,
        label="Append Church slug to Usernames",
        widget=forms.CheckboxInput(attrs=CHK),
        help_text=(
            "When enabled, Usernames display as username.churchslug "
            "(e.g. wunmijordan.gatewaynation)."
        ),
    )
    # Feature flags are handled by FeatureFlagsForm
    # core_values is stored as JSON — edited via the account page UI (not a form field)

    CHURCH_FIELDS = [
        "name",
        "slogan",
        "primary_color",
        "timezone",
        "latitude",
        "longitude",
        "attendance_radius_km",
        "hide_platform_credit",
    ]
    SETTING_FIELDS = [
        "enable_welcome_screen",
        "vision_statement",
        "mission_statement",
        "denominational_affiliation",
        "username_prefix",
        "username_use_church_slug_suffix",
    ]

    def __init__(self, *args, church=None, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.settings = settings
        self.is_campus_church = bool(getattr(church, "parent_church_id", None))
        self.hq_settings = (
            _campus_hq_settings(church) if self.is_campus_church else None
        )
        if church:
            source_church = (
                getattr(self.hq_settings, "church", None)
                if self.hq_settings
                else church
            ) or church
            self.initial.update(
                {
                    "name": church.name,
                    "slogan": getattr(church, "slogan", ""),
                    "primary_color": church.primary_color,
                    "timezone": church.timezone,
                    "latitude": church.latitude,
                    "longitude": church.longitude,
                    "attendance_radius_km": church.attendance_radius_km,
                    "hide_platform_credit": getattr(
                        church, "hide_platform_credit", False
                    ),
                }
            )
            self.fields["logo"].initial = source_church.logo
            if self.is_campus_church:
                locked_fields = {
                    "name",
                    "slogan",
                    "logo",
                    "primary_color",
                    "hide_platform_credit",
                    "enable_welcome_screen",
                    "vision_statement",
                    "mission_statement",
                    "denominational_affiliation",
                    "username_prefix",
                    "username_use_church_slug_suffix",
                }
                for field_name in locked_fields:
                    field = self.fields.get(field_name)
                    if not field:
                        continue
                    field.disabled = True
                    if hasattr(field.widget, "attrs"):
                        field.widget.attrs["disabled"] = "disabled"
                    if self.hq_settings and hasattr(self.hq_settings, field_name):
                        self.initial[field_name] = getattr(self.hq_settings, field_name)
        if settings:
            self.initial["enable_welcome_screen"] = settings.enable_welcome_screen
            self.initial["vision_statement"] = getattr(settings, "vision_statement", "")
            self.initial["mission_statement"] = getattr(
                settings, "mission_statement", ""
            )
            self.initial["denominational_affiliation"] = getattr(
                settings, "denominational_affiliation", ""
            )
            self.initial["slogan"] = getattr(church, "slogan", "")
            self.initial["username_prefix"] = getattr(settings, "username_prefix", "")
            self.initial["username_use_church_slug_suffix"] = getattr(
                settings, "username_use_church_slug_suffix", False
            )
            # Feature flags initialised in FeatureFlagsForm
            if self.is_campus_church and self.hq_settings:
                for field_name in {
                    "name",
                    "slogan",
                    "primary_color",
                    "hide_platform_credit",
                    "enable_welcome_screen",
                    "vision_statement",
                    "mission_statement",
                    "denominational_affiliation",
                    "username_prefix",
                    "username_use_church_slug_suffix",
                }:
                    if field_name == "name" or field_name == "primary_color":
                        continue
                    if hasattr(self.hq_settings, field_name):
                        self.initial[field_name] = getattr(self.hq_settings, field_name)
                self.initial["name"] = source_church.name
                self.initial["slogan"] = getattr(source_church, "slogan", "")
                self.initial["primary_color"] = source_church.primary_color
                self.initial["hide_platform_credit"] = getattr(
                    source_church, "hide_platform_credit", False
                )

    def save(self):
        c = self.cleaned_data
        for f in self.CHURCH_FIELDS:
            if f in c:
                setattr(self.church, f, c[f])
        logo = c.get("logo")
        if logo is False:
            self.church.logo = None
        elif logo:
            self.church.logo = logo
        self.church.save()
        for f in self.SETTING_FIELDS:
            if f in c:
                setattr(self.settings, f, c[f])
        self.settings.save()
        return self.church, self.settings


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Tab 2 â€” Guest Pipeline
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class GuestPipelineSettingsForm(forms.Form):
    """
    Guest pipeline thresholds + SMS messages.
    Lookup tables (VisitChannel, VisitPurpose, ChurchService) are managed
    via the inline AJAX views on the same settings tab, not this form.
    """

    # Pipeline thresholds
    committed_attendance_threshold = forms.IntegerField(
        min_value=1,
        max_value=52,
        required=False,
        label="Services attended to reach Committed",
        widget=forms.NumberInput(attrs=FC),
        help_text="How many service attendances within the rolling window qualify a guest as Committed.",
    )
    committed_attendance_window_weeks = forms.IntegerField(
        min_value=1,
        max_value=52,
        required=False,
        label="Rolling window (weeks)",
        widget=forms.NumberInput(attrs=FC),
        help_text="e.g. 4 attendances within 6 weeks.",
    )

    # Guest ID prefix
    guest_id_prefix = forms.CharField(
        max_length=6,
        label="Guest ID prefix",
        widget=forms.TextInput(
            attrs={"class": "form-control text-uppercase", "maxlength": 6}
        ),
        help_text="e.g. GST -> GST000001",
    )

    # Thank-you SMS
    send_first_visit_thankyou = forms.BooleanField(
        required=False,
        label="Send first-visit thank-you SMS",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    thankyou_first_visit_message = forms.CharField(
        required=False,
        label="First-visit message",
        widget=forms.Textarea(attrs={"rows": 3, **FC}),
        help_text="Use {name} for the guest's name.",
    )
    send_second_visit_thankyou = forms.BooleanField(
        required=False,
        label="Send second-visit thank-you SMS",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    thankyou_second_visit_message = forms.CharField(
        required=False,
        label="Second-visit message",
        widget=forms.Textarea(attrs={"rows": 3, **FC}),
        help_text="Use {name} for the guest's name.",
    )
    enable_sms_birthday = forms.BooleanField(
        required=False,
        label="Send birthday SMS to guests and members",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    enable_sms_bulk = forms.BooleanField(
        required=False,
        label="Allow bulk SMS to guests",
        widget=forms.CheckboxInput(attrs=CHK),
    )

    SETTING_FIELDS = [
        "committed_attendance_threshold",
        "committed_attendance_window_weeks",
        "guest_id_prefix",
        "send_first_visit_thankyou",
        "thankyou_first_visit_message",
        "send_second_visit_thankyou",
        "thankyou_second_visit_message",
        "enable_sms_birthday",
        "enable_sms_bulk",
    ]

    def __init__(self, *args, church=None, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.settings = settings
        if settings:
            self.initial.update(
                {
                    f: getattr(settings, f)
                    for f in self.SETTING_FIELDS
                    if hasattr(settings, f)
                }
            )

    def clean_guest_id_prefix(self):
        val = self.cleaned_data.get("guest_id_prefix", "").strip().upper()
        if not val.isalpha() or not (2 <= len(val) <= 6):
            raise forms.ValidationError("Prefix must be 2-6 letters only.")
        return val

    def clean(self):
        cleaned = super().clean()
        # Restore DB value (or safe default) when a field is left blank,
        # so a partial save on this tab never zeros out threshold settings.
        if not cleaned.get("committed_attendance_threshold"):
            cleaned["committed_attendance_threshold"] = (
                getattr(self.settings, "committed_attendance_threshold", None) or 4
            )
        if not cleaned.get("committed_attendance_window_weeks"):
            cleaned["committed_attendance_window_weeks"] = (
                getattr(self.settings, "committed_attendance_window_weeks", None) or 6
            )
        return cleaned

    def save(self):
        c = self.cleaned_data
        for f in self.SETTING_FIELDS:
            if f in c:
                setattr(self.settings, f, c[f])
        self.settings.save()
        return self.settings


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Tab 2b â€” Lookup table inline forms (VisitChannel, VisitPurpose, ChurchService)
# Used by the AJAX endpoints in tenants/views.py for the inline CRUD table.
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class LookupItemForm(forms.Form):
    """Generic add/edit form for a single lookup item (channel, purpose, service)."""

    name = forms.CharField(
        max_length=150,
        label="Name",
        widget=forms.TextInput(attrs={**FC_SM, "placeholder": "e.g. Sunday Service"}),
    )
    order = forms.IntegerField(
        required=False,
        label="Order",
        widget=forms.NumberInput(attrs={**FC_SM, "min": 0}),
        initial=0,
    )
    is_active = forms.BooleanField(
        required=False,
        label="Active",
        widget=forms.CheckboxInput(attrs=CHK),
        initial=True,
    )


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Tab 3 â€” LMS & Onboarding
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class LMSSettingsForm(forms.Form):
    """
    Controls how the LMS integrates with the guest->workforce pipeline.

    Per-course configuration (passing score, retakes, strict sequence)
    lives on LMSCourse itself. This form controls church-level defaults
    and pipeline integration rules.
    """

    # Which course type triggers induction promotion
    induction_course_type = forms.ChoiceField(
        choices=[
            ("induction", "Member Induction course (default)"),
            ("unit_training", "Unit Training course"),
            ("any", "Any completed course"),
        ],
        label="Induction course type",
        widget=forms.Select(attrs=FS),
        help_text="Which LMS course type counts toward workforce induction.",
        initial="induction",
    )

    # Default passing score applied to new induction courses
    default_passing_score = forms.IntegerField(
        min_value=0,
        max_value=100,
        label="Default passing score (%)",
        widget=forms.NumberInput(attrs=FC),
        initial=70,
        help_text="Applied when creating new induction courses. Can be overridden per course.",
    )

    # Promotion rule
    promotion_mode = forms.ChoiceField(
        choices=[
            ("auto", "Automatic â€” promote immediately on passing score"),
            ("manual", "Manual â€” admin approves each promotion"),
        ],
        label="Promotion mode",
        widget=forms.Select(attrs=FS),
        help_text=(
            "Auto: trainee is promoted to Workforce Member the moment they pass. "
            "Manual: admin sees a pending approval queue and promotes individually."
        ),
        initial="auto",
    )

    # Who can approve manual promotions
    promotion_approval_role = forms.ChoiceField(
        choices=[
            ("admin", "Church admin only"),
            ("unit_head", "Unit head or admin"),
            ("any_leader", "Any leadership role"),
        ],
        label="Promotion approval role",
        widget=forms.Select(attrs=FS),
        help_text="Only relevant when promotion mode is Manual.",
        initial="admin",
    )

    # Whether to allow retakes church-wide by default
    allow_retakes_default = forms.BooleanField(
        required=False,
        label="Allow course retakes by default",
        widget=forms.CheckboxInput(attrs=CHK),
        initial=True,
    )
    max_retakes_default = forms.IntegerField(
        min_value=0,
        max_value=20,
        label="Default maximum retakes (0 = unlimited)",
        widget=forms.NumberInput(attrs=FC),
        initial=2,
    )

    # Notification on completion
    notify_unit_head_on_completion = forms.BooleanField(
        required=False,
        label="Notify unit head when a trainee completes induction",
        widget=forms.CheckboxInput(attrs=CHK),
        initial=True,
    )
    notify_admin_on_completion = forms.BooleanField(
        required=False,
        label="Notify church admin on induction completion",
        widget=forms.CheckboxInput(attrs=CHK),
        initial=False,
    )

    # These are stored in ChurchSetting as a JSONField: lms_config
    LMS_CONFIG_FIELDS = [
        "induction_course_type",
        "default_passing_score",
        "promotion_mode",
        "promotion_approval_role",
        "allow_retakes_default",
        "max_retakes_default",
        "notify_unit_head_on_completion",
        "notify_admin_on_completion",
    ]

    def __init__(self, *args, church=None, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.settings = settings
        if settings:
            cfg = getattr(settings, "lms_config", {}) or {}
            for f in self.LMS_CONFIG_FIELDS:
                if f in cfg:
                    self.initial[f] = cfg[f]

    def save(self):
        c = self.cleaned_data
        cfg = {f: c[f] for f in self.LMS_CONFIG_FIELDS if f in c}
        self.settings.lms_config = cfg
        self.settings.save(update_fields=["lms_config", "updated_at"])
        return self.settings


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Tab 4 â€” Workforce
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class WorkforceSettingsForm(forms.Form):
    """
    Church-level workforce configuration.
    Stages, roles, and unit roles are managed via inline CRUD on the same tab.
    This form holds the church-level defaults and rules.
    """

    member_id_prefix = forms.CharField(
        max_length=6,
        label="Member ID prefix",
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control text-uppercase", "maxlength": 6}
        ),
        help_text="e.g. MBR -> MBR000001",
    )

    # Default stage for new workforce members
    default_workforce_stage = forms.CharField(
        max_length=100,
        required=False,
        label="Default stage for new members",
        widget=forms.TextInput(attrs={**FC, "placeholder": "e.g. Probation"}),
        help_text="Must match a WorkforceStage name. Leave blank to use first stage.",
    )

    # Absence flagging
    absence_flag_threshold = forms.IntegerField(
        min_value=1,
        max_value=52,
        required=False,
        label="Absences before flagging for review",
        widget=forms.NumberInput(attrs=FC),
        initial=3,
        help_text="Consecutive absences that trigger a 'For Review' flag on a unit membership.",
    )

    SETTING_FIELDS = [
        "member_id_prefix",
    ]
    WORKFORCE_CONFIG_FIELDS = ["default_workforce_stage", "absence_flag_threshold"]

    def __init__(self, *args, church=None, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.settings = settings
        self.is_campus_church = bool(getattr(church, "parent_church_id", None))
        self.hq_settings = (
            _campus_hq_settings(church) if self.is_campus_church else None
        )
        if settings:
            if self.is_campus_church:
                locked_fields = {
                    "member_id_prefix",
                    "default_workforce_stage",
                    "absence_flag_threshold",
                }
                source_settings = self.hq_settings or settings
                for field_name in locked_fields:
                    field = self.fields.get(field_name)
                    if not field:
                        continue
                    field.disabled = True
                    if hasattr(field.widget, "attrs"):
                        field.widget.attrs["disabled"] = "disabled"
                    if hasattr(source_settings, field_name):
                        self.initial[field_name] = getattr(source_settings, field_name)
            for f in self.SETTING_FIELDS:
                if hasattr(settings, f):
                    self.initial[f] = getattr(settings, f)
            cfg = getattr(settings, "workforce_config", {}) or {}
            for f in self.WORKFORCE_CONFIG_FIELDS:
                if f in cfg:
                    self.initial[f] = cfg[f]
            if self.is_campus_church and self.hq_settings:
                for f in self.SETTING_FIELDS:
                    if hasattr(self.hq_settings, f):
                        self.initial[f] = getattr(self.hq_settings, f)
                cfg = getattr(self.hq_settings, "workforce_config", {}) or {}
                for f in self.WORKFORCE_CONFIG_FIELDS:
                    if f in cfg:
                        self.initial[f] = cfg[f]

    def clean_member_id_prefix(self):
        val = (self.cleaned_data.get("member_id_prefix") or "").strip().upper()
        if not val:
            return getattr(self.settings, "member_id_prefix", "MBR")
        if not val.isalpha() or not (2 <= len(val) <= 6):
            raise forms.ValidationError("Prefix must be 2-6 letters only.")
        return val

    def save(self):
        c = self.cleaned_data
        for f in self.SETTING_FIELDS:
            if f in c:
                setattr(self.settings, f, c[f])
        cfg = {f: c[f] for f in self.WORKFORCE_CONFIG_FIELDS if f in c}
        self.settings.workforce_config = cfg
        self.settings.save()
        return self.settings


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Tab 5 â€” ID & Display
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class DisplaySettingsForm(forms.Form):
    """Date format and other display-only settings."""

    date_format = forms.CharField(
        max_length=20,
        label="Date display format",
        widget=forms.TextInput(attrs={"placeholder": "%d %b %Y", **FC}),
        help_text="Python strftime format. e.g. %d %b %Y -> 14 Jun 2025",
    )
    time_format = forms.CharField(
        max_length=20,
        label="Time display format",
        widget=forms.TextInput(attrs={"placeholder": "%H:%M:%S", **FC}),
        help_text="Python strftime format. e.g. %H:%M:%S -> 14:30:00",
    )
    SETTING_FIELDS = ["date_format", "time_format"]

    def __init__(self, *args, church=None, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.settings = settings
        if settings:
            self.initial.update(
                {
                    "date_format": settings.date_format,
                    # Use the stored DB value so the Display tab selects stay aligned
                    # with what was saved (get_effective_time_format normalizes e.g.
                    # %H:%M → %H:%M:%S which made the UI look like it reverted).
                    "time_format": getattr(settings, "time_format", None)
                    or "%H:%M:%S",
                }
            )
        if church and getattr(church, "parent_church_id", None):
            hq_settings = _campus_hq_settings(church)
            if hq_settings:
                for f in self.SETTING_FIELDS:
                    if hasattr(hq_settings, f):
                        self.initial[f] = getattr(hq_settings, f)
                    field = self.fields.get(f)
                    if field:
                        field.disabled = True
                        if hasattr(field.widget, "attrs"):
                            field.widget.attrs["disabled"] = "disabled"

    def save(self):
        c = self.cleaned_data
        self.settings.date_format = c["date_format"]
        self.settings.time_format = c["time_format"]
        self.settings.save(update_fields=["date_format", "time_format", "updated_at"])
        return self.settings


# ─────────────────────────────────────────────────────────────────────────────
# Feature Flags (Module Visibility) — separate mini-form so the flags card
# can submit independently without requiring all church-profile fields.
# ─────────────────────────────────────────────────────────────────────────────


class FeatureFlagsForm(forms.Form):
    """Church-wide module on/off toggles. Saved to ChurchSetting."""

    enable_guest_module = forms.BooleanField(
        required=False,
        label="Enable Guest module",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    enable_music_module = forms.BooleanField(
        required=False,
        label="Enable Music module",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    enable_media_module = forms.BooleanField(
        required=False,
        label="Enable Media module",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    enable_children_module = forms.BooleanField(
        required=False,
        label="Enable Children module",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    enable_youth_module = forms.BooleanField(
        required=False,
        label="Enable Youth module",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    enable_teenagers_module = forms.BooleanField(
        required=False,
        label="Enable Teenagers module",
        widget=forms.CheckboxInput(attrs=CHK),
    )

    FLAG_FIELDS = [
        "enable_guest_module",
        "enable_music_module",
        "enable_media_module",
        "enable_children_module",
        "enable_youth_module",
        "enable_teenagers_module",
    ]

    def __init__(self, *args, church=None, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.settings = settings
        if settings:
            for f in self.FLAG_FIELDS:
                if hasattr(settings, f):
                    self.initial[f] = getattr(settings, f)

    def save(self):
        c = self.cleaned_data
        for f in self.FLAG_FIELDS:
            if f in c:
                setattr(self.settings, f, c[f])
        self.settings.save(update_fields=[*self.FLAG_FIELDS, "updated_at"])
        return self.settings
