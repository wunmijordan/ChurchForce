import json

from django import forms
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from accounts.models import CustomUser, ChurchMember
from units.models import ChurchUnit, UnitMembership
from workforce.models import WorkforceMember, WorkforceRole, WorkforceMembershipRole
from permissions.models import UnitRole, MembershipRole

from accounts.member_service import generate_temp_password
from accounts.username_utils import (
    format_username,
    normalize_username_base,
    strip_username_decoration,
    username_preview,
)

# ─────────────────────────────────────────────────────────────────────────────
# Shared __init__ mixin
# ─────────────────────────────────────────────────────────────────────────────


class ChurchFormMixin:
    """
    Shared initialisation for forms that need church-scoped querysets.

    Provides:
        self.church         — Church instance
        self.current_user   — the logged-in admin performing the action
    """

    def _init_church_fields(self):
        """
        Scope all church-dependent querysets. Called by subclass __init__.
        Units split into type='unit' and type='group' for separate dropdowns.
        """
        if not self.church:
            return

        if "workforce_roles" in self.fields:
            self.fields["workforce_roles"].queryset = WorkforceRole.raw_objects.filter(
                church=self.church, is_active=True
            ).order_by("order", "name")

        if "workforce_stage" in self.fields:
            from workforce.models import WorkforceStage

            # Probationer is a system stage that is NOT freely selectable.
            # It is set automatically (post-training) or implied by the
            # global set_probation toggle — just like Inductee is implied
            # by the trainee pipeline. Exclude it so admins cannot
            # accidentally place someone here through a plain dropdown pick.
            self.fields["workforce_stage"].queryset = (
                WorkforceStage.raw_objects.filter(church=self.church, is_active=True)
                .exclude(slug=WorkforceStage.SLUG_PROBATIONER)
                .order_by("order")
            )

        if "unit_role_choice" in self.fields:
            self.fields["unit_role_choice"].queryset = (
                UnitRole.raw_objects.filter(
                    church=self.church,
                    unit__unit_type="unit",
                    is_active=True,
                )
                .select_related("unit")
                .order_by("unit__name", "order", "name")
            )

        if "unit_choice" in self.fields:
            self.fields["unit_choice"].queryset = ChurchUnit.raw_objects.filter(
                church=self.church, is_active=True, unit_type="unit"
            ).order_by("name")

        if "group_choice" in self.fields:
            self.fields["group_choice"].queryset = ChurchUnit.raw_objects.filter(
                church=self.church, is_active=True, unit_type="group"
            ).order_by("name")

        if "group_role_choice" in self.fields:
            self.fields["group_role_choice"].queryset = (
                UnitRole.raw_objects.filter(
                    church=self.church,
                    unit__unit_type="group",
                    is_active=True,
                )
                .select_related("unit")
                .order_by("unit__name", "order", "name")
            )
        if "username" in self.fields:
            preview = username_preview(self.church)
            self.fields["username"].widget.attrs["data-username-prefix"] = preview[
                "prefix"
            ]
            self.fields["username"].widget.attrs["data-username-suffix"] = preview[
                "suffix"
            ]

    def _normalize_submitted_username(self, value):
        if not value:
            return ""
        base = strip_username_decoration(value, church=self.church)
        normalized_base = normalize_username_base(base)
        return format_username(normalized_base, church=self.church)

    def _sync_workforce_role(self, workforce_member, selected_role):
        workforce_member.roles.all().delete()
        if selected_role:
            WorkforceMembershipRole.raw_objects.get_or_create(
                church=self.church,
                workforce_member=workforce_member,
                role=selected_role,
            )

    def _sync_memberships(
        self,
        workforce_member,
        assignments,
        *,
        set_probation=False,
        unit_prob_ids=None,
        unit_disc_pairs=None,
    ):
        """
        Sync UnitMembership rows.

        set_probation   — global: mark all memberships on probation.
        unit_prob_ids   — set of unit IDs that have per-card probation toggled ON.
                          When non-empty, the global flag is ignored for that unit.
        unit_disc_pairs — dict of {unit_id: disc_unit_id} for disciplinary transfers.
                          If a unit has a disc_unit_id, demote_to_probation is called
                          for that specific unit instead of setting the flag directly.
        """
        from permissions.registry import (
            TIER_UNIT_HEAD,
            TIER_ASSISTANT,
        )

        if unit_prob_ids is None:
            unit_prob_ids = set()
        if unit_disc_pairs is None:
            unit_disc_pairs = {}

        assignments = list(assignments or [])
        selected_unit_ids = {str(row["unit"].id) for row in assignments}

        existing_memberships = {
            membership.unit_id: membership
            for membership in UnitMembership.raw_objects.filter(
                church=self.church,
                workforce_member=workforce_member,
            ).select_related("unit")
        }

        for unit_id, membership in list(existing_memberships.items()):
            if unit_id not in selected_unit_ids:
                membership.delete()
                existing_memberships.pop(unit_id, None)

        for assignment in assignments:
            unit = assignment["unit"]
            uid = str(unit.id)
            role = assignment.get("role")
            is_unit_head = bool(role and role.tier == TIER_UNIT_HEAD)
            is_assistant = bool(role and role.tier == TIER_ASSISTANT)

            # Per-iteration init — these must be fresh for every unit.
            protected_tiers = []
            protected_role_ids = set()

            # Per-card probation: if unit is in unit_prob_ids → probation ON.
            # Otherwise fall back to global set_probation.
            per_card_prob = uid in unit_prob_ids
            effective_probation = per_card_prob or set_probation

            # Disciplinary unit transfer — handled after membership creation.
            disc_unit_id = unit_disc_pairs.get(uid)
            disc_unit = None
            if per_card_prob and disc_unit_id and disc_unit_id.isdigit():
                disc_unit = ChurchUnit.raw_objects.filter(
                    church=self.church,
                    is_active=True,
                    id=int(disc_unit_id),
                ).first()

            membership, created = UnitMembership.raw_objects.get_or_create(
                church=self.church,
                workforce_member=workforce_member,
                unit=unit,
                defaults={
                    "is_active": True,
                    "is_probation": effective_probation,
                    "is_unit_head": is_unit_head,
                    "is_assistant": is_assistant,
                },
            )

            update_fields = []
            if membership.is_active is not True:
                membership.is_active = True
                update_fields.append("is_active")
            if membership.is_probation != effective_probation:
                membership.is_probation = effective_probation
                update_fields.append("is_probation")
            if membership.is_unit_head != is_unit_head:
                membership.is_unit_head = is_unit_head
                update_fields.append("is_unit_head")
            if getattr(membership, "is_assistant", False) != is_assistant:
                membership.is_assistant = is_assistant
                update_fields.append("is_assistant")
            if update_fields:
                membership.save(update_fields=update_fields)

            if per_card_prob and disc_unit and disc_unit.id != unit.id:
                membership.is_probation = True
                membership.is_active = False
                membership.save(update_fields=["is_active", "is_probation"])

                UnitMembership.raw_objects.update_or_create(
                    church=self.church,
                    workforce_member=workforce_member,
                    unit=disc_unit,
                    defaults={"is_probation": True, "is_active": True},
                )
            if is_unit_head:
                protected_tiers.append(TIER_UNIT_HEAD)
            if is_assistant:
                protected_tiers.append(TIER_ASSISTANT)
            protected_role_ids.update(
                UnitRole.raw_objects.filter(
                    church=self.church,
                    unit=unit,
                    is_active=True,
                    tier__in=protected_tiers,
                ).values_list("id", flat=True)
            )

            MembershipRole.raw_objects.filter(
                church=self.church,
                membership=membership,
            ).exclude(role_id__in=protected_role_ids).delete()

            if role:
                MembershipRole.raw_objects.get_or_create(
                    church=self.church,
                    membership=membership,
                    role=role,
                )

    def _parse_assignment_payload(self, payload, *, unit_type):
        if not payload:
            return []
        try:
            rows = json.loads(payload)
        except (TypeError, ValueError):
            raise ValidationError("Assignment data could not be read.")

        if not isinstance(rows, list):
            raise ValidationError("Assignment data is invalid.")

        normalized = []
        seen_unit_ids = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValidationError("Assignment data is invalid.")
            unit_id = row.get("unit")
            role_id = row.get("role")
            if not unit_id:
                continue
            try:
                unit_id = int(unit_id)
            except (TypeError, ValueError):
                raise ValidationError("Assignment data is invalid.")
            if unit_id in seen_unit_ids:
                raise ValidationError(
                    "Please do not assign the same unit or group twice."
                )

            unit = ChurchUnit.raw_objects.filter(
                church=self.church,
                is_active=True,
                unit_type=unit_type,
                id=unit_id,
            ).first()
            if not unit:
                raise ValidationError(
                    "One of the selected units or groups no longer exists."
                )

            role = None
            if role_id not in (None, "", 0, "0"):
                try:
                    role_id = int(role_id)
                except (TypeError, ValueError):
                    raise ValidationError("Assignment role data is invalid.")
                role = UnitRole.raw_objects.filter(
                    church=self.church,
                    is_active=True,
                    unit=unit,
                    id=role_id,
                ).first()
                if not role:
                    raise ValidationError(
                        f"The selected role for {unit.name} is invalid."
                    )

            normalized.append({"unit": unit, "role": role})
            seen_unit_ids.add(unit_id)

        return normalized

    def _build_assignment_initial(self, memberships):
        from permissions.registry import (
            TIER_OVERSEER,
            TIER_UNIT_HEAD,
            TIER_ASSISTANT,
            TIER_CUSTOM,
        )

        payload = []
        for membership in memberships:
            explicit_role = (
                MembershipRole.raw_objects.filter(
                    church=self.church,
                    membership=membership,
                    role__tier__in=[TIER_OVERSEER, TIER_UNIT_HEAD, TIER_ASSISTANT],
                )
                .select_related("role")
                .first()
            )
            if not explicit_role:
                explicit_role = (
                    MembershipRole.raw_objects.filter(
                        church=self.church,
                        membership=membership,
                    )
                    .exclude(role__tier=TIER_CUSTOM)
                    .select_related("role")
                    .first()
                )
            if not explicit_role:
                explicit_role = (
                    MembershipRole.raw_objects.filter(
                        church=self.church,
                        membership=membership,
                    )
                    .select_related("role")
                    .first()
                )
            payload.append(
                {
                    "unit": membership.unit_id,
                    "unit_name": membership.unit.name,
                    "role": explicit_role.role_id if explicit_role else "",
                    "role_name": explicit_role.role.name if explicit_role else "",
                }
            )
        return json.dumps(payload)

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

    Password: admin fills in → used as-is. Blank → system generates temp password.
    Stage: select dropdown — all active stages selectable (Inductee no longer a stage).
    Workforce Role: single select from church's configured roles.
    Unit / Group: separate selects per type, each with a role dropdown (input-group style).
    """

    username = forms.CharField(
        label="Username",
        required=False,
        help_text="Leave blank to auto-generate from full name.",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Auto-generated if blank"}
        ),
    )
    password = forms.CharField(
        label="Password",
        required=False,
        help_text="Leave blank to auto-generate a temporary password.",
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Leave blank to auto-generate",
            }
        ),
    )
    confirm_password = forms.CharField(
        label="Confirm password",
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Confirm password"}
        ),
    )

    # Workforce stage — all active stages selectable
    workforce_stage = forms.ModelChoiceField(
        queryset=None,  # set in _init_church_fields
        required=False,
        empty_label="Select Stage",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_workforce_stage"}),
        label="Workforce Stage",
        help_text="Select the member's current workforce stage.",
    )

    # Workforce-wide role (single select)
    workforce_roles = forms.ModelChoiceField(
        queryset=WorkforceRole.objects.none(),
        required=False,
        empty_label="Select Role",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_workforce_roles"}),
        label="Workforce Role",
    )
    unit_choice = forms.ModelChoiceField(
        queryset=ChurchUnit.objects.none(),
        required=False,
        empty_label="Select a Unit",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_unit_choice"}),
        label="Unit",
    )
    unit_role_choice = forms.ModelChoiceField(
        queryset=UnitRole.objects.none(),
        required=False,
        empty_label="Select Role",
        widget=forms.Select(
            attrs={"class": "form-select", "id": "id_unit_role_choice"}
        ),
        label="Role in Unit",
    )
    unit_assignments = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_unit_assignments"}),
    )
    group_choice = forms.ModelChoiceField(
        queryset=ChurchUnit.objects.none(),
        required=False,
        empty_label="Select a Group",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_group_choice"}),
        label="Group",
    )
    group_role_choice = forms.ModelChoiceField(
        queryset=UnitRole.objects.none(),
        required=False,
        empty_label="Select Role",
        widget=forms.Select(
            attrs={"class": "form-select", "id": "id_group_role_choice"}
        ),
        label="Role in Group",
    )
    group_assignments = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_group_assignments"}),
    )
    # Global probation toggle — places newly-created member on probation across
    # ALL assigned unit and group memberships. Probationer stage is not
    # selectable in the dropdown; this toggle is the canonical way to register
    # a member who is already on probation at the time of creation.
    set_probation = forms.BooleanField(required=False, label="Set probation")

    is_superuser = forms.BooleanField(required=False, label="Superuser")

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
            "workforce_stage",
            "workforce_roles",
            "is_superuser",
        ]
        widgets = {
            "image": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "title": forms.Select(attrs={"class": "form-select"}),
            "full_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "John Doe"}
            ),
            "email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "john@example.com"}
            ),
            "phone_number": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "08012345678"}
            ),
            "date_of_birth": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "January 01",
                    "autocomplete": "off",
                }
            ),
            "marital_status": forms.Select(attrs={"class": "form-select"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop("current_user", None)
        self.church = kwargs.pop("church", None)
        super().__init__(*args, **kwargs)

        self._init_church_fields()
        self._normalise_select_fields(["title", "marital_status"])

        if self.current_user and not self.current_user.is_superuser:
            self.fields.pop("is_superuser", None)

        # Inductee is no longer a WorkforceStage — nothing to disable here.

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("confirm_password")
        if p1 or p2:
            if p1 != p2:
                self.add_error("confirm_password", "Passwords do not match.")

        try:
            cleaned["unit_assignments"] = self._parse_assignment_payload(
                cleaned.get("unit_assignments"),
                unit_type="unit",
            )
        except ValidationError as exc:
            self.add_error("unit_assignments", exc)

        try:
            cleaned["group_assignments"] = self._parse_assignment_payload(
                cleaned.get("group_assignments"),
                unit_type="group",
            )
        except ValidationError as exc:
            self.add_error("group_assignments", exc)

        selected_role = cleaned.get("workforce_roles")
        if selected_role and getattr(selected_role, "tier", None) in (1, 2):
            cleaned["unit_assignments"] = []
            cleaned["group_assignments"] = []
        if cleaned.get("username"):
            cleaned["username"] = self._normalize_submitted_username(
                cleaned["username"]
            )

        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)

        # Resolve password
        password = self.cleaned_data.get("password") or generate_temp_password()
        self.generated_password = password
        user.password = make_password(password)

        # Resolve username
        if not user.username:
            from accounts.member_service import generate_formatted_username

            user.username = generate_formatted_username(
                user.full_name or "member", self.church
            )
        self.generated_username = user.username

        if not commit:
            return user

        stage = self.cleaned_data.get("workforce_stage")
        is_probation = bool(self.cleaned_data.get("set_probation"))

        # When set_probation is ticked, override stage to Probationer.
        # The field is excluded from the dropdown so we resolve it here.
        if is_probation and not stage:
            from workforce.models import WorkforceStage

            stage = WorkforceStage.raw_objects.filter(
                church=self.church,
                slug=WorkforceStage.SLUG_PROBATIONER,
                is_active=True,
            ).first()

        user.is_active = bool(stage)
        user.save()

        if not self.church:
            return user

        with transaction.atomic():
            church_member, _ = ChurchMember.raw_objects.get_or_create(
                church=self.church,
                user=user,
                defaults={"is_active": bool(stage)},
            )
            if church_member.is_active != bool(stage):
                church_member.is_active = bool(stage)
                church_member.save(update_fields=["is_active"])

            if not stage:
                WorkforceMember.raw_objects.filter(
                    church=self.church,
                    member=church_member,
                ).delete()
                return user

            wf, _ = WorkforceMember.raw_objects.get_or_create(
                church=self.church,
                member=church_member,
                defaults={"stage": stage, "is_active": True},
            )

            update_fields = []
            if wf.stage_id != stage.id:
                wf.stage = stage
                update_fields.append("stage")
            if wf.is_active is not True:
                wf.is_active = True
                update_fields.append("is_active")
            if update_fields:
                wf.save(update_fields=update_fields)

            self._sync_workforce_role(wf, self.cleaned_data.get("workforce_roles"))
            self._sync_memberships(
                wf,
                list(self.cleaned_data.get("unit_assignments") or [])
                + list(self.cleaned_data.get("group_assignments") or []),
                set_probation=is_probation,
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
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Leave blank to keep current password",
            }
        ),
    )
    confirm_password = forms.CharField(
        label="Confirm new password",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )
    workforce_stage = forms.ModelChoiceField(
        queryset=None,
        required=False,
        empty_label="Inactive",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_workforce_stage"}),
        label="Workforce Stage",
        help_text="Leaving stage blank makes this member inactive.",
    )
    workforce_roles = forms.ModelChoiceField(
        queryset=WorkforceRole.objects.none(),
        required=False,
        empty_label="Select Role",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_workforce_roles"}),
        label="Workforce Role",
    )
    set_probation = forms.BooleanField(
        required=False,
        label="Set probation",
        help_text=(
            "Places this member on probation across ALL their unit and group "
            "memberships and sets their stage to Probationer."
        ),
    )
    disciplinary_probation_unit = forms.ModelChoiceField(
        queryset=ChurchUnit.objects.none(),
        required=False,
        empty_label="— All current units (global) —",
        widget=forms.Select(
            attrs={"class": "form-select", "id": "id_disciplinary_probation_unit"}
        ),
        label="Probation Unit",
        help_text=(
            "Optional. Leave blank to apply probation globally across all units. "
            "Select a specific unit to send the member there for probation "
            "(e.g. a disciplinary transfer)."
        ),
    )
    unit_choice = forms.ModelChoiceField(
        queryset=ChurchUnit.objects.none(),
        required=False,
        empty_label="Select a Unit",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_unit_choice"}),
        label="Unit",
    )
    unit_role_choice = forms.ModelChoiceField(
        queryset=UnitRole.objects.none(),
        required=False,
        empty_label="Select Role",
        widget=forms.Select(
            attrs={"class": "form-select", "id": "id_unit_role_choice"}
        ),
        label="Role in Unit",
    )
    unit_assignments = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_unit_assignments"}),
    )
    group_choice = forms.ModelChoiceField(
        queryset=ChurchUnit.objects.none(),
        required=False,
        empty_label="Select a Group",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_group_choice"}),
        label="Group",
    )
    group_role_choice = forms.ModelChoiceField(
        queryset=UnitRole.objects.none(),
        required=False,
        empty_label="Select Role",
        widget=forms.Select(
            attrs={"class": "form-select", "id": "id_group_role_choice"}
        ),
        label="Role in Group",
    )
    group_assignments = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"id": "id_group_assignments"}),
    )
    is_superuser = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label="Superuser",
    )

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
            "workforce_stage",
            "workforce_roles",
            "is_superuser",
        ]
        widgets = {
            "image": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "title": forms.Select(attrs={"class": "form-select"}),
            "full_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "phone_number": forms.TextInput(attrs={"class": "form-control"}),
            "date_of_birth": forms.TextInput(
                attrs={"class": "form-control", "autocomplete": "off"}
            ),
            "marital_status": forms.Select(attrs={"class": "form-select"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop("current_user", None)
        self.edit_mode = kwargs.pop("edit_mode", False)
        self.church = kwargs.pop("church", None)
        self.trainee_profile = kwargs.pop("trainee_profile", None)
        super().__init__(*args, **kwargs)

        self._init_church_fields()
        self._normalise_select_fields(["title", "marital_status"])
        self.fields["disciplinary_probation_unit"].queryset = (
            ChurchUnit.raw_objects.filter(church=self.church, is_active=True).order_by(
                "name"
            )
        )

        # Inductee is no longer a WorkforceStage — no exclusion needed.
        # Pre-populate workforce roles and units from existing assignments
        if self.instance and self.instance.pk and self.church:
            self._prepopulate_assignments()

        if self.edit_mode and self.current_user and not self.current_user.is_superuser:
            self.fields.pop("is_superuser", None)

    @property
    def is_active_trainee(self):
        return bool(
            self.trainee_profile and getattr(self.trainee_profile, "is_active", False)
        )

    def _prepopulate_assignments(self):
        """
        Pre-fill workforce_roles, units, unit_groups and unit_roles from
        existing DB records so the form shows current assignments on load.
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

        self.fields["workforce_stage"].initial = wf.stage_id
        self.fields["workforce_roles"].initial = (
            WorkforceMembershipRole.raw_objects.filter(
                church=self.church,
                workforce_member=wf,
            )
            .values_list("role_id", flat=True)
            .first()
        )
        active_memberships = UnitMembership.raw_objects.filter(
            church=self.church,
            workforce_member=wf,
            is_active=True,
        )
        active_membership_count = active_memberships.count()
        active_probation_count = active_memberships.filter(is_probation=True).count()
        self.fields["set_probation"].initial = (
            active_membership_count > 1
            and active_probation_count > 0
            and active_probation_count == active_membership_count
        )
        if self.trainee_profile and self.trainee_profile.reason == "probation":
            self.fields["disciplinary_probation_unit"].initial = (
                self.trainee_profile.probation_unit_id
            )

        unit_memberships = list(
            UnitMembership.raw_objects.filter(
                church=self.church,
                workforce_member=wf,
                unit__unit_type="unit",
            )
            .filter(Q(is_active=True) | Q(is_probation=True, is_active=False))
            .select_related("unit")
            .prefetch_related("roles")
        )
        if any(m.is_probation and not m.is_active for m in unit_memberships):
            unit_memberships = [
                m for m in unit_memberships
                if not (m.is_probation and m.is_active)
            ]
        self.initial["unit_assignments"] = self._build_assignment_initial(
            unit_memberships
        )

        group_memberships = list(
            UnitMembership.raw_objects.filter(
                church=self.church,
                workforce_member=wf,
                unit__unit_type="group",
            )
            .filter(Q(is_active=True) | Q(is_probation=True, is_active=False))
            .select_related("unit")
            .prefetch_related("roles")
        )
        if any(m.is_probation and not m.is_active for m in group_memberships):
            group_memberships = [
                m for m in group_memberships
                if not (m.is_probation and m.is_active)
            ]
        self.initial["group_assignments"] = self._build_assignment_initial(
            group_memberships
        )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("confirm_password")
        if p1 or p2:
            if p1 != p2:
                self.add_error("confirm_password", "Passwords do not match.")

        try:
            cleaned["unit_assignments"] = self._parse_assignment_payload(
                cleaned.get("unit_assignments"),
                unit_type="unit",
            )
        except ValidationError as exc:
            self.add_error("unit_assignments", exc)
        try:
            cleaned["group_assignments"] = self._parse_assignment_payload(
                cleaned.get("group_assignments"),
                unit_type="group",
            )
        except ValidationError as exc:
            self.add_error("group_assignments", exc)
        if not cleaned.get("set_probation"):
            cleaned["disciplinary_probation_unit"] = None

        # Parse per-card probation hidden fields from the unit/group assignment composer.
        def _parse_ids(key):
            raw = self.data.get(key, "")
            return {p.strip() for p in raw.split(",") if p.strip()}

        def _parse_disc_pairs(key):
            raw = self.data.get(key, "")
            pairs = {}
            for item in raw.split(","):
                item = item.strip()
                if ":" in item:
                    uid, did = item.split(":", 1)
                    if uid.strip() and did.strip():
                        pairs[uid.strip()] = did.strip()
            return pairs

        cleaned["unit_prob_ids"] = _parse_ids("unit_prob_ids")
        cleaned["unit_disc_pairs"] = _parse_disc_pairs("unit_disc_pairs")
        cleaned["group_prob_ids"] = _parse_ids("group_prob_ids")
        cleaned["group_disc_pairs"] = _parse_disc_pairs("group_disc_pairs")

        selected_role = cleaned.get("workforce_roles")
        if selected_role and getattr(selected_role, "tier", None) in (1, 2):
            cleaned["unit_assignments"] = []
            cleaned["group_assignments"] = []
        if cleaned.get("username"):
            cleaned["username"] = self._normalize_submitted_username(
                cleaned["username"]
            )
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

        stage = self.cleaned_data.get("workforce_stage")
        is_probation = bool(self.cleaned_data.get("set_probation"))

        # When probation is toggled, auto-resolve to Probationer stage regardless
        # of what the dropdown says (Probationer is excluded from the dropdown).
        if is_probation:
            from workforce.models import WorkforceStage as _WS

            probationer_stage = _WS.raw_objects.filter(
                church=self.church,
                slug=_WS.SLUG_PROBATIONER,
                is_active=True,
            ).first()
            if probationer_stage:
                stage = probationer_stage

        user.is_active = True if self.is_active_trainee else bool(stage)
        user.save()

        if not self.church:
            return user

        with transaction.atomic():
            member = ChurchMember.raw_objects.filter(
                church=self.church,
                user=user,
            ).first()

            if not member:
                return user

            desired_active = True if self.is_active_trainee else bool(stage)
            if member.is_active != desired_active:
                member.is_active = desired_active
                member.save(update_fields=["is_active"])

            if self.is_active_trainee:
                return user

            if not stage:
                WorkforceMember.raw_objects.filter(
                    church=self.church,
                    member=member,
                ).delete()
                return user

            wf, _ = WorkforceMember.raw_objects.get_or_create(
                church=self.church,
                member=member,
                defaults={"stage": stage, "is_active": True},
            )

            update_fields = []
            if wf.stage_id != stage.id:
                wf.stage = stage
                update_fields.append("stage")
            if wf.is_active is not True:
                wf.is_active = True
                update_fields.append("is_active")
            if update_fields:
                wf.save(update_fields=update_fields)

            self._sync_workforce_role(wf, self.cleaned_data.get("workforce_roles"))

            # Per-card probation from JS card toggles takes precedence over
            # the global set_probation flag. Merge unit + group state.
            all_prob_ids = (self.cleaned_data.get("unit_prob_ids") or set()) | (
                self.cleaned_data.get("group_prob_ids") or set()
            )
            all_disc_pairs = {
                **(self.cleaned_data.get("unit_disc_pairs") or {}),
                **(self.cleaned_data.get("group_disc_pairs") or {}),
            }
            # Global probation applies when the switch is on and no per-card
            # probation flags are overriding specific memberships.
            global_prob = bool(is_probation and not all_prob_ids)
            self._sync_memberships(
                wf,
                list(self.cleaned_data.get("unit_assignments") or [])
                + list(self.cleaned_data.get("group_assignments") or []),
                set_probation=global_prob,
                unit_prob_ids=all_prob_ids,
                unit_disc_pairs=all_disc_pairs,
            )

        return user


class ProfileEditForm(forms.ModelForm):
    """
    Allows a member to edit a safe subset of their own profile.
    Excluded: email, is_staff, is_superuser, groups, permissions,
              church-level fields — anything that would affect access control.
    """

    username = forms.CharField(
        label="Username",
        required=False,
        help_text="Change your Username.",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Auto-generated if blank"}
        ),
    )
    password = forms.CharField(
        label="Password",
        required=False,
        help_text="Change your Password.",
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Password"}
        ),
    )
    confirm_password = forms.CharField(
        label="Confirm password",
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "Confirm password"}
        ),
    )

    class Meta:
        model = CustomUser
        fields = [
            "full_name",
            "username",
            "title",
            "phone_number",
            "date_of_birth",
            "marital_status",
            "address",
            "image",
            "cover_image",
        ]
        widgets = {
            "full_name": forms.TextInput(attrs={"class": "form-control"}),
            "title": forms.Select(attrs={"class": "form-select"}),
            "phone_number": forms.TextInput(attrs={"class": "form-control"}),
            # date_of_birth is stored as a freeform string (e.g. "January 01")
            # so we use a plain TextInput — not type="date" — to avoid blank on load
            "date_of_birth": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g. January 01"}
            ),
            "marital_status": forms.Select(attrs={"class": "form-select"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "image": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "cover_image": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        self.current_user = kwargs.pop("current_user", None)
        self.church = kwargs.pop("church", None)

        super().__init__(*args, **kwargs)

        # normalize selects
        for field in ["title", "marital_status"]:
            if field in self.fields:
                choices = list(self.fields[field].choices)
                if choices and choices[0][0] != "":
                    self.fields[field].choices = [("", "")] + choices
        preview = username_preview(self.church)
        self.fields["username"].widget.attrs["data-username-prefix"] = preview["prefix"]
        self.fields["username"].widget.attrs["data-username-suffix"] = preview["suffix"]

    # ─────────────────────────────────────

    def clean(self):
        cleaned = super().clean()

        p1 = cleaned.get("password")
        p2 = cleaned.get("confirm_password")

        if p1 or p2:
            if p1 != p2:
                self.add_error("confirm_password", "Passwords do not match.")
        if cleaned.get("username"):
            base = strip_username_decoration(cleaned["username"], church=self.church)
            cleaned["username"] = format_username(
                normalize_username_base(base), church=self.church
            )

        return cleaned

    # ─────────────────────────────────────

    def save(self, commit=True):
        user = super().save(commit=False)

        password = self.cleaned_data.get("password")

        if password:
            user.password = make_password(password)

        if commit:
            user.save()

        return user
