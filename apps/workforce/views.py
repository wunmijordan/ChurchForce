from datetime import date, datetime, timedelta
import json
import mimetypes

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from guests.models import GuestEntry
from units.models import ChurchUnit, UnitMembership
from services.models import Event
from permissions.services.resolver import PermissionResolver
from permissions.models import UnitRole
from .models import ChatMessage, AttendanceRecord, PersonalReminder, UserActivity
from core.utils.colors import resolve_color

def get_user_color(user_id):
    return resolve_color(f"user:{user_id}", variant="hex")
from .utils import (
    serialize_message,
    build_mention_helpers,
    expand_team_events,
    get_available_events_for_user,
    get_visible_attendance_records,
    get_visible_clock_records,
    get_link_preview,
    validate_church_proximity,
)
from .broadcast import broadcast_attendance_summary


# ----------------------------------------
# Helpers
# ----------------------------------------

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


def _membership_role_label(membership):
    role_names = [r.role.name for r in membership.roles.all()]
    if role_names:
        return ", ".join(role_names)
    return "Member"


def _membership_user(membership):
    if not membership:
        return None
    wf = membership.workforce_member
    if not wf or not wf.member:
        return None
    return wf.member.user


def _get_guest_unit_ids(church):
    if not church:
        return set()

    cache_key = f"guest_unit_ids:{church.id}"

    def _build():
        unit_ids = set()
        roles = UnitRole.raw_objects.filter(church=church, is_active=True).select_related("unit")
        for role in roles:
            perms = role.permissions or {}
            for key, allowed in perms.items():
                if allowed and key.startswith("guests."):
                    unit_ids.add(role.unit_id)
                    break
        return list(unit_ids)

    return set(cache.get_or_set(cache_key, _build, 600))


def _get_guest_units(church):
    unit_ids = _get_guest_unit_ids(church)
    if not unit_ids:
        return ChurchUnit.raw_objects.none()
    return ChurchUnit.raw_objects.filter(id__in=unit_ids)


@login_required
def chat_room(request):
    """
    Central chatroom page. Query param `unit_id` selects a room.
    If no unit_id -> central room (everyone).
    """
    user = request.user
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    units_qs = (
        ChurchUnit.raw_objects.filter(church=church, is_active=True).order_by("name")
    )

    if _can(request, "chat.view_all"):
        units = list(units_qs)
    else:
        units = list(
            units_qs.filter(
                memberships__workforce_member__member__user=user
            ).distinct()
        )

    unit_id = request.GET.get("unit_id")
    guest_id = request.GET.get("guest_id")

    guest_units = _get_guest_units(church)
    if guest_id and not unit_id and guest_units.exists():
        unit_id = str(guest_units.first().id)

    selected_unit = None
    if unit_id and str(unit_id).isdigit():
        selected_unit = units_qs.filter(id=int(unit_id)).first()

    if selected_unit and not _can(request, "chat.view_all"):
        is_member = UnitMembership.raw_objects.filter(
            church=church,
            unit=selected_unit,
            workforce_member__member__user=user,
            is_active=True,
        ).exists()
        if not is_member:
            selected_unit = None

    memberships = (
        UnitMembership.raw_objects.filter(
            church=church,
            unit__in=units,
            is_active=True,
            workforce_member__is_active=True,
            workforce_member__member__is_active=True,
            workforce_member__member__user__is_active=True,
        )
        .select_related("unit", "workforce_member__member__user")
        .prefetch_related("roles__role")
    )

    members_by_unit = {}
    user_memberships = {}
    for membership in memberships:
        members_by_unit.setdefault(membership.unit_id, []).append(membership)
        member_user = _membership_user(membership)
        if member_user:
            user_memberships.setdefault(member_user.id, []).append(membership)

    role_by_user_id = {}
    if selected_unit:
        for membership in members_by_unit.get(selected_unit.id, []):
            member_user = _membership_user(membership)
            if member_user:
                role_by_user_id[member_user.id] = _membership_role_label(membership)

    for unit in units:
        enriched_users = []
        for membership in members_by_unit.get(unit.id, []):
            member_user = _membership_user(membership)
            if not member_user or member_user.is_superuser:
                continue

            last_msg_obj = (
                ChatMessage.raw_objects.filter(church=church, sender=membership)
                .only("message")
                .order_by("-created_at")
                .first()
            )
            last_msg = last_msg_obj.message if last_msg_obj and last_msg_obj.message else "No messages yet"

            is_leadership = any(r.role.is_leadership for r in membership.roles.all())
            enriched_users.append({
                "id": member_user.id,
                "member_id": membership.workforce_member.member.id,  # for private chat trigger
                "full_name": member_user.full_name,
                "username": member_user.username,
                "title": member_user.title,
                "phone_number": member_user.phone_number,
                "color": get_user_color(member_user.id),
                "color_class": get_user_color(member_user.id),       # alias for template
                "initials": member_user.initials,
                "image": member_user.image.url if member_user.image else None,
                "is_online": getattr(member_user, "is_online", False),
                "last_message": last_msg,
                "role": _membership_role_label(membership),
                "is_leadership": is_leadership,
            })

        enriched_users.sort(
            key=lambda u: (
                not u.get("is_leadership"),
                (u.get("full_name") or u.get("username") or "").lower(),
            )
        )

        unit.enriched_users = enriched_users

    mention_map, mention_regex = build_mention_helpers(church)

    message_qs = ChatMessage.raw_objects.filter(church=church).select_related(
        "sender", "guest_card", "parent__sender"
    )
    if selected_unit:
        message_qs = message_qs.filter(sender__unit=selected_unit)

    last_messages = message_qs.order_by("-created_at")[:50]
    last_messages_payload = [
        serialize_message(m, mention_map, mention_regex)
        for m in reversed(last_messages)
    ]

    attached_guest = None
    if guest_id and str(guest_id).isdigit():
        attached_guest = GuestEntry.raw_objects.filter(church=church, id=int(guest_id)).first()

    users = sorted(
        [m.workforce_member.member.user for m in memberships if _membership_user(m)],
        key=lambda u: ((u.full_name or u.username or "").lower()),
    )

    membership_ids = [m.id for m in memberships]
    assigned_map = {mid: [] for mid in membership_ids}
    for guest in GuestEntry.raw_objects.filter(church=church, assigned_to_id__in=membership_ids):
        assigned_map.setdefault(guest.assigned_to_id, []).append(guest)

    def _assigned_for_user(u):
        guests = []
        for m in user_memberships.get(u.id, []):
            guests.extend(assigned_map.get(m.id, []))
        return guests

    def _assignee_payload(membership):
        assignee = _membership_user(membership)
        if not assignee:
            return None
        return {
            "id": membership.id,
            "title": assignee.title,
            "full_name": assignee.full_name or assignee.username,
            "image": assignee.image.url if assignee.image else None,
            "unit_name": membership.unit.name if membership.unit else "",
        }

    def _user_payload(u):
        role_label = role_by_user_id.get(u.id, "")
        assigned = _assigned_for_user(u)
        last_msg_obj = (
            ChatMessage.raw_objects.filter(church=church)
            .filter(sender__workforce_member__member__user=u)
            .only("message")
            .order_by("-created_at")
            .first()
        )
        last_message = last_msg_obj.message if last_msg_obj and last_msg_obj.message else "No messages yet"
        return {
            "id": u.id,
            "full_name": u.full_name,
            "username": u.username,
            "title": u.title,
            "initials": u.initials,
            "phone_number": u.phone_number,
            "image": u.image.url if u.image else None,
            "last_message": last_message,
            "role": role_label,
            "color": get_user_color(u.id),
            "team_name": selected_unit.name if selected_unit else "",
            "is_online": u.is_online,
            "guests": [
                {
                    "id": g.id,
                    "name": g.full_name,
                    "custom_id": g.custom_id,
                    "image": g.picture.url if g.picture else None,
                    "title": g.title,
                    "date_of_visit": g.date_of_visit.strftime("%Y-%m-%d") if g.date_of_visit else "",
                    "assigned_user": _assignee_payload(g.assigned_to) if g.assigned_to else None,
                } for g in assigned
            ]
        }

    user_guests = [
        {
            "id": u.id,
            "name": u.full_name or u.username,
            "role": role_by_user_id.get(u.id, ""),
            "team_name": selected_unit.name if selected_unit else "",
            "guests": [
                {
                    "id": g.id,
                    "name": g.full_name,
                    "custom_id": g.custom_id,
                    "image": g.picture.url if g.picture else None,
                    "title": g.title,
                    "date_of_visit": g.date_of_visit.strftime("%Y-%m-%d") if g.date_of_visit else "",
                    "assigned": True,
                } for g in _assigned_for_user(u)
            ]
        } for u in users
    ]

    unassigned_guests = []
    if _can(request, "guests.manage_unassigned") or _can(request, "guests.view_all"):
        unassigned_qs = GuestEntry.raw_objects.filter(church=church, assigned_to__isnull=True)
        unassigned_guests = [
            {
                "id": g.id,
                "name": g.full_name,
                "custom_id": g.custom_id,
                "image": g.picture.url if g.picture else None,
                "title": g.title,
                "date_of_visit": g.date_of_visit.strftime("%Y-%m-%d") if g.date_of_visit else "",
                "assigned": False,
            } for g in unassigned_qs
        ]

    guest_unit_for_context = guest_units.first() if guest_units.exists() else None

    # Build ChatRoom list — template now routes by room_id not team_id
    from units.models import ChatRoom
    rooms_qs = ChatRoom.raw_objects.filter(
        church=church, is_active=True, unit__in=units
    ).select_related("unit").order_by("unit__name", "name")

    # Attach enriched_users per room (same data as unit, since rooms mirror units)
    for room in rooms_qs:
        room.enriched_users = getattr(room.unit, "enriched_users", [])
        room.color_class = getattr(room.unit, "color", "bg-dark")

    # Resolve current room from ?room_id= or fall back to selected unit's default room
    room_id_param = request.GET.get("room_id", "").strip()
    current_room = None
    if room_id_param and room_id_param.isdigit():
        current_room = rooms_qs.filter(id=int(room_id_param)).first()
    if not current_room and selected_unit:
        current_room = rooms_qs.filter(unit=selected_unit, is_default=True).first()
    if not current_room and rooms_qs.exists():
        current_room = rooms_qs.first()

    context = {
        "teams": units,              # kept for backward compat with JS
        "rooms": rooms_qs,           # new: ChatRoom-based list for template
        "current_room": current_room,
        "selected_team": selected_unit,
        "selected_unit_id": selected_unit.id if selected_unit else None,
        "magnet_team": guest_unit_for_context,
        "magnet_unit_id": guest_unit_for_context.id if guest_unit_for_context else None,
        "users": [
            _user_payload(u) for u in users
        ],
        "users_json": json.dumps([_user_payload(u) for u in users], cls=DjangoJSONEncoder),
        "user_guests_json": json.dumps(user_guests, cls=DjangoJSONEncoder),
        "unassigned_guests_json": json.dumps(unassigned_guests, cls=DjangoJSONEncoder),
        "last_messages_json": json.dumps(last_messages_payload, cls=DjangoJSONEncoder),
        "current_user_id": request.user.id,
        "current_user_role": role_by_user_id.get(request.user.id, ""),
        "attached_guest": {
            "id": attached_guest.id,
            "name": attached_guest.full_name,
            "custom_id": attached_guest.custom_id,
            "image": attached_guest.picture.url if attached_guest.picture else None,
            "title": attached_guest.title,
            "initials": attached_guest.initials,
            "date_of_visit": attached_guest.date_of_visit.strftime("%Y-%m-%d") if attached_guest.date_of_visit else "",
            "assigned": bool(attached_guest.assigned_to),
        } if attached_guest else None,
        # context_user_permissions kept for JS but no role strings — only capability booleans
        "context_user_permissions": json.dumps({
            "chat_view_all":            _can(request, "chat.view_all"),
            "guests_view_all":          _can(request, "guests.view_all"),
            "guests_manage_unassigned": _can(request, "guests.manage_unassigned"),
            "events_manage_all":        _can(request, "events.manage_all"),
            "attendance_view_all":      _can(request, "attendance.view_all"),
        }, cls=DjangoJSONEncoder),
        "selected_team_id": current_room.id if current_room else None,  # JS compat
        "page_title": current_room.name if current_room else "Chat",
        # rooms_json: serialised list for window.CHAT_ROOMS (forward picker)
        "rooms_json": json.dumps(
            [{"id": r.id, "name": r.name} for r in rooms_qs],
            cls=DjangoJSONEncoder,
        ),
    }

    return render(request, "workforce/chat_room.html", context)

@login_required
def load_more_messages(request):
    """AJAX endpoint to fetch older messages before a given timestamp, scoped by unit."""
    church = _get_church(request)
    if not church:
        return JsonResponse({"messages": []})

    before = request.GET.get("before")
    limit = int(request.GET.get("limit", 50))
    unit_id = request.GET.get("unit_id")

    qs = ChatMessage.raw_objects.filter(church=church).select_related(
        "sender", "guest_card", "parent__sender"
    )

    if unit_id and str(unit_id).isdigit():
        qs = qs.filter(sender__unit_id=int(unit_id))

    if before:
        before_dt = parse_datetime(before)
        if before_dt:
            qs = qs.filter(created_at__lt=before_dt)

    mention_map, mention_regex = build_mention_helpers(church)

    messages = list(qs.order_by("-created_at")[:limit])
    messages.reverse()

    payload = [serialize_message(m, mention_map, mention_regex) for m in messages]
    return JsonResponse({"messages": payload})


@csrf_exempt
def upload_file(request):
    """Upload a file (Cloudinary or local) and return full metadata."""
    if request.method != "POST" or "file" not in request.FILES:
        return JsonResponse({"error": "Invalid request"}, status=400)

    f = request.FILES["file"]

    try:
        if not settings.DEBUG:
            import cloudinary.uploader
            result = cloudinary.uploader.upload(
                f,
                folder="chat/files",
                resource_type="auto",
            )

            file_url = result["secure_url"]
            file_path = result["public_id"]
            original_name = result.get("original_filename") or f.name

        else:
            from django.core.files.storage import default_storage

            file_path = default_storage.save(f"chat/files/{f.name}", f)
            file_path = file_path.lstrip("/")

            if file_path.startswith("media/"):
                file_path = file_path[len("media/"):]

            file_url = f"/media/{file_path}"
            original_name = f.name

        guessed_type, _ = mimetypes.guess_type(f.name)

        return JsonResponse({
            "url": file_url,
            "path": file_path,
            "public_id": file_path,
            "name": original_name,
            "size": f.size,
            "type": guessed_type or f.content_type or "application/octet-stream",
        })

    except Exception as e:
        import logging
        logging.exception("File upload failed")
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def fetch_link_preview(request):
    url = request.GET.get("url")
    if not url:
        return JsonResponse({"error": "Missing URL"}, status=400)

    return JsonResponse(get_link_preview(url))


@login_required
def attendance_summary(request):
    """Admin unit dashboard attendance & clock summary view."""
    if not _can(request, "attendance.view_all"):
        return HttpResponseForbidden("You do not have permission to view attendance summary.")

    user = request.user
    today = timezone.localdate()
    last_30_days = today - timedelta(days=30)

    records = get_visible_attendance_records(user, since_date=last_30_days)
    clock_records = get_visible_clock_records(user, since_date=last_30_days)

    clock_map = {(c.user_id, c.event_id, c.date): c for c in clock_records}
    for r in records:
        clock = clock_map.get((r.user_id, r.event_id, r.date))
        r.clock_in_time = clock.clock_in.strftime("%H:%M") if clock and clock.clock_in else None
        r.clock_out_time = clock.clock_out.strftime("%H:%M") if clock and clock.clock_out else None

    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.GET.get("format") == "json":
        data = [
            {
                "date": r.date.strftime("%Y-%m-%d"),
                "event": r.event.name if r.event else "-",
                "team": (r.event.unit.name if r.event and r.event.unit else r.team.name) if (r.event or r.team) else "-",
                "user": f"{(r.user.title or '')} {r.user.full_name}",
                "status": r.status,
                "remarks": r.remarks or "-",
                "clock_in": r.clock_in_time or "-",
                "clock_out": r.clock_out_time or "-",
            }
            for r in records
        ]
        return JsonResponse({"records": data})

    return render(request, "accounts/admin_dashboard.html", {"records": records})


@login_required
def mark_attendance(request):
    """Handles attendance marking. Supports AJAX for no page reload."""
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    today = timezone.localdate()
    now_time = timezone.localtime()
    weekday = today.strftime("%A").lower()

    events = get_available_events_for_user(request.user)
    available_events = []

    for e in events:
        if e.event_type and e.event_type.lower() == "followup":
            available_events.append(e)
        elif e.day_of_week and e.day_of_week.lower() == weekday:
            available_events.append(e)
        elif e.event_type and e.event_type.lower() == "meeting" and weekday == "wednesday":
            available_events.append(e)

    actual_events_today = [e for e in available_events if (e.event_type or "").lower() != "followup"]
    show_other_option = len(actual_events_today) > 0

    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    guest_activity_types = ["followup", "message", "guest_view", "call", "report", "other"]

    user_guest_activity = UserActivity.raw_objects.filter(church=church, 
        church=church,
        user=request.user,
        activity_type__in=guest_activity_types,
        created_at__date__gte=week_start,
        created_at__date__lte=week_end,
    ).exists()

    can_mark_now = any(
        e.time is None or now_time >= timezone.make_aware(datetime.combine(today, e.time))
        for e in actual_events_today
    )

    if request.method == "POST":
        is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

        event_id = request.POST.get("event_id", "").strip()
        remarks = request.POST.get("remarks", "").strip()
        status = request.POST.get("status", "present")
        user_lat = request.POST.get("latitude")
        user_lon = request.POST.get("longitude")
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"

        if not event_id:
            if is_ajax:
                return JsonResponse({"success": False, "error": "Missing event ID"}, status=400)
            messages.error(request, "Invalid or missing event ID.")
            return HttpResponseRedirect(next_url)

        if event_id == "other":
            custom_name = request.POST.get("custom_event") or "Custom Event"
            event, _ = Event.raw_objects.filter(church=church).get_or_create(church=church,
                name=custom_name,
                defaults={
                    "church": church,
                    "event_type": "Other",
                    "date": today,
                }
            )
        else:
            try:
                event = Event.raw_objects.filter(church=church).get(id=int(event_id))
            except (Event.DoesNotExist, ValueError):
                if is_ajax:
                    return JsonResponse({"success": False, "error": "Event not found"}, status=404)
                messages.error(request, "Event not found.")
                return HttpResponseRedirect(next_url)

        mode = (getattr(event, "attendance_mode", "") or "").lower()
        is_physical = mode == "physical"

        if status == "present" and is_physical:
            try:
                validate_church_proximity(user_lat, user_lon, church_lat=float(church.latitude), church_lon=float(church.longitude), threshold_km=float(church.attendance_radius_km))
            except Exception as e:
                if is_ajax:
                    return JsonResponse({"success": False, "error": str(e)}, status=400)
                messages.error(request, str(e))
                return HttpResponseRedirect(next_url)

        if status == "present" and event.time:
            event_dt = datetime.combine(today, event.time)
            if now_time > timezone.make_aware(event_dt + timedelta(minutes=15)):
                status = "late"

        existing = AttendanceRecord.raw_objects.filter(church=church, 
            church=church,
            user=request.user,
            event=event,
            date=today,
        ).first()

        if existing:
            if is_ajax:
                return JsonResponse({
                    "success": False,
                    "error": f"You have already marked attendance for {event.name} today.",
                }, status=400)
            messages.warning(request, f"You have already marked attendance for {event.name} today.")
            return HttpResponseRedirect(next_url)

        AttendanceRecord.raw_objects.create(
            church=church,
            user=request.user,
            event=event,
            date=today,
            status=status,
            remarks=remarks,
            team=event.unit,
        )

        broadcast_attendance_summary(church)

        message_text = f"Attendance marked for {event.name} ({status})."
        if is_ajax:
            return JsonResponse({"success": True, "message": message_text})
        messages.success(request, message_text)
        return HttpResponseRedirect(next_url)

    context = {
        "events": available_events,
        "show_other_option": show_other_option,
        "skip_attendance": user_guest_activity,
        "can_mark_now": can_mark_now,
        "show_weekly_summary": today.weekday() == 5 and now_time.hour >= 20,
    }
    return render(request, "workforce/mark_attendance.html", context)


@login_required
def add_personal_reminder(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    if request.method == "POST":
        title = request.POST.get("title")
        description = request.POST.get("description")
        date_value = request.POST.get("date")
        time_value = request.POST.get("time") or None

        PersonalReminder.raw_objects.create(
            church=church,
            user=request.user,
            title=title,
            description=description,
            date=date_value,
            time=time_value,
        )
        messages.success(request, "Reminder added!")
        return redirect("accounts:admin-dashboard")

    return render(request, "workforce/add_reminder.html")


@login_required
def manage_events(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    if not _can(request, "events.manage_all"):
        return HttpResponseForbidden("You do not have permission to manage events.")

    from .forms import EventForm

    if request.method == "POST":
        form = EventForm(request.POST, request.FILES, user=request.user, church=church, permissions=request.permissions)
        if form.is_valid():
            event = form.save(commit=False)
            event.church = church
            event.created_by = request.user
            unit = form.cleaned_data.get("team") or form.cleaned_data.get("unit")
            event.unit = unit
            event.save()

            from django.template.loader import render_to_string
            html = render_to_string("workforce/partials/event_item.html", {"event": event})
            return JsonResponse({"status": "success", "html": html})

        return JsonResponse({"status": "error", "errors": form.errors}, status=400)

    form = EventForm(user=request.user, church=church, permissions=request.permissions)
    events = Event.raw_objects.filter(church=church, is_active=True).order_by("date")
    return render(
        request,
        "workforce/manage_events.html",
        {"form": form, "events": events, "page_title": "Programmes"},
    )


@login_required
def edit_event(request, pk):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    if not _can(request, "events.manage_all"):
        return HttpResponseForbidden("You do not have permission to manage events.")

    event = get_object_or_404(Event, pk=pk, church=church)

    from .forms import EventForm

    if request.method == "POST":
        form = EventForm(request.POST, request.FILES, instance=event, user=request.user, church=church, permissions=request.permissions)
        if form.is_valid():
            event = form.save(commit=False)

            if event.postponed:
                if not event.date and not event.time:
                    event.date = None
                    event.end_date = None
                    event.time = None

            unit = form.cleaned_data.get("team") or form.cleaned_data.get("unit")
            event.unit = unit
            event.save()

            from django.template.loader import render_to_string
            html = render_to_string("workforce/partials/event_item.html", {"event": event})
            return JsonResponse({"status": "success", "html": html})

        return JsonResponse({"status": "error", "errors": form.errors}, status=400)

    return JsonResponse({"status": "invalid"}, status=405)


@login_required
def delete_event(request, pk):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    if not _can(request, "events.manage_all"):
        return HttpResponseForbidden("You do not have permission to manage events.")

    event = get_object_or_404(Event, pk=pk, church=church)
    event.delete()
    return JsonResponse({"status": "deleted"})


@login_required
def api_events(request):
    """
    Unified API endpoint:
      - FullCalendar (with start/end params)
      - Unit modal (with unit_id)
    """
    user = request.user
    unit_id = request.GET.get("unit_id")

    events = get_available_events_for_user(user)

    if unit_id is not None:
        data = expand_team_events(user, unit_id)
        return JsonResponse({"events": data})

    weekday_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    }

    color_map = {
        "service": "#3b82f6",
        "meeting": "#10b981",
        "training": "#8b5cf6",
        "reminder": "#f59e0b",
        "event": "#e11d48",
        "other": "#9ca3af",
    }

    today = date.today()
    start_of_year = date(today.year, 1, 1)
    end_of_year = date(today.year, 12, 31)

    data = []

    def build_datetime(d, t):
        if not t:
            return d.isoformat()
        return datetime.combine(d, t).isoformat()

    for e in events:
        event_type = (e.event_type or "other").lower()
        color = color_map.get(event_type, "#4dabf7")

        base_event = {
            "title": e.name,
            "color": color,
            "extendedProps": {
                "type": e.event_type,
                "mode": getattr(e, "attendance_mode", ""),
                "location": getattr(e, "location", ""),
                "description": getattr(e, "description", ""),
                "time": e.time.strftime("%H:%M") if e.time else None,
                "team": getattr(e.unit, "name", None),
                "team_color": getattr(e.unit, "color_class", "bg-gray-800") if getattr(e, "unit", None) else None,
            },
        }

        if e.date:
            start_date = e.date
            end_date = e.end_date or (
                start_date + timedelta(days=getattr(e, "duration_days", 1) - 1)
            )
            base_event["start"] = build_datetime(start_date, e.time)
            base_event["end"] = build_datetime(end_date, e.time)
            data.append(base_event)

        elif e.is_recurring_weekly and e.day_of_week:
            weekday = weekday_map.get(e.day_of_week.lower())
            if weekday is not None:
                current = start_of_year
                while current <= end_of_year:
                    if current.weekday() == weekday:
                        recurring = base_event.copy()
                        recurring["start"] = build_datetime(current, e.time)
                        data.append(recurring)
                    current += timedelta(days=1)

    return JsonResponse(data, safe=False)


@csrf_exempt
@login_required
def log_user_activity(request):
    """Silent logging endpoint for guest/follow-up interactions."""
    church = _get_church(request)
    if not church:
        return JsonResponse({"status": "error", "message": "No church context"}, status=403)

    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            activity_type = data.get("activity_type", "other")
            guest_id = data.get("guest_id")
            description = data.get("description", "")

            UserActivity.raw_objects.create(
                church=church,
                user=request.user,
                activity_type=activity_type,
                guest_id=guest_id,
                description=description,
                created_at=timezone.now(),
            )

            return JsonResponse({"status": "success"}, status=201)
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)

    return JsonResponse({"status": "invalid_request"}, status=405)


@login_required
def get_active_events(request):
    """
    Returns active events - both one-time (with date) and recurring (without date).
    """
    events = get_available_events_for_user(request.user)
    data = []

    for e in events:
        if e.date:
            date_str = e.date.strftime("%Y-%m-%d")
            time_str = e.time.strftime("%H:%M") if e.time else None
            label = e.name
        else:
            date_str = None
            time_str = e.time.strftime("%H:%M") if e.time else None
            label = f"{e.name} (Recurring)"

        data.append({
            "id": e.id,
            "name": label,
            "date": date_str,
            "time": time_str,
            "event_type": e.event_type,
            "is_recurring": not bool(e.date),
        })

    return JsonResponse(data, safe=False)