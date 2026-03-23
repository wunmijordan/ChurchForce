from django import forms

from youth.models import YouthProfile, ImpactProject, CareerGoal
from units.models import ChurchUnit
from accounts.models import ChurchMember


class YouthProfileForm(forms.ModelForm):
    class Meta:
        model = YouthProfile
        fields = [
            "unit",
            "member",
            "full_name",
            "school",
            "career_interest",
            "phone",
            "email",
            "notes",
        ]
        widgets = {
            "unit": forms.Select(attrs={"class": "form-select"}),
            "member": forms.Select(attrs={"class": "form-select"}),
            "full_name": forms.TextInput(attrs={"class": "form-control"}),
            "school": forms.TextInput(attrs={"class": "form-control"}),
            "career_interest": forms.TextInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, church=None, **kwargs):
        super().__init__(*args, **kwargs)
        if church:
            self.fields["unit"].queryset = ChurchUnit.raw_objects.filter(
                church=church, is_active=True, youth_module=True
            )
            self.fields["member"].queryset = ChurchMember.raw_objects.filter(
                church=church, is_active=True
            ).select_related("user")


class ImpactProjectForm(forms.ModelForm):
    class Meta:
        model = ImpactProject
        fields = [
            "unit",
            "title",
            "focus_area",
            "start_date",
            "status",
            "notes",
        ]
        widgets = {
            "unit": forms.Select(attrs={"class": "form-select"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "focus_area": forms.TextInput(attrs={"class": "form-control"}),
            "start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, church=None, **kwargs):
        super().__init__(*args, **kwargs)
        if church:
            self.fields["unit"].queryset = ChurchUnit.raw_objects.filter(
                church=church, is_active=True, youth_module=True
            )


class CareerGoalForm(forms.ModelForm):
    class Meta:
        model = CareerGoal
        fields = [
            "youth",
            "title",
            "status",
            "notes",
        ]
        widgets = {
            "youth": forms.Select(attrs={"class": "form-select"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, church=None, **kwargs):
        super().__init__(*args, **kwargs)
        if church:
            self.fields["youth"].queryset = YouthProfile.raw_objects.filter(
                church=church, is_active=True
            )
