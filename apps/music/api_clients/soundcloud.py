"""
music/api_clients/soundcloud.py

SoundCloud public API client for track enrichment.

Uses the SoundCloud public API (client_id authentication) to search
for worship tracks and retrieve their streamable URLs.  Only tracks
that SoundCloud marks as publicly streamable are used — this covers
artists who have opted into SoundCloud's free-tier streaming, which
includes a substantial portion of the Nigerian and African gospel
catalogue.

Required settings:
    SOUNDCLOUD_CLIENT_ID  — register a free app at
                            https://developers.soundcloud.com

API notes
---------
- Search endpoint:  GET /tracks?q=…&client_id=…
- Stream URL:       track["stream_url"] + "?client_id=…"
  The stream URL returns a 302 redirect to the actual CDN audio file.
  Modern browsers follow the redirect automatically; the <audio> src
  can point directly to the stream URL.
- Rate limit:       ~15,000 req/day on a free app credential.
- Terms:            Streaming is permitted for apps that use the
  SoundCloud widget / player.  Storing the audio is not permitted.
  We store only the stream URL, never the audio bytes.

Coverage notes
--------------
SoundCloud has strong coverage of:
    - Nathaniel Bassey, Sinach, Mercy Chinwo, Dunsin Oyekan
    - Steve Crown, Moses Bliss, Frank Edwards, Ada Ehi
    - Hillsong, Bethel, Elevation, Maverick City
Weaker coverage of:
    - Tope Alabi (some tracks region-locked)
    - Joyous Celebration (label restrictions)
"""

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

SOUNDCLOUD_API_BASE = "https://api.soundcloud.com"

# Seconds to wait between requests to stay well under rate limits
_INTER_REQUEST_DELAY = 0.25


class SoundCloudError(Exception):
    pass


class SoundCloudUnavailable(SoundCloudError):
    """Raised when the track exists but is not publicly streamable."""

    pass


class SoundCloudClient:

    def __init__(self):
        self.client_id = getattr(settings, "SOUNDCLOUD_CLIENT_ID", "")
        if not self.client_id:
            raise SoundCloudError(
                "SOUNDCLOUD_CLIENT_ID must be set in settings.  "
                "Register a free app at https://developers.soundcloud.com "
                "to obtain a client_id."
            )

    # ── Public API ────────────────────────────────────────────────────────────

    def find_track(self, title: str, artist: str = "") -> dict | None:
        """
        Search SoundCloud for *title* by *artist*.

        Returns a dict with keys:
            id, title, permalink_url, stream_url, artwork_url, duration_ms
        or None if no streamable match is found.
        """
        candidates = self._search(title, artist, limit=8)
        return self._pick_best(candidates, title, artist)

    def stream_url(self, track: dict) -> str:
        """
        Return the full stream URL (with client_id appended) for a track
        dict returned by find_track().
        """
        base = track.get("stream_url", "")
        if not base:
            return ""
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}client_id={self.client_id}"

    # ── Internal ──────────────────────────────────────────────────────────────

    def _search(self, title: str, artist: str, limit: int = 8) -> list[dict]:
        query = f"{title} {artist}".strip() if artist else title
        try:
            time.sleep(_INTER_REQUEST_DELAY)
            resp = requests.get(
                f"{SOUNDCLOUD_API_BASE}/tracks",
                params={
                    "q": query,
                    "limit": limit,
                    "client_id": self.client_id,
                    "filter": "streamable",  # only tracks we can stream
                },
                timeout=12,
            )
            resp.raise_for_status()
            return resp.json() or []
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 401:
                raise SoundCloudError(
                    "SoundCloud client_id invalid or expired."
                ) from exc
            if exc.response is not None and exc.response.status_code == 429:
                raise SoundCloudError(
                    "SoundCloud rate limit hit — slow down requests."
                ) from exc
            logger.warning("SoundCloud search HTTP error: %s", exc)
            return []
        except requests.RequestException as exc:
            logger.warning("SoundCloud search failed: %s", exc)
            return []

    def _pick_best(
        self,
        candidates: list[dict],
        title: str,
        artist: str,
    ) -> dict | None:
        """
        Score candidates and return the best streamable match.
        Returns None if no suitable candidate found.
        """
        title_l = title.lower()
        artist_l = artist.lower()
        best: dict | None = None
        best_score = -1

        for track in candidates:
            # Must have a stream_url to be usable
            if not track.get("stream_url"):
                continue
            # Must be streamable
            if not track.get("streamable", False):
                continue

            t_title = (track.get("title") or "").lower()
            t_user = (track.get("user", {}).get("username") or "").lower()
            t_genre = (track.get("genre") or "").lower()

            score = 0

            # Title match
            if title_l in t_title:
                score += 5
            elif all(w in t_title for w in title_l.split() if len(w) > 3):
                score += 3

            # Artist match
            if artist_l and (artist_l in t_user or artist_l in t_title):
                score += 4

            # Genre signals
            for g in ("gospel", "worship", "christian", "praise", "afrobeats"):
                if g in t_genre:
                    score += 1

            # Penalise covers
            for bad in ("karaoke", "cover", "remix", "piano", "acoustic version"):
                if bad in t_title:
                    score -= 3

            # Prefer higher play counts as a proxy for "correct" track
            plays = track.get("playback_count") or 0
            if plays > 100_000:
                score += 2
            elif plays > 10_000:
                score += 1

            if score > best_score:
                best_score = score
                best = track

        # Require at least a loose match — title words must appear somewhere
        if best is None or best_score < 1:
            return None
        return best
