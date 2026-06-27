"""
accounts/templatetags/access_tags.py

Permission template tags for ChurchForce.

Available tags/filters:
    {% if request|is_admin %}                         — superuser OR tier<=1
    {% if permissions|can:"some.permission" %}         — specific permission key
    {% can_unit permissions "perm" unit as ok %}       — unit-scoped permission
    {% if permissions|tier_gte:2 %}                    — user tier <= given value
    {{ permissions|tier_label }}                       — human-readable tier label
    {{ request.user|role_label }}                      — short role display
"""

from django import template
from django.conf import settings
from django.utils.html import format_html
from django.urls import reverse
import hashlib
from django.utils.safestring import mark_safe
from core.request_context import get_current_request

register = template.Library()


ROLE_COLOR_MAP = {
    "superuser": "bg-cyan-lt",
    "pastor": "bg-purple-lt",
    "admin": "bg-orange-lt",
    "assistant pastor": "bg-yellow-lt",
    "minister": "bg-indigo-lt",
    "member": "bg-gray-lt",
}

STAGE_COLOR_MAP = {
    "trainee": "bg-blue-lt",
    "probation": "bg-yellow-lt",
    "probationer": "bg-yellow-lt",
    "active": "bg-green-lt",
    "leader": "bg-orange-lt",
}

COLOR_POOL = [
    "bg-blue-lt",
    "bg-green-lt",
    "bg-indigo-lt",
    "bg-purple-lt",
    "bg-pink-lt",
    "bg-teal-lt",
    "bg-cyan-lt",
    "bg-lime-lt",
]


def _auto_color(name: str):
    if not name:
        return "bg-gray-lt"
    h = int(hashlib.md5(name.lower().encode()).hexdigest(), 16)
    return COLOR_POOL[h % len(COLOR_POOL)]


def _pick_role_name(role_rows):
    if not role_rows:
        return "Member"

    for row in role_rows:
        name = str(getattr(row, "role__name", None) or getattr(row, "name", "")).strip()
        if name and name.lower() != "member":
            return name

    first = role_rows[0]
    return (
        str(getattr(first, "role__name", None) or getattr(first, "name", "")).strip()
        or "Member"
    )


@register.filter(name="can")
def can_permission(permissions, permission_key):
    """
    Check a permission via PermissionResolver.
    Usage: {% if permissions|can:"dashboard.admin" %}
    """
    if not permissions:
        return False
    return bool(permissions.can(permission_key))


@register.simple_tag
def can_unit(permissions, permission_key, unit):
    """
    Unit-scoped permission check.
    Usage: {% can_unit permissions "chat.manage" unit as ok %}
    """
    if not permissions:
        return False
    return bool(permissions.can(permission_key, unit=unit))


@register.simple_tag
def scoped_url(request, url_name, *args, **kwargs):
    """
    Reverse a URL and prefix the active church slug when needed.
    Safe for templates that may render on app-host pages lacking script_prefix.
    """
    path = reverse(url_name, args=args, kwargs=kwargs)
    church = getattr(request, "church", None)
    if not church:
        return path
    if path.startswith(f"/{church.slug}/"):
        return path

    host = request.get_host().split(":")[0]
    app_domains = list(getattr(settings, "APP_DOMAINS", []) or [])
    app_hosts = [domain.split(":")[0] for domain in app_domains]
    if request.path.startswith(f"/{church.slug}/") or host in app_hosts:
        return f"/{church.slug}{path if path.startswith('/') else '/' + path}"
    return path


@register.filter(name="is_admin")
def is_admin(request):
    """
    True if superuser OR has dashboard.admin permission (tier 1).
    Usage: {% if request|is_admin %}
    """
    if not request:
        return False
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    perms = getattr(request, "permissions", None)
    if perms and perms.can("dashboard.admin"):
        return True
    church = getattr(request, "church", None)
    if not church:
        return False
    from accounts.models import ChurchMember

    return ChurchMember.raw_objects.filter(
        church=church,
        user=user,
        is_active=True,
        is_admin=True,
    ).exists()


@register.filter(name="tier_gte")
def tier_gte(permissions, min_tier):
    """
    True if the user's effective global tier is <= min_tier
    (lower number = more powerful; 'gte' means 'at least this powerful').

    Usage: {% if permissions|tier_gte:2 %}  → True for Admin (1) and Sub-Admin (2)
           {% if permissions|tier_gte:3 %}  → True for Admin, Sub-Admin, Unit Head
    """
    if not permissions:
        return False
    try:
        t = permissions.get_tier()
    except AttributeError:
        return False
    if t is None:
        return False
    return t <= int(min_tier)


@register.filter(name="tier_label")
def tier_label_filter(permissions):
    """
    Return the human-readable tier label for the current user.
    Usage: {{ permissions|tier_label }}  → e.g. "Sub-Admin", "Unit / Group Head"
    """
    if not permissions:
        return "Member"
    try:
        return permissions.get_tier_label()
    except AttributeError:
        return "Member"


def _resolve_role_meta(user_or_member):
    role = "Member"
    stage = None

    def _current_role_church(user):
        request = get_current_request()
        if not request:
            return None

        # Campus workspaces expose the campus-church on the request so the
        # badge can resolve campus-specific roles instead of HQ roles.
        campus_church = getattr(request, "campus_church", None)
        if campus_church:
            return campus_church

        church = getattr(request, "church", None)
        if not church or not user:
            return church

        # If we're inside a campus-church request, prefer that church.
        if getattr(church, "parent_church_id", None):
            return church

        return church

    # ── Enriched dict (PRIMARY path — ZERO queries) ─────────────
    if isinstance(user_or_member, dict):
        user = user_or_member.get("user")

        if user and getattr(user, "is_superuser", False):
            return ("Superuser", None)

        # ── ROLE (exact view logic) ─────────────────────────────
        wf_roles = user_or_member.get("wf_role_names", [])

        if wf_roles:
            role = next(
                (r for r in wf_roles if str(r).strip().lower() != "member"),
                wf_roles[0],  # fallback to first role
            )
        else:
            role = user_or_member.get("role_label") or "Member"

        # ── STAGE ──────────────────────────────────────────────
        if user_or_member.get("is_trainee"):
            stage = "Trainee"
        elif user_or_member.get("is_disciplinary_probation"):
            stage = "Probation"
        elif user_or_member.get("is_probation"):
            stage = "Probation"
        else:
            stage = user_or_member.get("workforce_stage")

        return (role, stage)

    # ── Fallback path (DB lookup) ───────────────────────────────
    user = getattr(user_or_member, "user", user_or_member)

    if getattr(user, "is_superuser", False):
        return ("Superuser", None)

    try:
        from accounts.models import ChurchMember
        from workforce.models import (
            WorkforceMembershipRole,
            WorkforceTraineeProfile,
            WorkforceMember,
        )
        from permissions.services.resolver import PermissionResolver

        role_church = _current_role_church(user)
        member = None
        if role_church:
            member = (
                ChurchMember.raw_objects.filter(
                    church=role_church, user=user, is_active=True
                )
                .select_related("church")
                .first()
            )
        if not member:
            member = (
                ChurchMember.raw_objects.filter(user=user).select_related("church").first()
            )

        if not member:
            return ("Member", None)

        # ── ROLE: Admin via permission (important) ──────────────
        resolver = PermissionResolver(user, member.church)
        if resolver.can("dashboard.admin"):
            role = "Admin"

        # ── ROLE: Workforce roles (exact same logic) ────────────
        roles = list(
            WorkforceMembershipRole.raw_objects.filter(
                church=member.church,
                workforce_member__member=member,
            )
            .select_related("role")
            .order_by("role__order", "role__name")
            .values_list("role__name", flat=True)
        )

        if roles:
            role = next(
                (r for r in roles if str(r).strip().lower() != "member"),
                roles[0],
            )

        # ── STAGE ──────────────────────────────────────────────
        if WorkforceTraineeProfile.raw_objects.filter(
            church=member.church,
            member=member,
            reason="induction",
            is_active=True,
        ).exists():
            stage = "Trainee"
        else:
            wf = (
                WorkforceMember.raw_objects.filter(
                    church=member.church,
                    member=member,
                    is_active=True,
                )
                .select_related("stage")
                .first()
            )
            if wf and wf.stage:
                stage = wf.stage.name

        return (role, stage)

    except Exception:
        return ("Member", None)


@register.filter(name="role_label")
def role_label(user_or_member):
    role, stage = _resolve_role_meta(user_or_member)
    return f"{role} - {stage}" if stage else role


@register.filter(name="role_only")
def role_only(user_or_member):
    role, _ = _resolve_role_meta(user_or_member)
    return role


@register.filter(name="stage_only")
def stage_only(user_or_member):
    _, stage = _resolve_role_meta(user_or_member)
    return stage or ""


@register.filter(name="role_badge")
def role_badge(user_or_member):
    role, stage = _resolve_role_meta(user_or_member)

    role_key = role.lower()
    stage_key = (stage or "").lower()

    role_color = ROLE_COLOR_MAP.get(role_key) or _auto_color(role_key)
    stage_color = STAGE_COLOR_MAP.get(stage_key)

    if stage:
        return mark_safe(
            f'<span class="badge {role_color}">{role}</span> '
            f'<span class="badge {stage_color}">{stage}</span>'
        )

    return mark_safe(f'<span class="badge {role_color}">{role}</span>')


@register.simple_tag
def role_label_for(user, church):
    """
    Role label with church context — queries WorkforceRole directly.
    Usage: {% role_label_for item.user church as rl %}{{ rl }}
    """
    if not user or not church:
        return "Member"
    if getattr(user, "is_superuser", False):
        return "Superuser"
    try:
        from workforce.models import WorkforceMembershipRole
        from accounts.models import ChurchMember

        member = ChurchMember.raw_objects.filter(church=church, user=user).first()
        if not member:
            return "Member"
        roles = list(
            WorkforceMembershipRole.raw_objects.filter(
                church=church, workforce_member__member=member
            )
            .select_related("role")
            .order_by("role__order")
            .values_list("role__name", flat=True)
        )
        return _pick_role_name(roles)
    except Exception:
        pass
    return "Member"


@register.filter(name="online_badge")
def online_badge(user):
    """Render an online/offline status badge."""
    if getattr(user, "is_online", False):
        return format_html('<span class="badge bg-success-lt">● Online</span>')
    return format_html(
        '<span class="badge bg-secondary-lt text-muted">○ Offline</span>'
    )


# ── Generic utility filters ────────────────────────────────────────────────────


@register.filter(name="lookup")
def lookup(obj, key):
    """Get a key from a dict or attribute from an object.
    Usage: {% with fld=form|lookup:"field_name" %}{{ fld }}{% endwith %}
    """
    if obj is None:
        return None
    if hasattr(obj, "__getitem__"):
        try:
            return obj[key]
        except (KeyError, TypeError):
            pass
        return getattr(obj, key, None)


@register.filter(name="split")
def split(value, delimiter=","):
    """
    Split a string by a delimiter.
    Usage: {% for item in "a,b,c"|split:"," %}
    """
    if not value:
        return []
    return [v.strip() for v in str(value).split(delimiter) if v.strip()]


# ── Unit-permission snapshot ───────────────────────────────────────────────────


@register.simple_tag
def unit_perms(permissions, unit):
    """
    Return a dict of {perm_key: True} for the given unit.
    Useful for injecting into JS as a JSON blob.

    Usage:
        {% unit_perms permissions unit as up %}
        {% if up.guests_view %}…{% endif %}
        <script>const UP = {{ up|safe }};</script>

    Keys use underscores (not dots) for JS friendliness.
    """
    if not permissions or not unit:
        return {}
    try:
        raw = permissions.effective_permissions_for_unit(unit)
    except AttributeError:
        return {}
    # Convert "namespace.action" → "namespace_action" for JS
    return {k.replace(".", "_"): v for k, v in raw.items()}


@register.simple_tag
def can_any_unit(permissions, permission_key):
    """
    True if the user has the permission in at least one unit.
    Distinct from can_unit which requires a specific unit object.

    Usage: {% can_any_unit permissions "guests.view" as ok %}
    """
    if not permissions:
        return False
    return bool(permissions.can(permission_key))


@register.filter(name="is_unit_manager")
def is_unit_manager(permissions, unit):
    """
    True if the user can manage members in the given unit.
    Usage: {% if permissions|is_unit_manager:unit %}
    """
    if not permissions or not unit:
        return False
    try:
        return permissions.is_unit_manager(unit)
    except AttributeError:
        return False
