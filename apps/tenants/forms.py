"""
tenants/forms.py

Church-admin-facing settings forms. All tabs in the church settings
page correspond to a section here.

Tabs / sections:
    1. General          — Church profile, branding, geo, timezone
    2. Guest Pipeline   — Committed threshold, lookup tables (VisitChannel,
                          VisitPurpose, ChurchService) inline CRUD
    3. LMS & Onboarding — Which courses count, passing score, promotion rules
    4. Workforce        — Stages, roles, unit roles
    5. Notifications    — SMS templates, feature flags
    6. ID & Display     — Prefixes, date format, campus stages
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
CHK = {"class": "form-check-input"}


# ─────────────────────────────────────────────────────────────────────────────
# Signup form (public, not settings)
# ─────────────────────────────────────────────────────────────────────────────

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
        if Church.raw_objects.filter(slug=value).exists() or \
           Church.raw_objects.filter(subdomain=value).exists():
            raise forms.ValidationError(
                f"The URL 'workforce.church/{value}' is already taken. Please try a different name."
            )
        reserved = ["admin", "api", "billing", "support", "app", "dev"]
        if value in reserved:
            raise forms.ValidationError("This URL handle is reserved. Please choose another.")
        return value

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


# ─────────────────────────────────────────────────────────────────────────────
# Tab 1 — General
# ─────────────────────────────────────────────────────────────────────────────

class GeneralSettingsForm(forms.Form):
    """Church profile, branding, location."""

    name = forms.CharField(
        max_length=255, label="Organisation name",
        widget=forms.TextInput(attrs=FC),
    )
    primary_color = forms.CharField(
        max_length=20, label="Brand colour",
        widget=forms.TextInput(attrs={"class": "form-control", "type": "color"}),
    )
    logo = forms.ImageField(
        required=False, label="Logo",
        widget=forms.ClearableFileInput(attrs=FC),
    )
    timezone = forms.CharField(
        max_length=63, label="Timezone",
        widget=forms.TextInput(attrs={"placeholder": "Africa/Lagos", **FC}),
        help_text="e.g. Africa/Lagos, America/New_York, Europe/London",
    )
    latitude = forms.DecimalField(
        max_digits=9, decimal_places=6, label="Latitude",
        widget=forms.NumberInput(attrs={"step": "0.000001", **FC}),
        help_text="Right-click venue on Google Maps to get coordinates.",
    )
    longitude = forms.DecimalField(
        max_digits=9, decimal_places=6, label="Longitude",
        widget=forms.NumberInput(attrs={"step": "0.000001", **FC}),
    )
    attendance_radius_km = forms.DecimalField(
        max_digits=5, decimal_places=3, label="Clock-in radius (km)",
        widget=forms.NumberInput(attrs={"step": "0.001", **FC}),
        help_text="Geo-fence radius for attendance clock-in/out.",
    )
    hide_platform_credit = forms.BooleanField(
        required=False, label="Hide 'Powered by ChurchForce'",
        widget=forms.CheckboxInput(attrs=CHK),
        help_text="Remove ChurchForce branding from the footer (white-label).",
    )
    enable_welcome_screen = forms.BooleanField(
        required=False, label="Show welcome screen after login",
        widget=forms.CheckboxInput(attrs=CHK),
    )

    CHURCH_FIELDS = ["name", "primary_color", "timezone", "latitude",
                     "longitude", "attendance_radius_km", "hide_platform_credit"]
    SETTING_FIELDS = ["enable_welcome_screen"]

    def __init__(self, *args, church=None, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.settings = settings
        if church:
            self.initial.update({
                "name": church.name,
                "primary_color": church.primary_color,
                "timezone": church.timezone,
                "latitude": church.latitude,
                "longitude": church.longitude,
                "attendance_radius_km": church.attendance_radius_km,
                "hide_platform_credit": getattr(church, "hide_platform_credit", False),
            })
            self.fields["logo"].initial = church.logo
        if settings:
            self.initial["enable_welcome_screen"] = settings.enable_welcome_screen

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


# ─────────────────────────────────────────────────────────────────────────────
# Tab 2 — Guest Pipeline
# ─────────────────────────────────────────────────────────────────────────────

class GuestPipelineSettingsForm(forms.Form):
    """
    Guest pipeline thresholds + SMS messages.
    Lookup tables (VisitChannel, VisitPurpose, ChurchService) are managed
    via the inline AJAX views on the same settings tab, not this form.
    """

    # Pipeline thresholds
    committed_attendance_threshold = forms.IntegerField(
        min_value=1, max_value=52,
        label="Services attended to reach Committed",
        widget=forms.NumberInput(attrs=FC),
        help_text="How many service attendances within the rolling window qualify a guest as Committed.",
    )
    committed_attendance_window_weeks = forms.IntegerField(
        min_value=1, max_value=52,
        label="Rolling window (weeks)",
        widget=forms.NumberInput(attrs=FC),
        help_text="e.g. 4 attendances within 6 weeks.",
    )

    # Guest ID prefix
    guest_id_prefix = forms.CharField(
        max_length=6, label="Guest ID prefix",
        widget=forms.TextInput(attrs={"class": "form-control text-uppercase", "maxlength": 6}),
        help_text="e.g. GST -> GST000001",
    )

    # Thank-you SMS
    send_first_visit_thankyou = forms.BooleanField(
        required=False, label="Send first-visit thank-you SMS",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    thankyou_first_visit_message = forms.CharField(
        required=False, label="First-visit message",
        widget=forms.Textarea(attrs={"rows": 3, **FC}),
        help_text="Use {name} for the guest's name.",
    )
    send_second_visit_thankyou = forms.BooleanField(
        required=False, label="Send second-visit thank-you SMS",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    thankyou_second_visit_message = forms.CharField(
        required=False, label="Second-visit message",
        widget=forms.Textarea(attrs={"rows": 3, **FC}),
        help_text="Use {name} for the guest's name.",
    )
    enable_sms_birthday = forms.BooleanField(
        required=False, label="Send birthday SMS to guests and members",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    enable_sms_bulk = forms.BooleanField(
        required=False, label="Allow bulk SMS to guests",
        widget=forms.CheckboxInput(attrs=CHK),
    )

    SETTING_FIELDS = [
        "committed_attendance_threshold", "committed_attendance_window_weeks",
        "guest_id_prefix",
        "send_first_visit_thankyou", "thankyou_first_visit_message",
        "send_second_visit_thankyou", "thankyou_second_visit_message",
        "enable_sms_birthday", "enable_sms_bulk",
    ]

    def __init__(self, *args, church=None, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.settings = settings
        if settings:
            self.initial.update({f: getattr(settings, f) for f in self.SETTING_FIELDS if hasattr(settings, f)})

    def clean_guest_id_prefix(self):
        val = self.cleaned_data.get("guest_id_prefix", "").strip().upper()
        if not val.isalpha() or not (2 <= len(val) <= 6):
            raise forms.ValidationError("Prefix must be 2-6 letters only.")
        return val

    def save(self):
        c = self.cleaned_data
        for f in self.SETTING_FIELDS:
            if f in c:
                setattr(self.settings, f, c[f])
        self.settings.save()
        return self.settings


# ─────────────────────────────────────────────────────────────────────────────
# Tab 2b — Lookup table inline forms (VisitChannel, VisitPurpose, ChurchService)
# Used by the AJAX endpoints in tenants/views.py for the inline CRUD table.
# ─────────────────────────────────────────────────────────────────────────────

class LookupItemForm(forms.Form):
    """Generic add/edit form for a single lookup item (channel, purpose, service)."""
    name = forms.CharField(
        max_length=150, label="Name",
        widget=forms.TextInput(attrs={**FC_SM, "placeholder": "e.g. Sunday Service"}),
    )
    order = forms.IntegerField(
        required=False, label="Order",
        widget=forms.NumberInput(attrs={**FC_SM, "min": 0}),
        initial=0,
    )
    is_active = forms.BooleanField(
        required=False, label="Active",
        widget=forms.CheckboxInput(attrs=CHK),
        initial=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tab 3 — LMS & Onboarding
# ─────────────────────────────────────────────────────────────────────────────

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
        min_value=0, max_value=100,
        label="Default passing score (%)",
        widget=forms.NumberInput(attrs=FC),
        initial=70,
        help_text="Applied when creating new induction courses. Can be overridden per course.",
    )

    # Promotion rule
    promotion_mode = forms.ChoiceField(
        choices=[
            ("auto", "Automatic — promote immediately on passing score"),
            ("manual", "Manual — admin approves each promotion"),
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
        required=False, label="Allow course retakes by default",
        widget=forms.CheckboxInput(attrs=CHK),
        initial=True,
    )
    max_retakes_default = forms.IntegerField(
        min_value=0, max_value=20,
        label="Default maximum retakes (0 = unlimited)",
        widget=forms.NumberInput(attrs=FC),
        initial=2,
    )

    # Notification on completion
    notify_unit_head_on_completion = forms.BooleanField(
        required=False, label="Notify unit head when a trainee completes induction",
        widget=forms.CheckboxInput(attrs=CHK),
        initial=True,
    )
    notify_admin_on_completion = forms.BooleanField(
        required=False, label="Notify church admin on induction completion",
        widget=forms.CheckboxInput(attrs=CHK),
        initial=False,
    )

    # These are stored in ChurchSetting as a JSONField: lms_config
    LMS_CONFIG_FIELDS = [
        "induction_course_type", "default_passing_score",
        "promotion_mode", "promotion_approval_role",
        "allow_retakes_default", "max_retakes_default",
        "notify_unit_head_on_completion", "notify_admin_on_completion",
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


# ─────────────────────────────────────────────────────────────────────────────
# Tab 4 — Workforce
# ─────────────────────────────────────────────────────────────────────────────

class WorkforceSettingsForm(forms.Form):
    """
    Church-level workforce configuration.
    Stages, roles, and unit roles are managed via inline CRUD on the same tab.
    This form holds the church-level defaults and rules.
    """

    member_id_prefix = forms.CharField(
        max_length=6, label="Member ID prefix",
        widget=forms.TextInput(attrs={"class": "form-control text-uppercase", "maxlength": 6}),
        help_text="e.g. MBR -> MBR000001",
    )

    # Default stage for new workforce members
    default_workforce_stage = forms.CharField(
        max_length=100, required=False,
        label="Default stage for new members",
        widget=forms.TextInput(attrs={**FC, "placeholder": "e.g. Probation"}),
        help_text="Must match a WorkforceStage name. Leave blank to use first stage.",
    )

    # Absence flagging
    absence_flag_threshold = forms.IntegerField(
        min_value=1, max_value=52,
        label="Absences before flagging for review",
        widget=forms.NumberInput(attrs=FC),
        initial=3,
        help_text="Consecutive absences that trigger a 'For Review' flag on a unit membership.",
    )

    # Feature flags
    enable_guest_module = forms.BooleanField(
        required=False, label="Enable Guest module",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    enable_music_module = forms.BooleanField(
        required=False, label="Enable Music module",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    enable_media_module = forms.BooleanField(
        required=False, label="Enable Media module",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    enable_children_module = forms.BooleanField(
        required=False, label="Enable Children module",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    enable_youth_module = forms.BooleanField(
        required=False, label="Enable Youth module",
        widget=forms.CheckboxInput(attrs=CHK),
    )
    enable_teenagers_module = forms.BooleanField(
        required=False, label="Enable Teenagers module",
        widget=forms.CheckboxInput(attrs=CHK),
    )

    SETTING_FIELDS = [
        "member_id_prefix",
        "enable_guest_module", "enable_music_module", "enable_media_module",
        "enable_children_module", "enable_youth_module", "enable_teenagers_module",
    ]
    WORKFORCE_CONFIG_FIELDS = ["default_workforce_stage", "absence_flag_threshold"]

    def __init__(self, *args, church=None, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.settings = settings
        if settings:
            for f in self.SETTING_FIELDS:
                if hasattr(settings, f):
                    self.initial[f] = getattr(settings, f)
            cfg = getattr(settings, "workforce_config", {}) or {}
            for f in self.WORKFORCE_CONFIG_FIELDS:
                if f in cfg:
                    self.initial[f] = cfg[f]

    def clean_member_id_prefix(self):
        val = self.cleaned_data.get("member_id_prefix", "").strip().upper()
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


# ─────────────────────────────────────────────────────────────────────────────
# Tab 5 — ID & Display
# ─────────────────────────────────────────────────────────────────────────────

class DisplaySettingsForm(forms.Form):
    """Date format, campus growth stages."""

    date_format = forms.CharField(
        max_length=20, label="Date display format",
        widget=forms.TextInput(attrs={"placeholder": "%d %b %Y", **FC}),
        help_text="Python strftime format. e.g. %d %b %Y -> 14 Jun 2025",
    )
    campus_growth_stages = forms.CharField(
        required=False, label="Campus growth stages",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "cell, satellite, daughter", **FC}),
        help_text="Comma-separated. Leave blank to allow free-text entry.",
    )

    SETTING_FIELDS = ["date_format", "campus_growth_stages"]

    def __init__(self, *args, church=None, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.settings = settings
        if settings:
            stages = settings.campus_growth_stages or []
            self.initial.update({
                "date_format": settings.date_format,
                "campus_growth_stages": ", ".join(str(v) for v in stages),
            })

    def clean_campus_growth_stages(self):
        val = self.cleaned_data.get("campus_growth_stages", "")
        if not val:
            return []
        return [p.strip() for p in val.split(",") if p.strip()]

    def save(self):
        c = self.cleaned_data
        self.settings.date_format = c["date_format"]
        self.settings.campus_growth_stages = c["campus_growth_stages"]
        self.settings.save(update_fields=["date_format", "campus_growth_stages", "updated_at"])
        return self.settings


# ─────────────────────────────────────────────────────────────────────────────
# Backward-compat alias — existing code imports ChurchSettingsForm
# Delegates to GeneralSettingsForm so nothing breaks.
# ─────────────────────────────────────────────────────────────────────────────

class ChurchSettingsForm(GeneralSettingsForm):
    """
    Backward-compatible alias. New code should use the tab-specific forms.
    Existing views that import ChurchSettingsForm continue to work.
    """

    # Pull in the additional SETTING_FIELDS from old form so existing save() still works
    _EXTRA_SETTING_FIELDS = [
        "committed_attendance_threshold", "committed_attendance_window_weeks",
        "member_id_prefix", "guest_id_prefix", "date_format", "campus_growth_stages",
        "enable_guest_module", "enable_music_module", "enable_media_module",
        "enable_children_module", "enable_youth_module", "enable_teenagers_module",
        "enable_sms_birthday", "enable_sms_bulk",
        "send_first_visit_thankyou", "thankyou_first_visit_message",
        "send_second_visit_thankyou", "thankyou_second_visit_message",
    ]

    committed_attendance_threshold = forms.IntegerField(min_value=1, max_value=52, required=False, widget=forms.NumberInput(attrs=FC), label="Committed threshold")
    committed_attendance_window_weeks = forms.IntegerField(min_value=1, max_value=52, required=False, widget=forms.NumberInput(attrs=FC), label="Window (weeks)")
    member_id_prefix = forms.CharField(max_length=6, required=False, widget=forms.TextInput(attrs=FC), label="Member ID prefix")
    guest_id_prefix = forms.CharField(max_length=6, required=False, widget=forms.TextInput(attrs=FC), label="Guest ID prefix")
    date_format = forms.CharField(max_length=20, required=False, widget=forms.TextInput(attrs=FC), label="Date format")
    campus_growth_stages = forms.CharField(required=False, widget=forms.Textarea(attrs={**FC, "rows": 2}), label="Campus stages")
    enable_guest_module = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs=CHK), label="Guest module")
    enable_music_module = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs=CHK), label="Music module")
    enable_media_module = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs=CHK), label="Media module")
    enable_children_module = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs=CHK), label="Children module")
    enable_youth_module = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs=CHK), label="Youth module")
    enable_teenagers_module = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs=CHK), label="Teenagers module")
    enable_sms_birthday = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs=CHK), label="Birthday SMS")
    enable_sms_bulk = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs=CHK), label="Bulk SMS")
    send_first_visit_thankyou = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs=CHK), label="First visit SMS")
    thankyou_first_visit_message = forms.CharField(required=False, widget=forms.Textarea(attrs={**FC, "rows": 3}), label="First visit message")
    send_second_visit_thankyou = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs=CHK), label="Second visit SMS")
    thankyou_second_visit_message = forms.CharField(required=False, widget=forms.Textarea(attrs={**FC, "rows": 3}), label="Second visit message")

    def __init__(self, *args, church=None, settings=None, **kwargs):
        super().__init__(*args, church=church, settings=settings, **kwargs)
        if settings:
            for f in self._EXTRA_SETTING_FIELDS:
                if hasattr(settings, f):
                    v = getattr(settings, f)
                    if f == "campus_growth_stages":
                        v = ", ".join(str(x) for x in (v or []))
                    self.initial[f] = v

    def clean_member_id_prefix(self):
        val = (self.cleaned_data.get("member_id_prefix") or "MBR").strip().upper()
        if val and (not val.isalpha() or not (2 <= len(val) <= 6)):
            raise forms.ValidationError("Prefix must be 2-6 letters only.")
        return val or "MBR"

    def clean_guest_id_prefix(self):
        val = (self.cleaned_data.get("guest_id_prefix") or "GST").strip().upper()
        if val and (not val.isalpha() or not (2 <= len(val) <= 6)):
            raise forms.ValidationError("Prefix must be 2-6 letters only.")
        return val or "GST"

    def clean_campus_growth_stages(self):
        val = self.cleaned_data.get("campus_growth_stages", "")
        if not val:
            return []
        return [p.strip() for p in val.split(",") if p.strip()]

    def save(self):
        church, settings = super().save()
        c = self.cleaned_data
        for f in self._EXTRA_SETTING_FIELDS:
            if f in c and hasattr(settings, f):
                setattr(settings, f, c[f])
        settings.save()
        return church, settings
