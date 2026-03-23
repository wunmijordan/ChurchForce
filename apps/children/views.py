from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render, get_object_or_404
from django.utils import timezone

from permissions.services.resolver import PermissionResolver
from units.models import ChurchUnit
from children.forms import ChildProfileForm, CheckInSessionForm
from children.models import ChildProfile, CheckInSession, CheckInRecord


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
    return bool(settings and settings.enable_children_module)


@login_required
def index(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Children module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "children.view"):
        return HttpResponseForbidden("You do not have access to the children module.")

    children = ChildProfile.objects.filter(church=church, is_active=True)
    sessions = CheckInSession.objects.filter(church=church, is_active=True)[:5]

    return render(request, "children/index.html", {
        "page_title": "Children",
        "module_name": "Discovery",
        "focus": "Security, Parent Communication, and Fun",
        "children": children,
        "sessions": sessions,
    })


@login_required
def child_create(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Children module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "children.manage"):
        return HttpResponseForbidden("You do not have access to manage children.")

    if request.method == "POST":
        form = ChildProfileForm(request.POST, church=church)
        if form.is_valid():
            child = form.save(commit=False)
            child.church = church
            child.save()
            messages.success(request, "Child profile created.")
            return redirect("children:index")
        messages.error(request, "Please correct the errors below.")
    else:
        form = ChildProfileForm(church=church)

    form.fields["unit"].queryset = ChurchUnit.raw_objects.filter(church=church, is_active=True)

    return render(request, "children/child_form.html", {
        "form": form,
        "page_title": "Add Child",
    })


@login_required
def session_create(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Children module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "children.manage"):
        return HttpResponseForbidden("You do not have access to manage sessions.")

    if request.method == "POST":
        form = CheckInSessionForm(request.POST, church=church)
        if form.is_valid():
            session = form.save(commit=False)
            session.church = church
            session.created_by = getattr(request, "member", None)
            session.save()
            messages.success(request, "Check-in session created.")
            return redirect("children:index")
        messages.error(request, "Please correct the errors below.")
    else:
        form = CheckInSessionForm(church=church)

    form.fields["unit"].queryset = ChurchUnit.raw_objects.filter(church=church, is_active=True)

    return render(request, "children/session_form.html", {
        "form": form,
        "page_title": "Create Check-In Session",
    })


@login_required
def session_detail(request, session_id):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Children module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "children.view"):
        return HttpResponseForbidden("You do not have access to view sessions.")

    session = get_object_or_404(
        CheckInSession.raw_objects.filter(church=church, is_active=True),
        id=session_id,
    )

    children = ChildProfile.objects.filter(church=church, is_active=True).order_by("last_name", "first_name")
    records = {
        rec.child_id: rec for rec in CheckInRecord.raw_objects.filter(
            church=church, session=session
        )
    }

    return render(request, "children/session_detail.html", {
        "session": session,
        "children": children,
        "records": records,
        "page_title": f"Check-In: {session.title}",
    })


@login_required
def update_checkin(request, session_id):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not _module_enabled(church):
        messages.error(request, "Children module is disabled in settings.")
        return redirect("dashboard")

    if not _can(request, "children.manage"):
        return HttpResponseForbidden("You do not have access to manage check-ins.")

    session = get_object_or_404(
        CheckInSession.raw_objects.filter(church=church, is_active=True),
        id=session_id,
    )
    child_id = request.POST.get("child_id")
    action = request.POST.get("action")

    if not child_id or action not in ("check_in", "check_out"):
        messages.error(request, "Invalid check-in request.")
        return redirect("children:session_detail", session_id=session.id)

    child = get_object_or_404(
        ChildProfile.raw_objects.filter(church=church, is_active=True),
        id=child_id,
    )

    record, _ = CheckInRecord.raw_objects.get_or_create(
        church=church,
        session=session,
        child=child,
        defaults={"status": "checked_in", "checked_in_by": getattr(request, "member", None)},
    )

    now = timezone.now()
    if action == "check_in":
        record.status = "checked_in"
        record.checked_in_at = now
        record.checked_in_by = getattr(request, "member", None)
    else:
        record.status = "checked_out"
        record.checked_out_at = now
        record.checked_out_by = getattr(request, "member", None)

    record.save()
    messages.success(request, f"{child} updated.")
    return redirect("children:session_detail", session_id=session.id)
