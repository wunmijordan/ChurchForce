from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from permissions.services.resolver import PermissionResolver
from youth.forms import YouthProfileForm, ImpactProjectForm, CareerGoalForm
from youth.models import YouthProfile, ImpactProject, CareerGoal


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
    return bool(settings and settings.enable_youth_module)


@login_required
def index(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Youth module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "youth.view"):
        return HttpResponseForbidden("You do not have access to the youth module.")

    youth_members = YouthProfile.objects.filter(church=church, is_active=True)
    projects = ImpactProject.objects.filter(church=church, is_active=True)[:5]
    goals = CareerGoal.objects.filter(church=church, is_active=True)[:5]

    return render(request, "youth/index.html", {
        "page_title": "Youth",
        "module_name": "Impact",
        "focus": "Networking, Career, and Social Action",
        "youth_members": youth_members,
        "projects": projects,
        "goals": goals,
    })


@login_required
def youth_create(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Youth module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "youth.manage"):
        return HttpResponseForbidden("You do not have access to manage youth.")

    if request.method == "POST":
        form = YouthProfileForm(request.POST, church=church)
        if form.is_valid():
            youth_member = form.save(commit=False)
            youth_member.church = church
            youth_member.save()
            messages.success(request, "Youth profile created.")
            return redirect("youth:index")
        messages.error(request, "Please correct the errors below.")
    else:
        form = YouthProfileForm(church=church)


    return render(request, "youth/youth_form.html", {
        "form": form,
        "page_title": "Add Youth",
    })


@login_required
def project_create(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Youth module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "youth.manage"):
        return HttpResponseForbidden("You do not have access to manage projects.")

    if request.method == "POST":
        form = ImpactProjectForm(request.POST, church=church)
        if form.is_valid():
            project = form.save(commit=False)
            project.church = church
            project.save()
            messages.success(request, "Impact project created.")
            return redirect("youth:index")
        messages.error(request, "Please correct the errors below.")
    else:
        form = ImpactProjectForm(church=church)


    return render(request, "youth/project_form.html", {
        "form": form,
        "page_title": "Add Impact Project",
    })
@login_required
def goal_create(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Youth module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "youth.manage"):
        return HttpResponseForbidden("You do not have access to manage goals.")

    if request.method == "POST":
        form = CareerGoalForm(request.POST, church=church)
        if form.is_valid():
            goal = form.save(commit=False)
            goal.church = church
            goal.save()
            messages.success(request, "Career goal created.")
            return redirect("youth:index")
        messages.error(request, "Please correct the errors below.")
    else:
        form = CareerGoalForm(church=church)

    return render(request, "youth/goal_form.html", {
        "form": form,
        "page_title": "Add Career Goal",
    })









