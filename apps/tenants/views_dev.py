from django.conf import settings
from django.contrib.auth import login
from django.http import Http404
from django.shortcuts import redirect
from django.utils import timezone

from tenants.onboarding import provision_church
from tenants.dev_utils import generate_dev_church_identity


def dev_quick_signup(request):
    """
    One-click tenant creation for development.
    """
    if not settings.DEBUG:
        raise Http404()

    identity = generate_dev_church_identity()

    church, user = provision_church(**identity)

    login(request, user)

    return redirect("dashboard:home")