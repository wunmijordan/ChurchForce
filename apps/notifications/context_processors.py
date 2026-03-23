"""
notifications/context_processors.py

Injects unread notification count and user settings into every template
context. All data is fetched from the ChurchMember on request, not from
CustomUser, because notifications and settings are church-scoped.
"""

from django.conf import settings as django_settings

from notifications.models import Notification, UserSettings


def unread_notifications(request):
    """
    Inject unread notifications and count for the current member.
    Returns empty values for unauthenticated or no-church requests.
    """
    member = getattr(request, "member", None)
    church = getattr(request, "church", None)

    if not request.user.is_authenticated or not member or not church:
        return {"unread_notifications": [], "unread_count": 0}

    notifications = Notification.raw_objects.filter(
        church=church,
        member=member,
        is_read=False,
    ).order_by("-created_at")[:50]  # cap at 50 to avoid template slowdown

    return {
        "unread_notifications": notifications,
        "unread_count":         notifications.count(),
    }


def user_settings(request):
    """
    Inject the member's notification settings into every template.
    Creates settings on the fly if they don't exist yet.
    """
    member = getattr(request, "member", None)
    church = getattr(request, "church", None)

    if not request.user.is_authenticated or not member or not church:
        return {}

    settings_obj = UserSettings.raw_objects.filter(member=member).first()

    return {
        "notification_settings": settings_obj,
        "sound_choices":         UserSettings.SOUND_CHOICES,
    }


def vapid_keys(request):
    """Expose the VAPID public key for Web Push subscription setup."""
    return {
        "VAPID_PUBLIC_KEY": getattr(django_settings, "VAPID_PUBLIC_KEY", ""),
    }
