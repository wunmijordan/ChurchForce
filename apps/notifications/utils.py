"""
notifications/utils.py

Core notification delivery utilities.

notify_members() is the single entry point for creating and pushing
notifications. It accepts ChurchMember objects (not CustomUser) because
notifications are church-scoped.

All old references to is_project_admin / is_magnet_admin / TeamMembership
have been removed — recipient selection is now the caller's responsibility,
using PermissionResolver.can() or direct queryset filters.
"""

import json
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────

def guest_full_name(guest):
    """Return guest's display name with title prefix."""
    if not guest:
        return "Unknown Guest"
    title = getattr(guest, "title", "") or ""
    name  = getattr(guest, "full_name", "Unnamed Guest") or "Unnamed Guest"
    return f"{title} {name}".strip()


def member_full_name(member):
    """
    Return a ChurchMember's display name via member.user.
    Prefers CustomUser.full_name, falls back to username.
    """
    if not member:
        return "Unknown Member"
    try:
        user  = member.user
        title = getattr(user, "title", "") or ""
        name  = getattr(user, "full_name", None) or user.username or "Unnamed"
        return f"{title} {name}".strip()
    except AttributeError:
        return "Unknown Member"


# Keep old name as alias for callers that haven't been updated yet
def user_full_name(user_or_member):
    """
    Accepts either a CustomUser or ChurchMember.
    Returns display name with title.
    """
    if not user_or_member:
        return "Unknown"
    # ChurchMember path
    if hasattr(user_or_member, "user"):
        return member_full_name(user_or_member)
    # CustomUser path
    title = getattr(user_or_member, "title", "") or ""
    name  = (
        getattr(user_or_member, "full_name", None)
        or (user_or_member.get_full_name() if hasattr(user_or_member, "get_full_name") else None)
        or getattr(user_or_member, "username", "Unnamed")
    )
    return f"{title} {name}".strip() if title else (name or "Unnamed")


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket push
# ─────────────────────────────────────────────────────────────────────────────

def push_websocket_notification(notification):
    """
    Send a real-time notification via WebSocket to the member's user group.
    Silently skips if the channel layer is not configured.
    """
    try:
        user         = notification.member.user
        channel_layer = get_channel_layer()
        if not channel_layer:
            return

        async_to_sync(channel_layer.group_send)(
            f"user_{user.id}",
            {
                "type": "send_notification",
                "content": {
                    "id":          notification.id,
                    "title":       notification.title,
                    "description": notification.description,
                    "link":        notification.link or "#",
                    "is_urgent":   notification.is_urgent,
                    "is_success":  notification.is_success,
                },
            },
        )
    except Exception as exc:
        logger.warning("push_websocket_notification failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Web Push
# ─────────────────────────────────────────────────────────────────────────────

def push_webpush_notification(notification):
    """
    Send a system Web Push notification (works even when browser is closed).
    Requires VAPID_PRIVATE_KEY in settings.
    Silently skips if pywebpush is not installed or VAPID key is missing.
    """
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return

    vapid_key = getattr(settings, "VAPID_PRIVATE_KEY", "")
    if not vapid_key:
        return

    member = notification.member
    settings_obj = getattr(member, "settings", None)
    sound        = settings_obj.notification_sound if settings_obj else "chime1"

    payload = json.dumps({
        "title":     notification.title,
        "body":      notification.description,
        "url":       notification.link or "/",
        "sound":     sound,
        "vibration": True,
    })

    for sub in member.push_subscriptions.filter(is_active=True):
        try:
            webpush(
                subscription_info=sub.subscription_data,
                data=payload,
                vapid_private_key=vapid_key,
                vapid_claims={"sub": f"mailto:{getattr(settings, 'VAPID_ADMIN_EMAIL', 'admin@churchforce.io')}"},
            )
        except WebPushException as exc:
            if "410" in str(exc) or "404" in str(exc):
                sub.delete()  # Expired subscription
            else:
                logger.warning("Web Push failed for member %s: %s", member, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def notify_members(members, title, description, church, link="#",
                   is_urgent=False, is_success=False, send_at=None):
    """
    Create Notification records for a list of ChurchMember objects and
    push them via WebSocket (and optionally Web Push).

    Args:
        members:     Iterable of ChurchMember instances.
        title:       Notification title string.
        description: Notification body string.
        church:      Church instance (required for church FK on Notification).
        link:        Optional URL the notification links to.
        is_urgent:   Mark as urgent (red badge in UI).
        is_success:  Mark as success (green badge in UI).
        send_at:     Optional future datetime for deferred delivery.
                     If set, the scheduler handles the push later.
                     If None, push immediately.
    """
    from notifications.models import Notification

    for member in members:
        try:
            notif = Notification.objects.create(
                church=church,
                member=member,
                title=title,
                description=description,
                link=link,
                is_urgent=is_urgent,
                is_success=is_success,
                send_at=send_at,
            )

            # Only push immediately if not deferred
            if not send_at:
                push_websocket_notification(notif)
                push_webpush_notification(notif)

        except Exception as exc:
            logger.error(
                "notify_members: failed to notify member=%s: %s", member, exc
            )


# ─────────────────────────────────────────────────────────────────────────────
# Recipient resolution helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_admin_members(church):
    """
    Return ChurchMember queryset for all members with dashboard.admin
    permission in this church. Used by signals to notify admins.
    """
    from accounts.models import ChurchMember
    from permissions.services.resolver import PermissionResolver

    members = ChurchMember.raw_objects.filter(
        church=church,
        is_active=True,
    ).select_related("user")

    return [
        m for m in members
        if m.user.is_superuser or PermissionResolver(m.user, church).can("dashboard.admin")
    ]


def get_unit_admin_members(church, unit):
    """
    Return ChurchMember list for members with admin permission in a
    specific unit. Used by chat/event signals.
    """
    from accounts.models import ChurchMember
    from permissions.services.resolver import PermissionResolver

    members = ChurchMember.raw_objects.filter(
        church=church,
        is_active=True,
    ).select_related("user")

    return [
        m for m in members
        if m.user.is_superuser
        or PermissionResolver(m.user, church).can("dashboard.admin")
        or PermissionResolver(m.user, church).can("chat.manage", unit=unit)
    ]