"""
services/views.py

Event management: create, edit, delete.
Also provides an AJAX endpoint to fetch modules for a given LMS course
(used by the event form to dynamically populate the modules_covered field).
"""

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from apps.permissions.services.resolver import PermissionResolver
from accounts.models import ChurchMember
from services.models import Event


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


def _can(request, permission, unit=None):
    if request.user.is_superuser:
        return True
    perms = _get_permissions(request)
    return bool(perms and perms.can(permission, unit=unit))


@login_required
def course_modules_json(request):
    """
    AJAX: return the modules for a given LMS course so the event form
    can dynamically populate the modules_covered checkbox list.

    GET /services/course-modules/?course_id=<id>
    Returns: {"modules": [{"id": 1, "title": "...", "order": 1}, ...]}
    """
    church = _get_church(request)
    if not church:
        return JsonResponse({"modules": []})

    course_id = request.GET.get("course_id", "")
    if not course_id or not str(course_id).isdigit():
        return JsonResponse({"modules": []})

    from lms.models import LMSCourse, LMSModule

    course = LMSCourse.raw_objects.filter(
        church=church, id=int(course_id), is_active=True
    ).first()
    if not course:
        return JsonResponse({"modules": []})

    modules = (
        LMSModule.raw_objects.filter(church=church, course=course, is_active=True)
        .order_by("order")
        .values("id", "title", "order")
    )
    return JsonResponse({"modules": list(modules)})


@login_required
def manage_events(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    if not _can(request, "events.manage_all"):
        return HttpResponseForbidden("You do not have permission to manage events.")

    from .forms import EventForm

    if request.method == "POST":
        form = EventForm(
            request.POST,
            request.FILES,
            user=request.user,
            church=church,
            permissions=request.permissions,
        )
        if form.is_valid():
            try:
                event = form.save(commit=False)
                event.church = church
                event.created_by = ChurchMember.raw_objects.filter(
                    church=church,
                    user=request.user,
                    is_active=True,
                ).first()
                # Unit: read from POST directly so it always persists
                unit_id = request.POST.get("unit", "")
                if unit_id and str(unit_id).isdigit():
                    from units.models import ChurchUnit
                    event.unit = ChurchUnit.raw_objects.filter(
                        church=church, id=int(unit_id)
                    ).first()
                else:
                    event.unit = None
                event.save()

                # modules_covered M2M — read raw IDs from POST so the gate
                # is bypassed even when the form field queryset is empty
                # (e.g. course changed after form was initialised).
                _save_modules_covered(request, event, church)

            except Exception as exc:
                return JsonResponse(
                    {
                        "status": "error",
                        "errors": {"__all__": [f"Event could not be saved: {exc}"]},
                    },
                    status=400,
                )

            from django.template.loader import render_to_string

            html = render_to_string("services/event_item.html", {"event": event})
            return JsonResponse({"status": "success", "html": html})

        return JsonResponse({"status": "error", "errors": form.errors}, status=400)

    form = EventForm(user=request.user, church=church, permissions=request.permissions)
    events = Event.raw_objects.filter(church=church, is_active=True).order_by("date")
    from lms.models import LMSCourse

    return render(
        request,
        "services/manage_events.html",
        {
            "form": form,
            "events": events,
            "lms_courses": LMSCourse.raw_objects.filter(
                church=church, is_active=True
            ).order_by("title"),
            "page_title": "Programmes",
        },
    )


@login_required
def edit_event(request, uid):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    if not _can(request, "events.manage_all"):
        return HttpResponseForbidden("You do not have permission to manage events.")

    event = get_object_or_404(Event, uid=uid, church=church)

    from .forms import EventForm

    if request.method == "POST":
        form = EventForm(
            request.POST,
            request.FILES,
            instance=event,
            user=request.user,
            church=church,
            permissions=request.permissions,
        )
        if form.is_valid():
            try:
                event = form.save(commit=False)

                if event.postponed:
                    if not event.date and not event.time:
                        event.date = None
                        event.end_date = None
                        event.time = None

                # Unit: read from POST directly so changes always persist
                unit_id = request.POST.get("unit", "")
                if unit_id and str(unit_id).isdigit():
                    from units.models import ChurchUnit
                    event.unit = ChurchUnit.raw_objects.filter(
                        church=church, id=int(unit_id)
                    ).first()
                else:
                    event.unit = None
                event.save()

                # modules_covered M2M — bypass form field queryset limitations
                # by reading raw POST IDs and setting directly on the event.
                _save_modules_covered(request, event, church)

            except Exception as exc:
                return JsonResponse(
                    {
                        "status": "error",
                        "errors": {"__all__": [f"Event could not be saved: {exc}"]},
                    },
                    status=400,
                )

            from django.template.loader import render_to_string

            html = render_to_string("services/event_item.html", {"event": event})
            return JsonResponse({"status": "success", "html": html})

        return JsonResponse({"status": "error", "errors": form.errors}, status=400)

    return JsonResponse({"status": "invalid"}, status=405)


@login_required
def delete_event(request, uid):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    if not _can(request, "events.manage_all"):
        return HttpResponseForbidden("You do not have permission to manage events.")

    event = get_object_or_404(Event, uid=uid, church=church)
    event.delete()
    return JsonResponse({"status": "deleted"})


def _save_modules_covered(request, event, church):
    """
    Read modules_covered IDs from raw POST data and set them on the event.
    This bypasses the form field's queryset restriction (which may be empty
    if the course was changed after the form was initialised), ensuring that
    the M2M always persists whatever the user checked.

    Also syncs LMSSession.modules_covered so the attendance gate is
    immediately consistent with the event configuration.
    """
    from lms.models import LMSModule

    raw_ids = [
        v for v in request.POST.getlist("modules_covered")
        if v and v.isdigit()
    ]
    if event.event_type == "Training" and event.lms_course_id and raw_ids:
        valid = LMSModule.raw_objects.filter(
            church=church,
            course_id=event.lms_course_id,
            id__in=[int(i) for i in raw_ids],
            is_active=True,
        )
        event.modules_covered.set(valid)
    elif event.event_type == "Training":
        # No modules checked — clear to "blanket unlock" semantics
        event.modules_covered.clear()
    else:
        # Non-training event — no modules
        event.modules_covered.clear()

    # Keep LMSSession in sync
    _sync_session_modules(event)


def _sync_session_modules(event):
    """
    Push Event.modules_covered into the linked LMSSession.modules_covered
    so the attendance gate in lms/views.py course_detail and submit_module
    uses the same set without any separate configuration.
    """
    try:
        session = event.lms_session  # OneToOne reverse from LMSSession.event
        if session:
            session.modules_covered.set(event.modules_covered.all())
    except Exception:
        pass
