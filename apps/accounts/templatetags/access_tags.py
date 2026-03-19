"""
accounts/templatetags/access_tags.py

Permission template tags for ChurchForce.

ALL old single-tenant filters REMOVED (not shimmed):
    is_project_admin, is_project_wide_admin, is_magnet_admin,
    is_team_admin, in_team, has_group

Replace in templates:
    {% if request|is_admin %}                    — superuser OR dashboard.admin
    {% if permissions|can:"some.permission" %}   — specific permission
    {% can_unit permissions "perm" unit as ok %} — unit-scoped permission
"""

from django import template
from django.utils.html import format_html

register = template.Library()


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


@register.filter(name="is_admin")
def is_admin(request):
    """
    True if superuser OR has dashboard.admin permission.
    Usage: {% if request|is_admin %}
    This is the ONLY role-adjacent check allowed in templates.
    """
    if not request:
        return False
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    perms = getattr(request, "permissions", None)
    return bool(perms and perms.can("dashboard.admin"))


@register.filter(name="role_label")
def role_label(user_or_member):
    """
    Short display label for a user's role. For display only, never gating.
    Usage: {{ request.user|role_label }}
    Views should pass role labels in context for list views.
    """
    if not user_or_member:
        return "Member"
    user = getattr(user_or_member, "user", user_or_member)
    if getattr(user, "is_superuser", False):
        return "Superuser"
    return "Member"


@register.filter(name="online_badge")
def online_badge(user):
    """Render an online/offline status badge. Usage: {{ user|online_badge }}"""
    if getattr(user, "is_online", False):
        return format_html('<span class="badge bg-success-lt">● Online</span>')
    return format_html('<span class="badge bg-secondary-lt text-muted">○ Offline</span>')