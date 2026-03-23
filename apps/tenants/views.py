"""
tenants/views.py

Public-facing tenant views:
    signup                  â€” self-serve church registration (trial-first)
    trial_expired           â€” wall shown when 14-day trial ends
    subscription_inactive   â€” wall shown when a paid subscription lapses
"""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from .forms import ChurchSignupForm, ChurchSettingsForm
from .models import ChurchSetting
from units.forms import UnitQuickCreateForm

from core.scheduler import get_scheduler
from core.tasks import send_welcome_email  # Ensure you created this task

def signup(request):
    """
    Public self-serve signup for ChurchForce on Trial Plan.
    
    1. Validates unique URL, email, and password strength via Form.
    2. Provisions Church, Admin User, and Settings in an atomic transaction.
    3. Schedules a background welcome email.
    4. Logs the user in and redirects to pricing.
    """
    if request.user.is_authenticated:
        return redirect("post_login_redirect")

    form = ChurchSignupForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data

        try:
            # Wrap everything in a transaction so we don't get 'partial' signups
            with transaction.atomic():
                from tenants.onboarding import provision_church

                church, user = provision_church(
                    church_name=cd["church_name"],
                    slug=cd["url_handle"],
                    admin_full_name=cd["full_name"],
                    admin_email=cd["email"],
                    admin_password=cd["password"],
                )

                # Schedule the background email
                # We use on_commit to ensure email only sends if DB save succeeds
                scheduler = get_scheduler()
                if scheduler:
                    transaction.on_commit(lambda: scheduler.add_job(
                        send_welcome_email,
                        trigger='date',
                        args=[user.email, church.name, user.full_name, church.domain],
                        id=f"welcome_email_{user.id}",
                        replace_existing=True
                    ))

            # Log the user in
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")

            messages.success(
                request,
                f"Welcome to ChurchForce, {church.name}! "
                f"Your 14-day free trial has started."
            )

            # Redirect to pricing to choose a plan
            return redirect("billing:pricing")

        except ValueError as exc:
            # Catches business logic errors (e.g. duplicate slug found in provision_church)
            messages.error(request, str(exc))
        except Exception as e:
            # Log the error here if you have logging set up
            messages.error(
                request,
                "An unexpected error occurred during setup. Please try again or contact support."
            )

    return render(request, "tenants/signup.html", {"form": form})



def trial_expired(request):
    """
    Shown when a trial has ended and the tenant hasn't upgraded.
    Links to the billing pricing page.

    Template: tenants/trial_expired.html
    """
    church = getattr(request, "church", None)
    return render(request, "tenants/trial_expired.html", {
        "church": church,
        "pricing_url": "billing:pricing",
    })


def subscription_inactive(request):
    """
    Shown when a paid subscription lapses (non-trial).

    Template: tenants/subscription_inactive.html
    """
    church = getattr(request, "church", None)
    return render(request, "tenants/subscription_inactive.html", {
        "church": church,
        "portal_url": "billing:portal",
    })


@login_required
def church_settings(request):
    """
    Unified church settings page.
    Combines Church profile fields and ChurchSetting preferences
    into a single form, plus an inline unit-creation form.

    Template: tenants/church_settings.html
    """
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    # Permission gate
    perms    = getattr(request, "permissions", None)
    is_admin = (
        request.user.is_superuser
        or (perms and perms.can("dashboard.admin"))
    )
    if not is_admin:
        messages.error(request, "You do not have permission to manage church settings.")
        return redirect("dashboard")

    # Get or create ChurchSetting
    church_setting, _ = ChurchSetting.objects.get_or_create(church=church)

    if request.method == "POST":
        if "create_unit" in request.POST:
            unit_form = UnitQuickCreateForm(request.POST, church=church, settings=church_setting)
            settings_form = ChurchSettingsForm(church=church, settings=church_setting)

            if unit_form.is_valid():
                unit = unit_form.save()
                messages.success(request, f"'{unit.name}' created.")
                return redirect("tenants:church_settings")

            messages.error(request, "Please correct the unit form errors below.")
        else:
            settings_form = ChurchSettingsForm(request.POST, request.FILES, church=church, settings=church_setting)
            unit_form = UnitQuickCreateForm(church=church, settings=church_setting)

            if settings_form.is_valid():
                settings_form.save()
                messages.success(request, "Settings saved successfully.")
                return redirect("tenants:church_settings")

            messages.error(request, "Please correct the errors below.")
    else:
        settings_form = ChurchSettingsForm(church=church, settings=church_setting)
        unit_form = UnitQuickCreateForm(church=church, settings=church_setting)

    return render(request, "tenants/church_settings.html", {
        "settings_form": settings_form,
        "unit_form": unit_form,
        "church": church,
        "page_title": "Church Settings",
    })


