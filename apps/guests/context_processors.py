from guests.models import GuestEntry


def superuser_guests(request):
    """
    Inject a church-scoped guest list for superuser quick-access in templates.
    Only populated for superusers — regular users access guests via guest_list view.
    """
    church = getattr(request, "church", None)

    if (
        request.user.is_authenticated
        and request.user.is_superuser
        and church
    ):
        guests = (
            GuestEntry.raw_objects
            .filter(church=church, is_deleted=False)
            .order_by("-custom_id")
            .select_related("status", "assigned_to__workforce_member__member__user")
        )
    else:
        guests = []

    return {"superuser_guests": guests}