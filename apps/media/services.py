"""
media/services.py

Music → Media sync service.

sync_setlist_to_presentation(setlist):
    Called when a Setlist is finalised. Creates or updates the linked
    ServicePresentation with one lyric slide per LyricsSection of each
    SetlistSong. Existing auto-generated slides are replaced; manually
    added slides (is_auto=False) are preserved.

Bible passage fetching and seeding have been removed from this module.
Bible content for the welcome modal and birthday messages is handled
entirely by the AI skills system (ai_skills).  The BibleVersion /
BiblePassage models and the media presentation's bible_passage FK are
kept in place so the DB schema and existing data are not disturbed.
"""

import logging

logger = logging.getLogger(__name__)

LINES_PER_SLIDE = 4  # max lines of lyrics per slide

# Version display names — used by the media presentation UI when listing
# available Bible versions (read from BibleVersion rows, not seeded here).
BIBLE_VERSION_NAMES = {
    "KJV": "King James Version",
    "NIV": "New International Version",
    "NKJV": "New King James Version",
    "ESV": "English Standard Version",
    "NLT": "New Living Translation",
    "MSG": "The Message",
    "AMP": "Amplified Bible",
    "NASB": "New American Standard Bible",
    "RSV": "Revised Standard Version",
    "CSB": "Christian Standard Bible",
    "GNT": "Good News Translation",
    "NRSV": "New Revised Standard Version",
    "CEV": "Contemporary English Version",
    "GW": "God's Word Translation",
    "NET": "New English Translation",
    "YLT": "Young's Literal Translation",
    "DARBY": "Darby Translation",
    "ASV": "American Standard Version",
    "WEB": "World English Bible",
    "AMPCE": "Amplified Classic Edition",
    "OEB": "Open English Bible",
}


def sync_setlist_to_presentation(setlist):
    """
    Auto-generate PresentationSlides from all songs in a finalised Setlist.

    Strategy:
      1. Find or create the ServicePresentation for this setlist.
      2. Delete all existing slides that are marked is_auto=True.
      3. Re-create: title slide for the service, then for each song:
           - Song title slide
           - One PresentationSlide per LyricsSection
      4. Preserve manually added slides (is_auto=False).

    Called by music.models.Setlist.sync_to_media() and the finalise view.
    """
    from media.models import ServicePresentation, PresentationSlide, SlideTemplate
    from music.models import LyricsSection

    church = setlist.church

    pres, _ = ServicePresentation.objects.get_or_create(
        church=church,
        setlist=setlist,
        defaults={
            "title": f"Presentation — {setlist.title}",
            "event": setlist.event,
            "unit": setlist.unit,
        },
    )
    if setlist.event and pres.event != setlist.event:
        pres.event = setlist.event
        pres.save(update_fields=["event"])

    default_tmpl = SlideTemplate.objects.filter(church=church, is_default=True).first()

    PresentationSlide.objects.filter(presentation=pres, is_auto=True).delete()

    order = 0

    manual_slides = list(
        PresentationSlide.objects.filter(presentation=pres, is_auto=False).order_by(
            "order"
        )
    )

    # Service title slide
    PresentationSlide.objects.create(
        church=church,
        presentation=pres,
        slide_type="title",
        content=setlist.title,
        sub_content=str(setlist.event) if setlist.event else "",
        template=default_tmpl,
        order=order,
        is_auto=True,
        metadata={"kind": "service_title"},
    )
    order += 1

    for ss in setlist.songs.order_by("order"):
        track = ss.track

        PresentationSlide.objects.create(
            church=church,
            presentation=pres,
            slide_type="title",
            content=track.title,
            sub_content=track.artist,
            template=default_tmpl,
            order=order,
            is_auto=True,
            metadata={
                "kind": "song_title",
                "track_id": track.pk,
                "performance_key": ss.performance_key or track.original_key,
            },
        )
        order += 1

        section_qs = track.sections.order_by("order")
        if ss.sections_to_project:
            section_qs = section_qs.filter(pk__in=ss.sections_to_project)

        for section in section_qs:
            lines = [ln for ln in section.content.split("\n") if ln.strip()]
            chunks = [
                lines[i : i + LINES_PER_SLIDE]
                for i in range(0, len(lines), LINES_PER_SLIDE)
            ] or [[""]]

            for chunk in chunks:
                for _ in range(max(1, section.repeat_count)):
                    PresentationSlide.objects.create(
                        church=church,
                        presentation=pres,
                        slide_type="lyric",
                        content="\n".join(chunk),
                        sub_content=section.label,
                        template=default_tmpl,
                        lyrics_section=section,
                        order=order,
                        is_auto=True,
                        metadata={
                            "kind": "lyric",
                            "track_id": track.pk,
                            "section_type": section.section_type,
                            "section_label": section.label,
                        },
                    )
                    order += 1

    for slide in manual_slides:
        slide.order = order
        order += 1
    if manual_slides:
        PresentationSlide.objects.bulk_update(manual_slides, ["order"])

    logger.info(
        "sync_setlist_to_presentation: %s — %d slides generated", setlist.title, order
    )
    return pres


# ---------------------------------------------------------------------------
# Stub kept for import compatibility only.
# Any caller that still imports seed_bible_content_for_plan gets a no-op so
# existing code doesn't crash during the transition period.
# ---------------------------------------------------------------------------


def seed_bible_content_for_plan(church, plan_name: str = "trial"):
    """
    Deprecated — bible seeding is handled by ai_skills, not this module.
    Returns an empty summary dict so callers that check the return value
    continue to work without modification.
    """
    logger.debug(
        "seed_bible_content_for_plan called for church=%s plan=%s — no-op (removed).",
        getattr(church, "slug", church),
        plan_name,
    )
    return {"versions_created": 0, "passages_created": 0, "plan": plan_name}


def fetch_bible_passage(church, reference: str, version_code: str = "KJV"):
    """
    Deprecated — bible passage fetching is handled by ai_skills.
    Returns (None, False) so any remaining callers degrade gracefully.
    """
    logger.debug("fetch_bible_passage called for '%s' — no-op (removed).", reference)
    return None, False
