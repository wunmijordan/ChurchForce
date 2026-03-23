"""
tenants/context_processors.py

NOTE: The original file was named context_processor.py (singular) but
settings.py registers it as tenants.context_processors.church_context
(plural). This file is the corrected version � rename the original file
from context_processor.py to context_processors.py.
"""

from units.models import UnitMembership


def _member_units(request):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not church or not member:
        return [], []

    memberships = (
        UnitMembership.raw_objects
        .filter(church=church, is_active=True, workforce_member__member=member)
        .select_related("unit")
        .order_by("unit__name")
    )

    units = []
    groups = []
    for membership in memberships:
        unit = membership.unit
        if not unit or not unit.is_active:
            continue
        if unit.unit_type == "group":
            groups.append(unit)
        else:
            units.append(unit)

    return units, groups


def church_context(request):
    """
    Inject church, subscription, member, permissions into every
    template context. All values come from attributes set by
    TenantMiddleware and ChurchContextMiddleware.
    """
    units, groups = _member_units(request)
    church = getattr(request, "church", None)
    try:
        church_settings = getattr(church, "settings", None) if church else None
    except Exception:
        church_settings = None
    return {
        "church":       church,
        "subscription": getattr(request, "subscription", None),
        "member":       getattr(request, "member",       None),
        "permissions":  getattr(request, "permissions",  None),
        "nav_units":    units,
        "nav_groups":   groups,
        "church_settings": church_settings,
    }

