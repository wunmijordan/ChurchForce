"""
music/scheduler_jobs.py

Nightly catalog enrichment job.

Registered into the core APScheduler by register_music_jobs(), called
from core/scheduler.py:start().

Schedule: every night at 02:30 local time (Africa/Lagos by default,
i.e. 01:30 UTC).  Processes at most NIGHTLY_LIMIT tracks per church
per run so API quotas are never exhausted in a single pass.  Over
successive nights, the entire catalogue gets enriched.

Sources tried in order:
  1. SoundCloud  — full streamable audio
  2. Spotify     — 30-second preview MP3
  3. YouTube     — video ID for in-browser iframe embed
"""

import logging

logger = logging.getLogger(__name__)

# Maximum tracks enriched per church per nightly run.
# At 3 API calls per track and ~0.3 s delay between calls, 50 tracks
# takes about 45 seconds — well within a background job's tolerance.
NIGHTLY_LIMIT = 50


def run_enrich_catalog_tracks():
    """
    Nightly job: enrich unenriched catalog tracks for every active church.

    Skips churches whose settings disable the music module.
    Skips tracks that already have at least one audio source.
    Logs a summary per church and a grand total at the end.
    """
    from tenants.models import Church

    churches = Church.raw_objects.filter(is_active=True)
    grand = {"processed": 0, "enriched": 0, "errors": 0}

    for church in churches:
        # Skip churches that haven't enabled the music module
        settings_obj = getattr(church, "settings", None)
        if settings_obj and not getattr(settings_obj, "enable_music_module", True):
            continue

        try:
            from music.services.enrichment import enrich_tracks_for_church

            stats = enrich_tracks_for_church(
                church,
                try_soundcloud=True,
                try_spotify=True,
                try_youtube=True,
                force=False,
                limit=NIGHTLY_LIMIT,
                rate_limit_delay=0.4,
            )
            grand["processed"] += stats["processed"]
            grand["enriched"] += stats["enriched"]
            grand["errors"] += stats["errors"]

            if stats["enriched"] or stats["errors"]:
                logger.info(
                    "[EnrichJob] %s: processed=%d enriched=%d "
                    "sc=%d sp=%d yt=%d errors=%d",
                    church.name,
                    stats["processed"],
                    stats["enriched"],
                    stats["soundcloud"],
                    stats["spotify"],
                    stats["youtube"],
                    stats["errors"],
                )
        except Exception as exc:
            logger.error(
                "[EnrichJob] Failed for church %s: %s", church, exc, exc_info=True
            )
            grand["errors"] += 1

    logger.info(
        "[EnrichJob] Done. total_processed=%d total_enriched=%d total_errors=%d",
        grand["processed"],
        grand["enriched"],
        grand["errors"],
    )


def register_music_jobs(scheduler):
    """
    Register music scheduler jobs.
    Called from core/scheduler.py:start().
    """
    scheduler.add_job(
        run_enrich_catalog_tracks,
        trigger="cron",
        hour=2,
        minute=30,
        id="enrich_catalog_tracks",
        replace_existing=True,
        misfire_grace_time=3600,  # allow up to 1h late start
    )
    logger.info("[Scheduler] Music jobs registered (enrich_catalog_tracks @ 02:30).")
