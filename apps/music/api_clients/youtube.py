"""
music/api_clients/youtube.py

YouTube Data API v3 client for track enrichment.

Searches for a worship track by title + artist and returns the best
matching video ID, which is stored on Track.youtube_url as an embed
URL.  The existing inline-iframe player (added in session 1) then
plays it without any stream extraction.

Required settings:
    YOUTUBE_API_KEY  — Data API v3 key from Google Cloud Console
                       Enable "YouTube Data API v3" for the project.
                       Free tier: 10,000 units/day (search costs 100
                       units, so ~100 searches/day free).

API reference:
    https://developers.google.com/youtube/v3/docs/search/list
"""

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

# Worship-specific terms that improve match quality
_WORSHIP_HINTS = ["worship", "official", "lyric video", "live"]

# Channels we know are official / high-quality for Christian music;
# used to bias the score when ranking candidates.
_TRUSTED_CHANNEL_FRAGMENTS = [
    "hillsong",
    "bethel",
    "elevation",
    "maverick city",
    "nathaniel bassey",
    "mercy chinwo",
    "sinach",
    "dunsin",
    "tim godfrey",
    "ada ehi",
    "moses bliss",
    "frank edwards",
    "tasha cobbs",
    "kirk franklin",
    "phil wickham",
]


class YouTubeError(Exception):
    pass


class YouTubeClient:
    """
    Thin wrapper around YouTube Data API v3 search.

    Only does read operations — no OAuth required, just an API key.
    """

    def __init__(self):
        self.api_key = getattr(settings, "YOUTUBE_API_KEY", "")
        if not self.api_key:
            raise YouTubeError(
                "YOUTUBE_API_KEY must be set in settings (or the YOUTUBE_API_KEY "
                "environment variable).  Get one from Google Cloud Console → "
                "YouTube Data API v3."
            )

    # ── Public API ────────────────────────────────────────────────────────────

    def find_video_id(self, title: str, artist: str = "") -> str | None:
        """
        Search YouTube for *title* by *artist* and return the best
        matching video ID, or None if nothing suitable is found.

        Strategy
        --------
        1. Search with ``{title} {artist} worship official``.
        2. Score each result: prefer channels that match a trusted
           channel fragment, penalise covers/karaoke.
        3. Return the highest-scoring video ID.
        """
        query = self._build_query(title, artist)
        candidates = self._search(query, max_results=5)
        if not candidates:
            # Retry without worship hint if nothing found
            candidates = self._search(f"{title} {artist}".strip(), max_results=5)

        return self._pick_best(candidates, title, artist)

    def embed_url(self, video_id: str) -> str:
        return f"https://www.youtube.com/watch?v={video_id}"

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_query(self, title: str, artist: str) -> str:
        parts = [title]
        if artist:
            parts.append(artist)
        parts.append("worship official")
        return " ".join(parts)

    def _search(self, query: str, max_results: int = 5) -> list[dict]:
        """
        Call the YouTube search endpoint. Returns list of item dicts
        with keys: videoId, title, channelTitle.
        Returns [] on any error (caller decides whether to retry).
        """
        try:
            resp = requests.get(
                YOUTUBE_SEARCH_URL,
                params={
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "videoCategoryId": "10",  # Music
                    "maxResults": max_results,
                    "key": self.api_key,
                },
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return [
                {
                    "videoId": i["id"]["videoId"],
                    "title": i["snippet"]["title"],
                    "channelTitle": i["snippet"]["channelTitle"],
                }
                for i in items
                if i.get("id", {}).get("videoId")
            ]
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 403:
                raise YouTubeError(
                    "YouTube API quota exceeded or key invalid."
                ) from exc
            logger.warning("YouTube search HTTP error: %s", exc)
            return []
        except requests.RequestException as exc:
            logger.warning("YouTube search request failed: %s", exc)
            return []

    def _pick_best(
        self,
        candidates: list[dict],
        title: str,
        artist: str,
    ) -> str | None:
        """
        Score each candidate and return the video ID with the highest score.
        Returns None if the list is empty.
        """
        if not candidates:
            return None

        title_l = title.lower()
        artist_l = artist.lower()
        best_id = None
        best_score = -1

        for c in candidates:
            vid_title = c["title"].lower()
            channel = c["channelTitle"].lower()
            score = 0

            # Title match
            if title_l in vid_title:
                score += 4
            if artist_l and artist_l in vid_title:
                score += 2
            if artist_l and artist_l in channel:
                score += 3

            # Trusted channel bonus
            for frag in _TRUSTED_CHANNEL_FRAGMENTS:
                if frag in channel:
                    score += 2
                    break

            # Positive signals
            for hint in ("official", "lyric", "live"):
                if hint in vid_title:
                    score += 1

            # Negative signals — likely covers or low quality
            for bad in ("karaoke", "cover", "piano", "instrumental only", "tutorial"):
                if bad in vid_title:
                    score -= 3

            if score > best_score:
                best_score = score
                best_id = c["videoId"]

        # Require at least a weak positive signal to avoid garbage matches
        return best_id if best_score >= 0 else None
