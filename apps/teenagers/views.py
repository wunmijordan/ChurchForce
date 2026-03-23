from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from permissions.services.resolver import PermissionResolver
from units.models import ChurchUnit
from accounts.models import ChurchMember
from teenagers.forms import TeenProfileForm, TrendPulseForm, MentorAssignmentForm
from teenagers.models import TeenProfile, TrendPulse, MentorAssignment


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
    return bool(settings and settings.enable_teenagers_module)


@login_required
def index(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Teenagers module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "teenagers.view"):
        return HttpResponseForbidden("You do not have access to the teenagers module.")

    teens = TeenProfile.objects.filter(church=church, is_active=True)
    pulses = TrendPulse.objects.filter(church=church, is_active=True)[:5]
    assignments = MentorAssignment.objects.filter(church=church, is_active=True)[:5]

    return render(request, "teenagers/index.html", {
        "page_title": "Teenagers",
        "module_name": "Pulse",
        "focus": "Peer Connection, Mentorship, and Trends",
        "teens": teens,
        "pulses": pulses,
        "assignments": assignments,
    })


@login_required
def teen_create(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Teenagers module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "teenagers.manage"):
        return HttpResponseForbidden("You do not have access to manage teenagers.")

    if request.method == "POST":
        form = TeenProfileForm(request.POST, church=church)
        if form.is_valid():
            teen = form.save(commit=False)
            teen.church = church
            teen.save()
            messages.success(request, "Teen profile created.")
            return redirect("teenagers:index")
        messages.error(request, "Please correct the errors below.")
    else:
        form = TeenProfileForm(church=church)


    return render(request, "teenagers/teen_form.html", {
        "form": form,
        "page_title": "Add Teen",
    })


@login_required
def pulse_create(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Teenagers module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "teenagers.manage"):
        return HttpResponseForbidden("You do not have access to manage trends.")

    if request.method == "POST":
        form = TrendPulseForm(request.POST)
        if form.is_valid():
            pulse = form.save(commit=False)
            pulse.church = church
            pulse.reported_by = getattr(request, "member", None)
            pulse.save()
            messages.success(request, "Trend pulse added.")
            return redirect("teenagers:index")
        messages.error(request, "Please correct the errors below.")
    else:
        form = TrendPulseForm()

    return render(request, "teenagers/pulse_form.html", {
        "form": form,
        "page_title": "Add Trend Pulse",
    })



@login_required
def mentor_create(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Teenagers module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "teenagers.manage"):
        return HttpResponseForbidden("You do not have access to manage mentors.")

    if request.method == "POST":
        form = MentorAssignmentForm(request.POST, church=church)
        if form.is_valid():
            assignment = form.save(commit=False)
            assignment.church = church
            assignment.save()
            messages.success(request, "Mentor assignment created.")
            return redirect("teenagers:index")
        messages.error(request, "Please correct the errors below.")
    else:
        form = MentorAssignmentForm(church=church)

    return render(request, "teenagers/mentor_form.html", {
        "form": form,
        "page_title": "Assign Mentor",
    })



