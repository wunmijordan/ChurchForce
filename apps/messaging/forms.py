from django import forms
from messaging.models import GuestMessage


class BulkMessageForm(forms.ModelForm):
    """
    Form for composing a bulk SMS message to a filtered group of guests.

    guest_status is a ModelChoiceField scoped to the current church's
    GuestStatus records — not a hardcoded choices list, since statuses
    are configurable per church.

    Requires church kwarg to scope the status queryset.
    """

    from guests.models import GuestStatus

    guest_status = forms.ModelChoiceField(
        queryset=GuestStatus.objects.none(),
        required=False,
        empty_label="All guests (no status filter)",
        label="Filter by guest status",
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Leave blank to message all guests in the church.",
    )

    class Meta:
        model  = GuestMessage
        fields = ["subject", "body"]
        widgets = {
            "subject": forms.TextInput(attrs={
                "class":       "form-control",
                "placeholder": "Message subject (optional)",
            }),
            "body": forms.Textarea(attrs={
                "class": "form-control",
                "rows":  5,
                "placeholder": "Type your message here…",
            }),
        }
        labels = {
            "subject": "Subject",
            "body":    "Message",
        }

    def __init__(self, *args, **kwargs):
        self.church = kwargs.pop("church", None)
        super().__init__(*args, **kwargs)

        if self.church:
            from guests.models import GuestStatus
            self.fields["guest_status"].queryset = GuestStatus.raw_objects.filter(
                church=self.church, is_active=True
            ).order_by("order")