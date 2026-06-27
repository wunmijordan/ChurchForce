from django.shortcuts import get_object_or_404
from django.http import Http404
from units.models import ChurchUnit
from units.models import UnitMembership


def get_unit_for_request(request, slug):
    church = getattr(request, "church", None)
    unit = get_object_or_404(
        ChurchUnit.raw_objects.filter(
            church=church,
            is_active=True,
        ),
        slug=slug,
    )

    if getattr(request.user, "is_superuser", False):
        return unit

    perms = getattr(request, "permissions", None)
    if perms and perms.can("unit.view_all"):
        return unit

    member = getattr(request, "member", None)
    is_direct_member = bool(
        member
        and UnitMembership.raw_objects.filter(
            church=church,
            unit=unit,
            workforce_member__member=member,
            is_active=True,
        ).exists()
    )
    if is_direct_member:
        return unit

    raise Http404("Unit not found")
