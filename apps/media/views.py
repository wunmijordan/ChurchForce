from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from permissions.services.resolver import PermissionResolver


def _get_church(request):
    return getattr(request, "church", None)


def _get_permissions(request):
    perms = getattr(request, "permissions", None)
    if perms:
        return perms

    church = _get_church(request)
    if not church or not request.user.is_authenticated:
        return None

    return PermissionResolver(request.user, church)


def _can(request, permission):
    if request.user.is_superuser:
        return True
    perms = _get_permissions(request)
    return bool(perms and perms.can(permission))


def _module_enabled(church):
    settings = getattr(church, "settings", None)
    return bool(settings and settings.enable_media_module)


@login_required
def index(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Media module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "media.view"):
        return HttpResponseForbidden("You do not have access to the media module.")

    return render(request, "media/index.html", {
        "page_title": "Media",
    })
