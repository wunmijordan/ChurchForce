from django import forms

from accounts.models import ChurchMember
from guests.models import GuestEntry
from units.models import ChurchUnit
from teenagers.models import TeenProfile, TrendPulse, MentorAssignment


class TeenProfileForm(forms.ModelForm):
    class Meta:
        model = TeenProfile
        fields = [
            "unit",
            "member",
            "guest",
            "full_name",
            "school",
            "interests",
            "guardian_member",
            "guardian_guest",
            "guardian_name",
            "guardian_phone",
            "notes",
        ]
        widgets = {
            "unit": forms.Select(attrs={"class": "form-select"}),
            "member": forms.Select(attrs={"class": "form-select"}),
            "guest": forms.Select(attrs={"class": "form-select"}),
            "full_name": forms.TextInput(attrs={"class": "form-control"}),
            "school": forms.TextInput(attrs={"class": "form-control"}),
            "interests": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "guardian_member": forms.Select(attrs={"class": "form-select"}),
            "guardian_guest": forms.Select(attrs={"class": "form-select"}),
            "guardian_name": forms.TextInput(attrs={"class": "form-control"}),
            "guardian_phone": forms.TextInput(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, church=None, **kwargs):
        super().__init__(*args, **kwargs)
        if church:
            self.fields["unit"].queryset = ChurchUnit.raw_objects.filter(
                church=church, is_active=True, teenagers_module=True
            )
            self.fields["member"].queryset = ChurchMember.raw_objects.filter(
                church=church, is_active=True
            ).select_related("user")
            self.fields["guest"].queryset = GuestEntry.raw_objects.filter(
                church=church, is_active=True
            )
            self.fields["guardian_member"].queryset = ChurchMember.raw_objects.filter(
                church=church, is_active=True
            ).select_related("user")
            self.fields["guardian_guest"].queryset = GuestEntry.raw_objects.filter(
                church=church, is_active=True
            )

    def clean(self):
        cleaned = super().clean()
        member = cleaned.get("member")
        guest = cleaned.get("guest")
        full_name = (cleaned.get("full_name") or "").strip()

        if not member and not guest and not full_name:
            raise forms.ValidationError("Provide a linked member, guest, or name.")

        return cleaned


class MentorAssignmentForm(forms.ModelForm):
    class Meta:
        model = MentorAssignment
        fields = [
            "teen",
            "mentor",
            "start_date",
            "notes",
        ]
        widgets = {
            "teen": forms.Select(attrs={"class": "form-select"}),
            "mentor": forms.Select(attrs={"class": "form-select"}),
            "start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, church=None, **kwargs):
        super().__init__(*args, **kwargs)
        if church:
            self.fields["teen"].queryset = TeenProfile.raw_objects.filter(
                church=church, is_active=True
            )
            self.fields["mentor"].queryset = ChurchMember.raw_objects.filter(
                church=church, is_active=True
            ).select_related("user")


class TrendPulseForm(forms.ModelForm):
    class Meta:
        model = TrendPulse
        fields = [
            "topic",
            "summary",
        ]
        widgets = {
            "topic": forms.TextInput(attrs={"class": "form-control"}),
            "summary": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }
