from django import forms
from services.models import Event
from units.models import ChurchUnit
from permissions.services.resolver import PermissionResolver
from lms.models import LMSCourse, LMSModule


class EventForm(forms.ModelForm):
    """
    Form for creating and editing events.

    When event_type=Training:
      - lms_course field appears (FK to LMSCourse).
      - modules_covered field appears as a multiple-checkbox select,
        populated dynamically via JS from the selected course's modules.
        If no modules selected, the event covers the entire course
        (all modules unlocked on attendance).

    The signal sync_lms_session_on_event_save (lms/signals.py) auto-creates /
    updates an LMSSession from every Training event that has an lms_course set,
    and keeps LMSSession.modules_covered in sync with Event.modules_covered.
    """

    unit = forms.ModelChoiceField(
        queryset=ChurchUnit.objects.none(),
        required=False,
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    # modules_covered is a dynamic M2M; the queryset is set in __init__
    # based on the selected lms_course.
    modules_covered = forms.ModelMultipleChoiceField(
        queryset=LMSModule.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "module-checkbox"}),
        label="Modules covered",
        help_text=(
            "Select which modules this class covers. "
            "Leave all unchecked to unlock the entire course on attendance."
        ),
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
            "attendance_closes_at",
            "unit",
            "is_active",
            "count_towards_commitment",
            "is_recurring_weekly",
            "registrable",
            "registration_link",
            "lms_course",
            "modules_covered",
            "prepares_for_service",
            "trainee_access",
        ]
        widgets = {
            "event_image": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "day_of_week": forms.Select(attrs={"class": "form-select"}),
            "attendance_mode": forms.Select(attrs={"class": "form-select"}),
            "postponed": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "end_date": forms.DateInput(
                attrs={"type": "date", "class": "form-control"}
            ),
            "time": forms.TimeInput(attrs={"type": "time", "class": "form-control"}),
            "attendance_closes_at": forms.TimeInput(
                attrs={"type": "time", "class": "form-control"}
            ),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "event_type": forms.Select(attrs={"class": "form-select"}),
            "count_towards_commitment": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "duration_days": forms.NumberInput(attrs={"class": "form-control"}),
            "is_recurring_weekly": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "registrable": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "registration_link": forms.URLInput(attrs={"class": "form-control"}),
            "trainee_access": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        help_texts = {
            "duration_days": "Number of days this event lasts.",
            "trainee_access": "Tick to make this event visible to guest-pipeline trainees.",
        }
        labels = {
            "event_image": "Event banner / poster",
            "is_recurring_weekly": "Recurring weekly",
            "is_active": "Active event",
            "registrable": "Open for registration",
            "registration_link": "Registration link",
            "attendance_mode": "Attendance mode",
            "day_of_week": "Day of the week",
            "event_type": "Event type",
            "count_towards_commitment": "Counts toward Guest's commitment progression",
            "end_date": "End date",
            "duration_days": "Duration (days)",
            "postponed": "Postpone this event",
            "attendance_closes_at": "Attendance closes at",
            "lms_course": "Training course",
            "trainee_access": "Trainee access",
        }

    def __init__(self, *args, **kwargs):
        self.church = kwargs.pop("church", None)
        self.user = kwargs.pop("user", None)
        self.permissions = kwargs.pop("permissions", None)
        super().__init__(*args, **kwargs)

        self.fields["unit"].empty_label = (
            f"{self.church.name}'s Workforce" if self.church else "ChurchForce"
        )

        if not self.church:
            self.fields["unit"].queryset = ChurchUnit.raw_objects.none()
            self.fields["lms_course"].queryset = LMSCourse.raw_objects.none()
            self.fields["modules_covered"].queryset = LMSModule.raw_objects.none()
            return

        resolver = self.permissions or PermissionResolver(self.user, self.church)

        units_qs = ChurchUnit.raw_objects.filter(
            church=self.church,
            is_active=True,
        )
        if resolver and resolver.can("events.manage_all"):
            self.fields["unit"].queryset = units_qs
        else:
            self.fields["unit"].queryset = units_qs.filter(
                memberships__workforce_member__member__user=self.user
            ).distinct()

        self.fields["lms_course"] = forms.ModelChoiceField(
            queryset=LMSCourse.raw_objects.filter(church=self.church),
            required=False,
            empty_label="— Select a training course —",
            widget=forms.Select(attrs={"class": "form-select", "id": "id_lms_course"}),
            label="Training course",
        )

        # Determine which course to scope modules to:
        # On a POST with a course selected, or on an edit with an instance,
        # pre-populate the modules queryset so validation and initial values work.
        course_obj = None
        if self.instance and self.instance.pk and self.instance.lms_course_id:
            course_obj = self.instance.lms_course
        # Also check POST data (for validation on POST)
        if self.data:
            course_id = self.data.get("lms_course", "")
            if course_id and str(course_id).isdigit():
                course_obj = LMSCourse.raw_objects.filter(
                    church=self.church, id=int(course_id)
                ).first()

        if course_obj:
            self.fields["modules_covered"].queryset = LMSModule.raw_objects.filter(
                church=self.church, course=course_obj, is_active=True
            ).order_by("order")
        else:
            self.fields["modules_covered"].queryset = LMSModule.raw_objects.none()

        # "Prepares for" — shown when creating a Rehearsal event
        self.fields["prepares_for_service"] = forms.ModelChoiceField(
            queryset=Event.raw_objects.filter(
                church=self.church,
            ).order_by("-date"),
            required=False,
            empty_label="— Preparing for which service? (optional) —",
            widget=forms.Select(
                attrs={
                    "class": "form-select",
                    "id": "id_prepares_for_service",
                }
            ),
            label="Prepares for Service",
            help_text=(
                "Link this rehearsal to the service it is preparing for. "
                "The music team will see this connection on their rehearsal board."
            ),
        )
