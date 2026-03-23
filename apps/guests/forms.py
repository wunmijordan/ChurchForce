import datetime
from django import forms
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.utils.timezone import localdate

from permissions.services.resolver import PermissionResolver
from permissions.models import UnitRole
from units.models import UnitMembership
from .models import GuestEntry, FollowUpReport, GuestStatus, VisitPurpose, VisitChannel, ChurchService




def _get_guest_unit_ids(church):
    if not church:
        return set()

    cache_key = f"guest_unit_ids:{church.id}"

    def _build():
        unit_ids = set()
        roles = UnitRole.raw_objects.filter(church=church, is_active=True).select_related("unit")
        for role in roles:
            perms = role.permissions or {}
            for key, allowed in perms.items():
                if allowed and key.startswith("guests."):
                    unit_ids.add(role.unit_id)
                    break
        return list(unit_ids)

    return set(cache.get_or_set(cache_key, _build, 600))

class GuestEntryForm(forms.ModelForm):
    assigned_to = forms.ModelChoiceField(
        queryset=UnitMembership.objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select bg-grey text-white border-0'}),
        help_text="Assign this guest to a team member.",
    )

    referred_by_member = forms.ModelChoiceField(
        queryset=UnitMembership.raw_objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Referred by (member)",
        help_text="Select if the person who invited this guest is already a member.",
    )
    referred_by_name = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter name if not a member',
        }),
        label="Referred by (name)",
        help_text="Write a name if the referrer is not yet in the system.",
    )

    date_of_birth = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'January 01 (Ignore Year)',
        }),
        help_text="Date of Birth.",
    )

    date_of_visit = forms.DateField(
        widget=forms.DateInput(format='%Y-%m-%d', attrs={
            'type': 'date',
            'class': 'form-control',
            'autocomplete': 'off',
        }),
        help_text="Date of Visit.",
        input_formats=['%Y-%m-%d', '%d/%m/%Y'],
        required=False,
    )

    class Meta:
        model = GuestEntry
        exclude = ['custom_id', 'church', 'birthday_month',
                   'birthday_day', 'status',
                    'is_active', 'is_deleted', 'deleted_at', 'assigned_at', 
                    'converted_to', 'first_thankyou_sent', 'second_thankyou_sent',
                    'second_visit_date', 'first_contact_reminder_sent', 'in_contact_overdue_notified',
                    ]
        widgets = {
            'picture': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'title': forms.Select(attrs={'class': 'form-select'}),
            'full_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'John Doe'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'johndoe@example.com'}),
            'phone_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '08123xxxx89'}),
            'date_of_birth': forms.TextInput(attrs={
                'type': 'text',
                'class': 'form-control',
                'placeholder': 'January 01 (Ignore Year)',
                'autocomplete': 'off',
            }),
            'age_range': forms.Select(attrs={'class': 'form-select'}),
            'marital_status': forms.Select(attrs={'class': 'form-select'}),
            'gender': forms.Select(attrs={'class': 'form-select', 'required': 'required'}),
            'occupation': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Manager'}),
            'home_address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Home Address'}),
            'date_of_visit': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
            'purpose_of_visit': forms.Select(attrs={'class': 'form-select'}),
            'channel_of_visit': forms.Select(attrs={'class': 'form-select'}),
            'service_attended': forms.Select(attrs={'class': 'form-select'}),
            'assigned_to': forms.Select(attrs={'class': 'form-select'}),
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
            'title': 'Title.',
            'picture': "Guest's Picture.",
            'gender': 'Gender.',
            'full_name': 'Full Name.',
            'phone_number': 'Phone Number.',
            'email': 'Email Address.',
            'date_of_birth': 'Date of Birth.',
            'age_range': "Select the guest's age range.",
            'marital_status': 'Marital Status.',
            'home_address': 'Home Address.',
            'occupation': 'Occupation.',
            'date_of_visit': 'Date of Visit.',
            'purpose_of_visit': 'Purpose of Visit.',
            'channel_of_visit': 'How did the guest find out about us?',
            'service_attended': 'What service did the guest attend?',
            'assigned_to': 'Assign this guest to a unit member for follow-up.',
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        self.church = kwargs.pop('church', None)
        self.permissions = kwargs.pop('permissions', None)
        super().__init__(*args, **kwargs)

        resolver = self.permissions
        if not resolver and self.user and self.church:
            resolver = PermissionResolver(self.user, self.church)

        if self.church:
            # Scope referred_by_member to guest-unit members of this church
            from .views import _get_guest_unit_ids as _get_unit_ids
            unit_ids = _get_unit_ids(self.church)
            ref_qs = UnitMembership.raw_objects.filter(
                church=self.church,
                is_active=True,
                workforce_member__is_active=True,
                workforce_member__member__is_active=True,
            )
            if unit_ids:
                ref_qs = ref_qs.filter(unit_id__in=unit_ids)
            self.fields['referred_by_member'].queryset = ref_qs.select_related(
                'unit', 'workforce_member__member__user'
            ).order_by('workforce_member__member__user__full_name')
            self.fields['referred_by_member'].label_from_instance = (
                lambda obj: (
                    f"{(obj.workforce_member.member.user.full_name or obj.workforce_member.member.user.username)}"
                    + (f" ({obj.unit.name})" if obj.unit else "")
                )
            )
            self.fields['purpose_of_visit'].queryset = VisitPurpose.raw_objects.filter(church=self.church, is_active=True).order_by('order')
            self.fields['channel_of_visit'].queryset = VisitChannel.raw_objects.filter(church=self.church, is_active=True).order_by('order')
            self.fields['service_attended'].queryset = ChurchService.raw_objects.filter(church=self.church, is_active=True).order_by('order')

        allow_assign = bool(resolver and (resolver.can('guests.assign_all') or resolver.can('guests.assign_unit')))
        if allow_assign and self.church:
            unit_ids = _get_guest_unit_ids(self.church)

            if resolver and resolver.can('guests.assign_unit') and not resolver.can('guests.assign_all'):
                unit_ids = unit_ids & set(resolver.permission_map.get('guests.assign_unit', {}).get('units', set()))

            qs = UnitMembership.raw_objects.filter(
                church=self.church,
                unit_id__in=unit_ids,
                is_active=True,
                workforce_member__is_active=True,
                workforce_member__member__is_active=True,
                workforce_member__member__user__is_active=True,
            ).select_related('unit', 'workforce_member__member__user')

            self.fields['assigned_to'].queryset = qs.order_by('workforce_member__member__user__full_name')
            self.fields['assigned_to'].required = False

            self.fields['assigned_to'].label_from_instance = (
                lambda obj: (
                    f"{(obj.workforce_member.member.user.get_full_name() or obj.workforce_member.member.user.username)}"
                    + (f" ({obj.unit.name})" if obj.unit else "")
                )
            )
        else:
            self.fields.pop('assigned_to', None)

        select_fields = [
            'title', 'marital_status', 'gender', 'purpose_of_visit',
            'channel_of_visit', 'service_attended', 'status', 'age_range', 'assigned_to',
        ]

        for field_name in select_fields:
            if field_name in self.fields:
                choices = list(self.fields[field_name].choices)
                if choices and choices[0][0] == '':
                    choices[0] = ("", "")
                else:
                    choices = [("", "")] + choices
                self.fields[field_name].choices = choices

        if self.instance and self.instance.date_of_visit:
            self.fields['date_of_visit'].initial = self.instance.date_of_visit.strftime('%Y-%m-%d')

    def clean_phone_number(self):
        phone = self.cleaned_data.get('phone_number')
        if phone and not phone.isdigit():
            raise ValidationError('Phone number must contain only digits.')
        return phone

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and '@' not in email:
            raise ValidationError('Enter a valid email address.')
        return email

    def clean_date_of_birth(self):
        dob_raw = self.cleaned_data.get('date_of_birth')
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
            format='%Y-%m-%d',
            attrs={
                'type': 'date',
                'class': 'form-control bg-grey text-white border-0',
            }
        ),
        input_formats=['%Y-%m-%d', '%d/%m/%Y'],
        required=False,
    )

    class Meta:
        model = FollowUpReport
        exclude = ['guest', 'assigned_to', 'created_at', 'church']
        widgets = {
            'note': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Enter your follow-up notes here…',
                'rows': 5,
            }),
            # ── Service attendance ────────────────────────────────────
            'service_sunday':  forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'service_midweek': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'service_others':  forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            # ── Contact tracking ──────────────────────────────────────
            'contact_attempted': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'contact_answered':  forms.CheckboxInput(
                attrs={
                    'class': 'form-check-input',
                    'id':    'id_contact_answered',
                    # When ticked, triggers status auto-advance to In-Contact on save
                }
            ),
            'contact_type': forms.Select(attrs={'class': 'form-select bg-grey border-0'}),
            'contact_attachment': forms.ClearableFileInput(
                attrs={
                    'class': 'form-control bg-grey border-0',
                    'accept': 'image/*,.pdf',
                }
            ),
            # ── Terminal flag ─────────────────────────────────────────
            'not_planted': forms.CheckboxInput(
                attrs={
                    'class': 'form-check-input',
                    'id':    'id_not_planted',
                }
            ),
            'reviewed': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        self.guest = kwargs.pop('guest', None)
        self.church = kwargs.pop('church', None)
        super().__init__(*args, **kwargs)

        if self.instance.pk:
            if self.instance.report_date:
                self.initial['report_date'] = self.instance.report_date.strftime('%Y-%m-%d')
            self.fields['report_date'].widget.attrs.update({
                'readonly': True,
                'class': self.fields['report_date'].widget.attrs.get('class', '') + ' bg-secondary text-dark fw-bold',
            })
        else:
            if not self.initial.get('report_date'):
                today = localdate()
                self.initial['report_date'] = today.strftime('%Y-%m-%d')

    def clean(self):
        cleaned_data = super().clean()
        report_date = cleaned_data.get('report_date')

        if self.guest and FollowUpReport.raw_objects.filter(church=self.church, 
            guest=self.guest,
            report_date=report_date,
        ).exclude(pk=self.instance.pk).exists():
            raise ValidationError('You already submitted a report for this date.')

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
        return instance









