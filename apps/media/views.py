"""
media/views.py — ChurchForce Media Module

EasyWorship-grade presentation management: service presentations,
lyric slides (auto-synced from music), Bible passage insertion,
graphic templates, production schedule checklist, live presenter view.
"""

import logging
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from media.models import (
    ServicePresentation,
    PresentationSlide,
    SlideTemplate,
    BibleVersion,
    BiblePassage,
    ProductionSchedule,
    MediaAsset,
    BroadcastConfig,
)

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
    if not s or not s.enable_media_module:
        return False
    if unit:
        return unit.media_module
    return True


# ── Presentation List ─────────────────────────────────────────────────────────


@login_required
def presentation_list(request):
    church = _church(request)
    if not church:
        return HttpResponseForbidden()
    qs = (
        ServicePresentation.raw_objects.filter(church=church)
        .select_related("event", "unit", "setlist")
        .order_by("-created_at")
    )
    return render(
        request,
        "media/presentation_list.html",
        {
            "page_title": "Presentations",
            "presentations": qs[:40],
        },
    )


@login_required
def presentation_detail(request, uid):
    """Slide editor view."""
    church = _church(request)
    pres = get_object_or_404(ServicePresentation.raw_objects, church=church, uid=uid)
    slides = pres.slides.select_related(
        "template", "lyrics_section", "bible_passage"
    ).order_by("order")
    templates = SlideTemplate.raw_objects.filter(church=church).order_by(
        "-is_default", "name"
    )
    versions = BibleVersion.objects.filter(is_active=True).order_by("code")
    can_edit = _is_admin(request) or _can(request, "media.manage")

    return render(
        request,
        "media/presentation_detail.html",
        {
            "page_title": pres.title,
            "pres": pres,
            "slides": slides,
            "templates": templates,
            "bible_versions": versions,
            "can_edit": can_edit,
            "slide_types": PresentationSlide.SLIDE_TYPES,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def presentation_edit(request, uid=None):
    church = _church(request)
    if not _is_admin(request) and not _can(request, "media.manage"):
        return HttpResponseForbidden()

    pres = None
    if uid:
        pres = get_object_or_404(
            ServicePresentation.raw_objects, church=church, uid=uid
        )

    if request.method == "POST":
        from services.models import Event
        from units.models import ChurchUnit
        from music.models import Setlist

        title = request.POST.get("title", "").strip()
        event_id = request.POST.get("event_id") or None
        unit_id = request.POST.get("unit_id") or None
        setlist_id = request.POST.get("setlist_id") or None
        tmpl_id = request.POST.get("template_id") or None
        member = getattr(request, "member", None)

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
        setlist = (
            Setlist.raw_objects.filter(church=church, id=setlist_id).first()
            if setlist_id
            else None
        )
        tmpl = (
            SlideTemplate.raw_objects.filter(church=church, id=tmpl_id).first()
            if tmpl_id
            else None
        )

        if pres:
            pres.title = title
            pres.event = event
            pres.unit = unit
            pres.setlist = setlist
            pres.default_template = tmpl
            pres.save()
        else:
            pres = ServicePresentation.raw_objects.create(
                church=church,
                title=title,
                event=event,
                unit=unit,
                setlist=setlist,
                default_template=tmpl,
                created_by=member,
            )
        return redirect("media:presentation_detail", uid=pres.uid)

    from services.models import Event
    from units.models import ChurchUnit
    from music.models import Setlist

    events = Event.raw_objects.filter(church=church).order_by("-date", "name")[:50]
    units = ChurchUnit.raw_objects.filter(
        church=church, is_active=True, media_module=True
    ).order_by("name")
    setlists = Setlist.raw_objects.filter(church=church, finalized=True).order_by(
        "-created_at"
    )[:30]
    templates = SlideTemplate.raw_objects.filter(church=church).order_by(
        "-is_default", "name"
    )

    return render(
        request,
        "media/presentation_edit.html",
        {
            "page_title": "Edit Presentation" if pres else "New Presentation",
            "pres": pres,
            "events": events,
            "units": units,
            "setlists": setlists,
            "templates": templates,
        },
    )


# ── Slide CRUD ────────────────────────────────────────────────────────────────


@login_required
@require_POST
def slide_add(request, uid):
    church = _church(request)
    pres = get_object_or_404(ServicePresentation.raw_objects, church=church, uid=uid)
    if not _is_admin(request) and not _can(request, "media.manage"):
        return JsonResponse({"ok": False, "error": "Not authorised"}, status=403)

    slide_type = request.POST.get("slide_type", "lyric")
    content = request.POST.get("content", "")
    sub_content = request.POST.get("sub_content", "")
    tmpl_id = request.POST.get("template_id") or None
    bible_ref = request.POST.get("bible_reference", "").strip()
    bible_ver = request.POST.get("bible_version", "KJV")
    order = pres.slides.count()
    tmpl = (
        SlideTemplate.raw_objects.filter(church=church, id=tmpl_id).first()
        if tmpl_id
        else None
    )

    # Bible slide: fetch and split
    bible_passage = None
    if slide_type == "scripture" and bible_ref:
        from media.services import fetch_bible_passage

        bible_passage, _ = fetch_bible_passage(church, bible_ref, bible_ver)
        if bible_passage:
            # Create one slide per passage chunk
            created = []
            for i, chunk in enumerate(bible_passage.bible_slides):
                s = PresentationSlide.objects.create(
                    church=church,
                    presentation=pres,
                    slide_type="scripture",
                    content=chunk,
                    sub_content=bible_ref,
                    template=tmpl or pres.default_template,
                    bible_passage=bible_passage,
                    order=order + i,
                    is_auto=False,
                    metadata={"version": bible_ver},
                )
                created.append(
                    {
                        "id": s.pk,
                        "order": s.order,
                        "type": s.slide_type,
                        "content": s.content,
                    }
                )
            return JsonResponse({"ok": True, "slides": created, "multi": True})

    slide = PresentationSlide.objects.create(
        church=church,
        presentation=pres,
        slide_type=slide_type,
        content=content,
        sub_content=sub_content,
        template=tmpl or pres.default_template,
        order=order,
        is_auto=False,
    )
    return JsonResponse(
        {
            "ok": True,
            "id": slide.pk,
            "order": slide.order,
            "type": slide.slide_type,
            "content": slide.content,
        }
    )


@login_required
@require_POST
def slide_edit(request, uid, slide_id):
    church = _church(request)
    pres = get_object_or_404(ServicePresentation.raw_objects, church=church, uid=uid)
    slide = get_object_or_404(PresentationSlide, pk=slide_id, presentation=pres)
    slide.content = request.POST.get("content", slide.content)
    slide.sub_content = request.POST.get("sub_content", slide.sub_content)
    tmpl_id = request.POST.get("template_id") or None
    if tmpl_id:
        slide.template = SlideTemplate.raw_objects.filter(
            church=church, id=tmpl_id
        ).first()
    slide.save()
    return JsonResponse({"ok": True, "id": slide.pk, "content": slide.content})


@login_required
@require_POST
def slide_delete(request, uid, slide_id):
    church = _church(request)
    pres = get_object_or_404(ServicePresentation.raw_objects, church=church, uid=uid)
    PresentationSlide.objects.filter(pk=slide_id, presentation=pres).delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def slide_reorder(request, uid):
    church = _church(request)
    pres = get_object_or_404(ServicePresentation.raw_objects, church=church, uid=uid)
    ids = [int(i) for i in request.POST.getlist("ids[]")]
    for order, slide_id in enumerate(ids):
        PresentationSlide.objects.filter(pk=slide_id, presentation=pres).update(
            order=order
        )
    return JsonResponse({"ok": True})


# ── Presenter (Live) View ─────────────────────────────────────────────────────


@login_required
def presenter_view(request, uid):
    """
    Fullscreen presenter/projection view.
    Slides rendered as a browser-based presentation.
    Operator view has prev/next controls; audience view is fullscreen read-only.
    """
    church = _church(request)
    pres = get_object_or_404(ServicePresentation.raw_objects, church=church, uid=uid)
    slides = list(pres.slides.select_related("template").order_by("order"))
    mode = request.GET.get("mode", "operator")  # operator | audience

    return render(
        request,
        "media/presenter_view.html",
        {
            "pres": pres,
            "slides": slides,
            "slides_json": _slides_to_json(slides),
            "mode": mode,
            "current_index": pres.current_slide_index,
        },
    )


def _slides_to_json(slides):
    import json

    data = []
    for s in slides:
        tmpl = s.template
        data.append(
            {
                "id": s.pk,
                "order": s.order,
                "type": s.slide_type,
                "content": s.content,
                "sub_content": s.sub_content,
                "bg_color": tmpl.background_color if tmpl else "#000000",
                "text_color": tmpl.text_color if tmpl else "#ffffff",
                "font_size": tmpl.font_size if tmpl else 48,
                "font_family": tmpl.font_family if tmpl else "sans-serif",
                "bg_url": tmpl.background.url if (tmpl and tmpl.background) else "",
                "overlay_opacity": tmpl.overlay_opacity if tmpl else 0.4,
            }
        )
    return json.dumps(data)


@login_required
@require_POST
def presenter_advance(request, uid, index):
    """Save the current slide index for multi-device sync."""
    church = _church(request)
    pres = get_object_or_404(ServicePresentation.raw_objects, church=church, uid=uid)
    total = pres.slides.count()
    pres.current_slide_index = max(0, min(index, total - 1))
    pres.save(update_fields=["current_slide_index"])
    return JsonResponse({"ok": True, "index": pres.current_slide_index})


# ── Bible ─────────────────────────────────────────────────────────────────────


@login_required
def bible_versions(request):
    versions = list(BibleVersion.objects.filter(is_active=True).values("code", "name"))
    return JsonResponse({"versions": versions})


@login_required
def bible_fetch(request):
    church = _church(request)
    reference = request.GET.get("ref", "").strip()
    version = request.GET.get("version", "KJV")
    if not reference:
        return JsonResponse({"ok": False, "error": "No reference provided."})

    from media.services import fetch_bible_passage

    passage, created = fetch_bible_passage(church, reference, version)
    if not passage:
        return JsonResponse(
            {
                "ok": False,
                "error": "Passage not found. Check the reference and try again.",
            }
        )

    return JsonResponse(
        {
            "ok": True,
            "reference": passage.reference,
            "text": passage.text,
            "slides": passage.bible_slides,
            "version": version,
            "cached": not created,
        }
    )


# ── Production Schedule ───────────────────────────────────────────────────────


@login_required
def production_schedule(request, event_id):
    church = _church(request)
    from services.models import Event

    event = get_object_or_404(Event.raw_objects, church=church, id=event_id)
    sched, _ = ProductionSchedule.objects.get_or_create(
        church=church,
        event=event,
        defaults={"unit": event.unit},
    )
    assets = sched.assets.all()
    checklist_items = [
        ("slides_ready", "Slides Ready", sched.slides_ready),
        ("audio_ready", "Audio Ready", sched.audio_ready),
        ("video_ready", "Video Ready", sched.video_ready),
        ("broadcast_ready", "Broadcast Ready", sched.broadcast_ready),
    ]
    return render(
        request,
        "media/production_schedule.html",
        {
            "page_title": f"Production — {event.name}",
            "event": event,
            "schedule": sched,
            "assets": assets,
            "checklist_items": checklist_items,
        },
    )


@login_required
@require_POST
def schedule_checklist(request, event_id):
    church = _church(request)
    from services.models import Event

    event = get_object_or_404(Event.raw_objects, church=church, id=event_id)
    sched = get_object_or_404(ProductionSchedule, church=church, event=event)
    sched.slides_ready = request.POST.get("slides_ready") == "on"
    sched.audio_ready = request.POST.get("audio_ready") == "on"
    sched.video_ready = request.POST.get("video_ready") == "on"
    sched.broadcast_ready = request.POST.get("broadcast_ready") == "on"
    sched.notes = request.POST.get("notes", "")
    sched.save()
    sched.auto_update_status()
    return JsonResponse(
        {"ok": True, "status": sched.status, "is_fully_ready": sched.is_fully_ready}
    )


# ── Slide Templates ───────────────────────────────────────────────────────────


@login_required
def template_list(request):
    church = _church(request)
    tmpls = SlideTemplate.raw_objects.filter(church=church).order_by(
        "-is_default", "name"
    )
    return render(
        request,
        "media/template_list.html",
        {
            "page_title": "Slide Templates",
            "templates": tmpls,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def template_edit(request, pk=None):
    church = _church(request)
    if not _is_admin(request):
        return HttpResponseForbidden()
    tmpl = None
    if pk:
        tmpl = get_object_or_404(SlideTemplate.raw_objects, church=church, pk=pk)
        if tmpl.is_system:
            from django.contrib import messages as msg

            msg.error(request, "System templates cannot be edited.")
            return redirect("media:template_list")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        bg_color = request.POST.get("background_color", "#000000")
        txt_color = request.POST.get("text_color", "#ffffff")
        font = request.POST.get("font_family", "sans-serif")
        size = int(request.POST.get("font_size", 48))
        opacity = float(request.POST.get("overlay_opacity", 0.4))
        tmpl_type = request.POST.get("template_type", "lyrics")
        is_default = request.POST.get("is_default") == "on"

        if is_default:
            SlideTemplate.raw_objects.filter(
                church=church, template_type=tmpl_type
            ).update(is_default=False)

        if tmpl:
            tmpl.name = name
            tmpl.background_color = bg_color
            tmpl.text_color = txt_color
            tmpl.font_family = font
            tmpl.font_size = size
            tmpl.overlay_opacity = opacity
            tmpl.template_type = tmpl_type
            tmpl.is_default = is_default
            tmpl.save()
        else:
            tmpl = SlideTemplate.raw_objects.create(
                church=church,
                name=name,
                background_color=bg_color,
                text_color=txt_color,
                font_family=font,
                font_size=size,
                overlay_opacity=opacity,
                template_type=tmpl_type,
                is_default=is_default,
            )
        return redirect("media:template_list")

    return render(
        request,
        "media/template_edit.html",
        {
            "page_title": "Edit Template" if tmpl else "New Template",
            "tmpl": tmpl,
            "types": SlideTemplate.TEMPLATE_TYPES,
            "font_families": [
                "sans-serif",
                "serif",
                "monospace",
                "Georgia",
                "Palatino",
                "Times New Roman",
                "Arial",
                "Helvetica",
                "Impact",
                "Trebuchet MS",
            ],
        },
    )
