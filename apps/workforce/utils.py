from django.contrib.auth.models import Group
import requests, mimetypes, os, re, urllib.parse, urllib
from bs4 import BeautifulSoup
from accounts.models import CustomUser, ChurchMember
from guests.models import GuestEntry
from core.utils.colors import resolve_color

def get_user_color(user_id):
    return resolve_color(f"user:{user_id}", variant="hex"), handle_file_upload
from django.db.models.fields.files import FieldFile
from django.core.files.storage import default_storage
from django.utils import timezone
from services.models import Event
from .models import AttendanceRecord, PersonalReminder, UserActivity, ChatMessage, ClockRecord
from django.db.models import Q
from django.conf import settings
from geopy.distance import distance
from django.core.exceptions import ValidationError
from cloudinary.utils import cloudinary_url
from units.models import ChurchUnit, UnitMembership
from permissions.services.resolver import PermissionResolver


import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup

YOUTUBE_OEMBED = "https://www.youtube.com/oembed?format=json&url="


def _get_church(user):
    if not user or not user.is_authenticated:
        return None
    membership = ChurchMember.raw_objects.filter(user=user, is_active=True).select_related("church").first()
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
        desc_tag = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
        image_tag = soup.find("meta", property="og:image")

        title = (
            title_tag["content"]
            if title_tag and title_tag.has_attr("content")
            else (title_tag.string if title_tag else "")
        )

        return {
            "url": url,
            "title": title,
            "description": desc_tag["content"] if desc_tag and desc_tag.has_attr("content") else "",
            "image": image_tag["content"] if image_tag and image_tag.has_attr("content") else "",
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

    users = list(CustomUser.objects.filter(church_memberships__church=church).distinct())
    mention_map = {}
    for u in users:
        display = f"@{ (u.title + ' ') if getattr(u, 'title', None) else '' }{ (u.full_name or u.username) }".strip()
        mention_map[display] = u
    regex = re.compile(r"(" + "|".join(map(re.escape, mention_map.keys())) + r")") if mention_map else None
    return mention_map, regex


def cloudinary_url(public_id, resource_type):
    cloud = settings.CLOUDINARY_STORAGE["CLOUD_NAME"]
    rtype = "video" if resource_type.startswith("video") else "image"

    return f"https://res.cloudinary.com/{cloud}/{rtype}/upload/{public_id}"



def serialize_message(m, mention_map=None, mention_regex=None):
    """Unified serializer for both WebSocket + Views."""
    mentions_payload = []
    if mention_regex and m.message:
        found = set(mention_regex.findall(m.message))
        for token in found:
            u = mention_map.get(token)
            if u:
                mentions_payload.append({
                    "id": u.id,
                    "username": u.username,
                    "title": getattr(u, "title", ""),
                    "name": u.full_name or u.username,
                    "color": get_user_color(u.id),
                })

    guest_payload = None
    if m.guest_card:
        g = GuestEntry.objects.select_related("assigned_to").get(id=m.guest_card.id)
        guest_payload = {
            "id": g.id,
            "name": g.full_name,
            "custom_id": g.custom_id,
            "image": g.picture.url if g.picture else None,
            "title": g.title,
            "date_of_visit": g.date_of_visit.strftime("%Y-%m-%d") if g.date_of_visit else "",
            "assigned_user": {
                "id": g.assigned_to.id,
                "title": g.assigned_to.title,
                "full_name": g.assigned_to.full_name,
                "image": g.assigned_to.image.url if g.assigned_to.image else None,
            } if g.assigned_to else None
        }

    def build_file_payload(file_obj, message):
        """Return a safe, correct, fully self-contained file payload."""
        if not file_obj:
            return None

        try:
            file_path = str(file_obj).lstrip("/")

            file_name = (
                message.file_name or 
                urllib.parse.unquote(os.path.basename(file_path))
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

    parent_payload = None
    if m.parent:
        parent = m.parent
        parent_payload = {
            "id": parent.id,
            "sender_id": parent.sender.id,
            "sender_title": getattr(parent.sender, "title", ""),
            "sender_name": parent.sender.full_name or parent.sender.username,
            "sender_color": get_user_color(parent.sender.id),
            "message": parent.message[:50] if parent.message else "(Attachment)" if parent.file else "(No content)",
            "guest": {
                "id": parent.guest_card.id,
                "name": parent.guest_card.full_name,
                "title": parent.guest_card.title,
                "image": parent.guest_card.picture.url if parent.guest_card.picture else None,
                "date_of_visit": parent.guest_card.date_of_visit.strftime("%Y-%m-%d") if parent.guest_card.date_of_visit else "",
            } if parent.guest_card else None,
            "file": build_file_payload(parent.file, parent),
            "link_preview": {
                "url": parent.link_url,
                "title": parent.link_title,
                "description": parent.link_description,
                "image": parent.link_image,
            } if parent.link_url else None,
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

    pinned_by_payload = None
    if getattr(m, "pinned_by", None):
        pinned_by_payload = {
            "id": m.pinned_by.id,
            "name": m.pinned_by.full_name or m.pinned_by.username,
            "title": getattr(m.pinned_by, "title", ""),
        }

    return {
        "id": m.id,
        "message": m.message,
        "sender_id": m.sender.id,
        "sender_title": getattr(m.sender, "title", ""),
        "sender_name": m.sender.full_name or m.sender.username,
        "sender_image": m.sender.image.url if m.sender.image else None,
        "color": get_user_color(m.sender.id),
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
                user=user,
                event=event,
                date=today,
                defaults={"status": "absent"}
            )
            if created:
                created_count += 1

    return created_count



def validate_church_proximity(user_lat, user_lon, church_lat, church_lon, threshold_km=0.100):
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
        raise ValidationError("Unable to determine your location. Please enable location access.")

    if church_lat is None or church_lon is None:
        raise ValidationError("Church location is not configured. Contact your administrator.")

    user_distance = distance((float(church_lat), float(church_lon)), (lat, lon)).km

    if user_distance > threshold_km:
        raise ValidationError(
            f"You appear to be {user_distance:.2f} km away from the venue. "
            "Please select other options instead."
        )


# -------------------- Calendar + Events --------------------

from datetime import timedelta, date, datetime


def get_calendar_items(user):
    today = timezone.localdate()
    start_of_year = date(today.year, 1, 1)
    end_of_year = date(today.year, 12, 31)

    events = get_available_events_for_user(user)
    reminders = PersonalReminder.objects.filter(user=user, date__gte=today)

    calendar_items = []

    weekday_map = {
        'sunday': 6,
        'monday': 0,
        'tuesday': 1,
        'wednesday': 2,
        'thursday': 3,
        'friday': 4,
        'saturday': 5,
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
            "time": e.time.strftime("%H:%M") if e.time else None,
            "duration_days": getattr(e, "duration_days", 1),
            "team": getattr(e.unit, "name", None),
            "team_color": getattr(e.unit, "color_class", "bg-gray-800") if getattr(e, "unit", None) else None,
        }

        if e.date and e.duration_days > 1:
            start = build_datetime(e.date, e.time)
            end = build_datetime(e.date + timedelta(days=e.duration_days - 1), e.time)
            calendar_items.append({**base_event, "start": start, "end": end})

        elif e.date:
            calendar_items.append({**base_event, "start": build_datetime(e.date, e.time)})

        elif e.is_recurring_weekly and e.day_of_week:
            current = start_of_year
            weekday = weekday_map.get(e.day_of_week.lower())
            while current <= end_of_year:
                if current.weekday() == weekday:
                    calendar_items.append({
                        **base_event,
                        "start": build_datetime(current, e.time),
                    })
                current += timedelta(days=1)

    for r in reminders:
        calendar_items.append({
            "title": r.title,
            "start": build_datetime(r.date, getattr(r, "time", None)),
            "type": "reminder",
            "mode": "personal",
            "color": color_map["reminder"],
            "description": getattr(r, "note", ""),
            "team": None,
            "team_color_class": None,
        })

    return calendar_items


def get_available_events_for_user(user):
    today = timezone.localdate()
    start_of_year = date(today.year, 1, 1)
    end_of_year = date(today.year, 12, 31)

    church = _get_church(user)
    if not church:
        return []

    perms = _get_permissions(user, church)

    base_qs = Event.raw_objects.filter(church=church,
        is_active=True
    ).filter(
        Q(date__isnull=False, date__lte=end_of_year) |
        Q(is_recurring_weekly=True)
    )

    if _can(perms, "events.view_all"):
        events = base_qs
    else:
        events = base_qs.filter(
            Q(unit__isnull=True) | Q(unit__memberships__workforce_member__member__user=user)
        )

    available_events = []
    for e in events:
        if e.date and start_of_year <= e.date <= end_of_year:
            available_events.append(e)
        elif e.is_recurring_weekly and e.day_of_week:
            available_events.append(e)

    return available_events


def get_available_teams_for_user(user):
    """
    Returns active units the user can select in modals.
    """
    church = _get_church(user)
    if not church:
        return ChurchUnit.objects.none()

    perms = _get_permissions(user, church)
    qs = ChurchUnit.raw_objects.filter(church=church, is_active=True)

    if _can(perms, "team.view_all"):
        return qs.order_by("name")

    return qs.filter(memberships__workforce_member__member__user=user).distinct().order_by("name")


def expand_team_events(user, team_id):
    today = timezone.localdate()
    events = get_available_events_for_user(user)

    if team_id == "null" or team_id is None:
        filtered_events = [e for e in events if e.unit is None]
    else:
        try:
            team_id_int = int(team_id)
            filtered_events = [e for e in events if getattr(e.unit, "id", None) == team_id_int]
        except (ValueError, TypeError):
            filtered_events = []

    weekday_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
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

                        data.append({
                            "id": e.id,
                            "name": e.name,
                            "team_name": getattr(e.unit, "name", "ChurchForce") if e.unit else "ChurchForce",
                            "event_type": e.event_type,
                            "attendance_mode": e.attendance_mode,
                            "time": e.time.isoformat() if e.time else None,
                            "date": current,
                            "event_image": e.event_image.url if e.event_image else None,
                            "team_id": getattr(e.unit, "id", None),
                            "team_color_class": getattr(e.unit, "color_class", "bg-warning-800"),
                            "team_color_hex": getattr(e.unit, "color_hex", "#f59f00"),
                            "is_recurring_weekly": True,
                            "is_active": e.is_active,
                            "registrable": bool(e.registration_link),
                            "registration_link": e.registration_link,
                            "postponed": e.postponed,
                            "tag": tag,
                        })
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

            data.append({
                "id": e.id,
                "name": e.name,
                "team_name": getattr(e.unit, "name", "ChurchForce") if e.unit else "ChurchForce",
                "event_type": e.event_type,
                "attendance_mode": e.attendance_mode,
                "time": e.time.isoformat() if e.time else None,
                "date": e.date,
                "event_image": e.event_image.url if e.event_image else None,
                "team_id": getattr(e.unit, "id", None),
                "team_color_class": getattr(e.unit, "color_class", "bg-warning-800"),
                "team_color_hex": getattr(e.unit, "color_hex", "#f59f00"),
                "is_recurring_weekly": e.is_recurring_weekly,
                "is_active": e.is_active,
                "registrable": bool(e.registration_link),
                "registration_link": e.registration_link,
                "postponed": e.postponed,
                "tag": tag,
            })

    return data


def get_visible_attendance_records(user, since_date=None):
    """
    Returns attendance records the user is allowed to see based on permissions.
    """
    church = _get_church(user)
    if not church:
        return AttendanceRecord.objects.none()

    base_qs = AttendanceRecord.objects.select_related(
        "event", "user", "team", "event__unit"
    ).filter(church=church).order_by("-date")

    if since_date:
        base_qs = base_qs.filter(date__gte=since_date)

    perms = _get_permissions(user, church)

    if user.is_superuser or _can(perms, "attendance.view_all"):
        return base_qs.exclude(user__is_superuser=True)

    unit_ids = UnitMembership.objects.filter(
        workforce_member__member__user=user
    ).values_list("unit_id", flat=True)

    records = base_qs.filter(
        Q(event__unit_id__in=unit_ids) | Q(team_id__in=unit_ids)
    ).exclude(user__is_superuser=True)

    return records


def get_visible_clock_records(user, since_date=None):
    """
    Returns clock records the user is allowed to see based on permissions.
    """
    church = _get_church(user)
    if not church:
        return ClockRecord.objects.none()

    base_qs = ClockRecord.objects.select_related("event", "user", "team").filter(church=church).order_by("-date")

    if since_date:
        base_qs = base_qs.filter(date__gte=since_date)

    perms = _get_permissions(user, church)

    if user.is_superuser or _can(perms, "attendance.view_all"):
        return base_qs.exclude(user__is_superuser=True)

    unit_ids = UnitMembership.objects.filter(
        workforce_member__member__user=user
    ).values_list("unit_id", flat=True)

    records = base_qs.filter(
        Q(team_id__in=unit_ids) |
        Q(event__unit_id__in=unit_ids)
    )

    return records.exclude(user__is_superuser=True)