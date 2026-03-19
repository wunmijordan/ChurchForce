"""
units/views.py

Views for unit/group management, member transfer, probation,
attendance tracking, and campus management.
"""

import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from units.models import (
    ChurchUnit, UnitMembership, UnitAttendanceRecord,
    Campus, CampusMembership,
)


def _is_admin(request):
    if request.user.is_superuser:
        return True
    perms = getattr(request, "permissions", None)
    return bool(perms and perms.can("dashboard.admin"))


def _can_manage_unit(request, unit):
    if _is_admin(request):
        return True
    member = getattr(request, "member", None)
    if not member:
        return False
    return UnitMembership.raw_objects.filter(
        church=request.church, unit=unit,
        workforce_member__member=member, is_unit_head=True, is_active=True,
    ).exists()


# ─────────────────────────────────────────────────────────────────────────────
# Unit / Group list & CRUD
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def unit_list(request):
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    if _is_admin(request):
        units  = ChurchUnit.raw_objects.filter(church=church, unit_type="unit",  is_active=True)
        groups = ChurchUnit.raw_objects.filter(church=church, unit_type="group", is_active=True)
    else:
        member = getattr(request, "member", None)
        if not member:
            return HttpResponseForbidden("No member context.")
        ids = UnitMembership.raw_objects.filter(
            church=church, workforce_member__member=member, is_active=True
        ).values_list("unit_id", flat=True)
        units  = ChurchUnit.raw_objects.filter(id__in=ids, unit_type="unit",  is_active=True)
        groups = ChurchUnit.raw_objects.filter(id__in=ids, unit_type="group", is_active=True)

    return render(request, "units/unit_list.html", {
        "units": units, "groups": groups,
        "is_admin": _is_admin(request), "page_title": "Units & Groups",
    })


@login_required
def unit_detail(request, unit_id):
    church = getattr(request, "church", None)
    unit   = get_object_or_404(ChurchUnit.raw_objects.filter(church=church, is_active=True), id=unit_id)

    member = getattr(request, "member", None)
    is_member = member and UnitMembership.raw_objects.filter(
        church=church, unit=unit, workforce_member__member=member, is_active=True
    ).exists()

    if not _is_admin(request) and not is_member:
        return HttpResponseForbidden("You are not a member of this unit.")

    memberships = UnitMembership.raw_objects.filter(
        church=church, unit=unit, is_active=True
    ).select_related(
        "workforce_member__member__user", "trainee_profile__member__user"
    ).order_by("-is_unit_head", "joined_at")

    from workforce.models import WorkforceMember
    available_members = WorkforceMember.raw_objects.filter(
        church=church, is_active=True
    ).exclude(
        unit_memberships__unit=unit, unit_memberships__is_active=True
    ).select_related("member__user")

    return render(request, "units/unit_detail.html", {
        "unit": unit, "memberships": memberships,
        "flagged": memberships.filter(for_review=True),
        "available_members": available_members,
        "can_manage": _can_manage_unit(request, unit),
        "page_title": unit.name,
    })


@login_required
def unit_create(request):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden("Admin access required.")

    if request.method == "POST":
        name        = request.POST.get("name", "").strip()
        unit_type   = request.POST.get("unit_type", "unit")
        description = request.POST.get("description", "").strip()
        color       = request.POST.get("color", "blue").strip()
        report_to_id = request.POST.get("report_to", "").strip()
        is_default  = request.POST.get("is_default") == "on"

        if not name:
            messages.error(request, "Name is required.")
        elif ChurchUnit.raw_objects.filter(church=church, slug=slugify(name)).exists():
            messages.error(request, "A unit with this name already exists.")
        else:
            report_to = ChurchUnit.raw_objects.filter(
                church=church, id=int(report_to_id)
            ).first() if report_to_id and report_to_id.isdigit() else None

            unit = ChurchUnit.raw_objects.create(
                church=church, name=name, unit_type=unit_type,
                description=description, color=color,
                report_to=report_to, is_default=is_default, is_active=True,
            )
            messages.success(request, f"'{unit.name}' created.")
            return redirect("units:detail", unit_id=unit.id)

    all_units = ChurchUnit.raw_objects.filter(church=church, is_active=True)
    return render(request, "units/unit_form.html", {
        "all_units": all_units, "page_title": "Create Unit / Group", "action": "create",
    })


@login_required
def unit_edit(request, unit_id):
    church = getattr(request, "church", None)
    unit   = get_object_or_404(ChurchUnit.raw_objects.filter(church=church, is_active=True), id=unit_id)

    if not _can_manage_unit(request, unit):
        return HttpResponseForbidden("Admin or unit head required.")

    if request.method == "POST":
        unit.name        = request.POST.get("name", unit.name).strip()
        unit.description = request.POST.get("description", unit.description).strip()
        unit.color       = request.POST.get("color", unit.color).strip()
        unit.is_default  = request.POST.get("is_default") == "on"
        rid = request.POST.get("report_to", "").strip()
        unit.report_to   = ChurchUnit.raw_objects.filter(church=church, id=int(rid)).first() \
                           if rid and rid.isdigit() else None
        unit.save()
        messages.success(request, "Unit updated.")
        return redirect("units:detail", unit_id=unit.id)

    all_units = ChurchUnit.raw_objects.filter(church=church, is_active=True).exclude(id=unit.id)
    return render(request, "units/unit_form.html", {
        "unit": unit, "all_units": all_units,
        "page_title": f"Edit {unit.name}", "action": "edit",
    })


@login_required
@require_POST
def unit_delete(request, unit_id):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()
    unit = get_object_or_404(ChurchUnit.raw_objects.filter(church=church, is_active=True), id=unit_id)
    unit.is_active = False
    unit.save(update_fields=["is_active"])
    messages.success(request, f"'{unit.name}' deactivated.")
    return redirect("units:list")


# ─────────────────────────────────────────────────────────────────────────────
# Member management
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def add_member_to_unit(request, unit_id):
    church = getattr(request, "church", None)
    unit   = get_object_or_404(ChurchUnit.raw_objects.filter(church=church, is_active=True), id=unit_id)
    if not _can_manage_unit(request, unit):
        return HttpResponseForbidden()

    from workforce.models import WorkforceMember
    wf_id = request.POST.get("workforce_member_id", "").strip()
    is_probation = request.POST.get("is_probation") == "on"

    wf = WorkforceMember.raw_objects.filter(
        church=church, id=int(wf_id)
    ).first() if wf_id and wf_id.isdigit() else None

    if not wf:
        messages.error(request, "Member not found.")
        return redirect("units:detail", unit_id=unit.id)

    m, created = UnitMembership.raw_objects.get_or_create(
        church=church, workforce_member=wf, unit=unit,
        defaults={"is_probation": is_probation, "is_active": True},
    )
    if not created:
        m.is_active = True
        m.is_probation = is_probation
        m.save(update_fields=["is_active", "is_probation"])

    messages.success(request, f"{wf} added to {unit.name}.")
    return redirect("units:detail", unit_id=unit.id)


@login_required
@require_POST
def remove_member_from_unit(request, unit_id, membership_id):
    church = getattr(request, "church", None)
    unit   = get_object_or_404(ChurchUnit.raw_objects.filter(church=church, is_active=True), id=unit_id)
    if not _can_manage_unit(request, unit):
        return HttpResponseForbidden()

    m = get_object_or_404(UnitMembership.raw_objects.filter(church=church, unit=unit), id=membership_id)
    m.is_active = False
    m.save(update_fields=["is_active"])
    messages.success(request, "Member removed.")
    return redirect("units:detail", unit_id=unit.id)


@login_required
@require_POST
def transfer_member(request):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()

    m_id     = request.POST.get("membership_id", "").strip()
    to_id    = request.POST.get("to_unit_id", "").strip()
    is_prob  = request.POST.get("is_probation") == "on"

    membership = get_object_or_404(UnitMembership.raw_objects.filter(church=church, is_active=True), id=m_id)
    to_unit    = get_object_or_404(ChurchUnit.raw_objects.filter(church=church, is_active=True), id=to_id)

    old_unit = membership.unit
    membership.is_active = False
    membership.save(update_fields=["is_active"])

    new_m, _ = UnitMembership.raw_objects.get_or_create(
        church=church, workforce_member=membership.workforce_member,
        trainee_profile=membership.trainee_profile, unit=to_unit,
        defaults={"is_probation": is_prob, "is_active": True},
    )
    new_m.is_active = True
    new_m.is_probation = is_prob
    new_m.save(update_fields=["is_active", "is_probation"])

    messages.success(request, f"Transferred from {old_unit} → {to_unit}.")
    return redirect(request.POST.get("next") or request.META.get("HTTP_REFERER") or "/")


@login_required
@require_POST
def set_unit_head(request, unit_id, membership_id):
    church = getattr(request, "church", None)
    unit   = get_object_or_404(ChurchUnit.raw_objects.filter(church=church, is_active=True), id=unit_id)
    if not _is_admin(request):
        return HttpResponseForbidden()

    UnitMembership.raw_objects.filter(church=church, unit=unit, is_unit_head=True).update(is_unit_head=False)
    m = get_object_or_404(UnitMembership.raw_objects.filter(church=church, unit=unit, is_active=True), id=membership_id)
    m.is_unit_head = True
    m.save(update_fields=["is_unit_head"])
    messages.success(request, "Unit head updated.")
    return redirect("units:detail", unit_id=unit.id)


# ─────────────────────────────────────────────────────────────────────────────
# Probation & review flags
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def set_probation(request, membership_id):
    church = getattr(request, "church", None)
    m = get_object_or_404(UnitMembership.raw_objects.filter(church=church, is_active=True), id=membership_id)
    if not _can_manage_unit(request, m.unit):
        return HttpResponseForbidden()
    m.is_probation = True
    m.save(update_fields=["is_probation"])
    messages.success(request, "Member placed on probation.")
    return redirect("units:detail", unit_id=m.unit.id)


@login_required
@require_POST
def clear_probation(request, membership_id):
    church = getattr(request, "church", None)
    m = get_object_or_404(UnitMembership.raw_objects.filter(church=church, is_active=True), id=membership_id)
    if not _can_manage_unit(request, m.unit):
        return HttpResponseForbidden()
    m.is_probation = False
    m.save(update_fields=["is_probation"])
    messages.success(request, "Probation cleared.")
    return redirect("units:detail", unit_id=m.unit.id)


@login_required
@require_POST
def clear_for_review(request, membership_id):
    church = getattr(request, "church", None)
    m = get_object_or_404(UnitMembership.raw_objects.filter(church=church, is_active=True), id=membership_id)
    if not _can_manage_unit(request, m.unit):
        return HttpResponseForbidden()
    m.for_review = False
    m.consecutive_absences = 0
    m.save(update_fields=["for_review", "consecutive_absences"])
    messages.success(request, "Review flag cleared.")
    return redirect("units:detail", unit_id=m.unit.id)


# ─────────────────────────────────────────────────────────────────────────────
# Unit attendance
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def mark_unit_attendance(request, unit_id):
    church = getattr(request, "church", None)
    unit   = get_object_or_404(ChurchUnit.raw_objects.filter(church=church, is_active=True), id=unit_id)
    if not _can_manage_unit(request, unit):
        return JsonResponse({"error": "Not allowed."}, status=403)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    session = data.get("session", "Meeting").strip()
    from datetime import date as dateclass
    from django.utils.dateparse import parse_date
    meeting_date = parse_date(data.get("date", "")) or dateclass.today()
    records  = data.get("records", [])
    marker   = getattr(request, "member", None)
    count    = 0

    for rec in records:
        m_id   = rec.get("membership_id")
        status = rec.get("status", "absent")
        if status not in ("present", "absent", "excused"):
            continue
        membership = UnitMembership.raw_objects.filter(
            church=church, unit=unit, id=m_id, is_active=True
        ).first()
        if not membership:
            continue
        UnitAttendanceRecord.raw_objects.update_or_create(
            church=church, membership=membership, unit=unit,
            date=meeting_date, session=session,
            defaults={"status": status, "marked_by": marker, "is_active": True},
        )
        count += 1

    return JsonResponse({"success": True, "marked": count})


# ─────────────────────────────────────────────────────────────────────────────
# Campus views
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def campus_list(request):
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden()

    from billing.services import can_add_campus, campus_limit as get_limit
    return render(request, "units/campus_list.html", {
        "campuses":   Campus.raw_objects.filter(church=church, is_active=True).order_by("name"),
        "can_add":    can_add_campus(church) and _is_admin(request),
        "limit":      get_limit(church),
        "page_title": "Campuses",
    })


@login_required
def campus_create(request):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()

    from billing.services import can_add_campus
    if not can_add_campus(church):
        messages.error(request, "Your plan does not allow additional campuses. Please upgrade.")
        return redirect("units:campus_list")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Campus name required.")
        else:
            rid = request.POST.get("report_to", "").strip()
            campus = Campus.raw_objects.create(
                church=church, name=name,
                growth_stage=request.POST.get("growth_stage", "").strip(),
                address=request.POST.get("address", "").strip(),
                report_to=Campus.raw_objects.filter(church=church, id=int(rid)).first()
                          if rid and rid.isdigit() else None,
                contributes_to_parent_metrics=request.POST.get("contributes") == "on",
                is_active=True,
            )
            messages.success(request, f"Campus '{campus.name}' created.")
            return redirect("units:campus_list")

    return render(request, "units/campus_form.html", {
        "parent_campuses": Campus.raw_objects.filter(church=church, is_active=True),
        "growth_choices":  Campus.GROWTH_STAGE_CHOICES,
        "page_title": "Add Campus", "action": "create",
    })


@login_required
@require_POST
def transfer_to_campus(request):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()

    from accounts.models import ChurchMember
    member = get_object_or_404(ChurchMember.raw_objects.filter(church=church, is_active=True),
                                id=request.POST.get("member_id", ""))
    campus = get_object_or_404(Campus.raw_objects.filter(church=church, is_active=True),
                                id=request.POST.get("campus_id", ""))
    is_primary = request.POST.get("is_primary") == "on"

    if is_primary:
        CampusMembership.raw_objects.filter(church=church, member=member, is_primary=True).update(is_primary=False)

    CampusMembership.raw_objects.update_or_create(
        church=church, member=member, campus=campus,
        defaults={"is_primary": is_primary, "is_active": True},
    )
    messages.success(request, f"{member} added to {campus.name}.")
    return redirect(request.POST.get("next") or request.META.get("HTTP_REFERER") or "/")


# ─────────────────────────────────────────────────────────────────────────────
# AJAX
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def ajax_unit_members(request, unit_id):
    church = getattr(request, "church", None)
    unit   = get_object_or_404(ChurchUnit.raw_objects.filter(church=church, is_active=True), id=unit_id)

    data = []
    for m in UnitMembership.raw_objects.filter(
        church=church, unit=unit, is_active=True
    ).select_related("workforce_member__member__user"):
        try:
            u = m.workforce_member.member.user
            data.append({
                "id": m.id, "member_id": m.workforce_member.member.id,
                "name": u.full_name or u.username,
                "is_head": m.is_unit_head, "is_probation": m.is_probation,
                "is_online": u.is_online,
            })
        except AttributeError:
            continue

    return JsonResponse({"members": data})