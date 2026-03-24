import json
from django import forms
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError

from accounts.models import CustomUser, ChurchMember
from workforce.models import (
    WorkforceRole,
    WorkforceMember,
    WorkforceMembershipRole,
    WorkforceStage,
)
from units.models import (
    ChurchUnit,
    UnitMembership,
)
from permissions.models import UnitRole, MembershipRole
from .member_service import generate_temp_password


# ─────────────────────────────────────────────────────────────
# Church Form Mixin
# ─────────────────────────────────────────────────────────────

class ChurchFormMixin:
    """
    Provides church-scoped querysets and template data.
    """

    def _init_church_fields(self):
        if not self.church:
            return

        self.fields["workforce_roles"].queryset = (
            WorkforceRole.raw_objects
            .filter(church=self.church, is_active=True)
            .order_by("order", "name")
        )

        # exposed to template JS
        self.available_units = (
            ChurchUnit.raw_objects
            .filter(church=self.church, is_active=True)
            .order_by("name")
        )

        self.available_unit_roles = (
            UnitRole.raw_objects
            .filter(church=self.church, is_active=True)
            .select_related("unit")
            .order_by("unit__name", "order", "name")
        )


# ─────────────────────────────────────────────────────────────
# Creation Form
# ─────────────────────────────────────────────────────────────

class CustomUserCreationForm(ChurchFormMixin, forms.ModelForm):

    username = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        help_text="Leave blank to auto-generate.",
    )

    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    confirm_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    workforce_roles = forms.ModelMultipleChoiceField(
        queryset=WorkforceRole.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
    )

    # JSON assignment payloads
    unit_assignments = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )

    is_active = forms.BooleanField(required=False, initial=True)
    is_probation = forms.BooleanField(required=False)
    is_superuser = forms.BooleanField(required=False)

    class Meta:
        model = CustomUser
        fields = [
            "image",
            "title",
            "full_name",
            "email",
            "username",
            "password",
            "confirm_password",
            "phone_number",
            "date_of_birth",
            "address",
            "marital_status",
            "workforce_roles",
            "unit_assignments",
            "is_active",
            "is_probation",
            "is_superuser",
        ]

        widgets = {
            "title": forms.Select(attrs={"class": "form-select"}),
            "full_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "phone_number": forms.TextInput(attrs={"class": "form-control"}),
            "marital_status": forms.Select(attrs={"class": "form-select"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    # ─────────────────────────────────────────────────────────

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop("current_user", None)
        self.church = kwargs.pop("church", None)

        super().__init__(*args, **kwargs)

        self._init_church_fields()

        # hide superuser toggle
        if not (self.current_user and self.current_user.is_superuser):
            self.fields.pop("is_superuser", None)

    # ─────────────────────────────────────────────────────────

    def clean(self):
        cleaned = super().clean()

        p1 = cleaned.get("password")
        p2 = cleaned.get("confirm_password")

        if p1 or p2:
            if p1 != p2:
                self.add_error("confirm_password", "Passwords do not match.")

        # parse assignments JSON
        try:
            cleaned["unit_assignments"] = json.loads(
                cleaned.get("unit_assignments") or "[]"
            )
        except Exception:
            raise ValidationError("Invalid unit assignment data.")

        return cleaned

    # ─────────────────────────────────────────────────────────

    def save(self, commit=True):

        user = super().save(commit=False)

        # password
        password = self.cleaned_data.get("password") or generate_temp_password()
        self.generated_password = password
        user.password = make_password(password)

        # username
        if not user.username:
            from accounts.member_service import generate_username
            user.username = generate_username(
                user.full_name or "member",
                self.church.slug,
            )

        self.generated_username = user.username

        if not commit:
            return user

        user.save()

        # ───────────────── ChurchMember
        church_member, _ = ChurchMember.raw_objects.get_or_create(
            church=self.church,
            user=user,
            defaults={"is_active": True},
        )

        # ───────────────── WorkforceMember
        stage = WorkforceStage.raw_objects.filter(
            church=self.church,
            is_active=True,
        ).order_by("order").first()

        wf, _ = WorkforceMember.raw_objects.get_or_create(
            church=self.church,
            member=church_member,
            defaults={"stage": stage, "is_active": True},
        )

        if stage and not wf.stage_id:
            wf.stage = stage
            wf.save(update_fields=["stage"])

        # workforce roles
        wf.roles.all().delete()

        for role in self.cleaned_data.get("workforce_roles", []):
            WorkforceMembershipRole.raw_objects.create(
                church=self.church,
                workforce_member=wf,
                role=role,
            )

        # ───────────────── Unit Assignments
        wf.unit_memberships.all().delete()

        assignments = self.cleaned_data.get("unit_assignments", [])

        for row in assignments:
            unit_id = row.get("unit")
            role_ids = row.get("roles", [])

            if not unit_id:
                continue

            membership = UnitMembership.raw_objects.create(
                church=self.church,
                workforce_member=wf,
                unit_id=unit_id,
            )

            roles = UnitRole.raw_objects.filter(
                id__in=role_ids,
                unit_id=unit_id,
                church=self.church,
            )

            for role in roles:
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