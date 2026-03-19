import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from notifications.models import Notification, UserSettings, PushSubscription


def _get_member(request):
    """
    Resolve the current user's ChurchMember for this church.
    Returns None if not found — views should guard against this.
    """
    return getattr(request, "member", None)


# ─────────────────────────────────────────────────────────────────────────────
# Notification reads
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def unread_notifications(request):
    """Return all unread notifications for the logged-in member."""
    member = _get_member(request)
    if not member:
        return JsonResponse([], safe=False)

    unread = Notification.raw_objects.filter(
        member=member,
        is_read=False,
    ).values("id", "title", "description", "link", "created_at", "is_urgent", "is_success")

    return JsonResponse(list(unread), safe=False)


@login_required
@csrf_exempt
def mark_notification_read(request, pk):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid method."}, status=400)

    member = _get_member(request)
    if not member:
        return JsonResponse({"status": "error", "message": "No church context."}, status=403)

    try:
        notif         = Notification.raw_objects.get(pk=pk, member=member)
        notif.is_read = True
        notif.save(update_fields=["is_read"])
        return JsonResponse({"status": "ok"})
    except Notification.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Notification not found."}, status=404)


@login_required
@csrf_exempt
def mark_all_read(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Invalid method."}, status=400)

    member = _get_member(request)
    if not member:
        return JsonResponse({"status": "error", "message": "No church context."}, status=403)

    Notification.raw_objects.filter(member=member, is_read=False).update(is_read=True)
    return JsonResponse({"status": "ok"})


# ─────────────────────────────────────────────────────────────────────────────
# User settings
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def user_settings(request):
    member = _get_member(request)
    if not member:
        return JsonResponse({"success": False, "error": "No church context."}, status=403)

    church   = request.church
    settings_obj, _ = UserSettings.raw_objects.get_or_create(
        member=member,
        defaults={"church": church},
    )

    if request.method == "POST":
        from notifications.forms import UserSettingsForm
        form = UserSettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            form.save()
            return JsonResponse({"success": True})
        return JsonResponse({"success": False, "errors": form.errors}, status=400)

    return JsonResponse({"success": True})


@login_required
@require_POST
def update_user_settings(request):
    member = _get_member(request)
    if not member:
        return JsonResponse({"status": "error", "message": "No church context."}, status=403)

    church       = request.church
    settings_obj, _ = UserSettings.raw_objects.get_or_create(
        member=member,
        defaults={"church": church},
    )

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    settings_obj.notification_sound = data.get(
        "notification_sound", settings_obj.notification_sound
    )
    settings_obj.vibration_enabled = data.get("vibration_enabled") in (
        True, "true", "on", "1"
    )
    settings_obj.save(update_fields=["notification_sound", "vibration_enabled"])

    return JsonResponse({
        "status":    "ok",
        "sound":     settings_obj.notification_sound,
        "vibration": settings_obj.vibration_enabled,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Web Push subscription management
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@login_required
def save_subscription(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request."}, status=400)

    member = _get_member(request)
    if not member:
        return JsonResponse({"error": "No church context."}, status=403)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    PushSubscription.raw_objects.update_or_create(
        member=member,
        defaults={
            "subscription_data": data,
            "church": request.church,
        },
    )
    return JsonResponse({"status": "ok"})


@login_required
def test_push(request):
    member = _get_member(request)
    if not member:
        return JsonResponse({"error": "No church context."}, status=403)

    sub = PushSubscription.raw_objects.filter(member=member).first()
    if not sub:
        return JsonResponse({"error": "No push subscription found."}, status=400)

    from notifications.utils import push_webpush_notification
    from notifications.models import Notification

    # Create a temporary notification object for the test push
    fake_notif = Notification(
        member=member,
        title="Test Notification",
        description="This is a test push from ChurchForce.",
        link="/",
    )
    fake_notif.member = member  # ensure member is set
    push_webpush_notification(fake_notif)

    return JsonResponse({"status": "sent"})