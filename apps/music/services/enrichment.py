"""
music/services/enrichment.py

Automatic track enrichment: given a Track record with only title/artist
metadata, this service tries to find real playable audio from external
sources and stores the URLs on the track.

Source priority
---------------
1. Spotify     — sets spotify_id (no preview URL — field removed from model).
2. YouTube     — video ID for iframe embed, always playable in-browser.
                 Stores: youtube_url
3. Apple Music — store link (optional, silently skipped if client absent).
                 Stores: apple_music_url

NOTE: soundcloud_stream_url, soundcloud_url, and spotify_preview_url were
removed from CatalogTrack. Enrichment filters and attribute accesses now
only reference fields that exist on the model (spotify_id, youtube_url,
apple_music_url). The try_soundcloud parameter is kept for call-site
compatibility but is now a no-op.

Design decisions
----------------
- We never overwrite church-uploaded audio_file — enrichment only fills
  fields that are blank.
- All API clients raise their own Error subclass; this service catches
  those and logs a warning, falling through to the next source.
- rate_limit_delay (seconds) is inserted between API calls so a bulk
  enrichment run doesn't exhaust quotas in the first minute.
"""

import logging
import time
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)


# ── Result helpers ────────────────────────────────────────────────────────────


class EnrichmentResult:
    """Outcome of a single track enrichment attempt."""

    def __init__(self):
        self.spotify = False
        self.youtube = False
        self.apple = False
        self.skipped = False  # Already enriched / already has audio
        self.error = False
        self.sources: list[str] = []

    @property
    def any_found(self) -> bool:
        return self.spotify or self.youtube or self.apple

    def __repr__(self) -> str:
        return (
            f"<EnrichmentResult sp={self.spotify} "
            f"yt={self.youtube} apple={self.apple} "
            f"skip={self.skipped} err={self.error}>"
        )


# ── Core enrichment function ──────────────────────────────────────────────────


def enrich_track(
    track,
    *,
    try_soundcloud: bool = True,  # kept for call-site compat — now a no-op
    try_spotify: bool = True,
    try_youtube: bool = True,
    force: bool = False,
    rate_limit_delay: float = 0.3,
) -> EnrichmentResult:
    """
    Enrich a single Track record from external sources.

    Parameters
    ----------
    track           : music.models.Track instance
    try_soundcloud  : ignored (field removed from model), kept for compat
    try_spotify     : attempt Spotify enrichment (sets spotify_id only)
    try_youtube     : attempt YouTube video ID enrichment
    force           : re-enrich even if already enriched (for re-runs)
    rate_limit_delay: seconds to sleep between API calls

    Returns an EnrichmentResult describing what happened.
    """
    result = EnrichmentResult()

    # Skip if the church has already uploaded their own file and not forcing
    raw_audio = str(track.audio_file) if track.audio_file else ""
    has_upload = bool(raw_audio and raw_audio not in ("None", ""))

    # A track is considered enriched when it has spotify_id or youtube_url
    already_enriched = bool(track.spotify_id or track.youtube_url)

    if (has_upload and already_enriched) and not force:
        result.skipped = True
        return result

    # If only re-enriching specific missing fields, skip only complete tracks
    if already_enriched and not force:
        result.skipped = True
        return result

    title = (track.title or "").strip()
    artist = (track.artist or "").strip()
    update_fields: list[str] = []

    # ── 1. Spotify (spotify_id only — preview_url field removed from model) ─
    if try_spotify and not track.spotify_id:
        try:
            from music.api_clients.spotify import SpotifyClient, SpotifyError

            sp = SpotifyClient()
            sp_data = sp.find_preview_url(title, artist)
            if sp_data and sp_data.get("spotify_id"):
                track.spotify_id = sp_data["spotify_id"]
                update_fields.append("spotify_id")
                result.spotify = True
                result.sources.append("spotify")
                logger.debug(
                    "Spotify: enriched %r → id %s",
                    title,
                    track.spotify_id,
                )
            time.sleep(rate_limit_delay)
        except Exception as exc:
            logger.warning(
                "Spotify enrichment failed for %r (%r): %s",
                title,
                artist,
                exc,
            )

    # ── 2. YouTube ────────────────────────────────────────────────────────
    if try_youtube and not track.youtube_url:
        try:
            from music.api_clients.youtube import YouTubeClient, YouTubeError

            yt = YouTubeClient()
            video_id = yt.find_video_id(title, artist)
            if video_id:
                track.youtube_url = yt.embed_url(video_id)
                update_fields.append("youtube_url")
                result.youtube = True
                result.sources.append("youtube")
                logger.debug("YouTube: enriched %r → %s", title, track.youtube_url)
            time.sleep(rate_limit_delay)
        except Exception as exc:
            logger.warning(
                "YouTube enrichment failed for %r (%r): %s",
                title,
                artist,
                exc,
            )

    # ── 3. Apple Music (optional — client may not be configured) ──────────
    if not getattr(track, "apple_music_url", None):
        try:
            from music.api_clients.apple_music import AppleMusicClient

            am = AppleMusicClient()
            am_url = am.find_url(title, artist)
            if am_url:
                track.apple_music_url = am_url
                update_fields.append("apple_music_url")
                result.apple = True
                result.sources.append("apple_music")
                logger.debug("Apple Music: enriched %r → %s", title, am_url)
            time.sleep(rate_limit_delay)
        except Exception:
            pass  # Apple Music client absent or not configured — skip silently

    # ── Persist ───────────────────────────────────────────────────────────
    if update_fields:
        # Record which source(s) were used
        if result.sources:
            track.enrichment_source = result.sources[0]  # primary source
            update_fields.append("enrichment_source")
        track.enriched_at = timezone.now()
        update_fields.append("enriched_at")
        try:
            track.save(update_fields=list(set(update_fields)))
        except Exception as exc:
            logger.error(
                "Failed to save enriched track %d (%r): %s",
                track.pk,
                title,
                exc,
            )
            result.error = True

    return result


# ── Bulk enrichment ───────────────────────────────────────────────────────────


def enrich_tracks_for_church(
    church,
    *,
    try_soundcloud: bool = True,  # kept for call-site compat — now a no-op
    try_spotify: bool = True,
    try_youtube: bool = True,
    force: bool = False,
    limit: int | None = None,
    rate_limit_delay: float = 0.3,
    log_fn=None,
) -> dict[str, Any]:
    """
    Enrich all unenriched tracks for one church.

    Parameters
    ----------
    church           : tenants.models.Church instance
    try_soundcloud   : ignored (field removed from model), kept for compat
    try_spotify      : enable Spotify source (spotify_id)
    try_youtube      : enable YouTube source
    force            : re-enrich already-enriched tracks
    limit            : cap the number of tracks processed
    rate_limit_delay : seconds between API calls per track
    log_fn           : optional callable(str) for progress logging

    Returns a stats dict:
        {
          "processed": int,
          "enriched":  int,
          "skipped":   int,
          "errors":    int,
          "spotify":    int,
          "youtube":    int,
        }
    """
    from music.models import Track

    def _log(msg: str):
        logger.info(msg)
        if log_fn:
            log_fn(msg)

    stats = {
        "processed": 0,
        "enriched": 0,
        "skipped": 0,
        "errors": 0,
        "spotify": 0,
        "youtube": 0,
    }

    qs = Track.raw_objects.filter(church=church, is_active=True)
    if not force:
        # Only fetch tracks missing both spotify_id and youtube_url —
        # these are the only enrichable identifier fields on the model.
        qs = qs.filter(
            spotify_id="",
            youtube_url="",
        )

    if limit:
        qs = qs[:limit]

    total = qs.count()
    _log(f"Enriching {total} track(s) for church '{church}' …")

    for track in qs.iterator():
        result = enrich_track(
            track,
            try_soundcloud=False,  # field removed — always skip
            try_spotify=try_spotify,
            try_youtube=try_youtube,
            force=force,
            rate_limit_delay=rate_limit_delay,
        )
        stats["processed"] += 1
        if result.skipped:
            stats["skipped"] += 1
        elif result.error:
            stats["errors"] += 1
        elif result.any_found:
            stats["enriched"] += 1
            if result.spotify:
                stats["spotify"] += 1
            if result.youtube:
                stats["youtube"] += 1

        if stats["processed"] % 20 == 0:
            _log(
                f"  … {stats['processed']}/{total} processed "
                f"({stats['enriched']} enriched so far)"
            )

    _log(
        f"Done. processed={stats['processed']} enriched={stats['enriched']} "
        f"sp={stats['spotify']} yt={stats['youtube']} "
        f"skipped={stats['skipped']} errors={stats['errors']}"
    )
    return stats
