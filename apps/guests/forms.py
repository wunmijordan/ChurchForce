import datetime
from django import forms
from django.core.exceptions import ValidationError
from django.utils.timezone import localdate
from services.models import Event
from permissions.services.resolver import PermissionResolver
from units.models import UnitMembership, ChurchUnit
from tenants.time_utils import format_church_datetime
from .models import (
    GuestEntry,
    FollowUpReport,
    GuestCallEvidence,
    GuestStatus,
    VisitPurpose,
    VisitChannel,
    ChurchService,
)


def _get_guest_unit_ids(church, resolver, permission):
    if not church or not resolver:
        return set()

    from .views import _guest_enabled_unit_ids

    return _guest_enabled_unit_ids(church) & set(resolver.unit_ids_for(permission))


class GuestEntryForm(forms.ModelForm):
    assigned_to = forms.ModelChoiceField(
        queryset=UnitMembership.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select bg-grey text-white border-0"}),
        label="Assigned Officer",
        help_text="Assign this Guest to a Unit Member for follow-up.",
    )

    referred_by_member = forms.ModelChoiceField(
        queryset=UnitMembership.raw_objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Referred by (member)",
        help_text="Select if the person who invited this guest is already a member.",
    )
    referred_by_name = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Enter name if not a member",
            }
        ),
        label="Referred by (name)",
        help_text="Write a name if the referrer is not yet in the system.",
    )

    date_of_birth = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "January 01 (Ignore Year)",
            }
        ),
        help_text="Date of Birth.",
    )

    date_of_visit = forms.DateField(
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={
                "type": "date",
                "class": "form-control",
                "autocomplete": "off",
            },
        ),
        help_text="Date of Visit.",
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        required=False,
    )

    class Meta:
        model = GuestEntry
        exclude = [
            "custom_id",
            "church",
            "birthday_month",
            "birthday_day",
            "status",
            "is_active",
            "is_deleted",
            "deleted_at",
            "assigned_at",
            "converted_to",
            "first_thankyou_sent",
            "second_thankyou_sent",
            "second_visit_date",
            "first_contact_reminder_sent",
            "in_contact_overdue_notified",
        ]
        widgets = {
            "picture": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "title": forms.Select(attrs={"class": "form-select"}),
            "full_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "John Doe"}
            ),
            "email": forms.EmailInput(
                attrs={"class": "form-control", "placeholder": "johndoe@example.com"}
            ),
            "phone_number": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "08123xxxx89"}
            ),
            "date_of_birth": forms.TextInput(
                attrs={
                    "type": "text",
                    "class": "form-control",
                    "placeholder": "January 01 (Ignore Year)",
                    "autocomplete": "off",
                }
            ),
            "age_range": forms.Select(attrs={"class": "form-select"}),
            "marital_status": forms.Select(attrs={"class": "form-select"}),
            "gender": forms.Select(
                attrs={"class": "form-select", "required": "required"}
            ),
            "occupation": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Manager"}
            ),
            "home_address": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Home Address",
                }
            ),
            "date_of_visit": forms.DateInput(
                format="%Y-%m-%d", attrs={"type": "date", "class": "form-control"}
            ),
            "purpose_of_visit": forms.Select(attrs={"class": "form-select"}),
            "channel_of_visit": forms.Select(attrs={"class": "form-select"}),
            "service_attended": forms.Select(attrs={"class": "form-select"}),
            "assigned_to": forms.Select(attrs={"class": "form-select"}),
        }
        labels = {
            "picture": "Guest Photo",
            "title": "Title",
            "full_name": "Full Name",
            "gender": "Gender",
            "phone_number": "Phone Number",
            "email": "Email Address",
            "date_of_birth": "Date of Birth",
            "age_range": "Age Range",
            "marital_status": "Marital Status",
            "occupation": "Occupation",
            "home_address": "Home Address",
            "date_of_visit": "First Visit Date",
            "purpose_of_visit": "Purpose of Visit",
            "channel_of_visit": "How They Heard About Us",
            "service_attended": "Service Attended",
            "assigned_to": "Assigned Follow-Up Worker",
        }
        help_texts = {
            "title": "Title.",
            "picture": "Guest's Picture.",
            "gender": "Gender.",
            "full_name": "Full Name.",
            "phone_number": "Phone Number.",
            "email": "Email Address.",
            "date_of_birth": "Date of Birth.",
            "age_range": "Select the guest's age range.",
            "marital_status": "Marital Status.",
            "home_address": "Home Address.",
            "occupation": "Occupation.",
            "date_of_visit": "Date of Visit.",
            "purpose_of_visit": "Purpose of Visit.",
            "channel_of_visit": "How did the guest find out about us?",
            "service_attended": "What service did the guest attend?",
            "assigned_to": "Assign this guest to a unit member for follow-up.",
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.church = kwargs.pop("church", None)
        self.permissions = kwargs.pop("permissions", None)
        super().__init__(*args, **kwargs)

        resolver = self.permissions
        if not resolver and self.user and self.church:
            resolver = PermissionResolver(self.user, self.church)

        if self.church:
            # Referred-by should show church-wide members (not guest-module scoped).
            ref_qs = (
                UnitMembership.raw_objects.filter(
                    church=self.church,
                    is_active=True,
                    workforce_member__is_active=True,
                    workforce_member__member__is_active=True,
                )
                .select_related("unit", "workforce_member__member__user")
                .order_by("workforce_member_id", "unit__unit_type", "unit__name", "id")
            )
            first_membership_ids = []
            seen_workforce_members = set()
            for membership in ref_qs:
                if membership.workforce_member_id in seen_workforce_members:
                    continue
                seen_workforce_members.add(membership.workforce_member_id)
                first_membership_ids.append(membership.id)

            referred_qs = (
                UnitMembership.raw_objects.filter(id__in=first_membership_ids)
                .select_related("unit", "workforce_member__member__user")
                .order_by("workforce_member__member__user__full_name")
            )
            self.fields["referred_by_member"].queryset = referred_qs
            self.fields["referred_by_member"].label_from_instance = lambda obj: (
                f"{(obj.workforce_member.member.user.title or '').strip()} "
                f"{(obj.workforce_member.member.user.full_name or obj.workforce_member.member.user.username).strip()}"
                + (f" ({obj.unit.name})" if obj.unit else "")
            ).strip()
            self.fields["purpose_of_visit"].queryset = VisitPurpose.raw_objects.filter(
                church=self.church, is_active=True
            ).order_by("order")
            self.fields["channel_of_visit"].queryset = VisitChannel.raw_objects.filter(
                church=self.church, is_active=True
            ).order_by("order")
            self.fields["service_attended"].queryset = ChurchService.raw_objects.filter(
                church=self.church, is_active=True
            ).order_by("order")

        allow_assign = bool(
            resolver
            and (
                resolver.can("guests.assign_all") or resolver.can("guests.assign_unit")
            )
        )
        if allow_assign and self.church:
            unit_ids = _get_guest_unit_ids(self.church, resolver, "guests.assign_unit")

            if (
                resolver
                and resolver.can("guests.assign_unit")
                and not resolver.can("guests.assign_all")
            ):
                unit_ids = unit_ids & set(resolver.unit_ids_for("guests.assign_unit"))

            qs = UnitMembership.raw_objects.filter(
                church=self.church,
                unit_id__in=unit_ids,
                is_active=True,
                workforce_member__is_active=True,
                workforce_member__member__is_active=True,
                workforce_member__member__user__is_active=True,
            ).select_related("unit", "workforce_member__member__user")

            self.fields["assigned_to"].queryset = qs.order_by(
                "workforce_member__member__user__full_name"
            )
            self.fields["assigned_to"].required = False

            self.fields["assigned_to"].label_from_instance = lambda obj: (
                f"{(obj.workforce_member.member.user.title)} {(obj.workforce_member.member.user.full_name or obj.workforce_member.member.user.username)}"
                + (f" ({obj.unit.name})" if obj.unit else "")
            )
        else:
            self.fields.pop("assigned_to", None)

        select_fields = [
            "title",
            "marital_status",
            "gender",
            "purpose_of_visit",
            "channel_of_visit",
            "service_attended",
            "status",
            "age_range",
            "assigned_to",
        ]

        for field_name in select_fields:
            if field_name in self.fields:
                choices = list(self.fields[field_name].choices)
                if choices and choices[0][0] == "":
                    choices[0] = ("", "")
                else:
                    choices = [("", "")] + choices
                self.fields[field_name].choices = choices

        if self.instance and self.instance.date_of_visit:
            self.fields["date_of_visit"].initial = self.instance.date_of_visit.strftime(
                "%Y-%m-%d"
            )

    def clean_phone_number(self):
        phone = self.cleaned_data.get("phone_number")
        if phone and not phone.isdigit():
            raise ValidationError("Phone number must contain only digits.")
        return phone

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email and "@" not in email:
            raise ValidationError("Enter a valid email address.")
        return email

    def clean_date_of_birth(self):
        dob_raw = self.cleaned_data.get("date_of_birth")
        if not dob_raw:
            return ""

        try:
            dob_parsed = datetime.datetime.strptime(dob_raw, "%B %d")
            return dob_parsed.strftime("%B %d")
        except ValueError:
            raise forms.ValidationError("Enter date in format: January 01")


class FollowUpReportForm(forms.ModelForm):
    report_date = forms.DateField(
        widget=forms.DateInput(
            format="%Y-%m-%d",
            attrs={
                "type": "date",
                "class": "form-control bg-grey text-white border-0",
            },
        ),
        input_formats=["%Y-%m-%d", "%d/%m/%Y"],
        required=False,
    )
    attended_events = forms.ModelMultipleChoiceField(
        queryset=Event.objects.none(),
        required=False,
        widget=forms.SelectMultiple(
            attrs={
                "class": "form-select",
                "size": "5",
                "data-dropdown-multiselect": "true",  # JS hook for custom dropdown UI
            }
        ),
        label="Services/Events attended this week",
        help_text="Hold Ctrl/Cmd to select multiple. Only commitment-eligible events shown.",
    )
    call_evidence = forms.ModelChoiceField(
        queryset=GuestCallEvidence.raw_objects.none(),
        required=False,
        empty_label="No linked verified call",
        widget=forms.Select(attrs={"class": "form-select bg-grey border-0 text-white"}),
        label="Verified call evidence",
        help_text="Optional: attach a verified call event as contact evidence.",
    )

    class Meta:
        model = FollowUpReport
        exclude = ["guest", "assigned_to", "created_at", "church"]
        widgets = {
            "note": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "placeholder": "Enter your follow-up notes here…",
                    "rows": 5,
                }
            ),
            # ── Contact tracking ──────────────────────────────────────
            "contact_attempted": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
            "contact_answered": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                    "id": "id_contact_answered",
                    # When ticked, triggers status auto-advance to In-Contact on save
                }
            ),
            "contact_type": forms.Select(
                attrs={"class": "form-select bg-grey border-0"}
            ),
            "contact_attachment": forms.ClearableFileInput(
                attrs={
                    "class": "form-control bg-grey border-0",
                    "accept": "image/*,.pdf",
                }
            ),
            # ── Terminal flag ─────────────────────────────────────────
            "not_planted": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                    "id": "id_not_planted",
                }
            ),
            "reviewed": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        self.guest = kwargs.pop("guest", None)
        self.church = kwargs.pop("church", None)
        super().__init__(*args, **kwargs)

        if self.instance.pk:
            if self.instance.report_date:
                self.initial["report_date"] = self.instance.report_date.strftime(
                    "%Y-%m-%d"
                )
            self.fields["report_date"].widget.attrs.update(
                {
                    "readonly": True,
                    "class": self.fields["report_date"].widget.attrs.get("class", "")
                    + " bg-secondary text-dark fw-bold",
                }
            )
        else:
            if not self.initial.get("report_date"):
                today = localdate()
                self.initial["report_date"] = today.strftime("%Y-%m-%d")

        if self.church:
            # Only show events that count toward commitment threshold.
            # This prevents unit meetings, training days, and other events
            # from accidentally inflating the commitment count.
            qs = Event.raw_objects.filter(
                church=self.church,
                postponed=False,
                count_towards_commitment=True,
                is_active=True,
            ).order_by("day_of_week", "time", "name")

            self.fields["attended_events"].queryset = qs

        if self.church and self.guest:
            self.fields["call_evidence"].queryset = (
                GuestCallEvidence.raw_objects.filter(
                    church=self.church,
                    guest=self.guest,
                    verified_contact=True,
                )
                .select_related("assignee")
                .order_by("-started_at", "-created_at")
            )
            self.fields["call_evidence"].label_from_instance = lambda obj: (
                f"{obj.provider.upper()} • {obj.get_call_status_display()} • "
                f"{obj.duration_seconds}s • "
                f"{format_church_datetime(obj.started_at or obj.created_at, self.church, separator=' ')}"
            )

        if self.instance.pk:
            self.initial["attended_events"] = self.instance.attended_events.all()

    def clean(self):
        cleaned_data = super().clean()
        report_date = cleaned_data.get("report_date")
        contact_answered = cleaned_data.get("contact_answered")
        call_evidence = cleaned_data.get("call_evidence")

        if (
            self.guest
            and FollowUpReport.raw_objects.filter(
                church=self.church,
                guest=self.guest,
                report_date=report_date,
            )
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            raise ValidationError("You already submitted a report for this date.")

        if call_evidence and (
            call_evidence.church_id != getattr(self.church, "id", None)
            or call_evidence.guest_id != getattr(self.guest, "id", None)
            or not call_evidence.verified_contact
        ):
            raise ValidationError("Selected call evidence is invalid for this guest.")

        if contact_answered and not call_evidence and self.church and self.guest:
            latest_verified = (
                GuestCallEvidence.raw_objects.filter(
                    church=self.church,
                    guest=self.guest,
                    verified_contact=True,
                )
                .order_by("-started_at", "-created_at")
                .first()
            )
            if latest_verified:
                cleaned_data["call_evidence"] = latest_verified

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.guest:
            instance.guest = self.guest
            instance.assigned_to = self.guest.assigned_to
        if self.church:
            instance.church = self.church
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class WorkforceInterestForm(forms.Form):
    """
    Workforce interest + biodata capture form.

    Sent to committed guests via a secure token link. The form:
    1. Confirms/updates biodata (pre-filled from guest profile).
    2. Captures the preferred unit the guest wants to serve in.
    3. Kicks off the pipeline: creates MembershipApplication + WorkforceTraineeProfile.

    Fields mirror CustomUserCreationForm so the data can be cleanly
    transferred to the new CustomUser created by ensure_member_identity().
    """

    # ── Identity fields ───────────────────────────────────────────────────────
    title = forms.ChoiceField(
        choices=[("", "— Select —")]
        + [
            ("Mr.", "Mr."),
            ("Mrs.", "Mrs."),
            ("Ms.", "Ms."),
            ("Dr.", "Dr."),
            ("Engr.", "Engr."),
            ("Prof.", "Prof."),
            ("Pastor", "Pastor"),
            ("Chief", "Chief"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    full_name = forms.CharField(
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Full name"}
        )
    )
    gender = forms.ChoiceField(
        choices=[("", "— Select —"), ("Male", "Male"), ("Female", "Female")],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    email = forms.EmailField(widget=forms.EmailInput(attrs={"class": "form-control"}))
    phone_number = forms.CharField(
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "e.g. 08012345678"}
        )
    )
    date_of_birth = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "January 01",
                "autocomplete": "off",
            }
        ),
        help_text="Enter as 'Month Day', e.g. January 01",
    )
    marital_status = forms.ChoiceField(
        required=False,
        choices=[("", "— Select —"), ("Single", "Single"), ("Married", "Married")],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    occupation = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Occupation"}
        ),
    )
    home_address = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 2, "placeholder": "Home address"}
        ),
    )

    # ── Unit preference ───────────────────────────────────────────────────────
    preferred_unit = forms.ModelChoiceField(
        queryset=ChurchUnit.objects.none(),
        required=False,
        empty_label="— Select a unit to serve in —",
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Choose the unit/department you would like to serve in.",
    )

    def __init__(self, *args, church=None, guest=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.church = church
        self.guest = guest

        self.fields["preferred_unit"].queryset = ChurchUnit.raw_objects.filter(
            church=church,
            is_active=True,
            unit_type="unit",
        ).order_by("name")

        # Pre-fill all fields from guest profile
        if guest:
            self.initial.update(
                {
                    "title": guest.title or "",
                    "full_name": guest.full_name or "",
                    "gender": guest.gender or "",
                    "email": guest.email or "",
                    "phone_number": guest.phone_number or "",
                    "date_of_birth": guest.date_of_birth or "",
                    "marital_status": guest.marital_status or "",
                    "occupation": getattr(guest, "occupation", "") or "",
                    "home_address": getattr(guest, "home_address", "") or "",
                }
            )

    def save(self):
        from guests.pipeline import submit_workforce_interest

        guest = self.guest
        cd = self.cleaned_data

        # ── Update all biodata on the guest record ───────────────────────────
        update_fields = ["updated_at"]
        for field in (
            "title",
            "full_name",
            "gender",
            "email",
            "phone_number",
            "date_of_birth",
            "marital_status",
            "occupation",
            "home_address",
        ):
            if cd.get(field) is not None:
                setattr(guest, field, cd[field])
                update_fields.append(field)

        guest.save(update_fields=list(set(update_fields)))

        # ── Continue pipeline ────────────────────────────────────────────────
        application = submit_workforce_interest(
            guest=guest,
            preferred_unit=cd.get("preferred_unit"),
        )
        return application


class OpenWorkforceInterestForm(forms.Form):
    """
    Self-contained workforce interest form — no GuestEntry required.
    Creates a GuestEntry + MembershipApplication on save().
    """

    first_name = forms.CharField(max_length=100, label="First Name")
    last_name = forms.CharField(max_length=100, label="Last Name")
    phone_number = forms.CharField(
        max_length=20,
        label="Phone Number",
        widget=forms.TextInput(attrs={"placeholder": "08012345678"}),
    )
    email = forms.EmailField(required=False, label="Email (optional)")
    preferred_unit = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label="Preferred Unit / Department",
        empty_label="— Select a unit —",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    reason_for_joining = forms.CharField(
        required=False,
        label="Why do you want to join the workforce?",
        widget=forms.Textarea(
            attrs={"rows": 3, "placeholder": "Optional — share your motivation"}
        ),
    )

    def __init__(self, *args, **kwargs):
        self.church = kwargs.pop("church", None)
        super().__init__(*args, **kwargs)

        if self.church:
            from units.models import ChurchUnit

            self.fields["preferred_unit"].queryset = ChurchUnit.raw_objects.filter(
                church=self.church, is_active=True
            ).order_by("name")

    def clean_phone_number(self):
        phone = self.cleaned_data.get("phone_number", "").strip()
        if not phone:
            raise forms.ValidationError("Phone number is required.")
        # Basic sanity: at least 7 digits
        digits_only = "".join(c for c in phone if c.isdigit())
        if len(digits_only) < 7:
            raise forms.ValidationError("Enter a valid phone number.")
        return phone

    def save(self, church, link=None):
        from guests.models import MembershipApplication, MembershipTrack
        from django.utils import timezone

        data = self.cleaned_data
        default_track = MembershipTrack.raw_objects.filter(
            church=church, is_default=True, is_active=True
        ).first()

        application = MembershipApplication.raw_objects.create(
            church=church,
            track=default_track,
            status=MembershipApplication.STATUS_TRAINING,
            preferred_unit=data.get("preferred_unit"),
            notes=data.get("reason_for_joining", ""),
            source_link=link,
            # Store name/contact directly on application
            applicant_name=f"{data['first_name']} {data['last_name']}",
            applicant_phone=data["phone_number"],
            applicant_email=data.get("email", ""),
            applied_at=timezone.now(),
            is_active=True,
        )
        return application
