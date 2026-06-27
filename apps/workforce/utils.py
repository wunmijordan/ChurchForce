from collections import Counter

from django.contrib.auth.models import Group
import requests, mimetypes, os, re, urllib.parse, urllib
from bs4 import BeautifulSoup
from accounts.models import CustomUser, ChurchMember
from guests.models import GuestEntry
from core.utils.colors import resolve_color


def get_user_color(user_id):
    return resolve_color(f"user:{user_id}", variant="hex")


from django.db.models.fields.files import FieldFile
from django.core.files.storage import default_storage
from django.utils import timezone
from services.models import Event
from .models import (
    AttendanceRecord,
    PersonalReminder,
    UserActivity,
    ChatMessage,
    ChatMessageReaction,
    ClockRecord,
    WorkforceMember,
    WorkforceTraineeProfile,
)
from django.db.models import Q
from django.conf import settings
from geopy.distance import distance
from django.core.exceptions import ValidationError
from cloudinary.utils import cloudinary_url
from units.models import ChurchUnit, UnitMembership
from permissions.services.resolver import PermissionResolver
from tenants.time_utils import format_time_value


import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup

YOUTUBE_OEMBED = "https://www.youtube.com/oembed?format=json&url="


def _get_church(user):
    if not user or not user.is_authenticated:
        return None
    membership = (
        ChurchMember.raw_objects.filter(user=user, is_active=True)
        .select_related("church")
        .first()
    )
    return membership.church if membership else None


def _get_permissions(user, church):
    if not user or not church:
        return None
    return PermissionResolver(user, church)


def _can(perms, key, unit=None):
    return bool(perms and perms.can(key, unit=unit))


# -------------------- Link Previews --------------------


def is_youtube(url):
    host = urlparse(url).netloc.lower()
    return "youtube.com" in host or "youtu.be" in host


def extract_youtube_id(url):
    try:
        u = urlparse(url)
        path = u.path

        if "youtu.be" in u.netloc:
            return path.lstrip("/")

        qs = urllib.parse.parse_qs(u.query)
        if "v" in qs:
            return qs.get("v", [None])[0]

        if path.startswith("/shorts/"):
            return path.split("/")[2]

        if path.startswith("/embed/"):
            return path.split("/")[2]

        if path.startswith("/live/"):
            return path.split("/")[2]

        return None
    except:
        return None


def get_best_youtube_thumb(video_id):
    base = f"https://i.ytimg.com/vi/{video_id}"
    candidates = [
        "maxresdefault.jpg",
        "sddefault.jpg",
        "hqdefault.jpg",
        "mqdefault.jpg",
        "default.jpg",
    ]

    headers = {"User-Agent": "Mozilla/5.0"}

    for file in candidates:
        url = f"{base}/{file}"

        try:
            r = requests.head(url, timeout=3, headers=headers)
            if r.status_code == 200:
                return url
        except:
            pass

    return f"{base}/default.jpg"


def get_link_preview(url):
    """Unified YouTube + OG preview for WebSockets."""
    headers = {"User-Agent": "Mozilla/5.0"}

    if is_youtube(url):
        vid = extract_youtube_id(url)
        if vid:
            meta_title = ""
            try:
                r = requests.get(
                    f"https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v={vid}",
                    headers=headers,
                    timeout=5,
                )
                if r.status_code == 200:
                    meta_title = r.json().get("title", "")
            except:
                pass

            return {
                "url": url,
                "title": meta_title or "YouTube Video",
                "description": "",
                "image": get_best_youtube_thumb(vid),
                "provider": "youtube",
            }

    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        title_tag = soup.find("meta", property="og:title") or soup.find("title")
        desc_tag = soup.find("meta", property="og:description") or soup.find(
            "meta", attrs={"name": "description"}
        )
        image_tag = soup.find("meta", property="og:image")

        title = (
            title_tag["content"]
            if title_tag and title_tag.has_attr("content")
            else (title_tag.string if title_tag else "")
        )

        return {
            "url": url,
            "title": title,
            "description": (
                desc_tag["content"] if desc_tag and desc_tag.has_attr("content") else ""
            ),
            "image": (
                image_tag["content"]
                if image_tag and image_tag.has_attr("content")
                else ""
            ),
        }

    except:
        return {
            "url": url,
            "title": url,
            "description": "",
            "image": "",
        }


# -------------------- Mentions & Chat Serialization --------------------


def build_mention_helpers(church):
    """Precompute mention_map + regex for mentions, scoped by church."""
    if not church:
        return {}, None

    users = list(
        CustomUser.objects.filter(church_memberships__church=church).distinct()
    )
    mention_map = {}
    for u in users:
        display = f"@{ (u.title + ' ') if getattr(u, 'title', None) else '' }{ (u.full_name or u.username) }".strip()
        mention_map[display] = u
    regex = (
        re.compile(r"(" + "|".join(map(re.escape, mention_map.keys())) + r")")
        if mention_map
        else None
    )
    return mention_map, regex


def cloudinary_url(public_id, resource_type):
    cloud = settings.CLOUDINARY_STORAGE["CLOUD_NAME"]
    rtype = "video" if resource_type.startswith("video") else "image"

    return f"https://res.cloudinary.com/{cloud}/{rtype}/upload/{public_id}"


CHAT_REACTION_ALLOWED_KEYS = frozenset(
    {
        "like",
        "love",
        "fire",
        "pray",
        "clap",
        "amen",
        "bless",
        "inspired",
        "plugged",
        "peace",
        "thanks",
        "joy",
        "wow",
        "blessed",
        "bible",
        "faith",
        "hope",
        "following",
        "checkmate",
    }
)


def build_chat_message_reaction_fields(message, viewer_member=None):
    """reaction_summary (top 3 keys), total count, and current viewer's reaction."""
    if hasattr(message, "_prefetched_objects_cache") and "reactions" in getattr(
        message, "_prefetched_objects_cache", {}
    ):
        rows = list(message.reactions.all())
    else:
        rows = list(
            ChatMessageReaction.raw_objects.filter(message=message).select_related(
                "member"
            )
        )
    counts = Counter(r.reaction for r in rows)
    total = len(rows)
    summary = dict(counts.most_common(3))
    my = None
    if viewer_member is not None:
        vid = getattr(viewer_member, "id", None)
        if vid is not None:
            for r in rows:
                if r.member_id == vid:
                    my = r.reaction
                    break
    return summary, total, my


def serialize_message(m, mention_map=None, mention_regex=None, viewer_member=None):
    """Unified serializer for both WebSocket + Views.

    ChatMessage.sender is now a ChurchMember FK, so we go
    sender.user to reach the CustomUser — no more UnitMembership chain.
    """
    mentions_payload = []
    if mention_regex and m.message:
        found = set(mention_regex.findall(m.message))
        for token in found:
            u = mention_map.get(token)
            if u:
                mentions_payload.append(
                    {
                        "id": u.id,
                        "username": u.username,
                        "title": getattr(u, "title", ""),
                        "name": u.full_name or u.username,
                        "color": get_user_color(u.id),
                    }
                )

    guest_payload = None
    if m.guest_card:
        g = (
            GuestEntry.raw_objects.select_related("assigned_to", "status")
            .filter(id=m.guest_card.id)
            .first()
        )
        if g:
            assigned = None
            if g.assigned_to:
                # assigned_to is a ChurchMember; reach user directly
                au = getattr(g.assigned_to, "user", None)
                assigned = {
                    "id": g.assigned_to.id,
                    "title": getattr(au, "title", "") if au else "",
                    "full_name": au.full_name if au else "",
                    "image": au.image.url if (au and au.image) else None,
                }
            guest_payload = {
                "id": g.id,
                "name": g.full_name,
                "custom_id": g.custom_id,
                "image": g.picture.url if g.picture else None,
                "title": g.title or "",
                "date_of_visit": (
                    g.date_of_visit.strftime("%Y-%m-%d") if g.date_of_visit else ""
                ),
                "status_color": g.status_color,
                "status": g.status.name if g.status else "",
                "phone": g.phone_number or "",
                "assigned_user": assigned,
            }

    def build_file_payload(file_obj, message):
        """Return a safe, correct, fully self-contained file payload."""
        if not file_obj:
            return None

        try:
            file_path = str(file_obj).lstrip("/")

            file_name = message.file_name or urllib.parse.unquote(
                os.path.basename(file_path)
            )

            guessed_mime, _ = mimetypes.guess_type(file_name)
            mime = guessed_mime or message.file_type or "application/octet-stream"

            if file_path.startswith("http"):
                file_url = file_path

            elif settings.DEBUG:
                if not file_path.startswith("media/"):
                    file_url = f"/media/{file_path}"
                else:
                    file_url = f"/{file_path}"

            else:
                resource_type = "video" if mime.startswith("video") else "image"
                cloud = settings.CLOUDINARY_STORAGE["CLOUD_NAME"]
                file_url = f"https://res.cloudinary.com/{cloud}/{resource_type}/upload/{file_path}"

            return {
                "id": message.id,
                "url": file_url,
                "name": file_name,
                "size": None,
                "type": mime,
            }

        except Exception as e:
            import logging

            logging.warning("build_file_payload error: %s", e)
            return None

    def _sender_user(church_member):
        """ChurchMember → CustomUser, tolerating None gracefully."""
        try:
            return church_member.user
        except Exception:
            return None

    parent_payload = None
    if m.parent:
        parent = m.parent
        pu = _sender_user(parent.sender)
        if pu:
            _parent_sender_id = pu.id
            _parent_sender_title = getattr(pu, "title", "")
            _parent_sender_name = pu.full_name or pu.username
        else:
            _parent_sender_id = parent.sender_id  # ChurchMember pk
            _parent_sender_title = ""
            _parent_sender_name = "Unknown"
        parent_payload = {
            "id": parent.id,
            "sender_id": _parent_sender_id,
            "sender_title": _parent_sender_title,
            "sender_name": _parent_sender_name,
            "sender_color": get_user_color(_parent_sender_id),
            "message": (
                parent.message[:50]
                if parent.message
                else "(Attachment)" if parent.file else "(No content)"
            ),
            "guest": (
                {
                    "id": parent.guest_card.id,
                    "name": parent.guest_card.full_name,
                    "title": parent.guest_card.title,
                    "image": (
                        parent.guest_card.picture.url
                        if parent.guest_card.picture
                        else None
                    ),
                    "date_of_visit": (
                        parent.guest_card.date_of_visit.strftime("%Y-%m-%d")
                        if parent.guest_card.date_of_visit
                        else ""
                    ),
                }
                if parent.guest_card
                else None
            ),
            "file": build_file_payload(parent.file, parent),
            "link_preview": (
                {
                    "url": parent.link_url,
                    "title": parent.link_title,
                    "description": parent.link_description,
                    "image": parent.link_image,
                }
                if parent.link_url
                else None
            ),
        }

    file_payload = build_file_payload(m.file, m)

    link_payload = None
    if m.link_url:
        link_payload = {
            "url": m.link_url,
            "title": m.link_title,
            "description": m.link_description,
            "image": m.link_image,
        }

    # pinned_by is now a ChurchMember FK — resolve via .user
    pinned_by_payload = None
    if getattr(m, "pinned_by", None):
        pb_user = _sender_user(m.pinned_by)
        if pb_user:
            pinned_by_payload = {
                "id": pb_user.id,
                "name": pb_user.full_name or pb_user.username,
                "title": getattr(pb_user, "title", ""),
            }

    # sender is a ChurchMember; reach the user directly via sender.user
    sender_user = _sender_user(m.sender)
    sender_id_out = sender_user.id if sender_user else m.sender_id

    rx_summary, rx_total, rx_mine = build_chat_message_reaction_fields(
        m, viewer_member
    )

    return {
        "id": m.id,
        "message": m.message,
        "sender_id": sender_id_out,
        "sender_title": getattr(sender_user, "title", "") if sender_user else "",
        "sender_name": (
            (sender_user.full_name or sender_user.username)
            if sender_user
            else "Unknown"
        ),
        "sender_image": (
            sender_user.image.url if (sender_user and sender_user.image) else None
        ),
        "color": get_user_color(sender_id_out),
        "created_at": m.created_at.isoformat(),
        "guest": guest_payload,
        "reply_to_id": m.parent.id if m.parent else None,
        "parent": parent_payload,
        "file": file_payload,
        "link_preview": link_payload,
        "mentions": mentions_payload,
        "pinned": getattr(m, "pinned", False),
        "pinned_at": m.pinned_at.isoformat() if getattr(m, "pinned_at", None) else None,
        "pinned_by": pinned_by_payload,
        "is_deleted": getattr(m, "is_deleted", False),
        "edited_at": m.edited_at.isoformat() if getattr(m, "edited_at", None) else None,
        "forwarded_from": (
            {
                "id": m.forwarded_from.id,
                "message": (m.forwarded_from.message or "")[:60],
                "room_name": (
                    m.forwarded_from.room.name
                    if getattr(m.forwarded_from, "room", None)
                    else "General Workforce"
                ),
            }
            if getattr(m, "forwarded_from", None) and not m.forwarded_from.is_deleted
            else None
        ),
        # Provide the source room info so the client can render "Forwarded from X"
        "forwarded_from_room_id": (
            m.forwarded_from.room_id
            if getattr(m, "forwarded_from", None) and m.forwarded_from and m.forwarded_from.room_id
            else None
        ),
        "forwarded_from_room_name": (
            (
                m.forwarded_from.room.name
                if getattr(m.forwarded_from, "room", None)
                else "General Workforce"
            )
            if getattr(m, "forwarded_from", None) and m.forwarded_from
            else ""
        ),
        "was_forwarded": (
            m.forwards.filter(is_deleted=False).exists()
            if getattr(m, "pk", None)
            else False
        ),
        "room_id": m.room_id if hasattr(m, "room_id") else None,
        "reaction_summary": rx_summary,
        "total_reactions": rx_total,
        "my_reaction": rx_mine,
    }


# -------------------- Attendance Helpers --------------------


def generate_daily_attendance():
    today = timezone.localdate()
    created_count = 0

    users = CustomUser.objects.filter(is_active=True)

    for user in users:
        available_events = get_available_events_for_user(user)

        for event in available_events:
            _, created = AttendanceRecord.objects.get_or_create(
                user=user, event=event, date=today, defaults={"status": "absent"}
            )
            if created:
                created_count += 1

    return created_count


def validate_church_proximity(
    user_lat, user_lon, church_lat, church_lon, threshold_km=0.100
):
    """
    Ensure user is within threshold_km of the church venue.

    church_lat and church_lon come from church.latitude / church.longitude
    (mandatory fields on the Church model).
    threshold_km defaults to 100m but is overridden by church.attendance_radius_km.
    """
    try:
        lat = float(user_lat)
        lon = float(user_lon)
    except (TypeError, ValueError):
        raise ValidationError(
            "Unable to determine your location. Please enable location access."
        )

    if church_lat is None or church_lon is None:
        raise ValidationError(
            "Church location is not configured. Contact your administrator."
        )

    user_distance = distance((float(church_lat), float(church_lon)), (lat, lon)).km

    if user_distance > threshold_km:
        raise ValidationError(
            f"You appear to be {user_distance:.2f} km away from the venue. "
            "Please select other options instead."
        )


def classify_event_location(event, church, user_lat=None, user_lon=None):
    """
    Classify where the attendance was recorded from.

    Returns one of: onsite, offsite, virtual, or "" when it cannot be
    determined safely.
    """
    mode = (getattr(event, "attendance_mode", "") or "").lower()
    if mode == "virtual":
        return "virtual"

    if mode not in ("physical", "hybrid"):
        return ""

    try:
        if user_lat in (None, "") or user_lon in (None, ""):
            return "offsite" if mode == "hybrid" else "onsite"
        if church.latitude is None or church.longitude is None:
            return "offsite" if mode == "hybrid" else "onsite"
        user_distance = distance(
            (float(church.latitude), float(church.longitude)),
            (float(user_lat), float(user_lon)),
        ).km
    except Exception:
        return "offsite" if mode == "hybrid" else "onsite"

    if mode == "physical":
        return "onsite"
    return (
        "onsite" if user_distance <= float(church.attendance_radius_km) else "offsite"
    )


# -------------------- Calendar + Events --------------------

from datetime import timedelta, date, datetime


def get_calendar_items(user):
    church = _get_church(user)
    today = timezone.localdate()
    start_of_year = date(today.year, 1, 1)
    end_of_year = date(today.year, 12, 31)

    events = get_available_events_for_user(user)
    workforce_member = WorkforceMember.raw_objects.filter(
        member__user=user, is_active=True
    ).first()
    reminders = (
        PersonalReminder.objects.filter(user=workforce_member, date__gte=today)
        if workforce_member
        else PersonalReminder.objects.none()
    )

    calendar_items = []

    weekday_map = {
        "sunday": 6,
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
    }

    color_map = {
        "service": "#3b82f6",
        "meeting": "#10b981",
        "training": "#8b5cf6",
        "followup": "#e11d48",
        "reminder": "#f59e0b",
        "other": "#9ca3af",
    }

    def build_datetime(d, t):
        if not t:
            return d.isoformat()
        return datetime.combine(d, t).isoformat()

    for e in events:
        event_type = (e.event_type or "other").lower()
        color = color_map.get(event_type, "#4dabf7")

        base_event = {
            "title": e.name,
            "type": e.event_type,
            "mode": getattr(e, "mode", ""),
            "color": color,
            "description": getattr(e, "description", ""),
            "time": format_time_value(e.time, church) if e.time else None,
            "duration_days": getattr(e, "duration_days", 1),
            "unit": getattr(e.unit, "name", None),
            "unit_color": (
                getattr(e.unit, "color_class", "bg-gray-800")
                if getattr(e, "unit", None)
                else None
            ),
        }

        if e.date and e.duration_days > 1:
            start = build_datetime(e.date, e.time)
            end = build_datetime(e.date + timedelta(days=e.duration_days - 1), e.time)
            calendar_items.append({**base_event, "start": start, "end": end})

        elif e.date:
            calendar_items.append(
                {**base_event, "start": build_datetime(e.date, e.time)}
            )

        elif e.is_recurring_weekly and e.day_of_week:
            current = start_of_year
            weekday = weekday_map.get(e.day_of_week.lower())
            while current <= end_of_year:
                if current.weekday() == weekday:
                    calendar_items.append(
                        {
                            **base_event,
                            "start": build_datetime(current, e.time),
                        }
                    )
                current += timedelta(days=1)

    for r in reminders:
        calendar_items.append(
            {
                "title": r.title,
                "start": build_datetime(r.date, getattr(r, "time", None)),
                "type": "reminder",
                "mode": "personal",
                "color": color_map["reminder"],
                "description": getattr(r, "note", ""),
                "unit": None,
                "unit_color_class": None,
            }
        )

    return calendar_items


def get_available_events_for_user(user):
    today = timezone.localdate()
    start_of_year = date(today.year, 1, 1)
    end_of_year = date(today.year, 12, 31)

    church = _get_church(user)
    if not church:
        return []

    perms = _get_permissions(user, church)

    base_qs = Event.raw_objects.filter(church=church, is_active=True).filter(
        Q(date__isnull=False, date__lte=end_of_year) | Q(is_recurring_weekly=True)
    )

    if _can(perms, "events.view_all"):
        events = base_qs
    else:
        # Detect pipeline trainees: have a WorkforceTraineeProfile but no
        # WorkforceMember yet — ChurchMembers in the induction pipeline.
        church_member = ChurchMember.raw_objects.filter(
            church=church, user=user, is_active=True
        ).first()

        is_pipeline_trainee = bool(
            church_member
            and WorkforceTraineeProfile.raw_objects.filter(
                church=church, member=church_member, reason="induction"
            ).exists()
            and not WorkforceMember.raw_objects.filter(
                church=church, member=church_member
            ).exists()
        )

        if is_pipeline_trainee:
            # Trainees only see events explicitly flagged for trainee access.
            events = base_qs.filter(trainee_access=True).distinct()
        else:
            # Regular workforce members: church-wide events + their unit events.
            events = base_qs.filter(
                Q(unit__isnull=True)
                | Q(unit__memberships__workforce_member__member__user=user)
                | Q(unit__memberships__trainee_profile__member__user=user)
            ).distinct()

    available_events = []
    for e in events:
        if e.date and not e.attendance_is_open():
            continue
        if e.date and start_of_year <= e.date <= end_of_year:
            available_events.append(e)
        elif e.is_recurring_weekly and e.day_of_week:
            available_events.append(e)

    return available_events


def get_available_units_for_user(user):
    """
    Returns active units the user can select in modals.
    """
    church = _get_church(user)
    if not church:
        return ChurchUnit.objects.none()

    perms = _get_permissions(user, church)
    qs = ChurchUnit.raw_objects.filter(church=church, is_active=True)

    if _can(perms, "unit.view_all"):
        return qs.order_by("name")

    return (
        qs.filter(memberships__workforce_member__member__user=user)
        .distinct()
        .order_by("name")
    )


def expand_unit_events(user, unit_id):
    today = timezone.localdate()
    events = get_available_events_for_user(user)

    if unit_id == "null" or unit_id is None:
        filtered_events = [e for e in events if e.unit is None]
    else:
        try:
            unit_id_int = int(unit_id)
            filtered_events = [
                e for e in events if getattr(e.unit, "id", None) == unit_id_int
            ]
        except (ValueError, TypeError):
            filtered_events = []

    weekday_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }

    first_day = today.replace(day=1)
    next_month = (first_day + timedelta(days=32)).replace(day=1)
    last_day = next_month - timedelta(days=1)

    data = []

    for e in filtered_events:
        if e.is_recurring_weekly and e.day_of_week:
            weekday = weekday_map.get(e.day_of_week.lower())
            if weekday is not None:
                current = first_day
                while current <= last_day:
                    if current.weekday() == weekday:
                        diff_days = (current - today).days
                        if diff_days < 0:
                            tag = "Past"
                        elif diff_days == 0:
                            tag = "Today"
                        elif diff_days <= 7:
                            tag = "Next Week"
                        elif diff_days <= 14:
                            tag = "In Two Weeks"
                        else:
                            tag = "Upcoming"

                        data.append(
                            {
                                "id": e.id,
                                "name": e.name,
                                "unit_name": (
                                    getattr(e.unit, "name", "ChurchForce")
                                    if e.unit
                                    else "ChurchForce"
                                ),
                                "event_type": e.event_type,
                                "attendance_mode": e.attendance_mode,
                                "time": e.time.isoformat() if e.time else None,
                                "date": current,
                                "event_image": (
                                    e.event_image.url if e.event_image else None
                                ),
                                "unit_id": getattr(e.unit, "id", None),
                                "unit_color_class": getattr(
                                    e.unit, "color_class", "bg-warning-800"
                                ),
                                "unit_color_hex": getattr(
                                    e.unit, "color_hex", "#f59f00"
                                ),
                                "is_recurring_weekly": True,
                                "is_active": e.is_active,
                                "registrable": bool(e.registration_link),
                                "registration_link": e.registration_link,
                                "postponed": e.postponed,
                                "tag": tag,
                            }
                        )
                    current += timedelta(days=1)

        elif e.date:
            diff_days = (e.date - today).days
            if diff_days < 0:
                tag = "Past"
            elif diff_days == 0:
                tag = "Today"
            elif diff_days <= 7:
                tag = "Next Week"
            elif diff_days <= 14:
                tag = "In Two Weeks"
            else:
                tag = "Upcoming"

            data.append(
                {
                    "id": e.id,
                    "name": e.name,
                    "unit_name": (
                        getattr(e.unit, "name", "ChurchForce")
                        if e.unit
                        else "ChurchForce"
                    ),
                    "event_type": e.event_type,
                    "attendance_mode": e.attendance_mode,
                    "time": e.time.isoformat() if e.time else None,
                    "date": e.date,
                    "event_image": e.event_image.url if e.event_image else None,
                    "unit_id": getattr(e.unit, "id", None),
                    "unit_color_class": getattr(
                        e.unit, "color_class", "bg-warning-800"
                    ),
                    "unit_color_hex": getattr(e.unit, "color_hex", "#f59f00"),
                    "is_recurring_weekly": e.is_recurring_weekly,
                    "is_active": e.is_active,
                    "registrable": bool(e.registration_link),
                    "registration_link": e.registration_link,
                    "postponed": e.postponed,
                    "tag": tag,
                }
            )

    return data


def get_visible_attendance_records(user, since_date=None):
    """
    Returns AttendanceRecord queryset visible to `user`.
    user (AttendanceRecord.user) is now a ChurchMember FK, so the
    superuser-exclusion path is simply user__user__is_superuser.
    """
    church = _get_church(user)
    if not church:
        return AttendanceRecord.objects.none()

    superuser_filter = Q(user__user__is_superuser=True)

    base_qs = (
        AttendanceRecord.objects.select_related(
            "event",
            "event__unit",
            "user",
            "user__user",
        )
        .filter(church=church)
        .order_by("-date")
    )

    if since_date:
        base_qs = base_qs.filter(date__gte=since_date)

    perms = _get_permissions(user, church)

    if user.is_superuser or _can(perms, "attendance.view_all"):
        return base_qs.exclude(superuser_filter)

    # Non-admin: records for events in the user's units OR the user's own records
    unit_ids = UnitMembership.objects.filter(
        workforce_member__member__user=user
    ).values_list("unit_id", flat=True)

    return base_qs.filter(
        Q(event__unit_id__in=unit_ids)
        | Q(event__unit__isnull=True)  # church-wide events
        | Q(user__user=user)  # the user's own records
    ).exclude(superuser_filter)


def get_visible_clock_records(user, since_date=None):
    """
    Returns ClockRecord queryset visible to `user`.
    user (ClockRecord.user) is now a ChurchMember FK.
    """
    church = _get_church(user)
    if not church:
        return ClockRecord.objects.none()

    superuser_filter = Q(user__user__is_superuser=True)

    base_qs = (
        ClockRecord.objects.select_related("event", "event__unit", "user", "user__user")
        .filter(church=church)
        .order_by("-date")
    )

    if since_date:
        base_qs = base_qs.filter(date__gte=since_date)

    perms = _get_permissions(user, church)

    if user.is_superuser or _can(perms, "attendance.view_all"):
        return base_qs.exclude(superuser_filter)

    unit_ids = UnitMembership.objects.filter(
        workforce_member__member__user=user
    ).values_list("unit_id", flat=True)

    return base_qs.filter(
        Q(event__unit_id__in=unit_ids)
        | Q(event__unit__isnull=True)
        | Q(user__user=user)
    ).exclude(superuser_filter)
