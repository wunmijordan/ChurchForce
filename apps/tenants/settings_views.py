"""
tenants/settings_views.py

In-app church settings views — accessible to church admins via the
normal authenticated UI, not the Django admin.

Covers:
    church_settings     — unified settings page for the church admin
    member_settings     — individual member notification preferences

Permission required: dashboard.admin (or superuser)
"""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from tenants.models import Church, ChurchSetting


# ─────────────────────────────────────────────────────────────────────────────
# Forms
# ─────────────────────────────────────────────────────────────────────────────

class ChurchProfileForm(forms.ModelForm):
    """Editable fields on the Church model itself."""

    class Meta:
        model  = Church
        fields = [
            "name", "logo", "primary_color",
            "latitude", "longitude", "attendance_radius_km",
            "timezone",
        ]
        widgets = {
            "name":                 forms.TextInput(attrs={"class": "form-control"}),
            "primary_color":        forms.TextInput(attrs={"class": "form-control", "type": "color"}),
            "latitude":             forms.NumberInput(attrs={"class": "form-control", "step": "0.000001"}),
            "longitude":            forms.NumberInput(attrs={"class": "form-control", "step": "0.000001"}),
            "attendance_radius_km": forms.NumberInput(attrs={"class": "form-control", "step": "0.001"}),
            "timezone":             forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Africa/Lagos"}),
            "logo":                 forms.ClearableFileInput(attrs={"class": "form-control"}),
        }
        labels = {
            "name":                 "Organisation name",
            "primary_color":        "Brand colour",
            "latitude":             "Venue latitude",
            "longitude":            "Venue longitude",
            "attendance_radius_km": "Attendance radius (km)",
            "timezone":             "Local timezone",
            "logo":                 "Logo",
        }
        help_texts = {
            "latitude":  "Right-click your venue on Google Maps → 'What's here?' to get coordinates.",
            "longitude": "Used for geo-fenced clock-in/out.",
            "timezone":  "e.g. Africa/Lagos, America/New_York, Europe/London",
        }


class ChurchSettingForm(forms.ModelForm):
    """Editable fields on ChurchSetting."""

    class Meta:
        model  = ChurchSetting
        fields = [
            "committed_attendance_threshold",
            "committed_attendance_window_weeks",
            "member_id_prefix",
            "guest_id_prefix",
            "date_format",
            "enable_music_module",
            "enable_media_module",
            "enable_sms_birthday",
            "enable_sms_bulk",
        ]
        widgets = {
            "committed_attendance_threshold":      forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 52}),
            "committed_attendance_window_weeks":   forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 52}),
            "member_id_prefix":  forms.TextInput(attrs={"class": "form-control text-uppercase", "maxlength": 6}),
            "guest_id_prefix":   forms.TextInput(attrs={"class": "form-control text-uppercase", "maxlength": 6}),
            "date_format":       forms.TextInput(attrs={"class": "form-control", "placeholder": "%d %b %Y"}),
            "enable_music_module":  forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "enable_media_module":  forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "enable_sms_birthday":  forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "enable_sms_bulk":      forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "committed_attendance_threshold":     "Attendance threshold for Committed status",
            "committed_attendance_window_weeks":  "Rolling window (weeks)",
            "member_id_prefix":  "Member ID prefix",
            "guest_id_prefix":   "Guest ID prefix",
            "date_format":       "Date display format",
            "enable_music_module":  "Enable music module",
            "enable_media_module":  "Enable media module",
            "enable_sms_birthday":  "Enable birthday SMS",
            "enable_sms_bulk":      "Enable bulk SMS",
        }

    def clean_member_id_prefix(self):
        val = self.cleaned_data.get("member_id_prefix", "").strip().upper()
        if not val.isalpha() or not (2 <= len(val) <= 6):
            raise forms.ValidationError("Prefix must be 2–6 letters only.")
        return val

    def clean_guest_id_prefix(self):
        val = self.cleaned_data.get("guest_id_prefix", "").strip().upper()
        if not val.isalpha() or not (2 <= len(val) <= 6):
            raise forms.ValidationError("Prefix must be 2–6 letters only.")
        return val


class ChurchThankYouForm(forms.ModelForm):
    """Separate form section for thank-you message configuration."""

    class Meta:
        model  = ChurchSetting
        fields = [
            "send_first_visit_thankyou",
            "thankyou_first_visit_message",
            "send_second_visit_thankyou",
            "thankyou_second_visit_message",
        ]
        widgets = {
            "thankyou_first_visit_message":  forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "thankyou_second_visit_message": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "send_first_visit_thankyou":     forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "send_second_visit_thankyou":    forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "send_first_visit_thankyou":     "Send first-visit thank-you SMS",
            "thankyou_first_visit_message":  "First-visit message",
            "send_second_visit_thankyou":    "Send second-visit thank-you SMS",
            "thankyou_second_visit_message": "Second-visit message",
        }
        help_texts = {
            "thankyou_first_visit_message":  "Use {name} for the guest's name.",
            "thankyou_second_visit_message": "Use {name} for the guest's name.",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Views
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def church_settings(request):
    """
    Unified church settings page.
    Combines Church profile fields and ChurchSetting preferences
    into a single page with two form sections.

    Template: tenants/church_settings.html
    """
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    # Permission gate
    perms    = getattr(request, "permissions", None)
    is_admin = (
        request.user.is_superuser
        or (perms and perms.can("dashboard.admin"))
    )
    if not is_admin:
        messages.error(request, "You do not have permission to manage church settings.")
        return redirect("dashboard")

    # Get or create ChurchSetting
    church_setting, _ = ChurchSetting.objects.get_or_create(church=church)

    if request.method == "POST":
        profile_form    = ChurchProfileForm(request.POST, request.FILES, instance=church)
        settings_form   = ChurchSettingForm(request.POST, instance=church_setting)
        thankyou_form   = ChurchThankYouForm(request.POST, instance=church_setting)

        if profile_form.is_valid() and settings_form.is_valid() and thankyou_form.is_valid():
            profile_form.save()
            settings_form.save()
            thankyou_form.save()
            messages.success(request, "Settings saved successfully.")
            return redirect("tenants:church_settings")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        profile_form    = ChurchProfileForm(instance=church)
        settings_form   = ChurchSettingForm(instance=church_setting)
        thankyou_form   = ChurchThankYouForm(instance=church_setting)

    return render(request, "tenants/church_settings.html", {
        "profile_form":   profile_form,
        "settings_form":  settings_form,
        "thankyou_form":  thankyou_form,
        "church":         church,
        "page_title":     "Church Settings",
    })


@login_required
def member_settings(request):
    """
    Individual member notification and display preferences.
    Every authenticated member can access this regardless of role.

    Template: tenants/member_settings.html
    """
    member = getattr(request, "member", None)
    church = getattr(request, "church", None)

    if not member or not church:
        return HttpResponseForbidden("No church context.")

    from notifications.models import UserSettings
    from notifications.forms import UserSettingsForm

    user_settings, _ = UserSettings.raw_objects.get_or_create(
        member=member,
        defaults={"church": church},
    )

    if request.method == "POST":
        form = UserSettingsForm(request.POST, instance=user_settings)
        if form.is_valid():
            form.save()
            messages.success(request, "Your preferences have been saved.")
            return redirect("tenants:member_settings")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = UserSettingsForm(instance=user_settings)

    return render(request, "tenants/member_settings.html", {
        "form":       form,
        "church":     church,
        "page_title": "My Settings",
    })