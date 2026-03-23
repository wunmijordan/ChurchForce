from django import forms
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError

from accounts.models import CustomUser, ChurchMember
from units.models import ChurchUnit, UnitMembership
from workforce.models import WorkforceMember, WorkforceRole, WorkforceMembershipRole
from permissions.models import UnitRole, MembershipRole

from accounts.member_service import generate_temp_password


# ─────────────────────────────────────────────────────────────────────────────
# Shared __init__ mixin
# ─────────────────────────────────────────────────────────────────────────────

class ChurchFormMixin:
    """
    Shared initialisation for forms that need church-scoped querysets.

    Subclasses must call super().__init__(*args, **kwargs) after popping
    church and current_user from kwargs, which this mixin handles.

    Provides:
        self.church         — Church instance
        self.current_user   — the logged-in admin performing the action
    """

    def _init_church_fields(self):
        """
        Scope workforce_roles, unit_roles, and units querysets to self.church.
        Uses raw_objects so this works correctly outside of a request context
        (management commands, tests) as well as during normal requests.
        """
        if not self.church:
            return

        self.fields["workforce_roles"].queryset = (
            WorkforceRole.raw_objects
            .filter(church=self.church, is_active=True)
            .order_by("order", "name")
        )

        self.fields["unit_roles"].queryset = (
            UnitRole.raw_objects
            .filter(church=self.church, is_active=True)
            .select_related("unit")
            .order_by("unit__name", "order", "name")
        )

        self.fields["units"].queryset = (
            ChurchUnit.raw_objects
            .filter(church=self.church, is_active=True)
            .order_by("name")
        )

    def _normalise_select_fields(self, field_names):
        """Ensure select fields have a blank first choice."""
        for field_name in field_names:
            if field_name not in self.fields:
                continue
            choices = list(self.fields[field_name].choices)
            if choices and choices[0][0] != "":
                self.fields[field_name].choices = [("", "")] + choices
            elif choices:
                choices[0] = ("", "")
                self.fields[field_name].choices = choices


# ─────────────────────────────────────────────────────────────────────────────
# Creation form
# ─────────────────────────────────────────────────────────────────────────────

class CustomUserCreationForm(ChurchFormMixin, forms.ModelForm):
    """
    Admin-driven member creation form.

    Password behaviour:
        - If admin fills in password → used as-is (they share it verbally).
        - If admin leaves password blank → system generates a temp password.
        - Either way, the generated/entered password is stored on
          self.generated_password after save() so the view can display it once.
    """

    username = forms.CharField(
        label="Username",
        required=False,
        help_text="Leave blank to auto-generate from full name.",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Auto-generated if blank"}),
    )
    password = forms.CharField(
        label="Password",
        required=False,
        help_text="Leave blank to auto-generate a temporary password.",
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Leave blank to auto-generate"}),
    )
    confirm_password = forms.CharField(
        label="Confirm password",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Confirm password"}),
    )
    workforce_roles = forms.ModelMultipleChoiceField(
        queryset=WorkforceRole.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
        label="Workforce roles",
    )
    unit_roles = forms.ModelMultipleChoiceField(
        queryset=UnitRole.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
        label="Unit roles",
    )
    units = forms.ModelMultipleChoiceField(
        queryset=ChurchUnit.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
        label="Unit memberships",
    )
    is_active = forms.BooleanField(required=False, label="Active")
    is_superuser = forms.BooleanField(required=False, label="Superuser")

    class Meta:
        model = CustomUser
        fields = [
            "image", "title", "full_name", "email", "username",
            "password", "confirm_password", "phone_number", "date_of_birth",
            "address", "marital_status",
            "workforce_roles", "units", "unit_roles",
            "is_active", "is_superuser",
        ]
        widgets = {
            "image":          forms.ClearableFileInput(attrs={"class": "form-control"}),
            "title":          forms.Select(attrs={"class": "form-select"}),
            "full_name":      forms.TextInput(attrs={"class": "form-control", "placeholder": "John Doe"}),
            "email":          forms.EmailInput(attrs={"class": "form-control", "placeholder": "john@example.com"}),
            "phone_number":   forms.TextInput(attrs={"class": "form-control", "placeholder": "08012345678"}),
            "date_of_birth":  forms.DateInput(attrs={
                "type": "text", "class": "form-control",
                "placeholder": "January 01", "autocomplete": "off",
            }),
            "marital_status": forms.Select(attrs={"class": "form-select"}),
            "address":        forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop("current_user", None)
        self.church = kwargs.pop("church", None)
        super().__init__(*args, **kwargs)

        self._init_church_fields()
        self._normalise_select_fields(["title", "marital_status"])

        if self.current_user and not self.current_user.is_superuser:
            self.fields.pop("is_superuser", None)

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("confirm_password")

        # Only validate if admin explicitly entered a password
        if p1 or p2:
            if p1 != p2:
                self.add_error("confirm_password", "Passwords do not match.")

        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)

        # ── Resolve password ──────────────────────────────────────────
        password = self.cleaned_data.get("password") or generate_temp_password()
        self.generated_password = password      # expose to view for one-time display
        user.password = make_password(password)

        # ── Resolve username ──────────────────────────────────────────
        if not user.username:
            from accounts.member_service import generate_username
            user.username = generate_username(user.full_name or "member", self.church.slug)
        self.generated_username = user.username

        if not commit:
            return user

        user.save()

        if not self.church:
            return user

        # ── ChurchMember ──────────────────────────────────────────────
        church_member, _ = ChurchMember.raw_objects.get_or_create(
            church=self.church,
            user=user,
            defaults={"is_active": True},
        )

        # ── WorkforceMember ───────────────────────────────────────────
        # Requires a stage — fall back to the first available one
        from workforce.models import WorkforceStage
        stage = WorkforceStage.raw_objects.filter(
            church=self.church,
            is_active=True,
        ).order_by("order").first()

        if stage:
            wf, _ = WorkforceMember.raw_objects.get_or_create(
                church=self.church,
                member=church_member,
                defaults={"stage": stage, "is_active": True},
            )
            # Ensure stage is set even if record already existed without one
            if not wf.stage_id:
                wf.stage = stage
                wf.save(update_fields=["stage"])

            # ── Workforce roles ───────────────────────────────────────
            wf.roles.all().delete()
            for role in self.cleaned_data.get("workforce_roles", []):
                WorkforceMembershipRole.raw_objects.create(
                    church=self.church,
                    workforce_member=wf,
                    role=role,
                )

            # ── Unit memberships ──────────────────────────────────────
            wf.unit_memberships.all().delete()
            units = self.cleaned_data.get("units", [])
            selected_unit_roles = self.cleaned_data.get("unit_roles", [])

            for unit in units:
                membership = UnitMembership.raw_objects.create(
                    church=self.church,
                    workforce_member=wf,
                    unit=unit,
                )
                # Attach only unit roles that belong to this specific unit
                for role in selected_unit_roles:
                    if role.unit_id == unit.id:
                        MembershipRole.raw_objects.create(
                            church=self.church,
                            membership=membership,
                            role=role,
                        )

        return user


# ─────────────────────────────────────────────────────────────────────────────
# Edit form
# ─────────────────────────────────────────────────────────────────────────────

class CustomUserChangeForm(ChurchFormMixin, forms.ModelForm):
    """
    Admin edit form for an existing member.

    Structural fields (roles, units, stage) are admin-controlled.
    Personal profile fields (photo, phone, DOB, address) can also be
    edited here by admin; members edit their own via the profile view.

    Password change is optional — leave blank to keep existing password.
    """

    username = forms.CharField(
        label="Username",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    password = forms.CharField(
        label="New password",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "Leave blank to keep current password"}),
    )
    confirm_password = forms.CharField(
        label="Confirm new password",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    workforce_roles = forms.ModelMultipleChoiceField(
        queryset=WorkforceRole.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
        label="Workforce roles",
    )
    unit_roles = forms.ModelMultipleChoiceField(
        queryset=UnitRole.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
        label="Unit roles",
    )
    units = forms.ModelMultipleChoiceField(
        queryset=ChurchUnit.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
        label="Unit memberships",
    )
    is_active = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label="Active",
    )
    is_superuser = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label="Superuser",
    )

    class Meta:
        model = CustomUser
        fields = [
            "image", "title", "full_name", "email", "username",
            "password", "confirm_password", "phone_number", "date_of_birth",
            "address", "marital_status",
            "workforce_roles", "units", "unit_roles",
            "is_active", "is_superuser",
        ]
        widgets = {
            "image":          forms.ClearableFileInput(attrs={"class": "form-control"}),
            "title":          forms.Select(attrs={"class": "form-select"}),
            "full_name":      forms.TextInput(attrs={"class": "form-control"}),
            "email":          forms.EmailInput(attrs={"class": "form-control"}),
            "phone_number":   forms.TextInput(attrs={"class": "form-control"}),
            "date_of_birth":  forms.DateInput(attrs={
                "type": "text", "class": "form-control", "autocomplete": "off",
            }),
            "marital_status": forms.Select(attrs={"class": "form-select"}),
            "address":        forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop("current_user", None)
        self.edit_mode = kwargs.pop("edit_mode", False)
        self.church = kwargs.pop("church", None)
        super().__init__(*args, **kwargs)

        self._init_church_fields()
        self._normalise_select_fields(["title", "marital_status"])

        # Pre-populate workforce roles and units from existing assignments
        if self.instance and self.instance.pk and self.church:
            self._prepopulate_assignments()

        if self.edit_mode and self.current_user and not self.current_user.is_superuser:
            self.fields.pop("is_superuser", None)

    def _prepopulate_assignments(self):
        """
        Pre-fill workforce_roles and units fields from existing DB records
        so the form shows the member's current assignments on load.
        """
        member = ChurchMember.raw_objects.filter(
            church=self.church,
            user=self.instance,
        ).first()

        if not member:
            return

        wf = WorkforceMember.raw_objects.filter(
            church=self.church,
            member=member,
        ).first()

        if not wf:
            return

        self.fields["workforce_roles"].initial = (
            WorkforceMembershipRole.raw_objects
            .filter(church=self.church, workforce_member=wf)
            .values_list("role_id", flat=True)
        )

        self.fields["units"].initial = (
            UnitMembership.raw_objects
            .filter(church=self.church, workforce_member=wf)
            .values_list("unit_id", flat=True)
        )

        self.fields["unit_roles"].initial = (
            MembershipRole.raw_objects
            .filter(church=self.church, membership__workforce_member=wf)
            .values_list("role_id", flat=True)
        )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("confirm_password")
        if p1 or p2:
            if p1 != p2:
                self.add_error("confirm_password", "Passwords do not match.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)

        # Keep existing password if no new one provided
        password = self.cleaned_data.get("password")
        if password:
            user.password = make_password(password)
        else:
            user.password = CustomUser.objects.get(pk=self.instance.pk).password

        if not commit:
            return user

        user.save()

        if not self.church:
            return user

        # ── Update workforce and unit assignments ─────────────────────
        member = ChurchMember.raw_objects.filter(
            church=self.church,
            user=user,
        ).first()

        if not member:
            return user

        wf = WorkforceMember.raw_objects.filter(
            church=self.church,
            member=member,
        ).first()

        if not wf:
            return user

        # Workforce roles — replace entirely
        wf.roles.all().delete()
        for role in self.cleaned_data.get("workforce_roles", []):
            WorkforceMembershipRole.raw_objects.create(
                church=self.church,
                workforce_member=wf,
                role=role,
            )

        # Unit memberships — replace entirely
        wf.unit_memberships.all().delete()
        units = self.cleaned_data.get("units", [])
        selected_unit_roles = self.cleaned_data.get("unit_roles", [])

        for unit in units:
            membership = UnitMembership.raw_objects.create(
                church=self.church,
                workforce_member=wf,
                unit=unit,
            )
            for role in selected_unit_roles:
                if role.unit_id == unit.id:
                    MembershipRole.raw_objects.create(
                        church=self.church,
                        membership=membership,
                        role=role,
                    )

        return user


class ProfileEditForm(forms.ModelForm):
    """
    Allows a member to edit a safe subset of their own profile.
    Excluded: username, email, is_staff, is_superuser, groups, permissions,
              church-level fields — anything that would affect access control.
    """
    class Meta:
        model  = CustomUser
        fields = [
            "full_name", "title", "phone_number",
            "date_of_birth", "marital_status", "address", "image",
        ]
        widgets = {
            "full_name":     forms.TextInput(attrs={"class": "form-control"}),
            "title":         forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Mr, Mrs, Dr"}),
            "phone_number":  forms.TextInput(attrs={"class": "form-control"}),
            "date_of_birth": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "marital_status":forms.Select(attrs={"class": "form-select"}),
            "address":       forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "image":         forms.ClearableFileInput(attrs={"class": "form-control"}),
        }