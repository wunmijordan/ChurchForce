from messaging.forms import BulkMessageForm


def bulk_message_form(request):
    """
    Inject a BulkMessageForm into every template context so the bulk
    message modal can render on any page that uses base.html.

    Scopes the guest_status queryset to the current church.
    Returns an empty dict if the user is not authenticated.
    """
    if not request.user.is_authenticated:
        return {}

    church = getattr(request, "church", None)
    return {
        "bulk_message_form": BulkMessageForm(church=church),
    }