"""
music/views.py — ChurchForce Music Module

Views: track library, track detail/edit, chord chart viewer,
setlist builder, rehearsal board, mix notes.

All views are unit-scoped: a music unit member can only see
tracks and setlists for their unit (plus church-wide tracks).
Admins see everything.
"""

import json
import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from music.models import (
    Track,
    LyricsSection,
    ChordChart,
    Setlist,
    SetlistSong,
    RehearsalSession,
    MixNote,
    SongMixPreset,
    TrackTag,
    MUSICAL_KEYS,
)
from music.services.catalog import catalog_genres

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _church(request):
    return getattr(request, "church", None)


def _can(request, perm, unit=None):
    if request.user.is_superuser:
        return True
    p = getattr(request, "permissions", None)
    return bool(p and p.can(perm, unit=unit))


def _is_admin(request):
    return request.user.is_superuser or _can(request, "dashboard.admin")


def _module_ok(church, unit=None):
    s = getattr(church, "settings", None)
    if not s or not s.enable_music_module:
        return False
    if unit:
        return unit.music_module
    return True


def _unit_tracks(church, unit):
    """Tracks visible to this unit: unit-specific + church-wide."""
    return (
        Track.raw_objects.filter(church=church, is_active=True)
        .filter(models_q_unit_or_null(unit))
        .order_by("title")
        .prefetch_related("tags", "sections")
    )


def models_q_unit_or_null(unit):
    from django.db.models import Q

    return Q(unit=unit) | Q(unit__isnull=True)


# ── Track Library ─────────────────────────────────────────────────────────────


@login_required
def track_library(request, unit_slug=None):
    """Main music library for a unit or church-wide (admin)."""
    church = _church(request)
    if not church:
        return HttpResponseForbidden()

    unit = None
    if unit_slug:
        from units.models import ChurchUnit

        unit = get_object_or_404(
            ChurchUnit.raw_objects, church=church, slug=unit_slug, is_active=True
        )
        if not _module_ok(church, unit):
            messages.error(request, "Music module is not enabled for this unit.")
            return redirect("units:detail", slug=unit_slug)

    can_manage = _is_admin(request) or _can(request, "music.manage")

    if request.method == "POST" and can_manage:
        action = (request.POST.get("action") or "").strip()
        if action == "import_catalog_genre":
            from music.services.catalog import seed_tracks_for_church

            genre = (request.POST.get("genre") or "").strip().lower()
            if genre not in catalog_genres():
                messages.warning(request, "Invalid catalog genre selected.")
                return redirect(request.path)
            stats = seed_tracks_for_church(
                church,
                unit=unit,
                genres=[genre],
            )
            messages.success(
                request,
                f"Imported {stats['created']} song(s) from {genre.replace('_', ' ').title()} catalog.",
            )
            return redirect(request.path)

    tracks = Track.raw_objects.filter(church=church, is_active=True)
    if unit and not _is_admin(request):
        from django.db.models import Q

        tracks = tracks.filter(Q(unit=unit) | Q(unit__isnull=True))

    # Filters
    q = request.GET.get("q", "").strip()
    tag_id = request.GET.get("tag", "")
    key = request.GET.get("key", "")
    if q:
        tracks = tracks.filter(title__icontains=q) | tracks.filter(artist__icontains=q)
    if tag_id:
        tracks = tracks.filter(tags__id=tag_id)
    if key:
        tracks = tracks.filter(original_key=key)

    tracks = tracks.distinct().prefetch_related("tags", "sections").order_by("title")
    tags = TrackTag.raw_objects.filter(church=church).order_by("name")

    return render(
        request,
        "music/track_library.html",
        {
            "page_title": f"{unit.name} — Music Library" if unit else "Music Library",
            "unit": unit,
            "tracks": tracks,
            "tags": tags,
            "keys": MUSICAL_KEYS,
            "q": q,
            "active_tag": tag_id,
            "active_key": key,
            "can_manage": can_manage,
            "catalog_genres": [
                {"key": g, "label": g.replace("_", " ").title()}
                for g in catalog_genres()
            ],
        },
    )


@login_required
def track_detail(request, uid):
    """Track detail: lyrics, chord charts, setlist history, mix notes."""
    church = _church(request)
    track = get_object_or_404(Track.raw_objects, church=church, uid=uid, is_active=True)
    charts = track.chord_charts.all()
    sections = track.sections.order_by("order")
    setlist_songs = (
        SetlistSong.objects.filter(track=track, setlist__church=church)
        .select_related("setlist__event")
        .order_by("-setlist__created_at")[:10]
    )

    return render(
        request,
        "music/track_detail.html",
        {
            "page_title": track.title,
            "track": track,
            "charts": charts,
            "sections": sections,
            "setlist_songs": setlist_songs,
            "keys": MUSICAL_KEYS,
            "can_edit": _is_admin(request),
        },
    )


@login_required
def mixer_view(request, uid):
    """
    Pro mixer surface for one track: custom mixes, part isolation, transpose helpers,
    and rehearsal/presentation context.
    """
    church = _church(request)
    track = get_object_or_404(Track.raw_objects, church=church, uid=uid, is_active=True)
    stems = track.stems.filter(is_active=True).order_by("order", "name")
    recent_setlists = (
        SetlistSong.objects.filter(track=track, setlist__church=church)
        .select_related("setlist", "setlist__event")
        .order_by("-setlist__created_at")[:8]
    )
    from django.utils import timezone as _tz

    _today_m = _tz.localdate()
    # Upcoming rehearsal for this track: concrete dated sessions from today,
    # fallback to recurring-weekly sessions if none found.
    upcoming_rehearsal = (
        RehearsalSession.raw_objects.filter(
            church=church,
            completed=False,
            setlist__songs__track=track,
            event__date__gte=_today_m,
            event__is_recurring_weekly=False,
        )
        .select_related("event", "event__prepares_for_service", "setlist", "unit")
        .order_by("event__date")
        .first()
    ) or (
        RehearsalSession.raw_objects.filter(
            church=church,
            completed=False,
            setlist__songs__track=track,
            event__is_recurring_weekly=True,
        )
        .select_related("event", "event__prepares_for_service", "setlist", "unit")
        .order_by("event__date", "event__time")
        .first()
    )
    can_manage = _is_admin(request) or _can(request, "music.manage")
    member = getattr(request, "member", None)
    mix_preset = (
        SongMixPreset.raw_objects.filter(
            church=church, track=track, member=member, name="My Mix"
        ).first()
        if member
        else None
    )
    return render(
        request,
        "music/mixer.html",
        {
            "page_title": f"Mixer — {track.title}",
            "track": track,
            "stems": stems,
            "recent_setlists": recent_setlists,
            "upcoming_rehearsal": upcoming_rehearsal,
            "can_manage": can_manage,
            "mix_preset": mix_preset,
            "mix_preset_channels_json": json.dumps(
                (mix_preset.channel_state if mix_preset else {}) or {}
            ),
            "mix_preset_order_json": json.dumps(
                (mix_preset.strip_order if mix_preset else []) or []
            ),
        },
    )


@login_required
@require_POST
def mixer_ai_analyze(request, uid):
    """Analyze song structure with Claude — returns structured JSON dict."""
    church = _church(request)
    track = get_object_or_404(Track.raw_objects, church=church, uid=uid, is_active=True)
    sections = list(
        track.sections.order_by("order").values("section_type", "label", "content")
    )
    from core.ai_skills import analyze_song_structure

    result = analyze_song_structure(
        title=track.title,
        artist=track.artist or "",
        lyrics=track.lyrics or "",
        key=track.original_key or "",
        tempo=track.tempo,
        sections=[
            {
                "type": item.get("section_type", ""),
                "label": item.get("label", ""),
                "content": item.get("content", ""),
            }
            for item in sections
        ],
    )
    # result is now always a dict; include legacy markdown for old clients
    return JsonResponse({"ok": True, "analysis": result})


@login_required
@require_http_methods(["GET", "POST"])
def stem_upload(request, uid):
    """Upload or register an external-URL stem for a track."""
    from music.models import SongStem

    church = _church(request)
    track = get_object_or_404(Track.raw_objects, church=church, uid=uid, is_active=True)
    if not _is_admin(request) and not _can(request, "music.manage"):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        family = request.POST.get("family", "other")
        audio_url = (request.POST.get("audio_url") or "").strip()
        gain = float(request.POST.get("default_gain", 0.8) or 0.8)
        pan = float(request.POST.get("default_pan", 0.0) or 0.0)
        order = int(request.POST.get("order", track.stems.count()) or 0)

        if not name:
            return JsonResponse({"ok": False, "error": "Name required"})

        stem, created = SongStem.objects.update_or_create(
            church=church,
            track=track,
            name=name,
            defaults={
                "family": family,
                "audio_url": audio_url,
                "default_gain": gain,
                "default_pan": pan,
                "order": order,
            },
        )

        if "file" in request.FILES:
            from core.storage import smart_upload, StorageError

            f_file = request.FILES["file"]
            try:
                result = smart_upload(
                    f_file,
                    folder=f"music/stems/{track.uid}",
                    resource_type="video",
                )
                stem.audio_file = result["path"]
                stem.save(update_fields=["audio_file"])
            except StorageError as exc:
                return JsonResponse({"ok": False, "error": str(exc)}, status=500)

        return JsonResponse(
            {
                "ok": True,
                "id": stem.id,
                "name": stem.name,
                "family": stem.family,
                "url": stem.playback_url,
                "gain": stem.default_gain,
                "pan": stem.default_pan,
                "muted": stem.default_muted,
                "order": stem.order,
            }
        )
    return JsonResponse({"ok": True, "stem_count": track.stems.count()})


@login_required
@require_POST
def stem_delete(request, uid, stem_id):
    """Remove a stem from a track."""
    from music.models import SongStem

    church = _church(request)
    track = get_object_or_404(Track.raw_objects, church=church, uid=uid, is_active=True)
    if not _is_admin(request) and not _can(request, "music.manage"):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)
    SongStem.objects.filter(pk=stem_id, track=track).delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def stem_save_preset(request, uid):
    """Save a named mix preset for a track (beyond the default 'My Mix')."""
    church = _church(request)
    track = get_object_or_404(Track.raw_objects, church=church, uid=uid, is_active=True)
    member = getattr(request, "member", None)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    name = (payload.get("name") or "My Mix").strip()
    preset, _ = SongMixPreset.raw_objects.update_or_create(
        church=church,
        track=track,
        member=member,
        name=name,
        defaults={
            "transpose_semitones": int(payload.get("transpose", 0) or 0),
            "channel_state": payload.get("channels", {}) or {},
            "strip_order": payload.get("order", []) or [],
            "is_active": True,
        },
    )
    return JsonResponse({"ok": True, "preset_id": preset.id, "name": preset.name})


@login_required
@require_POST
def mixer_save_state(request, uid):
    """Persist custom mix state for the current member."""
    church = _church(request)
    track = get_object_or_404(Track.raw_objects, church=church, uid=uid, is_active=True)
    member = getattr(request, "member", None)
    if not member:
        return JsonResponse({"ok": False, "error": "No member context."}, status=400)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        payload = {}

    preset, _ = SongMixPreset.raw_objects.update_or_create(
        church=church,
        track=track,
        member=member,
        name="My Mix",
        defaults={
            "transpose_semitones": int(payload.get("transpose", 0) or 0),
            "channel_state": payload.get("channels", {}) or {},
            "strip_order": payload.get("order", []) or [],
            "is_active": True,
        },
    )
    return JsonResponse({"ok": True, "preset_id": preset.id})


@login_required
@require_http_methods(["GET", "POST"])
def track_edit(request, uid=None):
    """Create or edit a Track."""
    church = _church(request)
    if not _is_admin(request) and not _can(request, "music.manage"):
        return HttpResponseForbidden()

    track = None
    if uid:
        track = get_object_or_404(Track.raw_objects, church=church, uid=uid)

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        artist = request.POST.get("artist", "").strip()
        key = request.POST.get("original_key", "")
        tempo = request.POST.get("tempo") or None
        time_sig = request.POST.get("time_signature", "4/4")
        youtube = request.POST.get("youtube_url", "").strip()
        ccli = request.POST.get("ccli_id", "").strip()
        lyrics = request.POST.get("lyrics", "").strip()
        unit_id = request.POST.get("unit_id") or None

        if not title:
            messages.error(request, "Title is required.")
        else:
            from units.models import ChurchUnit

            unit = (
                ChurchUnit.raw_objects.filter(church=church, id=unit_id).first()
                if unit_id
                else None
            )
            member = getattr(request, "member", None)

            if track:
                track.title = title
                track.artist = artist
                track.original_key = key
                track.tempo = tempo
                track.time_signature = time_sig
                track.youtube_url = youtube
                track.ccli_id = ccli
                track.lyrics = lyrics
                track.unit = unit
                track.save()
                messages.success(request, "Track updated.")
            else:
                track = Track.raw_objects.create(
                    church=church,
                    title=title,
                    artist=artist,
                    original_key=key,
                    tempo=tempo,
                    time_signature=time_sig,
                    youtube_url=youtube,
                    ccli_id=ccli,
                    lyrics=lyrics,
                    unit=unit,
                    added_by=member,
                )
                messages.success(request, f"Track '{title}' added to library.")

            # Tag assignments
            tag_ids = request.POST.getlist("tags")
            track.tags.set(TrackTag.raw_objects.filter(church=church, id__in=tag_ids))

            return redirect("music:track_detail", uid=track.uid)

    from units.models import ChurchUnit

    units = ChurchUnit.raw_objects.filter(
        church=church, is_active=True, music_module=True
    ).order_by("name")
    tags = TrackTag.raw_objects.filter(church=church).order_by("name")

    return render(
        request,
        "music/track_edit.html",
        {
            "page_title": "Edit Track" if track else "Add Track",
            "track": track,
            "units": units,
            "tags": tags,
            "keys": MUSICAL_KEYS,
            "selected_tags": (
                list(track.tags.values_list("id", flat=True)) if track else []
            ),
        },
    )


# ── Lyrics Sections ───────────────────────────────────────────────────────────


@login_required
@require_POST
def section_save(request, track_uid):
    """Create or update a LyricsSection. Returns JSON."""
    church = _church(request)
    track = get_object_or_404(Track.raw_objects, church=church, uid=track_uid)
    if not _is_admin(request) and not _can(request, "music.manage"):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    section_id = request.POST.get("section_id") or None
    section_type = request.POST.get("section_type", "verse")
    label = request.POST.get("label", "").strip()
    content = request.POST.get("content", "").strip()
    repeat_count = int(request.POST.get("repeat_count", 1))
    order = int(request.POST.get("order", 0))

    if section_id:
        sec = get_object_or_404(LyricsSection, pk=section_id, track=track)
        sec.section_type = section_type
        sec.label = label
        sec.content = content
        sec.repeat_count = repeat_count
        sec.order = order
        sec.label = ""  # force regenerate
        sec.save()
    else:
        sec = LyricsSection.objects.create(
            church=church,
            track=track,
            section_type=section_type,
            label=label,
            content=content,
            repeat_count=repeat_count,
            order=order,
        )

    return JsonResponse(
        {
            "ok": True,
            "id": sec.pk,
            "label": sec.label,
            "type": sec.section_type,
            "content": sec.content,
        }
    )


@login_required
@require_POST
def section_delete(request, track_uid, section_id):
    church = _church(request)
    track = get_object_or_404(Track.raw_objects, church=church, uid=track_uid)
    if not _is_admin(request):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)
    LyricsSection.objects.filter(pk=section_id, track=track).delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def section_ai_structure(request, track_uid):
    """Run AI lyrics structuring on the track's raw lyrics text."""
    church = _church(request)
    track = get_object_or_404(Track.raw_objects, church=church, uid=track_uid)
    if not _is_admin(request) and not _can(request, "music.manage"):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    if not track.lyrics:
        return JsonResponse({"ok": False, "error": "No lyrics to structure."})

    from core.ai_skills import structure_lyrics

    sections = structure_lyrics(track.lyrics, title=track.title, artist=track.artist)
    if not sections:
        return JsonResponse({"ok": False, "error": "AI structuring failed. Try again."})

    # Wipe existing sections and create fresh ones
    LyricsSection.objects.filter(track=track).delete()
    created = []
    for i, s in enumerate(sections):
        sec = LyricsSection.objects.create(
            church=church,
            track=track,
            section_type=s.get("type", "verse"),
            label=s.get("label", ""),
            content=s.get("content", ""),
            order=i,
        )
        created.append(
            {
                "id": sec.pk,
                "label": sec.label,
                "type": sec.section_type,
                "content": sec.content,
            }
        )

    return JsonResponse({"ok": True, "sections": created})


# ── Chord Charts ──────────────────────────────────────────────────────────────


@login_required
@require_POST
def chart_save(request, track_uid):
    """Create or update a ChordChart."""
    church = _church(request)
    track = get_object_or_404(Track.raw_objects, church=church, uid=track_uid)
    chart_id = request.POST.get("chart_id") or None
    member = getattr(request, "member", None)
    key = request.POST.get("key", "")
    label = request.POST.get("label", "").strip()
    content = request.POST.get("content", "").strip()
    is_default = request.POST.get("is_default") == "on"

    if chart_id:
        chart = get_object_or_404(ChordChart, pk=chart_id, track=track)
        chart.key = key
        chart.label = label
        chart.content = content
    else:
        chart = ChordChart(
            church=church,
            track=track,
            key=key,
            label=label,
            content=content,
            created_by=member,
        )

    if is_default:
        ChordChart.objects.filter(track=track).update(is_default=False)
        chart.is_default = True

    chart.save()
    return JsonResponse(
        {
            "ok": True,
            "id": chart.pk,
            "key": chart.key,
            "label": chart.label,
            "is_default": chart.is_default,
        }
    )


# ── Setlist ───────────────────────────────────────────────────────────────────


@login_required
def setlist_list(request, unit_slug=None):
    church = _church(request)
    qs = Setlist.raw_objects.filter(church=church).select_related("event", "unit")

    unit = None
    if unit_slug:
        from units.models import ChurchUnit

        unit = get_object_or_404(
            ChurchUnit.raw_objects, church=church, slug=unit_slug, is_active=True
        )
        qs = qs.filter(unit=unit)
    elif not _is_admin(request):
        # Non-admins only see their unit's setlists
        member = getattr(request, "member", None)
        if member:
            from units.models import UnitMembership

            unit_ids = UnitMembership.raw_objects.filter(
                church=church, workforce_member__member=member, is_active=True
            ).values_list("unit_id", flat=True)
            qs = qs.filter(unit_id__in=unit_ids)

    qs = qs.order_by("-created_at")
    return render(
        request,
        "music/setlist_list.html",
        {
            "page_title": "Setlists",
            "setlists": qs,
            "unit": unit,
        },
    )


@login_required
def setlist_detail(request, uid):
    """Setlist builder — drag-and-drop song ordering + who's playing what."""
    church = _church(request)
    setlist = get_object_or_404(Setlist.raw_objects, church=church, uid=uid)
    songs = (
        setlist.songs.select_related("track", "track__unit")
        .prefetch_related(
            "track__sections",
            "track__chord_charts",
            "assignments__member__user",
        )
        .order_by("order")
    )

    # Available tracks for the add-song modal
    from django.db.models import Q

    tracks = (
        Track.raw_objects.filter(church=church, is_active=True)
        .filter(Q(unit=setlist.unit) | Q(unit__isnull=True))
        .order_by("title")
    )

    can_edit = not setlist.finalized and (
        _is_admin(request) or _can(request, "music.manage")
    )

    # Members available for assignment (unit members first, then all)
    from accounts.models import ChurchMember
    from music.models import SetlistSongAssignment

    assignable_members = (
        ChurchMember.raw_objects.filter(church=church, is_active=True)
        .select_related("user")
        .order_by("user__full_name")
    )

    # Build assignments map: {song_id: [assignment, ...]}
    all_assignments = (
        SetlistSongAssignment.raw_objects.filter(
            church=church,
            setlist_song__setlist=setlist,
        )
        .select_related("member__user")
        .order_by("setlist_song__order", "instrument")
    )
    assignments_by_song = {}
    for a in all_assignments:
        assignments_by_song.setdefault(a.setlist_song_id, []).append(a)

    # Current member (for "my songs" link)
    my_member = getattr(request, "member", None)

    from music.models import SetlistSongAssignment as _SSA
    instrument_choices = _SSA.INSTRUMENT_CHOICES

    return render(
        request,
        "music/setlist_detail.html",
        {
            "page_title": setlist.title,
            "setlist": setlist,
            "songs": songs,
            "tracks": tracks,
            "keys": MUSICAL_KEYS,
            "can_edit": can_edit,
            "assignable_members": assignable_members,
            "assignments_by_song": assignments_by_song,
            "instrument_choices": instrument_choices,
            "my_member": my_member,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def setlist_edit(request, uid=None, unit_slug=None):
    """Create or edit a Setlist."""
    church = _church(request)
    if not _is_admin(request) and not _can(request, "music.manage"):
        return HttpResponseForbidden()

    setlist = None
    if uid:
        setlist = get_object_or_404(Setlist.raw_objects, church=church, uid=uid)
        if setlist.finalized:
            messages.warning(request, "Finalized setlists cannot be edited.")
            return redirect("music:setlist_detail", uid=uid)

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        event_id = request.POST.get("event_id") or None
        unit_id = request.POST.get("unit_id") or None
        notes = request.POST.get("notes", "").strip()

        from services.models import Event
        from units.models import ChurchUnit

        event = (
            Event.raw_objects.filter(church=church, id=event_id).first()
            if event_id
            else None
        )
        unit = (
            ChurchUnit.raw_objects.filter(church=church, id=unit_id).first()
            if unit_id
            else None
        )
        member = getattr(request, "member", None)

        if setlist:
            setlist.title = title
            setlist.event = event
            setlist.unit = unit
            setlist.notes = notes
            setlist.save()
        else:
            setlist = Setlist.raw_objects.create(
                church=church,
                title=title,
                event=event,
                unit=unit,
                notes=notes,
                created_by=member,
            )
        return redirect("music:setlist_detail", uid=setlist.uid)

    from services.models import Event
    from units.models import ChurchUnit

    events = Event.raw_objects.filter(church=church).order_by("-date", "name")[:50]
    units = ChurchUnit.raw_objects.filter(
        church=church, is_active=True, music_module=True
    ).order_by("name")
    return render(
        request,
        "music/setlist_edit.html",
        {
            "page_title": "Edit Setlist" if setlist else "New Setlist",
            "setlist": setlist,
            "events": events,
            "units": units,
        },
    )


@login_required
@require_POST
def setlist_add_song(request, uid):
    church = _church(request)
    setlist = get_object_or_404(Setlist.raw_objects, church=church, uid=uid)
    if setlist.finalized:
        return JsonResponse({"ok": False, "error": "Setlist is finalized."})

    track_id = request.POST.get("track_id")
    track = get_object_or_404(Track.raw_objects, church=church, id=track_id)

    if SetlistSong.objects.filter(setlist=setlist, track=track).exists():
        return JsonResponse({"ok": False, "error": "Song already in setlist."})

    order = setlist.songs.count()
    ss = SetlistSong.objects.create(
        church=church,
        setlist=setlist,
        track=track,
        order=order,
        performance_key=track.original_key,
    )
    return JsonResponse(
        {
            "ok": True,
            "id": ss.pk,
            "order": ss.order,
            "track_title": track.title,
            "artist": track.artist,
            "key": ss.performance_key,
        }
    )


@login_required
@require_POST
def setlist_remove_song(request, uid, song_id):
    church = _church(request)
    setlist = get_object_or_404(Setlist.raw_objects, church=church, uid=uid)
    if setlist.finalized:
        return JsonResponse({"ok": False, "error": "Setlist is finalized."})
    SetlistSong.objects.filter(pk=song_id, setlist=setlist).delete()
    # Re-number remaining songs
    for i, ss in enumerate(setlist.songs.order_by("order")):
        if ss.order != i:
            ss.order = i
            ss.save(update_fields=["order"])
    return JsonResponse({"ok": True})


@login_required
@require_POST
def setlist_reorder(request, uid):
    church = _church(request)
    setlist = get_object_or_404(Setlist.raw_objects, church=church, uid=uid)
    if setlist.finalized:
        return JsonResponse({"ok": False, "error": "Setlist is finalized."})
    ids = request.POST.getlist("ids[]")
    try:
        ids = [int(i) for i in ids]
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid ids."})
    for new_order, song_id in enumerate(ids):
        SetlistSong.objects.filter(pk=song_id, setlist=setlist).update(order=new_order)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def setlist_finalize(request, uid):
    """Finalize a setlist and trigger media sync."""
    church = _church(request)
    setlist = get_object_or_404(Setlist.raw_objects, church=church, uid=uid)
    if not _is_admin(request) and not _can(request, "music.manage"):
        return JsonResponse({"ok": False, "error": "Not authorised."}, status=403)
    if setlist.finalized:
        return JsonResponse({"ok": False, "error": "Already finalized."})
    if not setlist.songs.exists():
        return JsonResponse({"ok": False, "error": "Add songs before finalizing."})

    setlist.finalized = True
    setlist.save(update_fields=["finalized"])

    # Trigger music → media sync
    try:
        setlist.sync_to_media()
        synced = True
    except Exception as exc:
        logger.error("setlist_finalize sync failed: %s", exc)
        synced = False

    return JsonResponse(
        {
            "ok": True,
            "media_synced": synced,
            "message": "Setlist finalized."
            + (" Lyric slides synced to Media." if synced else ""),
        }
    )


@login_required
@require_POST
def setlist_unfinalize(request, uid):
    church = _church(request)
    setlist = get_object_or_404(Setlist.raw_objects, church=church, uid=uid)
    if not _is_admin(request):
        return JsonResponse({"ok": False, "error": "Not authorised."}, status=403)
    setlist.finalized = False
    setlist.save(update_fields=["finalized"])
    return JsonResponse({"ok": True})


# ── Rehearsal ─────────────────────────────────────────────────────────────────


@login_required
def rehearsal_board(request, unit_slug=None):
    church = _church(request)
    base_qs = (
        RehearsalSession.raw_objects.filter(church=church)
        .select_related("event", "event__prepares_for_service", "setlist", "unit")
        .order_by("event__date")
    )

    unit = None
    if unit_slug:
        from units.models import ChurchUnit

        unit = get_object_or_404(
            ChurchUnit.raw_objects, church=church, slug=unit_slug, is_active=True
        )
        base_qs = base_qs.filter(unit=unit)

    from django.utils import timezone

    today = timezone.localdate()

    # Concrete upcoming: dated sessions from today onward (not recurring)
    upcoming_sessions = list(
        base_qs.filter(
            event__date__gte=today,
            event__is_recurring_weekly=False,
            completed=False,
        )[:20]
    )
    # If no concrete upcoming sessions found, also include recurring-weekly ones
    recurring_sessions = list(
        base_qs.filter(
            event__is_recurring_weekly=True,
            completed=False,
        )[:20]
    )
    # Merge: recurring first (they always happen), then concrete dated ones
    seen_pks = {s.pk for s in upcoming_sessions}
    for s in recurring_sessions:
        if s.pk not in seen_pks:
            upcoming_sessions.append(s)
            seen_pks.add(s.pk)
    # Sort by event date (None dates last for recurring events without a base date)
    upcoming_sessions.sort(key=lambda s: (s.event.date is None, s.event.date))
    upcoming_sessions = upcoming_sessions[:20]

    past_sessions = base_qs.filter(completed=True).order_by("-event__date")[:20]

    return render(
        request,
        "music/rehearsal_board.html",
        {
            "page_title": "Rehearsals",
            "upcoming_sessions": upcoming_sessions,
            "past_sessions": past_sessions,
            "unit": unit,
        },
    )


@login_required
def rehearsal_detail(request, uid):
    church = _church(request)
    session = get_object_or_404(
        RehearsalSession.raw_objects.select_related(
            "event", "event__prepares_for_service", "setlist", "unit"
        ),
        church=church,
        uid=uid,
    )

    # AttendanceRecord is the single source of truth for who attended.
    # Filter by the rehearsal's event — same records the workforce flow writes.
    from workforce.models import AttendanceRecord
    from units.models import UnitMembership

    # AttendanceRecord.user is now ChurchMember — order by user.user.last_name
    attendance = (
        AttendanceRecord.raw_objects.filter(church=church, event=session.event)
        .select_related("user__user")
        .order_by("user__user__last_name")
    )

    # Status breakdown for the summary bar
    status_counts = {}
    for att in attendance:
        status_counts[att.status] = status_counts.get(att.status, 0) + 1

    unit_members = (
        UnitMembership.raw_objects.filter(
            church=church, unit=session.unit, is_active=True
        ).select_related("workforce_member__member__user", "unit")
        if session.unit
        else []
    )

    return render(
        request,
        "music/rehearsal_detail.html",
        {
            "page_title": f"Rehearsal — {session.event}",
            "session": session,
            "attendance": attendance,
            "unit_members": unit_members,
            "can_edit": _is_admin(request) or _can(request, "music.manage"),
            "present_count": status_counts.get("present", 0),
            "absent_count": status_counts.get("absent", 0),
            "late_count": status_counts.get("late", 0),
            "excused_count": status_counts.get("excused", 0),
        },
    )


@login_required
@require_POST
def rehearsal_upload_recording(request, uid):
    """Upload or update rehearsal recording (file or URL)."""
    church = _church(request)
    session = get_object_or_404(RehearsalSession.raw_objects, church=church, uid=uid)
    if not (_is_admin(request) or _can(request, "music.manage")):
        return JsonResponse({"ok": False, "error": "Not authorised."}, status=403)

    file_obj = request.FILES.get("recording_file")
    recording_url = (request.POST.get("recording_url") or "").strip()

    update_fields = []
    if file_obj:
        session.recording_file = file_obj
        update_fields.append("recording_file")
    if recording_url:
        session.recording_url = recording_url
        update_fields.append("recording_url")
    if not update_fields:
        return JsonResponse(
            {"ok": False, "error": "No recording provided."}, status=400
        )

    session.save(update_fields=update_fields)
    return JsonResponse(
        {
            "ok": True,
            "recording_url": session.recording_url,
            "recording_file_url": (
                session.recording_file.url if session.recording_file else ""
            ),
        }
    )


@login_required
@require_POST
def quick_create_track(request):
    """
    AJAX endpoint: get-or-create a Track with minimal data.
    Called by the setlist builder when adding catalog or custom songs.
    Returns: {ok: bool, id: int, uid: str, title: str, artist: str, created: bool}
    """
    import json as _json

    church = _church(request)
    if not church:
        return JsonResponse({"ok": False, "error": "No church context."}, status=403)

    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON body."}, status=400)

    title = (body.get("title") or "").strip()
    if not title:
        return JsonResponse({"ok": False, "error": "Title is required."}, status=400)

    artist = (body.get("artist") or "").strip()
    key = (body.get("original_key") or "").strip()
    tempo = None
    try:
        raw = body.get("tempo")
        if raw:
            tempo = int(str(raw).strip())
    except (ValueError, TypeError):
        pass

    # Scope to the requesting user's unit if available
    from units.models import UnitMembership

    membership = (
        UnitMembership.raw_objects.filter(
            church=church,
            workforce_member__member__user=request.user,
            is_active=True,
        )
        .select_related("unit")
        .first()
    )
    unit = membership.unit if membership else None

    track, created = Track.raw_objects.get_or_create(
        church=church,
        title=title,
        artist=artist,
        defaults={
            "is_active": True,
            "unit": unit,
            "original_key": key,
            "tempo": tempo,
        },
    )

    # If the track already existed, backfill missing key/tempo
    if not created:
        changed = False
        if key and not track.original_key:
            track.original_key = key
            changed = True
        if tempo and not track.tempo:
            track.tempo = tempo
            changed = True
        if changed:
            track.save(update_fields=["original_key", "tempo"])

    return JsonResponse(
        {
            "ok": True,
            "id": track.id,
            "uid": str(track.uid),
            "title": track.title,
            "artist": track.artist,
            "created": created,
        }
    )


# ── Song Assignment Views ─────────────────────────────────────────────────────


@login_required
@require_POST
def setlist_song_assign(request, uid, song_id):
    """
    Assign a musician to a SetlistSong (who plays what).
    POST: member_id, instrument, notes
    """
    church = _church(request)
    setlist = get_object_or_404(Setlist.raw_objects, church=church, uid=uid)
    if not (_is_admin(request) or _can(request, "music.manage")):
        return JsonResponse({"ok": False, "error": "Not authorised."}, status=403)

    from music.models import SetlistSongAssignment
    from accounts.models import ChurchMember

    song = get_object_or_404(SetlistSong, pk=song_id, setlist=setlist)
    member_id = request.POST.get("member_id", "").strip()
    instrument = request.POST.get("instrument", "other")
    notes = request.POST.get("notes", "").strip()

    try:
        member = ChurchMember.raw_objects.get(pk=int(member_id), church=church)
    except (ChurchMember.DoesNotExist, ValueError):
        return JsonResponse({"ok": False, "error": "Member not found."}, status=400)

    assignment, created = SetlistSongAssignment.raw_objects.get_or_create(
        church=church,
        setlist_song=song,
        member=member,
        instrument=instrument,
        defaults={"notes": notes},
    )
    if not created:
        assignment.notes = notes
        assignment.save(update_fields=["notes"])

    return JsonResponse({
        "ok": True,
        "id": assignment.pk,
        "member_name": member.user.full_name,
        "instrument": assignment.get_instrument_display(),
        "created": created,
    })


@login_required
@require_POST
def setlist_song_unassign(request, uid, song_id, assignment_id):
    """Remove a musician assignment from a SetlistSong."""
    church = _church(request)
    setlist = get_object_or_404(Setlist.raw_objects, church=church, uid=uid)
    if not (_is_admin(request) or _can(request, "music.manage")):
        return JsonResponse({"ok": False, "error": "Not authorised."}, status=403)

    from music.models import SetlistSongAssignment

    SetlistSongAssignment.raw_objects.filter(
        church=church, pk=assignment_id, setlist_song_id=song_id
    ).delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def assignment_confirm(request, uid, song_id, assignment_id):
    """Musician confirms / un-confirms their assignment."""
    church = _church(request)
    from music.models import SetlistSongAssignment
    from accounts.models import ChurchMember

    member = getattr(request, "member", None)
    if not member:
        return JsonResponse({"ok": False, "error": "Not a member."}, status=403)

    assignment = get_object_or_404(
        SetlistSongAssignment.raw_objects,
        church=church, pk=assignment_id, member=member
    )
    assignment.confirmed = not assignment.confirmed
    assignment.save(update_fields=["confirmed"])
    return JsonResponse({"ok": True, "confirmed": assignment.confirmed})


@login_required
def my_setlist_view(request, uid):
    """
    Musician's personal view of a setlist — shows only their assigned songs
    with their instrument key context and chord chart.
    Similar to Planning Center's per-musician setlist card.
    """
    church = _church(request)
    setlist = get_object_or_404(Setlist.raw_objects, church=church, uid=uid)
    from music.models import SetlistSongAssignment
    from accounts.models import ChurchMember

    member = getattr(request, "member", None)

    my_assignments = (
        SetlistSongAssignment.raw_objects
        .filter(church=church, setlist_song__setlist=setlist, member=member)
        .select_related("setlist_song__track", "setlist_song__track__unit")
        .prefetch_related("setlist_song__track__sections", "setlist_song__track__chord_charts")
        .order_by("setlist_song__order")
        if member
        else []
    )

    return render(request, "music/my_setlist.html", {
        "page_title": f"My Songs — {setlist.title}",
        "setlist": setlist,
        "my_assignments": my_assignments,
        "member": member,
    })


# ── Song Resource Views ───────────────────────────────────────────────────────


@login_required
@require_POST
def resource_upload(request, uid):
    """Upload or link a SongResource for a Track."""
    church = _church(request)
    track = get_object_or_404(Track.raw_objects, church=church, uid=uid, is_active=True)
    if not (_is_admin(request) or _can(request, "music.manage")):
        return JsonResponse({"ok": False, "error": "Not authorised."}, status=403)

    from music.models import SongResource
    from accounts.models import ChurchMember

    name = (request.POST.get("name") or "").strip()
    resource_type = request.POST.get("resource_type", "other")
    external_url = (request.POST.get("external_url") or "").strip()
    file_obj = request.FILES.get("file")
    member = getattr(request, "member", None)

    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)
    if not file_obj and not external_url:
        return JsonResponse({"ok": False, "error": "Provide a file or URL."}, status=400)

    resource = SongResource.raw_objects.create(
        church=church,
        track=track,
        name=name,
        resource_type=resource_type,
        external_url=external_url or "",
        uploaded_by=member,
    )
    if file_obj:
        resource.file = file_obj
        resource.save(update_fields=["file"])

    return JsonResponse({
        "ok": True,
        "id": resource.pk,
        "name": resource.name,
        "type_display": resource.get_resource_type_display(),
        "url": resource.download_url,
    })


@login_required
@require_POST
def resource_delete(request, uid, resource_id):
    """Delete a SongResource."""
    church = _church(request)
    if not (_is_admin(request) or _can(request, "music.manage")):
        return JsonResponse({"ok": False, "error": "Not authorised."}, status=403)
    from music.models import SongResource
    SongResource.raw_objects.filter(church=church, pk=resource_id, track__uid=uid).delete()
    return JsonResponse({"ok": True})


# ── Enhanced track detail with resources ─────────────────────────────────────


@login_required
def track_resources(request, uid):
    """AJAX – list resources for a track (used in detail sidebar)."""
    church = _church(request)
    track = get_object_or_404(Track.raw_objects, church=church, uid=uid, is_active=True)
    from music.models import SongResource
    resources = SongResource.raw_objects.filter(church=church, track=track).order_by("resource_type", "name")
    return JsonResponse({
        "resources": [
            {
                "id": r.pk,
                "name": r.name,
                "type": r.resource_type,
                "type_display": r.get_resource_type_display(),
                "url": r.download_url,
            }
            for r in resources
        ]
    })
