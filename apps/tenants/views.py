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
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.views.decorators.http import require_http_methods

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
    Tabbed church settings page.
    Each tab POSTs with a hidden `tab` field identifying which form to process.

    Tabs:
        general     — Church profile, branding, geo
        pipeline    — Guest committed threshold, SMS templates
        lms         — LMS & onboarding rules
        workforce   — Stages, roles, feature flags
        display     — ID prefixes, date format
        lookups     — VisitChannel, VisitPurpose, ChurchService inline CRUD
    """
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    perms    = getattr(request, "permissions", None)
    is_admin = request.user.is_superuser or (perms and perms.can("dashboard.admin"))
    if not is_admin:
        messages.error(request, "You do not have permission to manage church settings.")
        return redirect("dashboard")

    from tenants.forms import (
        GeneralSettingsForm,
        GuestPipelineSettingsForm,
        LMSSettingsForm,
        WorkforceSettingsForm,
        DisplaySettingsForm,
    )
    from units.forms import UnitQuickCreateForm

    settings_obj, _ = ChurchSetting.objects.get_or_create(church=church)

    form_classes = {
        "general":   GeneralSettingsForm,
        "pipeline":  GuestPipelineSettingsForm,
        "lms":       LMSSettingsForm,
        "workforce": WorkforceSettingsForm,
        "display":   DisplaySettingsForm,
    }

    active_tab = "general"
    forms_map = {}

    if request.method == "POST":
        active_tab = request.POST.get("tab", "general")

        if active_tab == "unit_create":
            unit_form = UnitQuickCreateForm(request.POST, church=church, settings=settings_obj)
            if unit_form.is_valid():
                unit = unit_form.save()
                messages.success(request, f"Unit '{unit.name}' created.")
            else:
                messages.error(request, "Please correct the unit form errors.")
            return redirect("tenants:church_settings")

        FormClass = form_classes.get(active_tab)
        if FormClass:
            form = FormClass(
                request.POST, request.FILES,
                church=church, settings=settings_obj,
            )
            if form.is_valid():
                form.save()
                messages.success(request, "Settings saved.")
                # Module flags submitted from General tab with tab=workforce —
                # redirect back to General, not Workforce tab
                redirect_tab = request.POST.get("redirect_tab", active_tab)
                return redirect(f"{request.path}?tab={redirect_tab}")
            else:
                forms_map[active_tab] = form
                messages.error(request, "Please correct the errors below.")

    # Build all forms (bound only for the active tab on error, else unbound)
    for key, FormClass in form_classes.items():
        if key not in forms_map:
            forms_map[key] = FormClass(church=church, settings=settings_obj)

    unit_form = UnitQuickCreateForm(church=church, settings=settings_obj)

    # Lookup table data for the lookups tab
    from guests.models import VisitChannel, VisitPurpose, ChurchService
    lookup_context = {
        "visit_channels":  VisitChannel.raw_objects.filter(church=church).order_by("order"),
        "visit_purposes":  VisitPurpose.raw_objects.filter(church=church).order_by("order"),
        "church_services": ChurchService.raw_objects.filter(church=church).order_by("order"),
    }

    # WorkforceStage, WorkforceRole, UnitRole data for workforce tab
    from workforce.models import WorkforceStage, WorkforceRole
    from permissions.models import UnitRole
    from guests.models import MembershipTrack, MembershipTrackStep
    from lms.models import LMSCourse
    workforce_context = {
        "workforce_stages": WorkforceStage.raw_objects.filter(church=church).order_by("order"),
        "workforce_roles":  WorkforceRole.raw_objects.filter(church=church).order_by("order"),
        "unit_roles":       UnitRole.raw_objects.filter(church=church).order_by("order"),
    }
    lms_context = {
        "membership_tracks": MembershipTrack.raw_objects.filter(church=church, is_active=True),
        "induction_courses": LMSCourse.raw_objects.filter(church=church, course_type="induction", is_active=True),
    }

    # Build structured lookup_tables list for the template loop
    lookup_tables = [
        ("channel", "Visit Channels",   lookup_context["visit_channels"]),
        ("purpose", "Visit Purposes",   lookup_context["visit_purposes"]),
        ("service", "Church Services",  lookup_context["church_services"]),
    ]

    # All LMS courses (for LMS tab list)
    from lms.models import LMSCourse
    all_courses = LMSCourse.raw_objects.filter(church=church, is_active=True).order_by("course_type", "title")

    # Campus growth stages (for Display tab)
    campus_cfg     = settings_obj.campus_growth_stages or {}
    if isinstance(campus_cfg, list):
        # Migrate old list format to new dict format
        campus_stages_enabled = True
        campus_stages_list = [{"name": s, "description": "", "order": i+1} for i, s in enumerate(campus_cfg)]
    else:
        campus_stages_enabled = campus_cfg.get("enabled", True)
        campus_stages_list    = sorted(campus_cfg.get("stages", []), key=lambda x: x.get("order", 0))

    return render(request, "tenants/church_settings.html", {
        "forms": forms_map,
        "unit_form": unit_form,
        "active_tab": request.GET.get("tab", active_tab),
        "church": church,
        "settings_obj": settings_obj,
        "page_title": "Church Settings",
        "lookup_tables": lookup_tables,
        "all_courses": all_courses,
        "campus_stages_enabled": campus_stages_enabled,
        "campus_stages_list": campus_stages_list,
        **lookup_context,
        **workforce_context,
        **lms_context,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Lookup table AJAX CRUD
# Shared by VisitChannel, VisitPurpose, ChurchService
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_model(lookup_type):
    from guests.models import VisitChannel, VisitPurpose, ChurchService
    return {
        "channel": VisitChannel,
        "purpose": VisitPurpose,
        "service": ChurchService,
    }.get(lookup_type)


@require_http_methods(["POST"])
def lookup_add(request, lookup_type):
    """Add a new lookup item (channel / purpose / service)."""
    church = getattr(request, "church", None)
    perms  = getattr(request, "permissions", None)
    if not church or not (request.user.is_superuser or (perms and perms.can("dashboard.admin"))):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    Model = _lookup_model(lookup_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown lookup type"}, status=400)

    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)

    if Model.raw_objects.filter(church=church, name__iexact=name).exists():
        return JsonResponse({"ok": False, "error": f"'{name}' already exists"}, status=400)

    order = Model.raw_objects.filter(church=church).count()
    item = Model.raw_objects.create(church=church, name=name, order=order, is_active=True)
    return JsonResponse({"ok": True, "id": item.id, "name": item.name, "order": item.order})


@require_http_methods(["POST"])
def lookup_toggle(request, lookup_type, pk):
    """Toggle is_active on a lookup item."""
    church = getattr(request, "church", None)
    perms  = getattr(request, "permissions", None)
    if not church or not (request.user.is_superuser or (perms and perms.can("dashboard.admin"))):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    Model = _lookup_model(lookup_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown lookup type"}, status=400)

    item = get_object_or_404(Model.raw_objects, id=pk, church=church)
    item.is_active = not item.is_active
    item.save(update_fields=["is_active"])
    return JsonResponse({"ok": True, "is_active": item.is_active})


@require_http_methods(["POST"])
def lookup_reorder(request, lookup_type):
    """Reorder lookup items by accepting an ordered list of ids."""
    church = getattr(request, "church", None)
    perms  = getattr(request, "permissions", None)
    if not church or not (request.user.is_superuser or (perms and perms.can("dashboard.admin"))):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    Model = _lookup_model(lookup_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown lookup type"}, status=400)

    ids = request.POST.getlist("ids[]")
    try:
        ids = [int(i) for i in ids]
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid ids"}, status=400)

    items = {obj.id: obj for obj in Model.raw_objects.filter(church=church, id__in=ids)}
    for new_order, item_id in enumerate(ids):
        if item_id in items:
            items[item_id].order = new_order
            items[item_id].save(update_fields=["order"])

    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
def lookup_rename(request, lookup_type, pk):
    """Rename a lookup item."""
    church = getattr(request, "church", None)
    perms  = getattr(request, "permissions", None)
    if not church or not (request.user.is_superuser or (perms and perms.can("dashboard.admin"))):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    Model = _lookup_model(lookup_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown lookup type"}, status=400)

    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)

    item = get_object_or_404(Model.raw_objects, id=pk, church=church)
    item.name = name
    item.save(update_fields=["name"])
    return JsonResponse({"ok": True, "name": item.name})


def _wf_auth(request):
    church = getattr(request, "church", None)
    perms  = getattr(request, "permissions", None)
    is_admin = church and (request.user.is_superuser or (perms and perms.can("dashboard.admin")))
    return church, is_admin


@require_http_methods(["POST"])
def workforce_item_add(request, item_type):
    """Add WorkforceStage or WorkforceRole (inline, no popup)."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from workforce.models import WorkforceStage, WorkforceRole

    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)

    if item_type == "stage":
        if WorkforceStage.raw_objects.filter(church=church, name__iexact=name).exists():
            return JsonResponse({"ok": False, "error": f"Stage '{name}' already exists"}, status=400)
        # Insert after last non-locked stage
        last = WorkforceStage.raw_objects.filter(church=church).order_by("-order").first()
        order = (last.order + 1) if last else 1
        item = WorkforceStage.raw_objects.create(
            church=church, name=name, order=order, is_active=True
        )
        return JsonResponse({"ok": True, "id": item.id, "name": item.name,
                             "order": item.order, "is_locked": item.is_locked,
                             "is_default": item.is_default})

    elif item_type == "role":
        is_leadership = request.POST.get("is_leadership", "false").lower() == "true"
        if WorkforceRole.raw_objects.filter(church=church, name__iexact=name).exists():
            return JsonResponse({"ok": False, "error": f"Role '{name}' already exists"}, status=400)
        last = WorkforceRole.raw_objects.filter(church=church).order_by("-order").first()
        order = (last.order + 1) if last else 1
        item = WorkforceRole.raw_objects.create(
            church=church, name=name, order=order,
            is_leadership=is_leadership, is_active=True,
        )
        return JsonResponse({"ok": True, "id": item.id, "name": item.name,
                             "order": item.order, "is_leadership": item.is_leadership,
                             "is_locked": item.is_locked, "is_default": item.is_default})

    return JsonResponse({"ok": False, "error": "Unknown item type"}, status=400)


@require_http_methods(["POST"])
def workforce_item_rename(request, item_type, pk):
    """Rename a non-locked WorkforceStage or WorkforceRole."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from workforce.models import WorkforceStage, WorkforceRole
    Model = {"stage": WorkforceStage, "role": WorkforceRole}.get(item_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown type"}, status=400)

    item = get_object_or_404(Model.raw_objects, id=pk, church=church)
    if item.is_locked:
        return JsonResponse({"ok": False, "error": f"'{item.name}' is a system item and cannot be renamed."}, status=400)

    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)

    item.name = name
    item.save(update_fields=["name"])
    return JsonResponse({"ok": True, "name": item.name})


@require_http_methods(["POST"])
def workforce_item_reorder(request, item_type):
    """Swap order of two items. Expects ids[]=id1&ids[]=id2."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from workforce.models import WorkforceStage, WorkforceRole
    Model = {"stage": WorkforceStage, "role": WorkforceRole}.get(item_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown type"}, status=400)

    # Receive ordered list of all ids
    ids = request.POST.getlist("ids[]")
    try:
        ids = [int(i) for i in ids]
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid ids"}, status=400)

    items = {obj.id: obj for obj in Model.raw_objects.filter(church=church, id__in=ids)}
    for new_order, item_id in enumerate(ids):
        if item_id in items and not items[item_id].is_locked:
            items[item_id].order = new_order
            items[item_id].save(update_fields=["order"])

    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
def workforce_item_delete(request, item_type, pk):
    """Delete a non-locked, non-default WorkforceStage or WorkforceRole."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from workforce.models import WorkforceStage, WorkforceRole, WorkforceMember
    Model = {"stage": WorkforceStage, "role": WorkforceRole}.get(item_type)
    if not Model:
        return JsonResponse({"ok": False, "error": "Unknown type"}, status=400)

    item = get_object_or_404(Model.raw_objects, id=pk, church=church)
    if item.is_locked:
        return JsonResponse({"ok": False, "error": f"'{item.name}' is a system item and cannot be deleted."}, status=400)

    # Safety: don't delete if members are on this stage/role
    if item_type == "stage" and WorkforceMember.raw_objects.filter(church=church, stage=item).exists():
        return JsonResponse({"ok": False, "error": f"'{item.name}' has active members. Reassign them first."}, status=400)

    item.delete()
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
def workforce_role_toggle_leadership(request, pk):
    """Toggle is_leadership on a WorkforceRole."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from workforce.models import WorkforceRole
    role = get_object_or_404(WorkforceRole.raw_objects, id=pk, church=church)
    role.is_leadership = not role.is_leadership
    role.save(update_fields=["is_leadership"])
    return JsonResponse({"ok": True, "is_leadership": role.is_leadership})


@require_http_methods(["POST"])
def campus_stage_save(request):
    """Save the full campus_growth_stages config (enabled flag + ordered list)."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    settings_obj, _ = ChurchSetting.objects.get_or_create(church=church)
    enabled = request.POST.get("enabled", "true").lower() == "true"

    # stages is a JSON array sent from the frontend
    import json
    try:
        stages_raw = request.POST.get("stages", "[]")
        stages = json.loads(stages_raw)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid stages data"}, status=400)

    # Validate each entry
    clean_stages = []
    for i, s in enumerate(stages):
        name = (s.get("name") or "").strip()
        if not name:
            continue
        clean_stages.append({
            "name": name,
            "description": (s.get("description") or "").strip(),
            "order": i + 1,
        })

    settings_obj.campus_growth_stages = {"enabled": enabled, "stages": clean_stages}
    settings_obj.save(update_fields=["campus_growth_stages", "updated_at"])
    return JsonResponse({"ok": True, "count": len(clean_stages)})


@require_http_methods(["POST"])
def lms_course_create(request):
    """Quick-create an LMS course from the settings page."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from lms.models import LMSCourse
    from units.models import ChurchUnit

    title = request.POST.get("title", "").strip()
    if not title:
        return JsonResponse({"ok": False, "error": "Title is required"}, status=400)

    course_type  = request.POST.get("course_type", "unit_training")
    delivery     = request.POST.get("delivery_mode", "physical")
    passing      = int(request.POST.get("passing_score", 70))
    allow_retake = request.POST.get("allow_retake", "true").lower() == "true"
    max_retakes  = int(request.POST.get("max_retakes", 2))
    strict_seq   = request.POST.get("strict_sequence", "true").lower() == "true"
    unit_id      = request.POST.get("unit_id") or None

    member = getattr(request, "member", None)
    unit   = None
    if unit_id:
        unit = ChurchUnit.raw_objects.filter(id=unit_id, church=church).first()

    course = LMSCourse.raw_objects.create(
        church=church,
        title=title,
        course_type=course_type,
        delivery_mode=delivery,
        passing_score=passing,
        allow_retake=allow_retake,
        max_retakes=max_retakes,
        strict_sequence=strict_seq,
        unit=unit,
        created_by=member,
        is_active=True,
    )
    return JsonResponse({
        "ok": True, "id": course.id, "title": course.title,
        "course_type": course.get_course_type_display(),
        "delivery_mode": course.get_delivery_mode_display(),
        "passing_score": course.passing_score,
    })


@require_http_methods(["POST"])
def membership_track_save(request, pk=None):
    """Create or update a MembershipTrack."""
    church, is_admin = _wf_auth(request)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    from guests.models import MembershipTrack

    name        = request.POST.get("name", "").strip()
    description = request.POST.get("description", "").strip()
    threshold   = int(request.POST.get("attendance_threshold", 4))
    window      = int(request.POST.get("attendance_window_weeks", 6))
    is_default  = request.POST.get("is_default", "false").lower() == "true"

    if not name:
        return JsonResponse({"ok": False, "error": "Name is required"}, status=400)

    if pk:
        track = get_object_or_404(MembershipTrack.raw_objects, id=pk, church=church)
        track.name = name
        track.description = description
        track.attendance_threshold = threshold
        track.attendance_window_weeks = window
    else:
        if MembershipTrack.raw_objects.filter(church=church, name__iexact=name).exists():
            return JsonResponse({"ok": False, "error": f"Track '{name}' already exists"}, status=400)
        track = MembershipTrack.raw_objects.create(
            church=church, name=name, description=description,
            attendance_threshold=threshold, attendance_window_weeks=window,
            is_default=is_default, is_active=True,
        )

    if is_default:
        # Only one track can be default
        MembershipTrack.raw_objects.filter(church=church).exclude(pk=track.pk).update(is_default=False)
        track.is_default = True

    track.save()
    return JsonResponse({
        "ok": True, "id": track.id, "name": track.name,
        "is_default": track.is_default,
        "attendance_threshold": track.attendance_threshold,
        "attendance_window_weeks": track.attendance_window_weeks,
    })


