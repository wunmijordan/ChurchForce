from datetime import date, datetime, timedelta
import json
import mimetypes

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from guests.models import GuestEntry
from units.models import ChurchUnit, UnitMembership
from services.models import Event
from permissions.services.resolver import PermissionResolver
from tenants.time_utils import (
    format_time_value,
    get_effective_date_format,
    get_effective_time_format,
)
from .models import (
    ChatMessage,
    ChatMessageReaction,
    AttendanceRecord,
    ClockRecord,
    PersonalReminder,
    UserActivity,
    LeaderTask,
)
from workforce.models import ChatSettings
from accounts.models import ChurchMember
from .services.promotion_engine import attempt_final_promotion
from core.utils.colors import resolve_color


def get_user_color(user_id):
    return resolve_color(f"user:{user_id}", variant="hex")


from .utils import (
    serialize_message,
    build_mention_helpers,
    expand_unit_events,
    get_available_events_for_user,
    get_visible_attendance_records,
    get_visible_clock_records,
    get_link_preview,
    classify_event_location,
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


def _attendance_member_for_user(church, user):
    """
    Returns the ChurchMember record for the given user in the given church.
    This is the FK used by AttendanceRecord.user and ClockRecord.user.
    Works for trainees, workforce members, and admins alike.
    """
    from accounts.models import ChurchMember

    return ChurchMember.raw_objects.filter(
        church=church, user=user, is_active=True
    ).first()


def _membership_user(membership):
    if not membership:
        return None
    wf = membership.workforce_member
    if not wf or not wf.member:
        return None
    return wf.member.user


def _get_guest_unit_ids(request):
    church = getattr(request, "church", None)
    if not church:
        return set()

    from guests.views import _guest_enabled_unit_ids

    perms = _get_permissions(request)
    if not perms:
        return set()

    return _guest_enabled_unit_ids(church) & set(perms.unit_ids_for("guests.view"))


def _get_guest_units(request):
    unit_ids = _get_guest_unit_ids(request)
    if not unit_ids:
        return ChurchUnit.raw_objects.none()
    return ChurchUnit.raw_objects.filter(id__in=unit_ids)


@login_required
def member_settings(request):

    member = getattr(request, "member", None)
    church = getattr(request, "church", None)

    if not member or not church:
        return HttpResponseForbidden("No church context.")

    from accounts.forms import ProfileEditForm
    from notifications.models import UserSettings
    from notifications.forms import UserSettingsForm

    active_tab = request.GET.get("tab", "profile")

    # ───────────────── POST ─────────────────
    if request.method == "POST":

        active_tab = request.POST.get("tab", "profile")

        if active_tab == "profile":

            profile_form = ProfileEditForm(
                request.POST,
                request.FILES,
                instance=request.user,
                current_user=request.user,
                church=church,
            )

            if profile_form.is_valid():
                try:
                    profile_form.save()
                    messages.success(request, "Profile updated successfully.")
                    return redirect(f"{request.path}?tab=profile")
                except Exception as exc:
                    messages.error(
                        request,
                        "Profile update could not be completed right now. Please try again.",
                    )
                    if settings.DEBUG:
                        messages.error(request, str(exc))

            messages.error(request, "Please correct the errors below.")

        elif active_tab == "preference":

            settings_obj, _ = UserSettings.objects.get_or_create(
                member=member,
                church=church,
            )

            preference_form = UserSettingsForm(
                request.POST,
                instance=settings_obj,
            )

            if preference_form.is_valid():
                preference_form.save()
                messages.success(request, "Preferences saved.")
                return redirect(f"{request.path}?tab=preference")

            messages.error(request, "Please correct the errors below.")

    # ───────────────── GET / INITIAL ─────────────────

    profile_form = ProfileEditForm(
        instance=request.user,
        current_user=request.user,
        church=church,
    )

    settings_obj, _ = UserSettings.objects.get_or_create(
        member=member,
        church=church,
    )

    preference_form = UserSettingsForm(instance=settings_obj)

    context = {
        "forms": {
            "profile": profile_form,
            "preference": preference_form,
        },
        "active_tab": active_tab,
        "member": member,
    }

    return render(
        request,
        "workforce/member_settings.html",
        context,
    )


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

    units_qs = ChurchUnit.raw_objects.filter(church=church, is_active=True).order_by(
        "name"
    )

    if _can(request, "chat.view_all"):
        units = list(units_qs)
    else:
        units = list(
            units_qs.filter(memberships__workforce_member__member__user=user).distinct()
        )

    unit_id = request.GET.get("unit_id")
    guest_id = request.GET.get("guest_id")

    guest_units = _get_guest_units(request)
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

    # ── UnitMembership records (for per-unit views + role labels) ────────────
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

    # ── All active ChurchMembers — base population for the user strip ────────
    # We use ChurchMember (not UnitMembership) so every church member appears
    # in the strip regardless of whether they have been placed in a unit yet.
    from accounts.models import ChurchMember as _CM

    all_church_members = list(
        _CM.raw_objects.filter(church=church, is_active=True)
        .select_related("user")
        .exclude(user__is_superuser=True)
        .order_by("user__first_name", "user__last_name")
    )

    # Build a lookup: user.id → first matching UnitMembership for each unit
    uid_to_membership = {}
    for membership in memberships:
        mu = _membership_user(membership)
        if mu and mu.id not in uid_to_membership:
            uid_to_membership[mu.id] = membership

    # Enrich per-unit user lists (still uses UnitMembership for unit-scoped rooms)
    for unit in units:
        enriched_users = []
        for membership in members_by_unit.get(unit.id, []):
            member_user = _membership_user(membership)
            if not member_user or member_user.is_superuser:
                continue

            last_msg_obj = (
                ChatMessage.raw_objects.filter(
                    church=church,
                    sender__user=member_user,
                )
                .only("message")
                .order_by("-created_at")
                .first()
            )
            last_msg = (
                last_msg_obj.message
                if last_msg_obj and last_msg_obj.message
                else "No messages yet"
            )

            from permissions.registry import TIER_UNIT_HEAD, TIER_CUSTOM

            role_tiers = [
                getattr(r.role, "tier", TIER_CUSTOM) for r in membership.roles.all()
            ]
            min_tier = min(role_tiers) if role_tiers else TIER_CUSTOM
            is_leadership = min_tier <= TIER_UNIT_HEAD
            enriched_users.append(
                {
                    "id": member_user.id,
                    "member_id": membership.workforce_member.member.id,
                    "full_name": member_user.full_name,
                    "username": member_user.username,
                    "title": member_user.title,
                    "phone_number": member_user.phone_number,
                    "color": get_user_color(member_user.id),
                    "color_class": get_user_color(member_user.id),
                    "initials": member_user.initials,
                    "image": member_user.image.url if member_user.image else None,
                    "is_online": getattr(member_user, "is_online", False),
                    "last_message": last_msg,
                    "role": _membership_role_label(membership),
                    "is_leadership": is_leadership,
                }
            )

        enriched_users.sort(
            key=lambda u: (
                not u.get("is_leadership"),
                (u.get("full_name") or u.get("username") or "").lower(),
            )
        )

        unit.enriched_users = enriched_users

    # ── Build enriched list for the general / central strip (all church members) ──
    def _cm_to_strip(cm):
        u = cm.user
        m = uid_to_membership.get(u.id)
        return {
            "id": u.id,
            "member_id": cm.id,
            "full_name": u.full_name or u.username or "",
            "username": u.username or "",
            "title": getattr(u, "title", "") or "",
            "phone_number": getattr(u, "phone_number", "") or "",
            "color": get_user_color(u.id),
            "color_class": get_user_color(u.id),
            "initials": getattr(
                u, "initials", (u.full_name or u.username or "?")[0].upper()
            ),
            "image": u.image.url if getattr(u, "image", None) and u.image else None,
            "is_online": getattr(u, "is_online", False),
            "last_message": "No messages yet",
            "role": _membership_role_label(m) if m else "",
            "is_leadership": False,
        }

    all_enriched_members = [_cm_to_strip(cm) for cm in all_church_members]

    mention_map, mention_regex = build_mention_helpers(church)

    message_qs = (
        ChatMessage.raw_objects.filter(church=church)
        .select_related("sender", "guest_card", "parent__sender")
        .prefetch_related("reactions__member")
    )

    viewer_cm = ChurchMember.raw_objects.filter(
        church=church, user=request.user, is_active=True
    ).first()

    attached_guest = None
    if guest_id and str(guest_id).isdigit():
        attached_guest = GuestEntry.raw_objects.filter(
            church=church, id=int(guest_id)
        ).first()

    # Use all ChurchMembers as the base user list — covers members without unit assignments
    users = sorted(
        [
            cm.user
            for cm in all_church_members
            if cm.user and cm.user.is_active and not cm.user.is_superuser
        ],
        key=lambda u: ((u.full_name or u.username or "").lower()),
    )

    membership_ids = [m.id for m in memberships]
    assigned_map = {mid: [] for mid in membership_ids}
    for guest in GuestEntry.raw_objects.filter(
        church=church, assigned_to_id__in=membership_ids
    ):
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
            ChatMessage.raw_objects.filter(church=church, sender__user=u)
            .only("message")
            .order_by("-created_at")
            .first()
        )
        last_message = (
            last_msg_obj.message
            if last_msg_obj and last_msg_obj.message
            else "No messages yet"
        )
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
            "unit_name": selected_unit.name if selected_unit else "",
            "is_online": u.is_online,
            "guests": [
                {
                    "id": g.id,
                    "name": g.full_name,
                    "custom_id": g.custom_id,
                    "image": g.picture.url if g.picture else None,
                    "title": g.title,
                    "date_of_visit": (
                        g.date_of_visit.strftime("%Y-%m-%d") if g.date_of_visit else ""
                    ),
                    "assigned_user": (
                        _assignee_payload(g.assigned_to) if g.assigned_to else None
                    ),
                }
                for g in assigned
            ],
        }

    user_guests = [
        {
            "id": u.id,
            "name": u.full_name or u.username,
            "role": role_by_user_id.get(u.id, ""),
            "unit_name": selected_unit.name if selected_unit else "",
            "guests": [
                {
                    "id": g.id,
                    "name": g.full_name,
                    "custom_id": g.custom_id,
                    "image": g.picture.url if g.picture else None,
                    "title": g.title,
                    "date_of_visit": (
                        g.date_of_visit.strftime("%Y-%m-%d") if g.date_of_visit else ""
                    ),
                    "assigned": True,
                }
                for g in _assigned_for_user(u)
            ],
        }
        for u in users
    ]

    unassigned_guests = []
    if _can(request, "guests.manage_unassigned") or _can(request, "guests.view_all"):
        unassigned_qs = GuestEntry.raw_objects.filter(
            church=church, assigned_to__isnull=True
        )
        unassigned_guests = [
            {
                "id": g.id,
                "name": g.full_name,
                "custom_id": g.custom_id,
                "image": g.picture.url if g.picture else None,
                "title": g.title,
                "date_of_visit": (
                    g.date_of_visit.strftime("%Y-%m-%d") if g.date_of_visit else ""
                ),
                "assigned": False,
            }
            for g in unassigned_qs
        ]

    guest_unit_for_context = guest_units.first() if guest_units.exists() else None

    # Build ChatRoom list — template now routes by room_id not unit_id
    from units.models import ChatRoom

    # Only surface top-level rooms (default unit rooms + explicitly-default extras).
    # Sub-rooms (project rooms that were created under a parent) are intentionally
    # excluded here — they live exclusively inside the sub-room modal and are never
    # shown in the main room sidebar.  We identify sub-rooms as non-default project
    # rooms; a default=True flag means the room IS the primary room for its unit.
    rooms_qs = (
        ChatRoom.raw_objects.filter(
            church=church,
            is_active=True,
            unit__in=units,
        )
        .exclude(room_type="private")
        .exclude(is_default=False, room_type="project")
        .select_related("unit")
        .order_by("unit__name", "name")
    )

    # Build a unit_id → enriched_users lookup from the in-memory unit objects
    # (which already have .enriched_users set by the loop above).
    # We CANNOT use getattr(room.unit, "enriched_users", []) because room.unit
    # is a fresh ORM instance loaded by select_related — a different Python object
    # than the unit instances in `units`, so the dynamically-set attribute is gone.
    _unit_enriched_map = {unit.id: unit.enriched_users for unit in units}

    # Attach enriched_users per room via the id-keyed dict
    for room in rooms_qs:
        if room.unit:
            room.enriched_users = _unit_enriched_map.get(room.unit_id, [])
        else:
            room.enriched_users = all_enriched_members
        room.color_class = getattr(room.unit, "color", "bg-dark")
        room.has_guest_module = bool(room.unit and room.unit.guest_management)

    # Resolve current room from ?room_id= or fall back to selected unit's default room
    room_id_param = request.GET.get("room_id", "").strip()
    current_room = None
    if room_id_param and room_id_param.isdigit():
        current_room = rooms_qs.filter(id=int(room_id_param)).first()
    if not current_room and selected_unit:
        current_room = rooms_qs.filter(unit=selected_unit, is_default=True).first()

    if current_room:
        message_qs = message_qs.filter(room=current_room)
    elif selected_unit:
        message_qs = message_qs.filter(room__unit=selected_unit)
    else:
        # General room: only messages with no room (sent to central group)
        message_qs = message_qs.filter(room__isnull=True)
    last_messages = message_qs.order_by("-created_at")[:50]
    last_messages_payload = [
        serialize_message(m, mention_map, mention_regex, viewer_cm)
        for m in reversed(last_messages)
    ]

    # ── Determine if the current user can send messages ──────────────────────
    # Members WITHOUT any UnitMembership (global-tier / no unit assigned) and
    # trainees (induction-reason trainee profile) can read the General Workforce
    # room but cannot send messages there.  Inside a specific unit room they
    # can only send if they belong to that unit.
    from .models import WorkforceTraineeProfile as _WTP

    _user_unit_memberships = UnitMembership.raw_objects.filter(
        church=church,
        workforce_member__member__user=user,
        is_active=True,
    )
    _user_has_unit_membership = _user_unit_memberships.exists()
    _user_is_trainee_only = (
        not _user_has_unit_membership
        and _WTP.raw_objects.filter(
            church=church,
            member__user=user,
            reason="induction",
            is_active=True,
        ).exists()
    )

    # For a specific room: user can send only if they belong to that room's unit
    # if current_room and current_room.unit:
    #    _user_can_send_in_current_room = _user_unit_memberships.filter(
    #        unit=current_room.unit
    #    ).exists() or _can(request, "chat.view_all")
    # else:
    # General Workforce room: only members WITH a unit membership can send
    #    _user_can_send_in_current_room = _user_has_unit_membership or _can(
    #        request, "chat.view_all"
    #    )

    # Build per-room send permissions for JS room switching
    _room_send_permissions = {}
    for _r in rooms_qs:
        if _r.unit:
            _room_send_permissions[str(_r.id)] = _user_unit_memberships.filter(
                unit=_r.unit
            ).exists() or _can(request, "chat.view_all")
        else:
            _room_send_permissions[str(_r.id)] = _user_has_unit_membership or _can(
                request, "chat.view_all"
            )

    # Build per-room privileged roles (unit heads / admins who can create sub-rooms)
    _room_privileged = {}
    for _r in rooms_qs:
        if _r.unit:
            _room_privileged[str(_r.id)] = _user_unit_memberships.filter(
                unit=_r.unit, is_unit_head=True
            ).exists() or _can(request, "dashboard.admin")
        else:
            _room_privileged[str(_r.id)] = _can(request, "dashboard.admin")

    cs, _ = ChatSettings.raw_objects.get_or_create(church=church)

    # Build CHAT_DEFAULT_TIER_MEMBERS: members in selected tiers, excluding superusers.
    # Tier lives on WorkforceRole (via WorkforceMembershipRole → WorkforceMember).
    # user__role is a single FK on CustomUser pointing to WorkforceRole — Django does
    # not allow __tier__in through a ForeignKey traversal that way.
    # Correct path: ChurchMember → workforce_profiles (WorkforceMember)
    #               → roles (WorkforceMembershipRole) → role (WorkforceRole) → tier
    from permissions.registry import TIER_ADMIN

    default_tier_list = cs.default_tier_list or [1, 2, 3]
    tier_filter = Q(workforce_profiles__roles__role__tier__in=default_tier_list)
    if TIER_ADMIN in default_tier_list:
        tier_filter |= Q(is_admin=True)
    default_tier_members = (
        ChurchMember.raw_objects.filter(
            church=church,
            user__is_superuser=False,
        )
        .filter(tier_filter)
        .select_related("user")
        .distinct()
    )

    default_tier_strip_members = [_cm_to_strip(cm) for cm in default_tier_members]

    # Serialise the same way as all_members_json (uses the same _cm_to_strip helper)
    chat_default_tier_members_json = json.dumps(
        default_tier_strip_members,
        cls=DjangoJSONEncoder,
    )

    # Chat background
    chat_bg_url = (
        cs.bg_image.url
        if cs.bg_image and cs.bg_type == "upload"
        else (church.logo.url if church.logo and cs.bg_type == "logo" else "")
    )
    chat_bg_type = cs.bg_type

    context = {
        "units": units,  # kept for backward compat with JS
        "rooms": rooms_qs,  # new: ChatRoom-based list for template
        "current_room": current_room,
        "selected_unit": selected_unit,
        "selected_unit_id": selected_unit.id if selected_unit else None,
        "magnet_unit": guest_unit_for_context,
        "magnet_unit_id": guest_unit_for_context.id if guest_unit_for_context else None,
        "users": [_user_payload(u) for u in users],
        "users_json": json.dumps(
            [_user_payload(u) for u in users], cls=DjangoJSONEncoder
        ),
        "user_guests_json": json.dumps(user_guests, cls=DjangoJSONEncoder),
        "unassigned_guests_json": json.dumps(unassigned_guests, cls=DjangoJSONEncoder),
        "last_messages_json": json.dumps(last_messages_payload, cls=DjangoJSONEncoder),
        "current_user_id": request.user.id,
        "current_user_role": role_by_user_id.get(request.user.id, ""),
        "attached_guest": (
            {
                "id": attached_guest.id,
                "name": attached_guest.full_name,
                "custom_id": attached_guest.custom_id,
                "image": attached_guest.picture.url if attached_guest.picture else None,
                "title": attached_guest.title,
                "initials": attached_guest.initials,
                "date_of_visit": (
                    attached_guest.date_of_visit.strftime("%Y-%m-%d")
                    if attached_guest.date_of_visit
                    else ""
                ),
                "assigned": bool(attached_guest.assigned_to),
            }
            if attached_guest
            else None
        ),
        # context_user_permissions kept for JS but no role strings — only capability booleans
        "context_user_permissions": json.dumps(
            {
                "is_admin": _can(request, "dashboard.admin"),
                "chat_view_all": _can(request, "chat.view_all"),
                "guests_view": _can(request, "guests.view"),
                "guests_view_all": _can(request, "guests.view_all"),
                "guests_manage_unassigned": _can(request, "guests.manage_unassigned"),
                "events_manage_all": _can(request, "events.manage_all"),
                "attendance_view_all": _can(request, "attendance.view_all"),
            },
            cls=DjangoJSONEncoder,
        ),
        "current_room_id": current_room.id if current_room else None,
        "selected_unit_id": current_room.id if current_room else None,  # JS compat
        "page_title": (
            current_room.name
            if current_room
            else (church.name if church else "ChurchForce")
        ),
        # Send / privilege permissions for JS
        # "user_can_send": _user_can_send_in_current_room,
        "user_has_unit_membership": _user_has_unit_membership,
        "user_is_trainee_only": _user_is_trainee_only,
        "room_send_permissions_json": json.dumps(
            _room_send_permissions, cls=DjangoJSONEncoder
        ),
        "room_privileged_json": json.dumps(_room_privileged, cls=DjangoJSONEncoder),
        "current_user_is_privileged": _can(request, "dashboard.admin")
        or (_user_unit_memberships.filter(is_unit_head=True).exists()),
        # rooms_json: serialised list for window.CHAT_ROOMS (forward picker + room switcher).
        # Includes sub-rooms so the modal can list them; they are excluded from the
        # main sidebar by the rooms_qs filter above.
        "rooms_json": json.dumps(
            [
                {
                    "id": r.id,
                    "name": r.name,
                    "color_class": r.color_class,
                    "has_guest_module": r.has_guest_module,
                    "members": r.enriched_users,
                    "parent_room_id": getattr(r, "parent_room_id", None),
                    "is_default": r.is_default,
                    "room_type": r.room_type,
                }
                for r in rooms_qs
            ]
            # Merge in all sub-rooms of accessible units so the modal can show them
            + [
                {
                    "id": sr.id,
                    "name": sr.name,
                    "color_class": (
                        getattr(sr.unit, "color", "bg-dark") if sr.unit else "bg-dark"
                    ),
                    "has_guest_module": bool(sr.unit and sr.unit.guest_management),
                    "members": (
                        [
                            _cm_to_strip(cm)
                            for cm in sr.members.select_related("user")
                            .filter(user__is_superuser=False)
                            .all()
                        ]
                        or default_tier_strip_members
                    ),
                    "parent_room_id": (
                        getattr(sr, "parent_room_id", None) or "__general__"
                    ),
                    "is_default": sr.is_default,
                    "room_type": sr.room_type,
                }
                for sr in ChatRoom.raw_objects.filter(
                    Q(unit__in=units) | Q(unit__isnull=True, parent_room__isnull=True),
                    church=church,
                    is_active=True,
                    is_default=False,
                    room_type="project",
                )
                .select_related("unit")
                .prefetch_related("members__user")
            ],
            cls=DjangoJSONEncoder,
        ),
        # All church members for the central strip (regardless of unit assignment)
        "all_members_json": json.dumps(all_enriched_members, cls=DjangoJSONEncoder),
        # Template list for the general strip ({% for cm in all_members %})
        "all_members": all_enriched_members,
        # Church localisation formats for JS (Python strftime strings)
        "church_date_format": get_effective_date_format(church, "%d %b %Y"),
        "church_time_format": get_effective_time_format(church, "%H:%M:%S"),
        "chat_default_tier_members_json": chat_default_tier_members_json,
        "chat_bg_url": chat_bg_url,
        "chat_bg_type": chat_bg_type,
    }

    return render(request, "workforce/chat_room.html", context)


@login_required
def load_more_messages(request):
    """
    AJAX endpoint to fetch older messages.

    Supports two scoping strategies (in priority order):
      1. ?room_id=<ChatRoom.id>   — preferred; scopes to a specific ChatRoom
      2. ?unit_id=<ChurchUnit.id> — legacy fallback; scopes by unit membership

    Pagination: ?before=<ISO timestamp> fetches messages older than that point.
    """
    church = _get_church(request)
    if not church:
        return JsonResponse({"messages": []})

    before = request.GET.get("before")
    limit = min(int(request.GET.get("limit", 50)), 100)
    room_id = request.GET.get("room_id")
    unit_id = request.GET.get("unit_id")

    qs = (
        ChatMessage.raw_objects.filter(church=church, is_deleted=False)
        .select_related(
            "sender__user",
            "guest_card",
            "parent__sender__user",
            "forwarded_from__room",
        )
        .prefetch_related("reactions__member")
    )

    viewer_cm = ChurchMember.raw_objects.filter(
        church=church, user=request.user, is_active=True
    ).first()

    if room_id and str(room_id).isdigit():
        # Room-based scope (preferred)
        qs = qs.filter(room_id=int(room_id))
    elif unit_id and str(unit_id).isdigit():
        # Legacy unit-based scope — filter by messages in rooms of this unit
        qs = qs.filter(room__unit_id=int(unit_id))
    else:
        # General room history should only include messages actually sent to
        # General.  Room-scoped messages must not bleed into the central feed.
        qs = qs.filter(room__isnull=True)

    if before:
        from django.utils.dateparse import parse_datetime as _pd

        before_dt = _pd(before)
        if before_dt:
            qs = qs.filter(created_at__lt=before_dt)

    mention_map, mention_regex = build_mention_helpers(church)

    messages = list(qs.order_by("-created_at")[:limit])
    messages.reverse()

    payload = [
        serialize_message(m, mention_map, mention_regex, viewer_cm) for m in messages
    ]
    return JsonResponse({"messages": payload})


@require_POST
def chat_message_react(request, message_id):
    """
    POST JSON: {"reaction": "like"} to set, or {"reaction": null} to remove.
    Returns JSON — never redirects (safe for fetch() callers).
    """
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "unauthenticated"}, status=401)

    import json

    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer

    from workforce.utils import (
        CHAT_REACTION_ALLOWED_KEYS,
        build_chat_message_reaction_fields,
    )

    church = _get_church(request)
    if not church:
        return JsonResponse({"ok": False}, status=403)

    member = ChurchMember.raw_objects.filter(
        church=church, user=request.user, is_active=True
    ).first()
    if not member:
        return JsonResponse({"ok": False}, status=403)

    try:
        body = json.loads((request.body or b"").decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

    if "reaction" not in body:
        return JsonResponse({"ok": False, "error": "reaction required"}, status=400)

    reaction = body.get("reaction")
    if reaction not in (None, "") and reaction not in CHAT_REACTION_ALLOWED_KEYS:
        return JsonResponse({"ok": False, "error": "invalid reaction"}, status=400)

    msg = (
        ChatMessage.raw_objects.filter(church=church, id=message_id, is_deleted=False)
        .select_related("room")
        .first()
    )
    if not msg:
        return JsonResponse({"ok": False}, status=404)

    if reaction in (None, ""):
        ChatMessageReaction.raw_objects.filter(message=msg, member=member).delete()
    else:
        ChatMessageReaction.raw_objects.update_or_create(
            message=msg,
            member=member,
            defaults={"reaction": reaction, "church": church},
        )

    summary, total, mine = build_chat_message_reaction_fields(msg, member)
    layer = get_channel_layer()
    if layer:
        async_to_sync(layer.group_send)(
            f"chat_room_{msg.room_id}" if msg.room_id else f"chat_church_{church.id}",
            {
                "type": "chat_reaction",
                "message_id": msg.id,
                "reaction_summary": summary,
                "total_reactions": total,
            },
        )

    return JsonResponse(
        {
            "ok": True,
            "reaction_summary": summary,
            "total_reactions": total,
            "my_reaction": mine,
        }
    )


@login_required
def load_private_messages(request, user_id):
    church = _get_church(request)
    if not church:
        return JsonResponse({"messages": []})

    from units.models import ChatRoom

    current_member = ChurchMember.raw_objects.filter(
        church=church,
        user=request.user,
        is_active=True,
    ).first()
    other_member = ChurchMember.raw_objects.filter(
        church=church,
        user_id=user_id,
        is_active=True,
    ).first()
    if not current_member or not other_member:
        return JsonResponse({"messages": []})

    low_id, high_id = sorted([current_member.id, other_member.id])
    room_name = f"Private:{low_id}:{high_id}"
    room = ChatRoom.raw_objects.filter(
        church=church,
        room_type="private",
        name=room_name,
        is_active=True,
    ).first()
    if not room:
        return JsonResponse({"messages": []})

    qs = (
        ChatMessage.raw_objects.filter(
            church=church,
            room=room,
            is_deleted=False,
        )
        .select_related("sender__user")
        .order_by("created_at")
    )
    messages = [
        {
            "id": msg.id,
            "message": msg.message or "",
            "sender_id": (
                msg.sender.user_id if msg.sender_id and msg.sender.user_id else None
            ),
            "sender_name": (
                msg.sender.user.full_name or msg.sender.user.username
                if msg.sender_id and msg.sender.user_id
                else ""
            ),
            "sender_title": (
                getattr(msg.sender.user, "title", "")
                if msg.sender_id and msg.sender.user_id
                else ""
            ),
            "created_at": msg.created_at.isoformat(),
        }
        for msg in qs[:200]
    ]
    unread_messages = qs.exclude(sender=current_member).exclude(read_by=current_member)
    for msg in unread_messages:
        msg.read_by.add(current_member)
    return JsonResponse({"messages": messages})


@login_required
def private_unread_counts(request):
    church = _get_church(request)
    if not church:
        return JsonResponse({"counts": {}})

    from units.models import ChatRoom

    current_member = ChurchMember.raw_objects.filter(
        church=church,
        user=request.user,
        is_active=True,
    ).first()
    if not current_member:
        return JsonResponse({"counts": {}})

    rooms = (
        ChatRoom.raw_objects.filter(
            church=church,
            room_type="private",
            is_active=True,
            members=current_member,
        )
        .prefetch_related("members__user")
        .distinct()
    )
    counts = {}
    for room in rooms:
        other = next(
            (member for member in room.members.all() if member.id != current_member.id),
            None,
        )
        if not other or not other.user_id:
            continue
        count = (
            ChatMessage.raw_objects.filter(
                church=church,
                room=room,
                is_deleted=False,
            )
            .exclude(sender=current_member)
            .exclude(read_by=current_member)
            .count()
        )
        if count:
            counts[str(other.user_id)] = count

    return JsonResponse({"counts": counts})


@csrf_exempt
def upload_file(request):
    """Upload a file (Cloudinary or local) and return full metadata."""
    if request.method != "POST" or "file" not in request.FILES:
        return JsonResponse({"error": "Invalid request"}, status=400)

    f = request.FILES["file"]

    try:
        from core.storage import smart_upload, StorageError

        try:
            result = smart_upload(f, folder="chat/files", resource_type="auto")
        except StorageError as exc:
            return JsonResponse({"error": str(exc)}, status=500)

        file_url = result["url"]
        file_path = result["path"]
        original_name = result["original_name"]

        guessed_type, _ = mimetypes.guess_type(f.name)

        return JsonResponse(
            {
                "url": file_url,
                "path": file_path,
                "public_id": file_path,
                "name": original_name,
                "size": f.size,
                "type": guessed_type or f.content_type or "application/octet-stream",
            }
        )

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
        return HttpResponseForbidden(
            "You do not have permission to view attendance summary."
        )

    user = request.user
    today = timezone.localdate()
    last_30_days = today - timedelta(days=30)

    records = get_visible_attendance_records(user, since_date=last_30_days)
    clock_records = get_visible_clock_records(user, since_date=last_30_days)

    clock_map = {(c.user_id, c.event_id, c.date): c for c in clock_records}
    for r in records:
        clock = clock_map.get((r.user_id, r.event_id, r.date))
        r.clock_in_time = (
            format_time_value(clock.clock_in, church)
            if clock and clock.clock_in
            else None
        )
        r.clock_out_time = (
            format_time_value(clock.clock_out, church)
            if clock and clock.clock_out
            else None
        )

    if (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or request.GET.get("format") == "json"
    ):

        def _member_display(church_member):
            """Resolve display name from ChurchMember via church_member.user."""
            try:
                u = church_member.user
                if u:
                    return f"{u.title or ''} {u.full_name or u.username}".strip()
            except Exception:
                pass
            return "—"

        data = [
            {
                "date": r.date.strftime("%Y-%m-%d"),
                "event": r.event.name if r.event else "-",
                "unit": r.event.unit.name if r.event and r.event.unit else "Workforce",
                "user": _member_display(r.user),
                "status": r.status,
                "location_label": (
                    getattr(r, "location_tag", "").capitalize()
                    if getattr(r, "location_tag", "")
                    else ""
                ),
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
        if e.date and not e.attendance_is_open():
            continue
        if e.event_type and e.event_type.lower() == "followup":
            available_events.append(e)
        elif e.day_of_week and e.day_of_week.lower() == weekday:
            available_events.append(e)
        elif (
            e.event_type
            and e.event_type.lower() == "meeting"
            and weekday == "wednesday"
        ):
            available_events.append(e)

    actual_events_today = [
        e for e in available_events if (e.event_type or "").lower() != "followup"
    ]
    show_other_option = len(actual_events_today) > 0

    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    guest_activity_types = [
        "followup",
        "message",
        "guest_view",
        "call",
        "report",
        "other",
    ]

    user_guest_activity = UserActivity.raw_objects.filter(
        church=church,
        user__user=request.user,
        activity_type__in=guest_activity_types,
        created_at__date__gte=week_start,
        created_at__date__lte=week_end,
    ).exists()

    can_mark_now = any(
        e.time is None
        or now_time >= timezone.make_aware(datetime.combine(today, e.time))
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
                return JsonResponse(
                    {"success": False, "error": "Missing event ID"}, status=400
                )
            messages.error(request, "Invalid or missing event ID.")
            return HttpResponseRedirect(next_url)

        if event_id == "other":
            custom_name = request.POST.get("custom_event") or "Custom Event"
            event, _ = Event.raw_objects.filter(church=church).get_or_create(
                church=church,
                name=custom_name,
                defaults={
                    "church": church,
                    "event_type": "Other",
                    "date": today,
                },
            )
        else:
            try:
                event = Event.raw_objects.filter(church=church).get(id=int(event_id))
            except (Event.DoesNotExist, ValueError):
                if is_ajax:
                    return JsonResponse(
                        {"success": False, "error": "Event not found"}, status=404
                    )
                messages.error(request, "Event not found.")
                return HttpResponseRedirect(next_url)

        if event.date and not event.attendance_is_open():
            error_msg = f"Attendance for {event.name} is no longer available."
            if is_ajax:
                return JsonResponse({"success": False, "error": error_msg}, status=400)
            messages.error(request, error_msg)
            return HttpResponseRedirect(next_url)

        mode = (getattr(event, "attendance_mode", "") or "").lower()
        is_physical = mode == "physical"

        if status == "present" and is_physical:
            try:
                validate_church_proximity(
                    user_lat,
                    user_lon,
                    church_lat=float(church.latitude),
                    church_lon=float(church.longitude),
                    threshold_km=float(church.attendance_radius_km),
                )
            except Exception as e:
                if is_ajax:
                    return JsonResponse({"success": False, "error": str(e)}, status=400)
                messages.error(request, str(e))
                return HttpResponseRedirect(next_url)

        if status == "present" and event.time:
            event_dt = datetime.combine(today, event.time)
            if now_time > timezone.make_aware(event_dt + timedelta(minutes=15)):
                status = "late"

        member = _attendance_member_for_user(church, request.user)
        if not member:
            error_msg = "You are not registered as a church member here."
            if is_ajax:
                return JsonResponse({"success": False, "error": error_msg}, status=400)
            messages.error(request, error_msg)
            return HttpResponseRedirect(next_url)

        existing = AttendanceRecord.raw_objects.filter(
            church=church,
            user=member,
            event=event,
            date=today,
        ).first()

        if existing:
            if is_ajax:
                return JsonResponse(
                    {
                        "success": False,
                        "error": f"You have already marked attendance for {event.name} today.",
                    },
                    status=400,
                )
            messages.warning(
                request, f"You have already marked attendance for {event.name} today."
            )
            return HttpResponseRedirect(next_url)

        AttendanceRecord.raw_objects.create(
            church=church,
            user=member,
            event=event,
            date=today,
            status=status,
            remarks=remarks,
            location_tag=(
                classify_event_location(event, church, user_lat, user_lon)
                if status in ("present", "late")
                else ""
            ),
        )

        # Record clock-in time when attendance is first marked.
        ClockRecord.raw_objects.get_or_create(
            church=church,
            user=member,
            event=event,
            date=today,
            defaults={"clock_in": timezone.now()},
        )

        broadcast_attendance_summary(church)

        message_text = f"Attendance marked for {event.name} ({status})."
        location_label = ""
        if status in ("present", "late"):
            if mode == "virtual":
                location_label = "Virtual"
            elif mode == "physical":
                location_label = "Onsite"
            elif mode == "hybrid":
                location_label = (
                    "Onsite"
                    if classify_event_location(event, church, user_lat, user_lon)
                    == "onsite"
                    else "Offsite"
                )
        if is_ajax:
            return JsonResponse(
                {
                    "success": True,
                    "message": message_text,
                    "location_label": location_label,
                }
            )
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
            user__member__user=request.user,
            title=title,
            description=description,
            date=date_value,
            time=time_value,
        )
        messages.success(request, "Reminder added!")
        return redirect("accounts:admin-dashboard")

    return render(request, "workforce/add_reminder.html")


@login_required
def api_events(request):
    """
    Unified API endpoint:
      - FullCalendar (with start/end params)
      - Unit modal (with unit_id)
    """
    church = _get_church(request)
    user = request.user
    unit_id = request.GET.get("unit_id")

    events = get_available_events_for_user(user)

    if unit_id is not None:
        data = expand_unit_events(user, unit_id)
        return JsonResponse({"events": data})

    weekday_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
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
                "time": format_time_value(e.time, church) if e.time else None,
                "unit": getattr(e.unit, "name", None),
                "unit_color": (
                    getattr(e.unit, "color_class", "bg-gray-800")
                    if getattr(e, "unit", None)
                    else None
                ),
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
        return JsonResponse(
            {"status": "error", "message": "No church context"}, status=403
        )

    if request.method == "POST":
        try:
            data = json.loads(request.body.decode("utf-8"))
            activity_type = data.get("activity_type", "other")
            guest_id = data.get("guest_id")
            description = data.get("description", "")

            UserActivity.raw_objects.create(
                church=church,
                user__user=request.user,
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
            time_str = format_time_value(e.time, church) if e.time else None
            label = e.name
        else:
            date_str = None
            time_str = format_time_value(e.time, church) if e.time else None
            label = f"{e.name} (Recurring)"

        data.append(
            {
                "id": e.id,
                "name": label,
                "date": date_str,
                "time": time_str,
                "event_type": e.event_type,
                "is_recurring": not bool(e.date),
            }
        )

    return JsonResponse(data, safe=False)


@login_required
@login_required
def promotion_queue(request):
    """
    List pending trainee promotion tasks.
    Visible to any member with workforce.promotions.review permission.
    Tasks with assigned_role are also shown to holders of that role.
    """
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)

    if not church or not _can(request, "workforce.promotions.review"):
        return HttpResponseForbidden()

    # Show all unassigned tasks + tasks assigned to any role the member holds
    from django.db.models import Q

    tasks = (
        LeaderTask.raw_objects.filter(
            church=church,
            task_type="promotion_review",
            completed=False,
            is_active=True,
        )
        .filter(
            Q(assigned_role__isnull=True)
            | Q(assigned_role__workforcemembershiprole__workforce_member__member=member)
        )
        .distinct()
        .select_related(
            "trainee__member__user",
            "trainee__lms_enrollment__course",
            "trainee__preferred_unit",
            "trainee__probation_unit",
            "assigned_role",
        )
        .order_by("created_at")
    )

    return render(
        request,
        "workforce/promotion_queue.html",
        {
            "tasks": tasks,
            "page_title": "Promotion Approvals",
        },
    )


@login_required
@require_POST
def approve_promotion(request, task_id):
    """
    Admin approves or rejects a trainee promotion.
    On approval: calls attempt_final_promotion() which runs the full
    readiness check and calls finalize_induction_from_trainee().
    """
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)

    if not church or not _can(request, "workforce.promotions.review"):
        return HttpResponseForbidden()

    task = get_object_or_404(
        LeaderTask.raw_objects.select_related("trainee__member__user"),
        id=task_id,
        church=church,
        task_type="promotion_review",
        completed=False,
        is_active=True,
    )

    action = request.POST.get("action", "approve")
    trainee = task.trainee

    if action == "approve":
        task.completed = True
        task.completed_at = timezone.now()
        task.completed_by = member
        task.save(update_fields=["completed", "completed_at", "completed_by"])

        try:
            attempt_final_promotion(trainee, triggered_by=member)
            messages.success(
                request, f"{trainee.member} has been promoted to full workforce member."
            )
        except Exception as exc:
            messages.error(request, f"Promotion failed: {exc}")

    elif action == "reject":
        reason = request.POST.get("rejection_reason", "").strip()
        task.completed = True
        task.completed_at = timezone.now()
        task.completed_by = member
        task.rejection_reason = reason
        task.save(
            update_fields=[
                "completed",
                "completed_at",
                "completed_by",
                "rejection_reason",
            ]
        )
        messages.warning(request, f"Promotion for {trainee.member} has been rejected.")

    return redirect("workforce:promotion_queue")


# ─────────────────────────────────────────────────────────────
# AI Chat Assist endpoint
# ─────────────────────────────────────────────────────────────
@login_required
@require_POST
def ai_chat_assist(request):
    """Proxy AI suggestions for the chat AI panel."""
    church = _get_church(request)
    if not church:
        return JsonResponse({"error": "No church context"}, status=403)

    try:
        body = json.loads(request.body)
        prompt = body.get("prompt", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not prompt:
        return JsonResponse({"error": "Empty prompt"}, status=400)

    try:
        from core.ai_skills import _chat
        from accounts.models import ChurchMember

        member = (
            ChurchMember.raw_objects.filter(
                church=church, user=request.user, is_active=True
            )
            .select_related("user")
            .first()
        )

        # Build a church-aware system prompt
        church_ctx = (
            f"You are a helpful assistant for {church.name}, a Christian church."
        )
        settings_obj = getattr(church, "settings", None)
        if settings_obj:
            if settings_obj.mission_statement:
                church_ctx += f" Mission: {settings_obj.mission_statement[:200]}"
            if settings_obj.denominational_affiliation:
                church_ctx += (
                    f" Denomination: {settings_obj.denominational_affiliation}."
                )

        result = _chat(
            system=church_ctx
            + " Respond concisely, warmly, and in a church-appropriate tone.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        return JsonResponse({"result": result})

    except Exception as exc:
        import logging

        logging.exception("ai_chat_assist failed: %s", exc)
        return JsonResponse({"error": "AI unavailable"}, status=503)


# ─────────────────────────────────────────────────────────────
# Create sub-room endpoint
# ─────────────────────────────────────────────────────────────
@login_required
@require_POST
def create_sub_room(request):
    """Create a project/sub-room under an existing ChatRoom's unit.

    Expects JSON body:
      {
        "parent_room_id": <int>,
        "name": "<str>",
        "description": "<str>",          # optional
        "member_ids": [<int>, ...]        # ChurchMember pks to add as initial members
      }

    Sub-rooms are exclusive to the modal; they are never surfaced in the main
    sidebar.  Their parent_room FK links them to the parent unit room, and their
    explicit `members` M2M controls who can participate.
    """
    church = _get_church(request)
    if not church:
        return JsonResponse({"error": "No church context"}, status=403)

    try:
        body = json.loads(request.body)
        parent_room_id = body.get("parent_room_id")
        name = body.get("name", "").strip()
        description = body.get("description", "").strip()
        member_ids = body.get("member_ids", [])  # list of ChurchMember pks
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not name:
        return JsonResponse({"error": "Name is required"}, status=400)
    from units.models import ChatRoom
    from accounts.models import ChurchMember

    parent = None
    if parent_room_id not in (None, "", "null", "__general__"):
        parent = ChatRoom.raw_objects.filter(
            church=church, id=parent_room_id, is_active=True
        ).first()
        if not parent:
            return JsonResponse({"error": "Parent room not found"}, status=404)

    creator = ChurchMember.raw_objects.filter(
        church=church, user=request.user, is_active=True
    ).first()

    sub = ChatRoom.raw_objects.create(
        church=church,
        unit=parent.unit if parent else None,
        name=name,
        description=description,
        room_type="project",
        is_default=False,
        parent_room=parent,
        created_by=creator,
    )

    # Add creator automatically, then any explicitly requested members
    # (all must be active ChurchMembers within the same church)
    initial_member_ids = set(member_ids)
    if creator and not creator.user.is_superuser:
        initial_member_ids.add(creator.pk)

    if initial_member_ids:
        valid_members = ChurchMember.raw_objects.filter(
            church=church,
            id__in=initial_member_ids,
            is_active=True,
            user__is_superuser=False,
        )
        sub.members.set(valid_members)

    return JsonResponse(
        {
            "id": sub.id,
            "name": sub.name,
            "room_type": sub.room_type,
            "parent_room_id": parent.id if parent else "__general__",
            "members": [
                {"id": m.id, "name": m.user.full_name or m.user.username}
                for m in sub.members.select_related("user").all()
            ],
        }
    )


@login_required
@require_POST
def add_sub_room_member(request, room_id):
    church = _get_church(request)
    if not church:
        return JsonResponse({"error": "No church context"}, status=403)

    try:
        body = json.loads(request.body or "{}")
        user_id = body.get("user_id")
        member_id = body.get("member_id")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    from units.models import ChatRoom
    from accounts.models import ChurchMember

    sub_room = ChatRoom.raw_objects.filter(
        church=church,
        id=room_id,
        is_active=True,
        room_type="project",
    ).first()
    if not sub_room:
        return JsonResponse({"error": "Sub-room not found"}, status=404)

    member_qs = ChurchMember.raw_objects.filter(
        church=church,
        is_active=True,
        user__is_superuser=False,
    )
    if member_id not in (None, "", "null"):
        member = member_qs.filter(id=member_id).first()
    elif user_id not in (None, "", "null"):
        member = member_qs.filter(user_id=user_id).first()
    else:
        member = None

    if not member:
        return JsonResponse({"error": "Member not found"}, status=404)

    sub_room.members.add(member)
    user = member.user
    return JsonResponse(
        {
            "ok": True,
            "member": {
                "id": member.id,
                "member_id": member.id,
                "user_id": user.id,
                "full_name": user.full_name or user.username,
                "username": user.username,
                "title": getattr(user, "title", ""),
                "image": user.image.url if getattr(user, "image", None) else None,
            },
        }
    )


@login_required
def get_sub_room_members(request, room_id):
    """GET /workforce/sub_room/<room_id>/members/ — returns current non-global members."""
    church = _get_church(request)
    if not church:
        return JsonResponse({"error": "No church context"}, status=403)

    from units.models import ChatRoom

    sub_room = (
        ChatRoom.raw_objects.filter(
            church=church,
            id=room_id,
            is_active=True,
            room_type="project",
        )
        .prefetch_related("members__user")
        .first()
    )

    if not sub_room:
        return JsonResponse({"error": "Sub-room not found"}, status=404)

    members = [
        {
            "id": m.id,
            "member_id": m.id,
            "user_id": m.user.id,
            "full_name": m.user.full_name or m.user.username,
            "username": m.user.username,
            "title": getattr(m.user, "title", "") or "",
            "color": getattr(m.user, "color", "") or "",
            "image": m.user.image.url if getattr(m.user, "image", None) else None,
        }
        for m in sub_room.members.select_related("user")
        .filter(user__is_superuser=False)
        .all()
    ]

    return JsonResponse({"ok": True, "members": members})


@login_required
@require_POST
def remove_sub_room_member(request, room_id):
    """POST /workforce/sub_room/<room_id>/remove_member/ — removes a member from a sub-room."""
    church = _get_church(request)
    if not church:
        return JsonResponse({"error": "No church context"}, status=403)

    try:
        body = json.loads(request.body or "{}")
        user_id = body.get("user_id")
        member_id = body.get("member_id")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    from units.models import ChatRoom
    from accounts.models import ChurchMember

    sub_room = ChatRoom.raw_objects.filter(
        church=church,
        id=room_id,
        is_active=True,
        room_type="project",
    ).first()
    if not sub_room:
        return JsonResponse({"error": "Sub-room not found"}, status=404)

    member_qs = ChurchMember.raw_objects.filter(church=church, is_active=True)
    if member_id not in (None, "", "null"):
        member = member_qs.filter(id=member_id).first()
    elif user_id not in (None, "", "null"):
        member = member_qs.filter(user_id=user_id).first()
    else:
        member = None

    if not member:
        return JsonResponse({"error": "Member not found"}, status=404)

    sub_room.members.remove(member)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def edit_sub_room(request, room_id):
    """POST /workforce/sub_room/<room_id>/edit/ — renames a sub-room."""
    church = _get_church(request)
    if not church:
        return JsonResponse({"error": "No church context"}, status=403)

    try:
        body = json.loads(request.body or "{}")
        new_name = (body.get("name") or "").strip()
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not new_name:
        return JsonResponse({"error": "Name is required"}, status=400)

    from units.models import ChatRoom

    sub_room = ChatRoom.raw_objects.filter(
        church=church,
        id=room_id,
        is_active=True,
        room_type="project",
    ).first()
    if not sub_room:
        return JsonResponse({"error": "Sub-room not found"}, status=404)

    sub_room.name = new_name
    sub_room.save(update_fields=["name"])
    return JsonResponse({"ok": True, "name": sub_room.name})


@login_required
@require_POST
def delete_sub_room(request, room_id):
    """POST /workforce/sub_room/<room_id>/delete/ — soft-deletes a sub-room."""
    church = _get_church(request)
    if not church:
        return JsonResponse({"error": "No church context"}, status=403)

    from units.models import ChatRoom

    sub_room = ChatRoom.raw_objects.filter(
        church=church,
        id=room_id,
        is_active=True,
        room_type="project",
    ).first()
    if not sub_room:
        return JsonResponse({"error": "Sub-room not found"}, status=404)

    sub_room.is_active = False
    sub_room.save(update_fields=["is_active"])
    return JsonResponse({"ok": True})


@login_required
@require_POST
def clock_out(request):
    """
    Records clock-out time for the most recent clock-in today.
    Expects JSON body: {"event_id": <int>}
    """
    church = _get_church(request)
    if not church:
        return JsonResponse(
            {"success": False, "error": "No church context"}, status=403
        )

    try:
        body = json.loads(request.body)
        event_id = int(body.get("event_id", 0))
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({"success": False, "error": "Invalid request"}, status=400)

    member = _attendance_member_for_user(church, request.user)
    if not member:
        return JsonResponse(
            {
                "success": False,
                "error": "You are not registered as a church member here.",
            },
            status=400,
        )

    today = timezone.localdate()

    try:
        event = Event.raw_objects.filter(church=church, id=event_id).get()
    except (Event.DoesNotExist, ValueError):
        return JsonResponse({"success": False, "error": "Event not found."}, status=404)

    clock_record = ClockRecord.raw_objects.filter(
        church=church,
        user=member,
        event=event,
        date=today,
    ).first()

    if not clock_record:
        return JsonResponse(
            {"success": False, "error": "No clock-in found for this event today."},
            status=400,
        )

    if clock_record.clock_out:
        return JsonResponse(
            {"success": False, "error": "Already clocked out."}, status=400
        )

    clock_record.clock_out = timezone.now()
    clock_record.save(update_fields=["clock_out"])

    broadcast_attendance_summary(church)

    return JsonResponse(
        {
            "success": True,
            "clock_out": format_time_value(clock_record.clock_out, church),
            "message": f"Clocked out of {event.name}.",
        }
    )
