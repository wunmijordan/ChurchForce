"""
tenants/context_processors.py

NOTE: The original file was named context_processor.py (singular) but
settings.py registers it as tenants.context_processors.church_context
(plural). This file is the corrected version � rename the original file
from context_processor.py to context_processors.py.
"""

from units.models import UnitMembership


def _tenant_admin(request, church=None):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return False
    if request.user.is_superuser:
        return True

    church = church or getattr(request, "church", None)
    perms = getattr(request, "permissions", None)
    if perms and perms.can("dashboard.admin"):
        return True

    if not church:
        return False

    from accounts.models import ChurchMember

    return ChurchMember.raw_objects.filter(
        church=church,
        user=request.user,
        is_active=True,
        is_admin=True,
    ).exists()


def _member_units(request):
    """
    Admins/superusers see ALL active units.
    Regular members see only their own.
    """
    from units.models import ChurchUnit

    church = getattr(request, "church", None)
    if not church:
        return [], []

    user = getattr(request, "user", None)
    is_admin = _tenant_admin(request, church)

    if is_admin:
        qs = ChurchUnit.raw_objects.filter(church=church, is_active=True).order_by(
            "name"
        )
    else:
        member = getattr(request, "member", None)
        if not member:
            return [], []
        ids = UnitMembership.raw_objects.filter(
            church=church, is_active=True, workforce_member__member=member
        ).values_list("unit_id", flat=True)
        qs = ChurchUnit.raw_objects.filter(id__in=ids, is_active=True).order_by("name")

    units, groups = [], []
    for unit in qs:
        (groups if unit.unit_type == "group" else units).append(unit)
    return units, groups


from permissions.registry import TIER_ASSISTANT
from units.models import UnitMembership


def _unit_ids_upto_tier(request, max_tier):
    """
    Return unit IDs where the user's structural or role tier
    is <= max_tier.

    Prevents Custom (tier 6) roles from inheriting admin UI.
    """
    member = getattr(request, "member", None)
    church = getattr(request, "church", None)

    if not member or not church:
        return set()

    memberships = (
        UnitMembership.raw_objects.filter(
            church=church,
            workforce_member__member=member,
            is_active=True,
        )
        .select_related("unit")
        .prefetch_related("roles__role")
    )

    allowed_units = set()

    for m in memberships:

        # structural tiers
        if m.is_unit_head:
            allowed_units.add(m.unit_id)
            continue

        if getattr(m, "is_assistant", False):
            allowed_units.add(m.unit_id)
            continue

        # role tiers
        for ra in m.roles.all():
            role = getattr(ra, "role", None)
            if role and getattr(role, "tier", 6) <= max_tier:
                allowed_units.add(m.unit_id)
                break

    return allowed_units


def _campus_navbar_context(request, church, member, is_admin):
    """
    Build the three campus-related context variables consumed by base.html:

      led_campus       — Campus object the current member leads (HQ side).
                         Shown in the navbar only when the user is NOT an admin,
                         because admins already have the full account page.
      is_campus_church — True when the current request.church is itself a
                         campus-church (has a parent_church).
      hq_church        — The parent Church when is_campus_church is True.
    """
    no_campus = {
        "led_campus": None,
        "is_campus_church": False,
        "hq_church": None,
    }

    if not church:
        return no_campus

    # ── Is the current church a campus-church? ───────────────────────────────
    is_campus_church = bool(church.parent_church_id)
    hq_church = church.parent_church if is_campus_church else None

    # ── Does the current member lead a campus (HQ side)? ────────────────────
    led_campus = None
    if member and not is_admin:
        try:
            from tenants.models import Campus

            led_campus = (
                Campus.raw_objects.filter(
                    church=church,
                    campus_leader=member,
                    is_active=True,
                )
                .select_related("campus_church")
                .first()
            )
        except Exception:
            pass

    return {
        "led_campus": led_campus,
        "is_campus_church": is_campus_church,
        "hq_church": hq_church,
    }


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
    user = getattr(request, "user", None)
    perms = getattr(request, "permissions", None)
    is_admin = _tenant_admin(request, church)
    member = getattr(request, "member", None)

    training_badge = 0
    if church and member:
        try:
            from lms.models import LMSEnrollment, LMSSubmission

            if is_admin:
                training_badge = LMSSubmission.raw_objects.filter(
                    church=church, status="submitted", is_active=True
                ).count()
            else:
                training_badge = LMSEnrollment.raw_objects.filter(
                    church=church,
                    member=member,
                    status__in=["active", "failed"],
                    is_active=True,
                ).count()
        except Exception:
            pass

    # Resolve tier for template use (display label + coarse checks)
    user_tier = None
    user_tier_label = "Member"
    if user and user.is_authenticated:
        if user.is_superuser:
            user_tier = 0
            user_tier_label = "Superuser"
        elif perms:
            try:
                user_tier = perms.get_tier()
                user_tier_label = perms.get_tier_label()
            except AttributeError:
                pass

    # ── Centralised permission snapshot for templates ──────────────────────
    # Templates should use these booleans rather than recomputing.
    # All of these are derived from PermissionResolver — single source of truth.
    perm = perms  # alias for brevity below

    can_admin_dashboard = is_admin
    can_view_all_members = is_admin or bool(
        perm and perm.can("accounts.view_all_users")
    )
    can_manage_members = is_admin or bool(perm and perm.can("accounts.manage_users"))
    can_view_all_guests = is_admin or bool(perm and perm.can("guests.view_all"))
    can_manage_all_guests = is_admin or bool(perm and perm.can("guests.manage_all"))
    can_view_guests = bool(perm and perm.can("guests.view"))

    from permissions.registry import TIER_ASSISTANT

    can_create_guest = False

    if perm and church:

        try:
            from units.models import ChurchUnit

            # Units that actually support guest management
            guest_mgmt_unit_ids = set(
                ChurchUnit.raw_objects.filter(
                    church=church,
                    is_active=True,
                    guest_management=True,
                ).values_list("id", flat=True)
            )

            # Units where permission exists
            perm_unit_ids = perm.unit_ids_for("guests.create")

            # Units allowed by peer-tier rule (≤ Assistant)
            tier_safe_units = _unit_ids_upto_tier(
                request,
                TIER_ASSISTANT,
            )

            # FINAL intersection
            can_create_guest = bool(
                guest_mgmt_unit_ids & perm_unit_ids & tier_safe_units
            )

        except Exception:
            can_create_guest = False
    can_view_all_chat = is_admin or bool(perm and perm.can("chat.view_all"))
    can_send_bulk = is_admin or bool(perm and perm.can("messaging.send_bulk"))
    can_manage_all_events = is_admin or bool(perm and perm.can("events.manage_all"))
    can_manage_all_training = is_admin or bool(perm and perm.can("training.manage_all"))
    can_view_all_attendance = is_admin or bool(perm and perm.can("attendance.view_all"))
    can_view_all_units = is_admin or bool(perm and perm.can("unit.view_all"))

    return {
        "church": church,
        "subscription": getattr(request, "subscription", None),
        "member": member,
        "permissions": perms,
        "nav_units": units,
        "nav_groups": groups,
        "nav_is_admin": is_admin,
        "church_settings": church_settings,
        "training_badge": training_badge,
        "user_tier": user_tier,
        "user_tier_label": user_tier_label,
        # ── Campus navbar helpers ───────────────────────────────────────────
        # led_campus: the Campus the logged-in member leads (HQ side, non-admin)
        # is_campus_church: True when the active church IS a campus-church
        # hq_church: the parent Church if is_campus_church
        **_campus_navbar_context(request, church, member, is_admin),
        # ── Centralised boolean flags — use these in templates ──────────
        "can_admin_dashboard": can_admin_dashboard,
        "can_view_all_members": can_view_all_members,
        "can_manage_members": can_manage_members,
        "can_view_all_guests": can_view_all_guests,
        "can_manage_all_guests": can_manage_all_guests,
        "can_view_guests": can_view_guests,
        "can_create_guest": can_create_guest,
        "can_view_all_chat": can_view_all_chat,
        "can_send_bulk": can_send_bulk,
        "can_manage_all_events": can_manage_all_events,
        "can_manage_all_training": can_manage_all_training,
        "can_view_all_attendance": can_view_all_attendance,
        "can_view_all_units": can_view_all_units,
    }


def subscription_status(request):
    """
    Always provide a valid base template.
    NEVER return empty or None.
    """

    is_valid = getattr(request, "is_subscription_valid", None)

    # Default safely to public layout
    if is_valid is True:
        base_template = "base.html"
    else:
        base_template = "public_base.html"

    return {"base_template": base_template}
