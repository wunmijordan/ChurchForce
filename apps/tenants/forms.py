"""
In-app church settings views â€” accessible to church admins via the
normal authenticated UI, not the Django admin.

Covers:
    church_settings     â€” unified settings page for the church admin
    member_settings     â€” individual member notification preferences

Permission required: dashboard.admin (or superuser)
"""

from django.contrib.auth.password_validation import validate_password
from django.core import exceptions
from django.utils.text import slugify
from django import forms
from accounts.models import CustomUser
from tenants.models import Church, ChurchSetting


class ChurchSignupForm(forms.Form):
    """
    Minimal self-serve signup form.
    Plan selection happens AFTER signup on the billing/pricing page.
    Coordinates are set by the admin via Django admin after signup.
    """

    # Organisation
    church_name = forms.CharField(
        max_length=255,
        label="Organisation name",
        widget=forms.TextInput(attrs={"placeholder": "e.g. Grace Chapel"}),
    )
    url_handle = forms.SlugField(
        max_length=63,
        label="URL handle",
        help_text=(
            "Letters, numbers and hyphens only. "
            "This becomes your address: workforce.church/your-handle/"
        ),
        widget=forms.TextInput(attrs={"placeholder": "grace-chapel"}),
    )

    # Admin user
    full_name = forms.CharField(
        max_length=255,
        label="Your full name",
        widget=forms.TextInput(attrs={"placeholder": "John Doe"}),
    )
    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"placeholder": "john@gracechapel.org"}),
    )
    password = forms.CharField(
        label="Password",
        min_length=8,
        widget=forms.PasswordInput(),
    )
    password_confirm = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(),
    )

    def clean_email(self):
        """
        Check if the email address is already registered to a CustomUser.
        """
        email = self.cleaned_data.get("email")
        if email and CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "A user with this email address already exists. "
                "Please log in or use a different email."
            )
        return email

    def clean_url_handle(self):
        # 1. Clean the input
        value = slugify(self.cleaned_data.get("url_handle", ""))
        if not value:
            raise forms.ValidationError("Please enter a valid URL handle.")
            
        # 2. Check if it's already taken
        # We check both slug and subdomain to be safe
        if Church.raw_objects.filter(slug=value).exists() or \
           Church.raw_objects.filter(subdomain=value).exists():
            raise forms.ValidationError(
                f"The URL 'workforce.church/{value}' is already taken. "
                "Please try a different name."
            )
            
        # 3. Optional: Block "reserved" words
        reserved_words = ['admin', 'api', 'billing', 'support', 'app', 'dev']
        if value in reserved_words:
            raise forms.ValidationError("This URL handle is reserved. Please choose another.")

        return value
    
    def clean_password(self):
        password = self.cleaned_data.get("password")
        
        # This checks the password against your settings.AUTH_PASSWORD_VALIDATORS
        try:
            validate_password(password)
        except exceptions.ValidationError as e:
            # Re-raise the error so it shows up on the form field
            raise forms.ValidationError(list(e.messages))
            
        return password

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("password_confirm")
        if p1 and p2 and p1 != p2:
            self.add_error("password_confirm", "Passwords do not match.")
        return cleaned
    

class ChurchSettingsForm(forms.Form):
    """Unified settings form for Church + ChurchSetting."""

    # Church fields
    name = forms.CharField(
        max_length=255,
        label="Organisation name",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    primary_color = forms.CharField(
        max_length=20,
        label="Brand colour",
        widget=forms.TextInput(attrs={"class": "form-control", "type": "color"}),
    )
    logo = forms.ImageField(
        required=False,
        label="Logo",
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )
    timezone = forms.CharField(
        max_length=63,
        label="Timezone",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Africa/Lagos"}),
        help_text="e.g. Africa/Lagos, America/New_York, Europe/London",
    )
    latitude = forms.DecimalField(
        max_digits=9,
        decimal_places=6,
        label="Latitude",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.000001"}),
        help_text="Right-click your venue on Google Maps -> 'What's here?' to get coordinates.",
    )
    longitude = forms.DecimalField(
        max_digits=9,
        decimal_places=6,
        label="Longitude",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.000001"}),
        help_text="Used for geo-fenced clock-in/out.",
    )
    attendance_radius_km = forms.DecimalField(
        max_digits=5,
        decimal_places=3,
        label="Radius (km)",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
    )

    # ChurchSetting fields
    committed_attendance_threshold = forms.IntegerField(
        min_value=1,
        max_value=52,
        label="Attendance threshold for Committed status",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    committed_attendance_window_weeks = forms.IntegerField(
        min_value=1,
        max_value=52,
        label="Rolling window (weeks)",
        widget=forms.NumberInput(attrs={"class": "form-control"}),
    )
    member_id_prefix = forms.CharField(
        max_length=6,
        label="Member ID prefix",
        widget=forms.TextInput(attrs={"class": "form-control text-uppercase", "maxlength": 6}),
    )
    guest_id_prefix = forms.CharField(
        max_length=6,
        label="Guest ID prefix",
        widget=forms.TextInput(attrs={"class": "form-control text-uppercase", "maxlength": 6}),
    )
    date_format = forms.CharField(
        max_length=20,
        label="Date display format",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "%d %b %Y"}),
    )
    campus_growth_stages = forms.CharField(
        required=False,
        label="Campus growth stages",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "cell, satellite, daughter"}),
        help_text="Comma-separated list. Leave blank to allow free-text entry.",
    )

    # Feature flags
    enable_guest_module = forms.BooleanField(
        required=False,
        label="Enable guest module",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    enable_music_module = forms.BooleanField(
        required=False,
        label="Enable music module",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    enable_media_module = forms.BooleanField(
        required=False,
        label="Enable media module",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    enable_children_module = forms.BooleanField(
        required=False,
        label="Enable children module",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    enable_youth_module = forms.BooleanField(
        required=False,
        label="Enable youth module",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    enable_teenagers_module = forms.BooleanField(
        required=False,
        label="Enable teenagers module",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    enable_sms_birthday = forms.BooleanField(
        required=False,
        label="Enable birthday SMS",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    enable_sms_bulk = forms.BooleanField(
        required=False,
        label="Enable bulk SMS",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    enable_welcome_screen = forms.BooleanField(
        required=False,
        label="Enable welcome screen",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    # Thank-you messages
    send_first_visit_thankyou = forms.BooleanField(
        required=False,
        label="Send first-visit thank-you SMS",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    thankyou_first_visit_message = forms.CharField(
        required=False,
        label="First-visit message",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        help_text="Use {name} for the guest's name.",
    )
    send_second_visit_thankyou = forms.BooleanField(
        required=False,
        label="Send second-visit thank-you SMS",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    thankyou_second_visit_message = forms.CharField(
        required=False,
        label="Second-visit message",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        help_text="Use {name} for the guest's name.",
    )

    CHURCH_FIELDS = [
        "name",
        "primary_color",
        "timezone",
        "latitude",
        "longitude",
        "attendance_radius_km",
    ]

    SETTING_FIELDS = [
        "committed_attendance_threshold",
        "committed_attendance_window_weeks",
        "member_id_prefix",
        "guest_id_prefix",
        "date_format",
        "campus_growth_stages",
        "enable_guest_module",
        "enable_music_module",
        "enable_media_module",
        "enable_children_module",
        "enable_youth_module",
        "enable_teenagers_module",
        "enable_sms_birthday",
        "enable_sms_bulk",
        "enable_welcome_screen",
        "send_first_visit_thankyou",
        "thankyou_first_visit_message",
        "send_second_visit_thankyou",
        "thankyou_second_visit_message",
    ]

    def __init__(self, *args, church=None, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.settings = settings

        if church is not None:
            self.initial.update({
                "name": church.name,
                "primary_color": church.primary_color,
                "timezone": church.timezone,
                "latitude": church.latitude,
                "longitude": church.longitude,
                "attendance_radius_km": church.attendance_radius_km,
            })
            self.fields["logo"].initial = church.logo

        if settings is not None:
            campus_values = settings.campus_growth_stages or []
            campus_text = ", ".join([str(v) for v in campus_values])
            self.initial.update({
                "committed_attendance_threshold": settings.committed_attendance_threshold,
                "committed_attendance_window_weeks": settings.committed_attendance_window_weeks,
                "member_id_prefix": settings.member_id_prefix,
                "guest_id_prefix": settings.guest_id_prefix,
                "date_format": settings.date_format,
                "campus_growth_stages": campus_text,
                "enable_guest_module": settings.enable_guest_module,
                "enable_music_module": settings.enable_music_module,
                "enable_media_module": settings.enable_media_module,
                "enable_children_module": settings.enable_children_module,
                "enable_youth_module": settings.enable_youth_module,
                "enable_teenagers_module": settings.enable_teenagers_module,
                "enable_sms_birthday": settings.enable_sms_birthday,
                "enable_sms_bulk": settings.enable_sms_bulk,
                "enable_welcome_screen": settings.enable_welcome_screen,
                "send_first_visit_thankyou": settings.send_first_visit_thankyou,
                "thankyou_first_visit_message": settings.thankyou_first_visit_message,
                "send_second_visit_thankyou": settings.send_second_visit_thankyou,
                "thankyou_second_visit_message": settings.thankyou_second_visit_message,
            })

    def clean_member_id_prefix(self):
        val = self.cleaned_data.get("member_id_prefix", "").strip().upper()
        if not val.isalpha() or not (2 <= len(val) <= 6):
            raise forms.ValidationError("Prefix must be 2-6 letters only.")
        return val

    def clean_guest_id_prefix(self):
        val = self.cleaned_data.get("guest_id_prefix", "").strip().upper()
        if not val.isalpha() or not (2 <= len(val) <= 6):
            raise forms.ValidationError("Prefix must be 2-6 letters only.")
        return val

    def clean_campus_growth_stages(self):
        val = self.cleaned_data.get("campus_growth_stages")
        if val in (None, ""):
            return []
        if isinstance(val, list):
            return [str(v).strip() for v in val if str(v).strip()]
        parts = [p.strip() for p in str(val).split(",")]
        return [p for p in parts if p]

    def save(self):
        if self.church is None or self.settings is None:
            raise ValueError("ChurchSettingsForm requires church and settings to save.")

        cleaned = self.cleaned_data
        for field in self.CHURCH_FIELDS:
            setattr(self.church, field, cleaned[field])

        logo = cleaned.get("logo")
        if logo is False:
            self.church.logo = None
        elif logo:
            self.church.logo = logo

        self.church.save()

        for field in self.SETTING_FIELDS:
            setattr(self.settings, field, cleaned[field])
        self.settings.save()

        return self.church, self.settings


