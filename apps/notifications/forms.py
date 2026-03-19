from django import forms
from notifications.models import UserSettings


class UserSettingsForm(forms.ModelForm):
    """Form for a member to update their notification preferences."""

    class Meta:
        model  = UserSettings
        fields = ["notification_sound", "vibration_enabled"]
        widgets = {
            "notification_sound": forms.RadioSelect(attrs={"class": "form-check-input"}),
            "vibration_enabled":  forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "notification_sound": "Notification sound",
            "vibration_enabled":  "Enable vibration",
        }