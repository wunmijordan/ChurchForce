from django import forms
from services.models import Event
from units.models import ChurchUnit
from permissions.services.resolver import PermissionResolver


class EventForm(forms.ModelForm):
    """
    Form for creating and editing events.

    Requires church, user, and permissions kwargs so the unit/team
    queryset can be scoped correctly per tenant.
    """

    unit = forms.ModelChoiceField(
        queryset=ChurchUnit.objects.none(),
        required=False,
        empty_label="ChurchForce (all units)",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = Event
        fields = [
            "event_image",
            "name",
            "event_type",
            "attendance_mode",
            "day_of_week",
            "postponed",
            "date",
            "duration_days",
            "end_date",
            "time",
            "unit",
            "is_active",
            "is_recurring_weekly",
            "registrable",
            "registration_link",
        ]
        widgets = {
            "event_image":        forms.ClearableFileInput(attrs={"class": "form-control"}),
            "day_of_week":        forms.Select(attrs={"class": "form-select"}),
            "attendance_mode":    forms.Select(attrs={"class": "form-select"}),
            "postponed":          forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "date":               forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "end_date":           forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "time":               forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
            "name":               forms.TextInput(attrs={"class": "form-control"}),
            "event_type":         forms.Select(attrs={"class": "form-select"}),
            "duration_days":      forms.NumberInput(attrs={"class": "form-control"}),
            "is_recurring_weekly": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_active":          forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "registrable":        forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "registration_link":  forms.URLInput(attrs={"class": "form-control"}),
        }
        help_texts = {
            "duration_days": "Number of days this event lasts.",
        }
        labels = {
            "event_image":        "Event banner / poster",
            "is_recurring_weekly": "Recurring weekly",
            "is_active":          "Active event",
            "registrable":        "Open for registration",
            "registration_link":  "Registration link",
            "attendance_mode":    "Attendance mode",
            "day_of_week":        "Day of the week",
            "event_type":         "Event type",
            "end_date":           "End date",
            "duration_days":      "Duration (days)",
            "postponed":          "Postpone this event",
        }

    def __init__(self, *args, **kwargs):
        self.church      = kwargs.pop("church", None)
        self.user        = kwargs.pop("user", None)
        self.permissions = kwargs.pop("permissions", None)
        super().__init__(*args, **kwargs)

        if not self.church:
            self.fields["unit"].queryset = ChurchUnit.raw_objects.none()
            return

        # Resolve permissions if not passed in
        resolver = self.permissions
        if not resolver and self.user and self.church:
            resolver = PermissionResolver(self.user, self.church)

        # Scope teams to this church
        # raw_objects used — form runs inside a request but we want an
        # explicit church= filter regardless of context var state
        units_qs = ChurchUnit.raw_objects.filter(
            church=self.church,
            is_active=True,
        )

        if resolver and resolver.can("events.manage_all"):
            # Admin sees all units
            self.fields["unit"].queryset = units_qs
            return

        # Non-admin: only units the user is a member of
        if self.user:
            units_qs = units_qs.filter(
                memberships__workforce_member__member__user=self.user
            ).distinct()

        self.fields["unit"].queryset = units_qs