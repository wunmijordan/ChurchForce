from django import forms

from accounts.models import ChurchMember
from guests.models import GuestEntry
from children.models import ChildProfile, CheckInSession
from units.models import ChurchUnit


class ChildProfileForm(forms.ModelForm):
    class Meta:
        model = ChildProfile
        fields = [
            "unit",
            "guardian_member",
            "guardian_guest",
            "guardian_name",
            "guardian_phone",
            "guardian_email",
            "first_name",
            "last_name",
            "birth_date",
            "gender",
            "grade",
            "allergies",
            "notes",
        ]
        widgets = {
            "unit": forms.Select(attrs={"class": "form-select"}),
            "guardian_member": forms.Select(attrs={"class": "form-select"}),
            "guardian_guest": forms.Select(attrs={"class": "form-select"}),
            "guardian_name": forms.TextInput(attrs={"class": "form-control"}),
            "guardian_phone": forms.TextInput(attrs={"class": "form-control"}),
            "guardian_email": forms.EmailInput(attrs={"class": "form-control"}),
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "birth_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "gender": forms.Select(attrs={"class": "form-select"}),
            "grade": forms.TextInput(attrs={"class": "form-control"}),
            "allergies": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, church=None, **kwargs):
        super().__init__(*args, **kwargs)
        if church:
            self.fields["unit"].queryset = ChurchUnit.raw_objects.filter(
                church=church, is_active=True, children_module=True
            )
            self.fields["guardian_member"].queryset = ChurchMember.raw_objects.filter(
                church=church, is_active=True
            ).select_related("user")
            self.fields["guardian_guest"].queryset = GuestEntry.raw_objects.filter(
                church=church, is_active=True
            )

    def clean(self):
        cleaned = super().clean()
        guardian_member = cleaned.get("guardian_member")
        guardian_guest = cleaned.get("guardian_guest")
        guardian_name = (cleaned.get("guardian_name") or "").strip()

        if not guardian_member and not guardian_guest and not guardian_name:
            raise forms.ValidationError(
                "Add at least one guardian (member, guest, or name)."
            )
        return cleaned


class CheckInSessionForm(forms.ModelForm):
    class Meta:
        model = CheckInSession
        fields = [
            "unit",
            "title",
            "session_date",
            "notes",
        ]
        widgets = {
            "unit": forms.Select(attrs={"class": "form-select"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "session_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, church=None, **kwargs):
        super().__init__(*args, **kwargs)
        if church:
            self.fields["unit"].queryset = ChurchUnit.raw_objects.filter(
                church=church, is_active=True, children_module=True
            )
