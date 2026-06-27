from django import forms
from django.utils.text import slugify

from units.models import ChurchUnit, UnitAnnouncement, UnitTask, UnitReport


class UnitQuickCreateForm(forms.Form):
    name = forms.CharField(
        max_length=255,
        label="Unit name",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    unit_type = forms.ChoiceField(
        choices=ChurchUnit.UNIT_TYPE_CHOICES,
        label="Type",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    description = forms.CharField(
        required=False,
        label="Description",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )
    color = forms.CharField(
        required=False,
        max_length=20,
        label="Color",
        initial="blue",
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-color",
                "type": "color",
                "colorpick-eyedropper-active": "true",
            }
        ),
    )
    report_to = forms.ModelChoiceField(
        queryset=ChurchUnit.raw_objects.none(),
        required=False,
        label="Reports to",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    is_default = forms.BooleanField(
        required=False,
        label="Default unit for new members",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    guest_management = forms.BooleanField(
        required=False,
        label="Guest management",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    music_module = forms.BooleanField(
        required=False,
        label="Music module",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    media_module = forms.BooleanField(
        required=False,
        label="Media module",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    children_module = forms.BooleanField(
        required=False,
        label="Children module",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    youth_module = forms.BooleanField(
        required=False,
        label="Youth module",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    teenagers_module = forms.BooleanField(
        required=False,
        label="Teenagers module",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, church=None, settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.settings = settings
        if church is not None:
            self.fields["report_to"].queryset = ChurchUnit.raw_objects.filter(
                church=church, is_active=True
            )

        if settings is not None:
            if not settings.enable_guest_module:
                self.fields["guest_management"].disabled = True
            if not settings.enable_music_module:
                self.fields["music_module"].disabled = True
            if not settings.enable_media_module:
                self.fields["media_module"].disabled = True
            if not settings.enable_children_module:
                self.fields["children_module"].disabled = True
            if not settings.enable_youth_module:
                self.fields["youth_module"].disabled = True
            if not settings.enable_teenagers_module:
                self.fields["teenagers_module"].disabled = True

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Name is required.")
        if self.church is not None:
            slug = slugify(name)
            if ChurchUnit.raw_objects.filter(church=self.church, slug=slug).exists():
                raise forms.ValidationError("A unit with this name already exists.")
        return name

    def clean(self):
        cleaned = super().clean()
        settings = self.settings
        if not settings:
            return cleaned

        if cleaned.get("guest_management") and not settings.enable_guest_module:
            self.add_error("guest_management", "Guest module is disabled in settings.")
        if cleaned.get("music_module") and not settings.enable_music_module:
            self.add_error("music_module", "Music module is disabled in settings.")
        if cleaned.get("media_module") and not settings.enable_media_module:
            self.add_error("media_module", "Media module is disabled in settings.")
        if cleaned.get("children_module") and not settings.enable_children_module:
            self.add_error(
                "children_module", "Children module is disabled in settings."
            )
        if cleaned.get("youth_module") and not settings.enable_youth_module:
            self.add_error("youth_module", "Youth module is disabled in settings.")
        if cleaned.get("teenagers_module") and not settings.enable_teenagers_module:
            self.add_error(
                "teenagers_module", "Teenagers module is disabled in settings."
            )
        return cleaned

    def save(self):
        if self.church is None:
            raise ValueError("UnitQuickCreateForm requires church to save.")

        cleaned = self.cleaned_data
        return ChurchUnit.raw_objects.create(
            church=self.church,
            name=cleaned["name"],
            unit_type=cleaned["unit_type"],
            description=cleaned.get("description") or "",
            color=cleaned.get("color") or "blue",
            report_to=cleaned.get("report_to"),
            is_default=bool(cleaned.get("is_default")),
            guest_management=bool(cleaned.get("guest_management")),
            music_module=bool(cleaned.get("music_module")),
            media_module=bool(cleaned.get("media_module")),
            children_module=bool(cleaned.get("children_module")),
            youth_module=bool(cleaned.get("youth_module")),
            teenagers_module=bool(cleaned.get("teenagers_module")),
            is_active=True,
        )


class UnitAnnouncementForm(forms.ModelForm):
    class Meta:
        model = UnitAnnouncement
        fields = ["title", "body", "is_pinned", "is_banner"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "body": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "is_pinned": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_banner": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


from django import forms
from .models import UnitTask, UnitMembership


class UnitTaskForm(forms.ModelForm):
    """
    Task creation form.

    Status is NOT handled here anymore.
    Status belongs to TaskAssignment workflow.
    """

    assignees = forms.ModelMultipleChoiceField(
        queryset=UnitMembership.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check"}),
    )

    class Meta:
        model = UnitTask
        fields = [
            "title",
            "description",
            "task_type",
            "assigned_to",
            "assignees",
            "due_date",
        ]

        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "task_type": forms.Select(attrs={"class": "form-select"}),
            "assigned_to": forms.Select(attrs={"class": "form-select"}),
            "due_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
        }

    # --------------------------------------------------
    # INIT
    # --------------------------------------------------

    def __init__(self, *args, unit=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.unit = unit

        if unit:
            memberships = unit.memberships.filter(is_active=True)

            self.fields["assigned_to"].queryset = memberships
            self.fields["assignees"].queryset = memberships

        # neither required at field level
        self.fields["assigned_to"].required = False
        self.fields["assignees"].required = False

        # EDIT MODE SUPPORT
        if self.instance and self.instance.pk:
            if self.instance.task_type == "group":
                self.initial["assignees"] = self.instance.assignees.all()

    # --------------------------------------------------
    # VALIDATION
    # --------------------------------------------------

    def clean(self):
        cleaned = super().clean()

        task_type = cleaned.get("task_type")
        assigned_to = cleaned.get("assigned_to")
        assignees = cleaned.get("assignees")

        if task_type == "single":
            if not assigned_to:
                raise forms.ValidationError("Select a member for single assignment.")
            cleaned["assignees"] = self.fields["assignees"].queryset.none()

        elif task_type == "group":
            if not assignees or assignees.count() == 0:
                raise forms.ValidationError(
                    "Select at least one assignee for group task."
                )
            cleaned["assigned_to"] = None

        return cleaned

    # --------------------------------------------------
    # SAVE (SYNC ASSIGNMENTS ON EDIT)
    # --------------------------------------------------

    def save(self, commit=True):
        task = super().save(commit=False)

        if commit:
            task.save()
            self.save_m2m()

        return task


class UnitReportForm(forms.ModelForm):
    class Meta:
        model = UnitReport
        fields = ["category", "title", "body", "related_task", "report_to_unit", "copy_pastor"]
        widgets = {
            "category": forms.Select(attrs={"class": "form-select"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "body": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "related_task": forms.Select(attrs={"class": "form-select"}),
            "report_to_unit": forms.Select(attrs={"class": "form-select"}),
            "copy_pastor": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, unit=None, church=None, **kwargs):
        super().__init__(*args, **kwargs)
        if unit:
            self.fields["related_task"].queryset = UnitTask.raw_objects.filter(
                church=unit.church, unit=unit, is_active=True
            )
            self.fields["report_to_unit"].queryset = ChurchUnit.raw_objects.filter(
                church=unit.church, is_active=True, pk=unit.report_to_id
            )
        else:
            self.fields["related_task"].queryset = UnitTask.raw_objects.none()
            self.fields["report_to_unit"].queryset = ChurchUnit.raw_objects.none()
